"""
Air Ticket Agent - Sub-agent for handling flight ticket searches
Uses Amadeus API to search for real flight options
"""
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Optional

from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from services.amadeus_flight import AmadeusFlightService, get_flight_service
from services.airline_iata_resolver import resolve_airline_iata_codes
from services.city_iata_resolver import resolve_city_iata_codes
from services.llm_flight_selector_service import select_flights


_ERROR_REASONS = {"Amadeus API error", "Unknown error"}

_NO_RESULTS_MESSAGE = {
    "reason": "No suitable flights were found. Please change your flight preferences and submit feedback again."
}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run transport/flight service again'."
    )
}

_PARTIAL_ERROR_WARNING = (
    " There were a few API errors during the run. Therefore, the selected flight "
    "might not be the best option. You can wait for a few minutes and submit "
    "feedback saying 'Run transport/flight service again'."
)


def _resolve_preference(pref_dict: Optional[dict]) -> dict:
    """Resolve a FlightPreferenceConstraint dict into a search-ready preference dict."""
    if not pref_dict:
        return {}

    airlines = pref_dict.get("airlines")
    excluded_airlines = pref_dict.get("excluded_airlines")
    preferred_departure_timeslots = pref_dict.get("preferred_departure_timeslots")
    accept_redeye_raw = pref_dict.get("accept_redeye_flights")
    if accept_redeye_raw is None:
        accept_redeye_flights = not bool(preferred_departure_timeslots)
    else:
        accept_redeye_flights = accept_redeye_raw

    return {
        "max_price_per_ticket": pref_dict.get("max_price_per_ticket"),
        "airlines": resolve_airline_iata_codes(airlines) if airlines else None,
        "flight_class": pref_dict.get("flight_class"),
        "excluded_airlines": resolve_airline_iata_codes(excluded_airlines) if excluded_airlines else None,
        "accept_redeye_flights": accept_redeye_flights,
        "direct_flights_only": pref_dict.get("direct_flights_only", False),
        "preferred_departure_timeslots": preferred_departure_timeslots,
    }


def _search_mode(state: TripState, transport_constraints: dict) -> str:
    """Decide how much of the flight search to (re)run this pass.

    Returns one of "full", "outbound_only", "inbound_only", "none".
    """
    feedback = state.get("feedback")
    rerun_planning = transport_constraints.get("rerun_planning")

    if feedback is None or rerun_planning is True:
        return "full"

    last_feedback = state.get("last_feedback_constraints") or {}
    last_transport = last_feedback.get("transport") or {}
    wants_outbound = "outbound_air_ticket_preference" in last_transport
    wants_inbound = "inbound_air_ticket_preference" in last_transport

    if wants_outbound and wants_inbound:
        # Both directions changed at once: prefer a fresh round-trip search
        # (with one-way fallback) over jumping straight to two one-way
        # searches — "full" already does round-trip-first, one-way-fallback.
        return "full"
    if wants_outbound:
        return "outbound_only"
    if wants_inbound:
        return "inbound_only"
    return "none"


def _split_candidates(candidates: list) -> tuple:
    """Return (valid_candidates, has_errors, all_errors)."""
    if not candidates:
        return [], False, False
    valid = [c for c in candidates if c.get("reason") not in _ERROR_REASONS]
    has_errors = len(valid) < len(candidates)
    all_errors = has_errors and len(valid) == 0
    return valid, has_errors, all_errors


def _resolve_one_way_direction(candidates: list, preference: dict, direction: str) -> list:
    """direction is 'outbound' or 'inbound' — used only to route the correct preference
    dict and read the correct index from the LLM selection. One-way search candidates always
    carry their single leg list under "outbound_legs" regardless of which real-world direction
    they represent (only true round-trip candidates ever populate "inbound_legs"), so legs are
    always read from "outbound_legs" here."""
    if not candidates:
        return [dict(_NO_RESULTS_MESSAGE)]

    valid, has_errors, all_errors = _split_candidates(candidates)

    if all_errors:
        return [dict(_ERROR_MESSAGE)]
    if not valid:
        return [dict(_NO_RESULTS_MESSAGE)]

    selection = select_flights(
        outbound_candidates=valid if direction == "outbound" else None,
        inbound_candidates=valid if direction == "inbound" else None,
        outbound_preference=preference if direction == "outbound" else None,
        inbound_preference=preference if direction == "inbound" else None,
    )
    index = selection["outbound_index"] if direction == "outbound" else selection["inbound_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    legs = chosen.get("outbound_legs") or []
    return [{**leg, "reason": reason} for leg in legs]


def _resolve_round_trip(candidates: list):
    """Returns (outbound_legs, inbound_legs) for the LLM-chosen round-trip candidate,
    or None if the caller should fall back to a one-way pair search."""
    if not candidates:
        return None
    valid, has_errors, all_errors = _split_candidates(candidates)
    if all_errors or not valid:
        return None

    selection = select_flights(round_trip_candidates=valid)
    index = selection["round_trip_index"]
    if index is None or not (0 <= index < len(valid)):
        index = 0
    chosen = valid[index]
    reason = selection["reason"]
    if has_errors:
        reason += _PARTIAL_ERROR_WARNING
    outbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("outbound_legs") or [])]
    inbound_legs = [{**leg, "reason": reason} for leg in (chosen.get("inbound_legs") or [])]
    return outbound_legs, inbound_legs


def _calculate_departure_date(state: TripState) -> str:
    """Returns start_date from state if provided, otherwise defaults to 30 days from now."""
    if state.get("start_date"):
        return state["start_date"]
    departure = datetime.now() + timedelta(days=30)
    return departure.strftime("%Y-%m-%d")


