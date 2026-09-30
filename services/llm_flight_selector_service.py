import json
from typing import Optional, TypedDict

from services.city_iata_resolver import get_airport_coords
from services.currency import CONVERT_CURRENCY_TOOL, execute_tool_call
from services.langfuse_client import observe, update_current_generation
from services.openai_client import get_openai_client
from services.weather_mcp_service import get_weather_service, is_within_forecast_horizon

_MODEL = "gpt-4o-mini"

_MAX_TOOL_ITERATIONS = 3


class FlightSelection(TypedDict):
    round_trip_index: Optional[int]
    outbound_index: Optional[int]
    inbound_index: Optional[int]
    reason: str


_SELECT_TOOL = {
    "type": "function",
    "function": {
        "name": "select_flights",
        "description": "Select the best flight candidate(s) based on price, timing, and traveler preferences",
        "parameters": {
            "type": "object",
            "properties": {
                "round_trip_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the round-trip candidates list, or null if not applicable",
                },
                "outbound_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the outbound candidates list, or null if not applicable",
                },
                "inbound_index": {
                    "type": ["integer", "null"],
                    "description": "Index (0-based) of the best candidate in the inbound candidates list, or null if not applicable",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of why this flight (or these flights) were selected",
                },
            },
            "required": ["round_trip_index", "outbound_index", "inbound_index", "reason"],
        },
    },
}

_SYSTEM_PROMPT = """\
You are a travel assistant selecting the best flight option(s) for a traveler. \
Weigh price, departure time, arrival time, number of stops, and the traveler's stated \
preferences. Prefer fewer stops and reasonable prices unless preferences say otherwise. \
When a candidate's departure-hour weather forecast is shown, prefer candidates departing \
into better weather when the other factors are otherwise comparable, but don't sacrifice \
a clearly better price or schedule solely to avoid mediocre weather. Not every candidate \
will have weather data available; treat its absence as neutral, not a strike against it. \
Explain your choice briefly in the reason field.

Candidate prices are shown in each candidate's own currency, but any budget limit in the \
traveler's preferences (max_price_per_ticket) is denominated in USD. Call convert_currency \
to convert a price into USD before comparing it against that limit — never compare a \
non-USD price numerically against a USD budget. When you are done deliberating, call \
select_flights with your final choice."""


def _weather_text(from_iata: str, departure_date: str, depart_time: str, cache: dict) -> str:
    """Return a short ' weather: ...' fragment for a leg's departure, or '' if unavailable.

    Looks up and caches per (from_iata, departure_date, depart_time) triple within a single
    select_flights call, since the same departure leg often repeats across candidates. The
    forecast covers the hour nearest depart_time plus the hour before and after, since a
    flight's weather depends on its departure hour, not the whole day.
    """
    if not from_iata or not departure_date or not depart_time or not is_within_forecast_horizon(departure_date):
        return ""

    cache_key = (from_iata, departure_date, depart_time)
    if cache_key not in cache:
        coords = get_airport_coords(from_iata)
        if not coords:
            cache[cache_key] = None
        else:
            lat, lon = coords
            cache[cache_key] = get_weather_service().get_forecast(lat, lon, departure_date, depart_time)

    forecast = cache[cache_key]
    if not forecast or not forecast.get("hours"):
        return ""

    hour_parts = []
    for hour in forecast["hours"]:
        parts = []
        if hour.get("condition"):
            parts.append(str(hour["condition"]))
        if hour.get("precipitation_probability") is not None:
            parts.append(f"precip {hour['precipitation_probability']}%")
        if hour.get("temp") is not None:
            parts.append(f"{hour['temp']}°C")
        if parts:
            hour_parts.append(f"{hour['time']} {'/'.join(parts)}")

    if not hour_parts:
        return ""
    return f", weather: {', '.join(hour_parts)}"


