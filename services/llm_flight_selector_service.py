import json
import os
from typing import Optional, TypedDict

from openai import OpenAI


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
Explain your choice briefly in the reason field."""


def _candidates_summary(candidates: Optional[list]) -> str:
    if not candidates:
        return "(none)"
    lines = []
    for i, c in enumerate(candidates):
        outbound = c.get("outbound_legs") or []
        inbound = c.get("inbound_legs") or []
        first_out = outbound[0] if outbound else {}
        last_out = outbound[-1] if outbound else {}
        summary = (
            f"[{i}] price={c.get('price')} {c.get('currency', '')}, "
            f"outbound: {first_out.get('airline')} {first_out.get('from')}->{last_out.get('to')} "
            f"depart {first_out.get('depart_time')} arrive {last_out.get('arrival_time')} "
            f"({c.get('stops_outbound', 0)} stop(s))"
        )
        if inbound:
            first_in = inbound[0]
            last_in = inbound[-1]
            summary += (
                f"; inbound: {first_in.get('airline')} {first_in.get('from')}->{last_in.get('to')} "
                f"depart {first_in.get('depart_time')} arrive {last_in.get('arrival_time')} "
                f"({c.get('stops_inbound', 0)} stop(s))"
            )
        lines.append(summary)
    return "\n".join(lines)


def select_flights(
    round_trip_candidates: Optional[list] = None,
    outbound_candidates: Optional[list] = None,
    inbound_candidates: Optional[list] = None,
    outbound_preference: Optional[dict] = None,
    inbound_preference: Optional[dict] = None,
) -> FlightSelection:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    user_content = (
        f"Round-trip candidates:\n{_candidates_summary(round_trip_candidates)}\n\n"
        f"Outbound-only candidates:\n{_candidates_summary(outbound_candidates)}\n\n"
        f"Inbound-only candidates:\n{_candidates_summary(inbound_candidates)}\n\n"
        f"Outbound preferences: {json.dumps(outbound_preference or {})}\n"
        f"Inbound preferences: {json.dumps(inbound_preference or {})}"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        tools=[_SELECT_TOOL],
        tool_choice={"type": "function", "function": {"name": "select_flights"}},
        timeout=60,
    )
    args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    return FlightSelection(
        round_trip_index=args.get("round_trip_index"),
        outbound_index=args.get("outbound_index"),
        inbound_index=args.get("inbound_index"),
        reason=args.get("reason", ""),
    )
