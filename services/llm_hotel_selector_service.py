"""
LLM hotel selector.

Runs after `stayingapi_hotel.search_hotels()` has fetched and price/area-filtered
candidates, and decides which of them the traveler should actually book. Its main
job is the one a price sort can't do: when the traveler lands late, split the stay
across two hotels — one near the airport for the landing night, one better placed
for the rest of the trip.

Mirrors `llm_flight_selector_service.py`: a terminal tool forces a structured
answer, the loop is capped, and the final iteration withholds choice so the model
must commit.
"""
import json
import logging
import math
from typing import Optional

from services.langfuse_client import observe, update_current_generation
from services.llm_retry import call_with_retry
from services.openai_client import get_openai_client

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_MAX_TOOL_ITERATIONS = 3

# Departures at or after this hour on day 1 mean the traveler arrives late enough
# that a hotel near the airport is worth considering for the first night.
_LATE_ARRIVAL_HOUR = 21

# An arrival before this hour is still the night the traveler was already flying
# through, so the room they need is the one they land into. From 04:00 onward the
# traveler is effectively arriving on the new day and night 1 no longer exists.
_SMALL_HOURS_CUTOFF_HOUR = 4


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in kilometres."""
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _minutes(hhmmss: Optional[str]) -> Optional[int]:
    """Parse an HH:MM or HH:MM:SS clock time into minutes since midnight."""
    if not hhmmss:
        return None
    parts = hhmmss.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def resolve_check_in_date(
    departure_date: Optional[str],
    arrival_date: Optional[str],
    arrival_time: Optional[str],
    trip_start_date: Optional[str],
) -> Optional[str]:
    """Return the check-in date the hotel search should use for the outbound leg.

    A leg departing on day 1 and landing on day 2 shifts check-in to day 2, because
    night 1 was spent in the air and paying for it would be wasted — *unless* it
    lands in the small hours (before 04:00), where the traveler still needs the room
    they are landing into, so check-in stays on the trip's first day.
    """
    if not departure_date or not arrival_date or not trip_start_date:
        return trip_start_date

    if arrival_date <= departure_date:
        # Landed the same day it left — no night was spent in the air.
        return trip_start_date

    arrive_minutes = _minutes(arrival_time)
    if arrive_minutes is not None and arrive_minutes < _SMALL_HOURS_CUTOFF_HOUR * 60:
        return trip_start_date

    return arrival_date


_SELECT_TOOL = {
    "type": "function",
    "function": {
        "name": "select_hotel_stays",
        "description": (
            "Select the hotel(s) the traveler should book, covering the whole stay "
            "with one or more candidate hotels"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stays": {
                    "type": "array",
                    "description": (
                        "One entry per hotel booked, ordered by check_in_date. Together "
                        "they must cover the whole stay with no gap and no overlap."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_index": {
                                "type": "integer",
                                "description": "Index (0-based) of the chosen hotel in the candidate list",
                            },
                            "check_in_date": {
                                "type": "string",
                                "description": "Check-in date for this hotel, YYYY-MM-DD",
                            },
                            "check_out_date": {
                                "type": "string",
                                "description": "Check-out date for this hotel, YYYY-MM-DD",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Brief explanation of why this hotel suits this part of the stay",
                            },
                        },
                        "required": ["candidate_index", "check_in_date", "check_out_date", "reason"],
                    },
                },
            },
            "required": ["stays"],
        },
    },
}


_SYSTEM_PROMPT = """\
You are a travel assistant choosing accommodation for a traveler from a shortlist of \
hotel candidates. Weigh price, location, distance to the airport, and how well each \
candidate matches the traveler's stated preferences.

The traveler's stay window is given as an explicit check_in_date and check_out_date. \
Book exactly that window: the stays you return must together cover it with no gap and \
no overlap, and every stay must use the exact date strings given rather than ones you \
calculate yourself.

Splitting the stay across two hotels is sometimes right and sometimes wrong, so judge \
it from the arrival information:
- If the traveler lands late in the evening or in the small hours after a flight that \
departed the previous day, the first night is best spent at a hotel near the airport, \
with a better-placed hotel (closer to what they came to see) for the remaining nights.
- If the arrival is a normal daytime or early-evening arrival, book a single hotel for \
the whole stay.
- If there is no night 1 in the window at all, book a single hotel for the whole stay.
Never return a split that would leave an uncovered night, and never book more hotels \
than the window can support (a one-night window is one hotel).