def _candidates_summary(candidates: Optional[list], weather_cache: dict) -> str:
    if not candidates:
        return "(none)"
    lines = []
    for i, c in enumerate(candidates):
        outbound = c.get("outbound_legs") or []
        inbound = c.get("inbound_legs") or []
        first_out = outbound[0] if outbound else {}
        last_out = outbound[-1] if outbound else {}
        outbound_weather = _weather_text(
            first_out.get("from", ""), first_out.get("departure_date", ""),
            first_out.get("depart_time", ""), weather_cache
        )
        summary = (
            f"[{i}] price={c.get('price')} {c.get('currency', '')}, "
            f"outbound: {first_out.get('airline')} {first_out.get('from')}->{last_out.get('to')} "
            f"depart {first_out.get('depart_time')} arrive {last_out.get('arrival_time')} "
            f"({c.get('stops_outbound', 0)} stop(s), "
            f"leg price {first_out.get('price')} {first_out.get('currency') or c.get('currency', '')})"
            f"{outbound_weather}"
        )
        if inbound:
            first_in = inbound[0]
            last_in = inbound[-1]
            inbound_weather = _weather_text(
                first_in.get("from", ""), first_in.get("departure_date", ""),
                first_in.get("depart_time", ""), weather_cache
            )
            summary += (
                f"; inbound: {first_in.get('airline')} {first_in.get('from')}->{last_in.get('to')} "
                f"depart {first_in.get('depart_time')} arrive {last_in.get('arrival_time')} "
                f"({c.get('stops_inbound', 0)} stop(s), "
                f"leg price {first_in.get('price')} {first_in.get('currency') or c.get('currency', '')})"
                f"{inbound_weather}"
            )
        lines.append(summary)
    return "\n".join(lines)


@observe(as_type="generation", capture_input=False, capture_output=False)
def select_flights(
    round_trip_candidates: Optional[list] = None,
    outbound_candidates: Optional[list] = None,
    inbound_candidates: Optional[list] = None,
    outbound_preference: Optional[dict] = None,
    inbound_preference: Optional[dict] = None,
) -> FlightSelection:
    client = get_openai_client()

    weather_cache: dict = {}
    user_content = (
        f"Round-trip candidates:\n{_candidates_summary(round_trip_candidates, weather_cache)}\n\n"
        f"Outbound-only candidates:\n{_candidates_summary(outbound_candidates, weather_cache)}\n\n"
        f"Inbound-only candidates:\n{_candidates_summary(inbound_candidates, weather_cache)}\n\n"
        f"Outbound preferences: {json.dumps(outbound_preference or {})}\n"
        f"Inbound preferences: {json.dumps(inbound_preference or {})}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    tools = [CONVERT_CURRENCY_TOOL, _SELECT_TOOL]
    total_prompt_tokens = 0
    total_completion_tokens = 0
    last_model = _MODEL
    final_message = None

    for iteration in range(_MAX_TOOL_ITERATIONS):
        # On the last allowed iteration, force the terminal tool so a model that
        # keeps calling convert_currency can never exhaust the loop unselected.
        last_chance = iteration == _MAX_TOOL_ITERATIONS - 1
        response = client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=tools,
            tool_choice=(
                {"type": "function", "function": {"name": "select_flights"}}
                if last_chance else "auto"
            ),
            timeout=60,
        )
        message = response.choices[0].message
        last_model = response.model
        total_prompt_tokens += response.usage.prompt_tokens
        total_completion_tokens += response.usage.completion_tokens
        final_message = message

        tool_calls = message.tool_calls or []
        if any(tc.function.name == "select_flights" for tc in tool_calls):
            break

        if not tool_calls:
            # Model answered in prose instead of calling a tool — re-prompt with the
            # terminal tool forced rather than crashing on a missing tool call.
            messages.append({"role": "user", "content": "Call select_flights with your final choice."})
            continue

        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })
        for tc in tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": execute_tool_call(tc.function.name, tc.function.arguments),
            })

    select_calls = [
        tc for tc in (final_message.tool_calls or []) if tc.function.name == "select_flights"
    ]
    if not select_calls:
        raise ValueError("select_flights: model did not return a flight selection tool call")

    update_current_generation(
        model=last_model,
        input={"tools": tools, "messages": messages},
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
    args = json.loads(select_calls[0].function.arguments)
    return FlightSelection(
        round_trip_index=args.get("round_trip_index"),
        outbound_index=args.get("outbound_index"),
        inbound_index=args.get("inbound_index"),
        reason=args.get("reason", ""),
    )
