"""
Duffel Flight API Service
Wrapper for Duffel flight search functionality
"""
import json
import os
import time
from typing import Optional, List, Dict, Any
from urllib import error, request

DUFFEL_API_BASE_URL = "https://api.duffel.com"
DUFFEL_API_VERSION = "v2"


def _is_redeye(depart_time_str: str) -> bool:
    """Return True if departure time falls in the red-eye window (23:30-05:29)."""
    try:
        hour, minute = int(depart_time_str[:2]), int(depart_time_str[3:5])
        return (hour == 23 and minute >= 30) or hour < 5 or (hour == 5 and minute <= 29)
    except (ValueError, IndexError):
        return False


def _matches_timeslots(depart_time_str: str, timeslots: list[str]) -> bool:
    """Return True if depart_time_str (HH:MM:SS or HH:MM) falls within any HH:MM~HH:MM slot."""
    try:
        h, m = int(depart_time_str[:2]), int(depart_time_str[3:5])
        depart_min = h * 60 + m
        for slot in timeslots:
            start_str, end_str = slot.split("~")
            sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
            eh, em = int(end_str.split(":")[0]), int(end_str.split(":")[1])
            if sh * 60 + sm <= depart_min <= eh * 60 + em:
                return True
        return False
    except (ValueError, IndexError):
        return True  # Unparseable timeslot -> Don't filter


def _apply_leg_prices(legs: List[Dict[str, Any]], direction_total: float) -> List[Dict[str, Any]]:
    """Divide direction_total evenly across legs, returning new dicts with a 'price' key added."""
    if not legs:
        return []
    per_leg_price = direction_total / len(legs)
    return [{**leg, "price": int(round(per_leg_price))} for leg in legs]


def _passes_preference(legs: List[Dict[str, Any]], preference: Optional[Dict[str, Any]]) -> bool:
    """Check a direction's legs (already priced) against its preference constraints."""
    if not legs:
        return False
    if not preference:
        return True

    first_leg = legs[0]
    airline_code = first_leg.get("airline", "")
    depart_time = first_leg.get("depart_time", "")

    excluded_airlines = preference.get("excluded_airlines")
    if excluded_airlines and airline_code in excluded_airlines:
        return False

    airlines = preference.get("airlines")
    if airlines and airline_code not in airlines:
        return False

    if preference.get("direct_flights_only") and len(legs) > 1:
        return False

    if not preference.get("accept_redeye_flights", True) and depart_time and _is_redeye(depart_time):
        return False

    preferred_departure_timeslots = preference.get("preferred_departure_timeslots")
    if preferred_departure_timeslots and depart_time and not _matches_timeslots(depart_time, preferred_departure_timeslots):
        return False

    max_price_per_ticket = preference.get("max_price_per_ticket")
    if max_price_per_ticket is not None:
        direction_total_price = sum(leg.get("price", 0) for leg in legs)
        if direction_total_price > max_price_per_ticket:
            return False

    return True


def _cache_key(**kwargs) -> str:
    return json.dumps(kwargs, sort_keys=True, default=str)
