"""
Duffel Stays API Service
Wrapper for Duffel hotel search functionality
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib import error, request

DUFFEL_API_BASE_URL = "https://api.duffel.com"
DUFFEL_API_VERSION = "v2"


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str) -> str:
    base = datetime.strptime(check_in_date, "%Y-%m-%d")
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


def _nights(check_in_date: str, check_out_date: str) -> int:
    """Return the number of nights between two ISO dates, minimum 1."""
    return max(1, (date.fromisoformat(check_out_date) - date.fromisoformat(check_in_date)).days)


def _passes_hotel_filters(
    nightly_price_usd: float,
    area: str,
    max_price_per_night: Optional[int],
    preferred_area: Optional[str],
) -> bool:
    if max_price_per_night and nightly_price_usd > max_price_per_night:
        return False
    if preferred_area and preferred_area.lower() not in area.lower():
        return False
    return True


def _cache_key(**kwargs) -> str:
    return json.dumps(kwargs, sort_keys=True, default=str)


def _parse_search_result(result: Dict[str, Any], nights: int) -> Optional[Dict[str, Any]]:
    """Parse a single Duffel Stays search result into a flat hotel dict (no filtering yet)."""
    total_price = result.get("cheapest_rate_total_amount")
    if total_price is None:
        return None
    try:
        total_price = float(total_price)
    except (TypeError, ValueError):
        return None

    currency = result.get("cheapest_rate_currency") or "USD"
    accommodation = result.get("accommodation") or {}
    location = accommodation.get("location") or {}
    address = location.get("address") or {}
    coordinates = location.get("geographic_coordinates") or {}
    area = address.get("city_name") or address.get("region") or "unknown area"

    return {
        "type": "hotel",
        "name": accommodation.get("name") or "Unknown Hotel",
        "price_per_night": int(total_price / nights) if total_price > 0 else 0,
        "currency": currency,
        "area": area,
        "hotel_id": accommodation.get("id"),
        "lat": coordinates.get("latitude"),
        "lon": coordinates.get("longitude"),
        "supplier": "duffel",
        "reason": "Duffel Stays offer",
    }
