from copy import deepcopy

from states.trip_state import TripState
from orchestration.tracability import log_trace
from orchestration.merge_constraints import resolve_category_constraints
from services.llm_itinerary_service import get_llm_itinerary_service


_ERROR_REASONS = {"LLM API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's an LLM API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run attraction service again'."
    )
}


def _had_errors(itinerary: list) -> bool:
    """Check a list of day-plans (either a fresh LLM result or a previously-
    persisted state["itinerary"]) for an error-tier reason, covering both the
    raw error tags and the persisted long-form message, at either the day-plan
    or activity level."""
    error_texts = _ERROR_REASONS | {_ERROR_MESSAGE["reason"]}
    for day_plan in itinerary:
        if day_plan.get("reason") in error_texts:
            return True
        for act in day_plan.get("activities", []):
            if act.get("reason") in error_texts:
                return True
    return False


def attraction_agent(state: TripState) -> TripState:
    merged, unchanged = resolve_category_constraints(state, "attraction")
    existing_itinerary = state.get("itinerary") or []

    should_skip = (
        unchanged
        and merged.get("rerun_planning") is not True
        and state.get("checker_critique") is None
        and not _had_errors(existing_itinerary)
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["attraction"] = merged
        return {**state, "constraints": constraints}

    # Step 1: Extract state
    destination = state.get("destination", "")
    origin = state.get("origin")
    num_people = state.get("num_people") or 1
    days = state.get("days") or 1
    start_date = state.get("start_date")
    checker_critique = state.get("checker_critique")
    transport_options = state.get("transport_options") or {}
    flight = transport_options.get("flight") or {}
    outbound_legs = flight.get("outbound") or []
    inbound_legs = flight.get("inbound") or []
    hotel = (state.get("accommodation_options") or [{}])[0]
    arrival_time = outbound_legs[-1].get("arrival_time") if outbound_legs else None
    return_depart_time = inbound_legs[0].get("depart_time") if inbound_legs else None
    hotel_area = hotel.get("area")
    hotel_lat = hotel.get("lat")
    hotel_lon = hotel.get("lon")

    # Step 2: Extract constraints
    preference = merged.get("preference") or {}
    max_price_per_ticket = preference.get("max_price_per_ticket")
    styles = preference.get("styles")
    must_go_places = preference.get("must_go_places")
    exclusions = preference.get("exclusions")
    print(f"attraction_agent(): max_price_per_ticket: {max_price_per_ticket}, styles: {styles}, must_go_places: {must_go_places}, exclusions: {exclusions}")

    # Step 3: log_trace at entry
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="execute",
            reason="Generating attraction recommendations",
            inputs={"constraints": deepcopy(merged)},
        )

    # Step 4: Generate or update itinerary via LLM
    llm_service = get_llm_itinerary_service()

    # An error-sentinel itinerary from a previously failed round isn't a real
    # existing itinerary to update from — generate fresh instead.
    has_real_existing_itinerary = bool(existing_itinerary) and not _had_errors(existing_itinerary)

    try:
        if has_real_existing_itinerary:
            itinerary = llm_service.update_itinerary(
                existing_itinerary=existing_itinerary,
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                styles=styles,
                exclusions=exclusions,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
                critique=checker_critique,
                hotel_lat=hotel_lat,
                hotel_lon=hotel_lon,
            )
        else:
            itinerary = llm_service.generate_itinerary(
                destination=destination,
                days=days,
                hotel_area=hotel_area,
                arrival_time=arrival_time,
                start_date=start_date,
                styles=styles,
                exclusions=exclusions,
                must_go_places=must_go_places,
                max_price_per_ticket=max_price_per_ticket,
                num_people=num_people,
                origin=origin,
                return_depart_time=return_depart_time,
                critique=checker_critique,
                hotel_lat=hotel_lat,
                hotel_lon=hotel_lon,
            )

        if _had_errors(itinerary):
            itinerary = existing_itinerary if has_real_existing_itinerary else [dict(_ERROR_MESSAGE, day=None, activities=[])]
        elif not itinerary:
            itinerary = _build_fallback_itinerary(destination, days, hotel_area, hotel_lat, hotel_lon)
    except Exception as e:
        print(f"attraction_agent(): unexpected error: {e}")
        itinerary = _build_fallback_itinerary(destination, days, hotel_area, hotel_lat, hotel_lon)

    # Step 5: log_trace at exit
    if state.get("log_trace"):
        log_trace(
            state,
            node="attraction_agent",
            action="complete recommendations",
            reason="Attraction itinerary generated",
            outputs={"itinerary": deepcopy(itinerary)},
        )

    if merged.get("rerun_planning") is True:
        merged = {**merged, "rerun_planning": None}
    constraints["attraction"] = merged

    dirty_agents = list(state.get("dirty_agents", []))
    dirty_agents.append("checker_agent")
    print(f"attraction_agent(): generated {len(itinerary)} days")
    return {**state, "itinerary": itinerary, "dirty_agents": dirty_agents, "constraints": constraints}


def _build_fallback_itinerary(
    destination: str,
    days: int,
    hotel_area: str | None,
    hotel_lat: float | None = None,
    hotel_lon: float | None = None,
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
