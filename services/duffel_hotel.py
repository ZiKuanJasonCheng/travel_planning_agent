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

from services.currency import to_usd

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


class DuffelHotelService:
    """
    Service for querying hotel information from Duffel's Stays API
    """

    _CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        api_key = os.getenv("DUFFEL_API_KEY")

        self._cache: Dict[str, Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

        if not api_key:
            self.api_key = None
            self.use_mock = True
            print("Warning: DUFFEL_API_KEY not set. Using mock data.")
        else:
            self.api_key = api_key
            self.use_mock = False

    def search_hotels(
        self,
        destination: str,
        check_in_date: Optional[str] = None,
        check_out_date: Optional[str] = None,
        adults: int = 2,
        room_quantity: int = 1,
        max_price_per_night: Optional[int] = None,
        preferred_area: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for hotels using Duffel's Stays API.

        Returns:
            List of hotel dicts: {type, name, price_per_night, currency, area, hotel_id,
            lat, lon, supplier, reason}, or a single-item error list on failure.
        """
        check_in = check_in_date or _default_check_in_date()
        check_out = check_out_date or _default_check_out_date(check_in)

        cache_key = _cache_key(
            destination=destination, check_in_date=check_in, check_out_date=check_out,
            adults=adults, room_quantity=room_quantity,
            max_price_per_night=max_price_per_night, preferred_area=preferred_area,
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        if self.use_mock:
            result = self._mock_hotel_search(destination, check_in, check_out, max_price_per_night, preferred_area)
            self._cache[cache_key] = (time.time(), result)
            return result

        raise NotImplementedError  # replaced by the real API path in Task 4

    def _mock_hotel_search(
        self,
        destination: str,
        check_in_date: str,
        check_out_date: str,
        max_price_per_night: Optional[int],
        preferred_area: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Mock hotel search for development/testing when API credentials are not available."""
        nights = _nights(check_in_date, check_out_date)
        raw_candidates = [
            {"name": f"{destination} Central Hotel", "total_price": 480.0, "currency": "USD",
             "area": f"{destination} city center", "hotel_id": "mock_hotel_1", "lat": None, "lon": None},
            {"name": f"{destination} Riverside Inn", "total_price": 320.0, "currency": "USD",
             "area": f"{destination} riverside", "hotel_id": "mock_hotel_2", "lat": None, "lon": None},
            {"name": f"{destination} Budget Stay", "total_price": 180.0, "currency": "USD",
             "area": f"{destination} suburbs", "hotel_id": "mock_hotel_3", "lat": None, "lon": None},
        ]

        results = []
        for c in raw_candidates:
            nightly_price = int(c["total_price"] / nights)
            nightly_usd = to_usd(nightly_price, c["currency"])
            if not _passes_hotel_filters(nightly_usd, c["area"], max_price_per_night, preferred_area):
                continue
            results.append({
                "type": "hotel",
                "name": c["name"],
                "price_per_night": nightly_price,
                "currency": c["currency"],
                "area": c["area"],
                "hotel_id": c["hotel_id"],
                "lat": c["lat"],
                "lon": c["lon"],
                "supplier": "duffel",
                "reason": "Mock hotel data (Duffel API not configured)",
            })

        results.sort(key=lambda item: item.get("price_per_night", 10**9))
        return results[:5]


# Singleton instance
_hotel_service: Optional[DuffelHotelService] = None


def get_duffel_hotel_service() -> DuffelHotelService:
    """
    Get singleton instance of DuffelHotelService
    """
    global _hotel_service
    if _hotel_service is None:
        _hotel_service = DuffelHotelService()
    return _hotel_service