def _calculate_return_date(state: TripState, days: int) -> Optional[str]:
    """Calculate return date based on trip duration."""
    if days <= 1:
        return None
    departure_date = _calculate_departure_date(state)
    departure = datetime.strptime(departure_date, "%Y-%m-%d")
    return_date = departure + timedelta(days=days)
    return return_date.strftime("%Y-%m-%d")


def _search_all_combos(
    flight_service: AmadeusFlightService,
    origin_codes: list,
    dest_codes: list,
    departure_date: str,
    return_date: Optional[str],
    adults: int,
    outbound_preference: dict,
    inbound_preference: Optional[dict],
) -> list:
    """Search every origin×destination code combination and return combined results."""
    results = []
    for oc in origin_codes:
        for dc in dest_codes:
            results.extend(flight_service.search_flights(
                origin=oc, destination=dc, departure_date=departure_date, return_date=return_date,
                adults=adults, outbound_preference=outbound_preference, inbound_preference=inbound_preference,
            ))
    return results


def _search_one_way_pair(
    flight_service: AmadeusFlightService,
    origin_codes: list,
    dest_codes: list,
    departure_date: str,
    return_date: str,
    adults: int,
    outbound_preference: dict,
    inbound_preference: dict,
) -> tuple:
    """Search outbound and inbound as separate one-way tickets; return (outbound, inbound)."""
    outbound = _search_all_combos(
        flight_service, origin_codes, dest_codes, departure_date, None,
        adults, outbound_preference, None,
    )
    inbound = _search_all_combos(
        flight_service, dest_codes, origin_codes, return_date, None,
        adults, inbound_preference, None,
    )
    return outbound, inbound


def air_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending air ticket options.
    Uses Amadeus API to find real flight options, split by outbound/inbound direction.
    """
    destination = state.get("destination", "")
    days = state.get("days", 1)
    transport_constraints = dict(state.get("constraints", {}).get("transport") or {})

    if state.get("log_trace"):
        log_trace(
            state, node="air_ticket_agent", action="execute",
            reason="Searching for flight options",
            inputs={"constraints": deepcopy(transport_constraints), "destination": destination},
        )

    outbound_preference = _resolve_preference(transport_constraints.get("outbound_air_ticket_preference"))
    inbound_preference = _resolve_preference(transport_constraints.get("inbound_air_ticket_preference"))

    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    departure_date = _calculate_departure_date(state)
    return_date = _calculate_return_date(state, days) if days > 1 else None

    origin_codes = resolve_city_iata_codes(origin)
    dest_codes = resolve_city_iata_codes(destination)

    flight_service = get_flight_service()
    existing_transport_options = state.get("transport_options") or default_transport_options()
    existing_flight = existing_transport_options.get("flight") or {"outbound": [], "inbound": []}

    mode = _search_mode(state, transport_constraints)
    rerun_planning_was_set = transport_constraints.get("rerun_planning") is True

    try:
        if mode == "full":
            if return_date:
                round_trip_candidates = _search_all_combos(
                    flight_service, origin_codes, dest_codes, departure_date, return_date,
                    num_people, outbound_preference, inbound_preference,
                )
                resolved = _resolve_round_trip(round_trip_candidates)
                if resolved is not None:
                    outbound_legs, inbound_legs = resolved
                else:
                    outbound_candidates, inbound_candidates = _search_one_way_pair(
                        flight_service, origin_codes, dest_codes, departure_date, return_date,
                        num_people, outbound_preference, inbound_preference,
                    )
                    outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
                    inbound_legs = _resolve_one_way_direction(inbound_candidates, inbound_preference, "inbound")
            else:
                outbound_candidates = _search_all_combos(
                    flight_service, origin_codes, dest_codes, departure_date, None,
                    num_people, outbound_preference, None,
                )
                outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
                inbound_legs = []

        elif mode == "outbound_only":
            outbound_candidates = _search_all_combos(
                flight_service, origin_codes, dest_codes, departure_date, None,
                num_people, outbound_preference, None,
            )
            outbound_legs = _resolve_one_way_direction(outbound_candidates, outbound_preference, "outbound")
            inbound_legs = existing_flight.get("inbound", [])

        elif mode == "inbound_only":
            inbound_candidates = _search_all_combos(
                flight_service, dest_codes, origin_codes, return_date or departure_date, None,
                num_people, inbound_preference, None,
            )
            inbound_legs = _resolve_one_way_direction(inbound_candidates, inbound_preference, "inbound")
            outbound_legs = existing_flight.get("outbound", [])

        else:  # "none"
            outbound_legs = existing_flight.get("outbound", [])
            inbound_legs = existing_flight.get("inbound", [])

        transport_options = {
            **existing_transport_options,
            "flight": {"outbound": outbound_legs, "inbound": inbound_legs},
        }

    except Exception as e:
        print(f"Error in air_ticket_agent: {e}")
        transport_options = {
            **existing_transport_options,
            "flight": {
                "outbound": existing_flight.get("outbound") or [dict(_ERROR_MESSAGE)],
                "inbound": existing_flight.get("inbound") or [dict(_ERROR_MESSAGE)],
            },
        }

    new_state = {**state, "transport_options": transport_options}

    if rerun_planning_was_set:
        constraints = dict(new_state.get("constraints") or {})
        transport = dict(constraints.get("transport") or {})
        transport["rerun_planning"] = None
        constraints["transport"] = transport
        new_state["constraints"] = constraints

    if state.get("log_trace"):
        log_trace(
            new_state, node="air_ticket_agent", action="complete recommendations",
            reason="Flight options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    return new_state
