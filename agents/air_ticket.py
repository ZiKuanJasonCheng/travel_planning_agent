"""
Air Ticket Agent - Sub-agent for handling flight ticket searches
Uses Amadeus API to search for real flight options
"""
from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.amadeus_flight import get_flight_service
from services.airline_iata_resolver import resolve_airline_iata_codes
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Optional


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
    
    if "budget" in constraints and constraints["budget"]:
        max_price = constraints["budget"].get("max_price_per_ticket")
    
    if "preference" in constraints and constraints["preference"]:
        preferred_airlines = constraints["preference"].get("airlines")
        flight_class = constraints["preference"].get("flight_class")

    if preferred_airlines:
        preferred_airlines = resolve_airline_iata_codes(preferred_airlines)

    origin = state.get("origin") or _infer_origin(state)
    num_people = state.get("num_people") or 1

    # Calculate dates
    departure_date = _calculate_departure_date(state)
    return_date = _calculate_return_date(state, days) if days > 1 else None

    # Get flight service and search
    flight_service = get_flight_service()

    try:
        flight_options = flight_service.search_flights(
            origin=_normalize_destination(origin),
            destination=_normalize_destination(destination),
            departure_date=departure_date,
            return_date=return_date,
            adults=num_people,
            max_price=max_price,
            preferred_airlines=preferred_airlines,
            flight_class=flight_class
        )
        
        if flight_options:
            # Filter and select best options
            selected_flights = _select_best_flights(flight_options, max_price, preferred_airlines)
            
            # Update transport_options with flight results
            # Merge with existing options or replace if this is the first run
            existing_options = state.get("transport_options", [])
            
            # Filter out existing flight options and add new ones
            non_flight_options = [opt for opt in existing_options if opt.get("type") != "flight"]
            state["transport_options"] = non_flight_options + selected_flights
        else:
            print(f"No flights are found! Use fallback option!")
            # No flights found, use fallback
            _add_fallback_flight_option(state, destination, max_price, preferred_airlines)
            
    except Exception as e:
        print(f"Error in air_ticket_agent: {e}")
        # Fallback to default option
        _add_fallback_flight_option(state, destination, max_price, preferred_airlines)
    
    if state.get("log_trace"):
        log_trace(
            state,
            node="air_ticket_agent",
            action="complete recommendations",
            reason="Flight options generated",
            outputs={"transport_options": deepcopy(state.get("transport_options", []))}
        )
    
    print(f"air_ticket_agent(): Found {len([opt for opt in state.get('transport_options', []) if opt.get('type') == 'flight'])} flight options")
    
    return state


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


def _normalize_destination(destination: str) -> str:
    """
    Normalize destination name to airport code
    This is a simplified version - in production, use a proper geocoding service
    """
    # Simple mapping for common destinations
    destination_map = {
        "tokyo": "NRT",
        "tyo": "NRT",
        "nrt": "NRT",
        "paris": "CDG",
        "london": "LHR",
        "hong kong": "HKG",
        "hkg": "HKG",
        "singapore": "SIN",
        "sin": "SIN",
        "bangkok": "BKK",
        "bkk": "BKK",
        "seoul": "ICN",
        "icn": "ICN",
        "sydney": "SYD",
        "syd": "SYD",
        "shenzhen": "SZX",
        "kyoto": "KIX",
        "osaka": "KIX",
        "jeju": "CJU"
    }
    
    dest_lower = destination.lower().strip()
    return destination_map.get(dest_lower, destination.upper()[:3])


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


def _select_best_flights(
    flight_options: list,
    max_price: Optional[int],
    preferred_airlines: Optional[list]
) -> list:
    """
    Select the best flight options from search results
    """
    if not flight_options:
        return []
    
    # If we have preferred airlines, prioritize those
    if preferred_airlines:
        preferred = [flight for flight in flight_options if flight.get("airline") in preferred_airlines]
        if preferred:
            return preferred[:3]  # Return top 3 preferred
    
    # Otherwise, return top 3 cheapest options
    return flight_options[:3]


def _add_fallback_flight_option(
    state: TripState,
    destination: str,
    max_price: Optional[int],
    preferred_airlines: Optional[list]
):
    """
    Add a fallback flight option when API search fails or returns no results
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
    state["transport_options"] = non_flight_options + [fallback_option]
