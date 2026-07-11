from states.trip_state import TripState
from orchestration.tracability import log_trace
from orchestration.merge_constraints import resolve_category_constraints
from services.amadeus_hotel import get_amadeus_hotel_service
from services.booking_hotel import get_booking_hotel_service
from copy import deepcopy
from datetime import datetime, timedelta


_ERROR_REASONS = {"Amadeus Hotel API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
        "Please wait for a few minutes and submit a feedback saying "
        "'Run accommodation service again'."
    )
}


def _had_errors(options: list) -> bool:
    """Check a list of accommodation_options (either search_hotels()'s raw
    output or a previously-persisted state["accommodation_options"]) for an
    error-tier reason, covering both the raw error tags and the persisted
    long-form message."""
    error_texts = _ERROR_REASONS | {_ERROR_MESSAGE["reason"]}
    return any(opt.get("reason") in error_texts for opt in options)


def accommodation_agent(state: TripState) -> TripState:
    merged, unchanged = resolve_category_constraints(state, "accommodation")
    existing_options = state.get("accommodation_options") or []

    should_skip = (
        unchanged
        and merged.get("rerun_planning") is not True
        and not _had_errors(existing_options)
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["accommodation"] = merged
        return {**state, "constraints": constraints}

    destination = state.get("destination", "")
    days = state.get("days") or 1
    preference = merged.get("preference") or {}

    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="execute",
            reason="Generating hotel recommendations",
            inputs={"constraints": deepcopy(merged)}
        )

    max_price_per_night = preference.get("max_price_per_night")
    preferred_area = preference.get("area")

    check_in_date = state.get("start_date") or _default_check_in_date()
    check_out_date = state.get("end_date") or _default_check_out_date(check_in_date, days)
    print(f"accommodation_agent(): max_price_per_night: {max_price_per_night}, preferred_area: {preferred_area}, check_in_date: {check_in_date}, check_out_date: {check_out_date}")

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

    if _had_errors(hotels):
        accommodation_options = [dict(_ERROR_MESSAGE)]
    elif hotels:
        accommodation_options = hotels[:3]
    else:
        accommodation_options = [_build_fallback_hotel(max_price_per_night, preferred_area)]

    if merged.get("rerun_planning") is True:
        merged = {**merged, "rerun_planning": None}
    constraints["accommodation"] = merged

    if state["log_trace"]:
        log_trace(
            state,
            node="accommodation_agent",
            action="complete recommendations",
            reason="Hotel recommendations generated",
            outputs={"accommodation_options": deepcopy(accommodation_options)}
        )

    print(f"accommodation_agent(): accommodation_options: {accommodation_options}")

    return {**state, "accommodation_options": accommodation_options, "constraints": constraints}


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
