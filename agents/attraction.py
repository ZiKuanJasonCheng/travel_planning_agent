from copy import deepcopy

from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.llm_itinerary_service import get_llm_itinerary_service


def attraction_agent(state: TripState) -> TripState:
    # Step 1: Extract state
    destination = state.get("destination", "")
    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    days = state.get("days") or 1
    start_date = state.get("start_date")
    constraints = state.get("constraints", {}).get("attraction", {})
    transport = (state.get("transport_options") or [{}])[0]
    hotel = (state.get("accommodation_options") or [{}])[0]
    arrival_time = transport.get("arrival_time")
    return_depart_time = transport.get("return_depart_time")
    hotel_area = hotel.get("area")

    # Step 2: Extract constraints
    max_price_per_ticket = None
    styles = None
    must_go_places = None

    if constraints.get("budget"):
        max_price_per_ticket = constraints["budget"].get("max_price_per_ticket")
    if constraints.get("preference"):
        styles = constraints["preference"].get("styles")
        must_go_places = constraints["preference"].get("must_go_places")
    print(f"attraction_agent(): max_price_per_ticket: {max_price_per_ticket}, styles: {styles}, must_go_places: {must_go_places}")

    # Step 3: log_trace at entry
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="execute",
            reason="Generating attraction recommendations",
            inputs={"constraints": deepcopy(constraints)},
        )

    # Step 4: Generate or update itinerary via LLM
    llm_service = get_llm_itinerary_service()
    existing_itinerary = state.get("itinerary")

    try:
        if existing_itinerary:
            itinerary = llm_service.update_itinerary(
                existing_itinerary=existing_itinerary,
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                styles=styles,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
            )
        else:
            itinerary = llm_service.generate_itinerary(
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                start_date=start_date,
                styles=styles,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
            )

        if not itinerary:
            itinerary = _build_fallback_itinerary(destination, days, hotel_area)
    except Exception as e:
        print(f"attraction_agent(): unexpected error: {e}")
        itinerary = _build_fallback_itinerary(destination, days, hotel_area)

    # Step 5: log_trace at exit
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="complete recommendations",
            reason="Attraction itinerary generated",
            outputs={"itinerary": deepcopy(itinerary)},
        )

    print(f"attraction_agent(): generated {len(itinerary)} days")
    return {**state, "itinerary": itinerary}


def _build_fallback_itinerary(
    destination: str, days: int, hotel_area: str | None
) -> list[dict]:
    """Static fallback when the LLM service is unavailable."""
    area = hotel_area or destination
    base_activities = [
        {
            "name": f"Morning walk around {area}",
            "type": "activity",
            "short_desc": f"Explore the {area} neighbourhood on foot.",
            "time_slot": "morning",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Lunch at a local {destination} restaurant",
            "type": "restaurant",
            "short_desc": "Sample the local cuisine at a nearby restaurant.",
            "time_slot": "afternoon",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Sightseeing in {destination}",
            "type": "sightseeing",
            "short_desc": f"Visit notable landmarks and attractions in {destination}.",
            "time_slot": "afternoon",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "2 hours",
            "area": destination,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
        {
            "name": f"Dinner in {destination}",
            "type": "restaurant",
            "short_desc": "Enjoy a relaxed dinner with local flavours.",
            "time_slot": "evening",
            "estimated_cost": None,
            "currency": "",
            "minimum_duration": "1 hour",
            "area": area,
            "supplier": "fallback",
            "reason": "Static fallback",
        },
    ]
    return [{"day": d, "activities": list(base_activities)} for d in range(1, days + 1)]