Explain each choice briefly in the reason field, then call select_hotel_stays."""


def _candidate_line(index: int, hotel: dict) -> str:
    distance = hotel.get("distance_to_airport_km")
    distance_text = f"{distance} km" if distance is not None else "unknown"
    lat, lon = hotel.get("lat"), hotel.get("lon")
    coords = f"({lat}, {lon})" if lat is not None and lon is not None else "(unknown)"
    return (
        f"[{index}] {hotel.get('name', 'Unknown Hotel')}, area={hotel.get('area', 'unknown')}, "
        f"price_per_night={hotel.get('price_per_night')} {hotel.get('currency', '')}, "
        f"distance_to_airport={distance_text}, coords={coords}"
    )


def _candidates_summary(candidates: list) -> str:
    if not candidates:
        return "(none)"
    return "\n".join(_candidate_line(i, hotel) for i, hotel in enumerate(candidates))


def _stays_from_message(final_message) -> list[dict]:
    """Pull the `stays` list out of the terminal tool call, or [] if it never came."""
    for tool_call in final_message.tool_calls or []:
        if tool_call.function.name != "select_hotel_stays":
            continue
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            logger.error("select_hotels(): select_hotel_stays arguments were not valid JSON")
            return []
        stays = args.get("stays")
        return stays if isinstance(stays, list) else []
    return []


@observe(as_type="generation", capture_input=False, capture_output=False)
def select_hotels(
    candidates: list,
    check_in_date: Optional[str],
    check_out_date: Optional[str],
    destination: Optional[str] = None,
    airport_iata: Optional[str] = None,
    arrival_time: Optional[str] = None,
    arrival_date: Optional[str] = None,
    num_people: int = 1,
    preference: Optional[dict] = None,
) -> list[dict]:
    """Choose which candidate hotels to book, and for which nights.

    Returns a list of candidate dicts (in the order the model returned them) each
    augmented with `check_in_date` and `check_out_date`. An empty list means the
    model never produced a usable selection — callers should fall back to their own
    default ordering rather than treating it as "no hotels".
    """
    if not candidates:
        return []

    client = get_openai_client()

    arrival_note = f"Arrival time: {arrival_time or 'unknown'}"
    if arrival_date:
        arrival_note += f" on {arrival_date}"

    user_content = (
        f"Destination: {destination or 'unknown'}\n"
        f"Destination airport: {airport_iata or 'unknown'}\n"
        f"Number of travelers: {num_people}\n"
        f"{arrival_note}\n"
        f"Stay window: check_in_date={check_in_date}, check_out_date={check_out_date}\n"
        f"Traveler preferences: {json.dumps(preference or {})}\n\n"
        f"Hotel candidates:\n{_candidates_summary(candidates)}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    total_prompt_tokens = 0
    total_completion_tokens = 0
    last_model = _MODEL
    final_message = None

    for iteration in range(_MAX_TOOL_ITERATIONS):
        # On the last allowed iteration, force the terminal tool so a model that
        # keeps deliberating in prose can never exhaust the loop unselected.
        last_chance = iteration == _MAX_TOOL_ITERATIONS - 1
        response = call_with_retry(
            client.chat.completions.create,
            model=_MODEL,
            messages=messages,
            tools=[_SELECT_TOOL],
            tool_choice=(
                {"type": "function", "function": {"name": "select_hotel_stays"}}
                if last_chance else "auto"
            ),
            timeout=60,
        )
        message = response.choices[0].message
        last_model = response.model
        total_prompt_tokens += response.usage.prompt_tokens
        total_completion_tokens += response.usage.completion_tokens
        final_message = message

        if any(tc.function.name == "select_hotel_stays" for tc in (message.tool_calls or [])):
            break

        if not message.tool_calls:
            # Answered in prose instead of calling the tool — ask again with the
            # terminal tool forced rather than returning nothing.
            messages.append({"role": "user", "content": "Call select_hotel_stays with your final choice."})
            continue

        # A tool call that isn't ours: nothing else is offered, so this only happens
        # if the model invents one. Feed back an error so it can correct itself.
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in message.tool_calls],
        })
        for tool_call in message.tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps({"error": f"Unknown tool '{tool_call.function.name}'"}),
            })

    update_current_generation(
        model=last_model,
        input={"tools": [_SELECT_TOOL], "messages": messages},
        output={
            "role": final_message.role,
            "content": final_message.content,
            "tool_calls": [tc.model_dump() for tc in (final_message.tool_calls or [])],
        },
        usage_details={
            "input": total_prompt_tokens,
            "output": total_completion_tokens,
        },
        model_parameters={"tool_choice": "auto"},
    )

    stays = _stays_from_message(final_message)
    selected = []
    for stay in stays:
        if not isinstance(stay, dict):
            continue
        index = stay.get("candidate_index")
        # A hallucinated index would otherwise become a phantom hotel.
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            logger.warning(f"select_hotels(): dropping out-of-range candidate_index {index!r}")
            continue
        selected.append({
            **candidates[index],
            "check_in_date": stay.get("check_in_date") or check_in_date,
            "check_out_date": stay.get("check_out_date") or check_out_date,
        })
    return selected
