from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.amadeus_hotel import get_amadeus_hotel_service
from services.booking_hotel import get_booking_hotel_service
from copy import deepcopy
from datetime import datetime, timedelta


def accommodation_agent(state: TripState) -> TripState:
    constraints = state.get("constraints", {}).get("accommodation", {})
    destination = state.get("destination", "")
    days = state.get("days") or 1
    transport = (state.get("transport_options") or [{}])[0]
    
    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="execute",
            reason="Generating hotel recommendations",
            inputs={"constraints": deepcopy(constraints)}
        )

    max_price_per_night = None
    preferred_area = None

    if constraints.get("budget"):
        max_price_per_night = constraints["budget"].get("max_price_per_night")
    if constraints.get("preference"):
        preferred_area = constraints["preference"].get("area")

    check_in_date = state.get("start_date") or _default_check_in_date()
    check_out_date = state.get("end_date") or _default_check_out_date(check_in_date, days)
    print(f"accommodation_agent(): max_price_per_night: {max_price_per_night}, preferred_area: {preferred_area}, check_in_date: {check_in_date}, check_out_date: {check_out_date}")

    hotels = []

    num_people = state.get("num_people") or 1
    amadeus_service = get_amadeus_hotel_service()
    hotels = amadeus_service.search_hotels(
        destination=destination,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=num_people,
        room_quantity=1,
        max_price_per_night=max_price_per_night,
        preferred_area=preferred_area,
    )

    # Skip BOOKING.COM API
    #if not hotels:
    # booking_service = get_booking_hotel_service()
    # hotels = booking_service.search_hotels(
    #     destination=destination,
    #     check_in_date=check_in_date,
    #     check_out_date=check_out_date,
    #     adults=2,  # TBD: to be an input variable
    #     room_quantity=1,
    #     max_price_per_night=max_price_per_night,
    #     preferred_area=preferred_area,
    # )

    accommodation_options = (
        hotels[:3] if hotels
        else [_build_fallback_hotel(max_price_per_night, preferred_area)]
    )

    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="complete recommendations",
            reason="Hotel recommendations generated",
            outputs={"accommodation_options": deepcopy(accommodation_options)}
        )

    print(f"accommodation_agent(): accommodation_options: {accommodation_options}")

    return {**state, "accommodation_options": accommodation_options}


def _build_fallback_hotel(max_price_per_night, preferred_area):
    return {
        "type": "hotel",
        "name": "Fallback Hotel Option",
        "price_per_night": max_price_per_night or 200,
        "currency": "USD",
        "area": preferred_area or "city center",
        "supplier": "fallback",
        "reason": "No supplier inventory returned",
    }


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str, days: int) -> str:
    check_in = datetime.strptime(check_in_date, "%Y-%m-%d")
    nights = max(1, days)
    return (check_in + timedelta(days=nights)).strftime("%Y-%m-%d")