import logging
import os

from states.trip_state import TripState
from orchestration.tracability import log_trace
from orchestration.merge_constraints import resolve_category_constraints, UNLIMITED_PRICE
from services.city_iata_resolver import resolve_city_iata_codes
from services.llm_hotel_selector_service import resolve_check_in_date, select_hotels
from services.stayingapi_hotel import get_stayingapi_hotel_service
from copy import deepcopy
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


_ERROR_REASONS = {"StayingAPI Hotel API error", "Unknown error"}

_ERROR_MESSAGE = {
    "reason": (
        "There's a StayingAPI Hotel API error (or unknown error) at the moment. "
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


def _fill_unlimited_price(merged: dict) -> dict:
    """If no round has ever set a price cap, treat it as unlimited rather than
    leaving it None, so a restated-unchanged preference compares equal across
    rounds and search calls don't misread an unset field as a zero cap."""
    preference = merged.get("preference") or {}
    if preference.get("max_price_per_night") is not None:
        return merged
    return {**merged, "preference": {**preference, "max_price_per_night": UNLIMITED_PRICE}}


def accommodation_agent(state: TripState) -> TripState:
    merged, unchanged = resolve_category_constraints(state, "accommodation")
    merged = _fill_unlimited_price(merged)
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

    start_date = state.get("start_date")
    days = state.get("days") or 1
    outbound_legs = ((state.get("transport_options") or {}).get("flight") or {}).get("outbound") or []
    arrival_leg = outbound_legs[-1] if outbound_legs else {}

    check_in_date = resolve_check_in_date(
        departure_date=arrival_leg.get("departure_date"),
        arrival_date=arrival_leg.get("arrival_date"),
        arrival_time=arrival_leg.get("arrival_time"),
        trip_start_date=start_date,
    ) or _default_check_in_date()
    check_out_date = state.get("end_date") or _default_check_out_date(check_in_date, days)

    # Names the airport so candidates can carry a distance to it; the selector uses
    # that to judge which hotel suits a late landing.
    dest_airport_codes = resolve_city_iata_codes(destination)
    airport_iata = dest_airport_codes[0] if dest_airport_codes else None

    logger.info(f"Key inputs for accommodation_agent: max_price_per_night: {max_price_per_night}, preferred_area: {preferred_area}, check_in_date: {check_in_date}, check_out_date: {check_out_date}", extra={"to_terminal": False})

    num_people = state.get("num_people") or 1
    hotel_service = get_stayingapi_hotel_service()
    hotels = hotel_service.search_hotels(
        destination=destination,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=num_people,
        room_quantity=1,
        max_price_per_night=max_price_per_night,
        preferred_area=preferred_area,
        airport_iata=airport_iata,
    )

    if _had_errors(hotels):
        accommodation_options = [dict(_ERROR_MESSAGE)]
    elif hotels:
        accommodation_options = _select_stays(
            hotels, destination, airport_iata, arrival_leg, check_in_date, check_out_date,
            num_people, preference,
        )
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

    logger.info(f"accommodation_agent: accommodation_options: {accommodation_options}", extra={"to_terminal": False})

    return {**state, "accommodation_options": accommodation_options, "constraints": constraints}


def _select_stays(
    hotels: list,
    destination: str,
    airport_iata,
    arrival_leg: dict,
    check_in_date: str,
    check_out_date: str,
    num_people: int,
    preference: dict,
) -> list:
    """Pick which hotels to book, falling back to the single best candidate.

    The LLM decides whether the stay should be split across a land-night hotel and a
    main hotel. When it can't be asked (one candidate only, or no LLM configured) or
    fails, the fail-safe is the first hotel alone: `hotels` arrives price-sorted, so
    that is the cheapest acceptable option, and returning one hotel for the whole
    stay is always coherent, whereas a partially-applied split could leave a night
    uncovered.
    """
    shortlist = hotels[:3]
    fallback = _with_stay_dates(hotels[:1], check_in_date, check_out_date)

    if len(shortlist) < 2 or not os.getenv("OPENAI_API_KEY"):
        return fallback

    try:
        selected = select_hotels(
            candidates=shortlist,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            destination=destination,
            airport_iata=airport_iata,
            arrival_time=arrival_leg.get("arrival_time"),
            arrival_date=arrival_leg.get("arrival_date"),
            num_people=num_people,
            preference=preference,
        )
    except Exception as e:
        # Accommodation is not the traveler's primary decision — a selector failure
        # should degrade to one bookable hotel, not fail the whole plan.
        logger.error(f"accommodation_agent: hotel selection failed, using the first hotel: {e}")
        return fallback

    if not selected:
        logger.warning("accommodation_agent: hotel selector returned nothing, using the first hotel")
        return fallback

    logger.info(f"accommodation_agent: selected {len(selected)} stay(s) from {len(shortlist)} candidates")
    return selected


def _with_stay_dates(hotels: list, check_in_date: str, check_out_date: str) -> list:
    """Stamp the single stay window onto hotels that didn't come from the selector."""
    return [{**hotel, "check_in_date": check_in_date, "check_out_date": check_out_date} for hotel in hotels]


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
