"""
Air Ticket Agent - Sub-agent for handling flight ticket searches
Uses Amadeus API to search for real flight options
"""
from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.amadeus_flight import AmadeusFlightService, get_flight_service
from services.airline_iata_resolver import resolve_airline_iata_codes
from services.city_iata_resolver import resolve_city_iata_codes
from services.llm_flight_selector_service import select_flights
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Optional


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

    Returns one of "full", "outbound_only", "inbound_only", "both_one_way", "none".
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
        return "both_one_way"
    if wants_outbound:
        return "outbound_only"
    if wants_inbound:
        return "inbound_only"
    return "none"


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


def air_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending air ticket options
    Uses Amadeus API to find real flight options based on constraints
    """
    destination = state.get("destination", "")
    constraints = state.get("constraints", {}).get("transport", {})
    days = state.get("days", 1)
    
    if state.get("log_trace"):
        log_trace(
            state,
            node="air_ticket_agent",
            action="execute",
            reason="Searching for flight options",
            inputs={"constraints": deepcopy(constraints), "destination": destination}
        )
    
    # Extract constraints
    max_price = None
    preferred_airlines = None
    flight_class = None
    excluded_airlines = None
    accept_redeye_flights = True
    direct_flights_only = False
    preferred_departure_timeslots = None

    if "budget" in constraints and constraints["budget"]:
        max_price = constraints["budget"].get("max_price_per_ticket")

    if "preference" in constraints and constraints["preference"]:
        pref = constraints["preference"]
        preferred_airlines = pref.get("airlines")
        flight_class = pref.get("flight_class")
        excluded_airlines = pref.get("excluded_airlines")
        direct_flights_only = pref.get("direct_flights_only", False)
        preferred_departure_timeslots = pref.get("preferred_departure_timeslots")
        accept_redeye_raw = pref.get("accept_redeye_flights")  # None if not explicitly set
        if accept_redeye_raw is None:
            # Auto-reject red-eye when user specified preferred timeslots
            accept_redeye_flights = not bool(preferred_departure_timeslots)
        else:
            accept_redeye_flights = accept_redeye_raw

    if preferred_airlines:
        preferred_airlines = resolve_airline_iata_codes(preferred_airlines)
    if excluded_airlines:
        excluded_airlines = resolve_airline_iata_codes(excluded_airlines)

    origin = state.get("origin")  #or _infer_origin(state)
    num_people = state.get("num_people") or 1

    # Calculate dates
    departure_date = _calculate_departure_date(state)
    return_date = _calculate_return_date(state, days) if days > 1 else None

    origin_codes = resolve_city_iata_codes(origin)
    dest_codes = resolve_city_iata_codes(destination)

    # Get flight service and search
    flight_service = get_flight_service()

    try:
        flight_options = _search_all_combos(
            flight_service, origin_codes, dest_codes,
            departure_date, return_date, num_people,
            max_price, preferred_airlines, flight_class,
            excluded_airlines, accept_redeye_flights, direct_flights_only,
            preferred_departure_timeslots,
        )

        if not flight_options and return_date:
            print("No round-trip flights found. Searching outbound and inbound separately.")
            outbound_options, inbound_options = _search_one_way_pair(
                flight_service=flight_service,
                origin_codes=origin_codes,
                dest_codes=dest_codes,
                departure_date=departure_date,
                return_date=return_date,
                adults=num_people,
                max_price=max_price,
                preferred_airlines=preferred_airlines,
                flight_class=flight_class,
                excluded_airlines=excluded_airlines,
                accept_redeye_flights=accept_redeye_flights,
                direct_flights_only=direct_flights_only,
                preferred_departure_timeslots=preferred_departure_timeslots,
            )
            selected_flights = outbound_options[:3] + inbound_options[:3]
        elif flight_options:
            selected_flights = flight_options[:3]
        else:
            selected_flights = []

        if selected_flights:
            existing_options = state.get("transport_options", [])
            non_flight_options = [opt for opt in existing_options if opt.get("type") != "flight"]
            transport_options = non_flight_options + selected_flights
        else:
            print("No flights are found! Use fallback option!")
            transport_options = _build_fallback_flight_options(state, destination, max_price, preferred_airlines)

    except Exception as e:
        print(f"Error in air_ticket_agent: {e}")
        transport_options = _build_fallback_flight_options(state, destination, max_price, preferred_airlines)

    if state.get("log_trace"):
        log_trace(
            state,
            node="air_ticket_agent",
            action="complete recommendations",
            reason="Flight options generated",
            outputs={"transport_options": deepcopy(transport_options)}
        )

    print(f"air_ticket_agent(): Found {len([opt for opt in transport_options if opt.get('type') == 'flight'])} flight options")

    return {**state, "transport_options": transport_options}


def _infer_origin(state: TripState) -> str:
    """
    Infer origin location from state
    In a real system, this would come from user input
    For now, use a default or try to extract from existing transport options
    """
    # Check if there's a return flight that might indicate origin
    existing_options = state.get("transport_options", [])
    for option in existing_options:
        if option.get("type") == "flight" and option.get("from"):
            return option["from"]
    
    # Default origin - could be made configurable
    # Common defaults: "NYC" (New York), "LAX" (Los Angeles), "SFO" (San Francisco)
    # For international travel, might want to use user's location
    return "HKG"  # Default to Hong Kong


def _calculate_departure_date(state: TripState) -> str:
    """
    Returns start_date from state if provided, otherwise defaults to 30 days from now.
    """
    if state.get("start_date"):
        return state["start_date"]
    departure = datetime.now() + timedelta(days=30)
    return departure.strftime("%Y-%m-%d")


def _calculate_return_date(state: TripState, days: int) -> Optional[str]:
    """
    Calculate return date based on trip duration
    """
    if days <= 1:
        return None
    
    departure_date = _calculate_departure_date(state)
    departure = datetime.strptime(departure_date, "%Y-%m-%d")
    return_date = departure + timedelta(days=days)
    return return_date.strftime("%Y-%m-%d")


def _search_all_combos(
    flight_service: AmadeusFlightService,
    origin_codes: list[str],
    dest_codes: list[str],
    departure_date: str,
    return_date: Optional[str],
    adults: int,
    max_price: Optional[int],
    preferred_airlines: Optional[list],
    flight_class: Optional[str],
    excluded_airlines: Optional[list] = None,
    accept_redeye_flights: bool = True,
    direct_flights_only: bool = False,
    preferred_departure_timeslots: Optional[list] = None,
) -> list:
    """Search every origin×destination code combination and return combined results."""
    results = []
    for oc in origin_codes:
        for dc in dest_codes:
            results.extend(flight_service.search_flights(
                origin=oc,
                destination=dc,
                departure_date=departure_date,
                return_date=return_date,
                adults=adults,
                max_price=max_price,
                preferred_airlines=preferred_airlines,
                flight_class=flight_class,
                excluded_airlines=excluded_airlines,
                accept_redeye_flights=accept_redeye_flights,
                direct_flights_only=direct_flights_only,
                preferred_departure_timeslots=preferred_departure_timeslots,
            ))
    return results


def _search_one_way_pair(
    flight_service: AmadeusFlightService,
    origin_codes: list[str],
    dest_codes: list[str],
    departure_date: str,
    return_date: str,
    adults: int,
    max_price: Optional[int],
    preferred_airlines: Optional[list],
    flight_class: Optional[str],
    excluded_airlines: Optional[list] = None,
    accept_redeye_flights: bool = True,
    direct_flights_only: bool = False,
    preferred_departure_timeslots: Optional[list] = None,
) -> tuple[list, list]:
    """Search outbound and inbound as separate one-way tickets; return (outbound, inbound)."""
    outbound = _search_all_combos(
        flight_service, origin_codes, dest_codes, departure_date, None,
        adults, max_price, preferred_airlines, flight_class,
        excluded_airlines, accept_redeye_flights, direct_flights_only,
        preferred_departure_timeslots,
    )
    inbound = _search_all_combos(
        flight_service, dest_codes, origin_codes, return_date, None,
        adults, max_price, preferred_airlines, flight_class,
        excluded_airlines, accept_redeye_flights, direct_flights_only,
        preferred_departure_timeslots,
    )
    return outbound, inbound


def _build_fallback_flight_options(
    state: TripState,
    destination: str,
    max_price: Optional[int],
    preferred_airlines: Optional[list]
) -> list:
    """
    Build fallback flight options when API search fails or returns no results
    """
    airline = preferred_airlines[0] if preferred_airlines else "CX"
    price = max_price if max_price else 500

    fallback_option = {
        "type": "flight",
        "to": destination,
        "airline": airline,
        "price": price,
        "depart_time": "18:25:00",
        "arrival_time": "22:00:00",
        "reason": "Fallback option (API unavailable)"
    }

    existing_options = state.get("transport_options", [])
    non_flight_options = [opt for opt in existing_options if opt.get("type") != "flight"]
    return non_flight_options + [fallback_option]
