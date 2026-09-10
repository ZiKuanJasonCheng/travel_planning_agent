"""
StayingAPI Hotel Service
Wrapper for StayingAPI hotel search functionality (Duffel Stays backup/replacement)
"""
import json
import os
import time
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib import error, request

from services.currency import to_usd

STAYINGAPI_BASE_URL = "https://api.stayingapi.com/v1"
_JOB_POLL_INTERVAL_SECONDS = 1
_JOB_POLL_TIMEOUT_SECONDS = 10


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


def _parse_search_result(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a single StayingAPI Property into a flat hotel dict (no filtering yet)."""
    price = result.get("price") or {}
    nightly_price = price.get("nightlyPrice")
    if nightly_price is None:
        return None
    try:
        nightly_price = float(nightly_price)
    except (TypeError, ValueError):
        return None

    currency = price.get("currency") or "USD"
    location = result.get("location") or {}
    area = location.get("city") or location.get("region") or "unknown area"

    return {
        "type": "hotel",
        "name": result.get("name") or "Unknown Hotel",
        "price_per_night": int(nightly_price),
        "currency": currency,
        "area": area,
        "hotel_id": result.get("id"),
        "lat": location.get("lat"),
        "lon": location.get("lng"),
        "supplier": "stayingapi",
        "reason": "StayingAPI offer",
    }


class StayingAPIHotelService:
    """
    Service for querying hotel information from StayingAPI's Search API
    """

    _CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        api_key = os.getenv("STAYINGAPI_API_KEY")

        self._cache: Dict[str, Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

        if not api_key:
            self.api_key = None
            self.use_mock = True
            print("Warning: STAYINGAPI_API_KEY not set. Using mock data.")
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
        Search for hotels using StayingAPI's Search API.

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

        try:
            params = {
                "location": destination,
                "checkIn": check_in,
                "checkOut": check_out,
                "adults": adults,
                "rooms": room_quantity,
                "currency": "USD",
                "sort": "price_asc",
                "limit": 10,
            }

            print(f"search_hotels(): params: {params}")
            raw_results = self._request_search(params)
            print(f"search_hotels(): len(raw_results): {len(raw_results)}")

            hotels = []
            for item in raw_results:
                hotel = _parse_search_result(item)
                if hotel is None:
                    continue
                nightly_usd = to_usd(hotel["price_per_night"], hotel["currency"])
                if not _passes_hotel_filters(nightly_usd, hotel["area"], max_price_per_night, preferred_area):
                    continue
                hotels.append(hotel)

            hotels.sort(key=lambda item: item.get("price_per_night", 10**9))
            result = hotels[:5]
            self._cache[cache_key] = (time.time(), result)
            return result

        except error.HTTPError as http_error:
            print(f"StayingAPI Hotel API Error: {http_error}, Response body: {http_error.read().decode('utf-8')}")
            return [{"reason": "StayingAPI Hotel API error"}]
        except Exception as e:
            print(f"Error searching hotels: {e}")
            return [{"reason": "Unknown error"}]

    def _request_search(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = f"{STAYINGAPI_BASE_URL}/search?{urllib.parse.urlencode(params)}"
        body = self._get_json(url)

        # A live API key can return 202 + a job to poll instead of results
        # synchronously; sandbox (stay_test_) keys always respond inline.
        data = body.get("data")
        if isinstance(data, dict) and data.get("status") == "pending":
            body = self._poll_job(data["pollUrl"])
            data = body.get("data")

        if isinstance(data, dict):
            return data.get("results") or []
        if isinstance(data, list):
            return data
        return []

    def _poll_job(self, poll_url: str) -> Dict[str, Any]:
        deadline = time.time() + _JOB_POLL_TIMEOUT_SECONDS
        url = f"https://api.stayingapi.com{poll_url}" if poll_url.startswith("/") else poll_url
        while time.time() < deadline:
            body = self._get_json(url)
            status = (body.get("data") or {}).get("status")
            if status == "completed":
                return {"data": (body.get("data") or {}).get("result")}
            if status == "failed":
                return {"data": {"results": []}}
            time.sleep(_JOB_POLL_INTERVAL_SECONDS)
        return {"data": {"results": []}}

    def _get_json(self, url: str) -> Dict[str, Any]:
        req = request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        with request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _mock_hotel_search(
        self,
        destination: str,
        check_in_date: str,
        check_out_date: str,
        max_price_per_night: Optional[int],
        preferred_area: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Mock hotel search for development/testing when API credentials are not available."""
        raw_candidates = [
            {"name": f"{destination} Central Hotel", "nightly_price": 120.0, "currency": "USD",
             "area": f"{destination} city center", "hotel_id": "mock_hotel_1", "lat": None, "lon": None},
            {"name": f"{destination} Riverside Inn", "nightly_price": 80.0, "currency": "USD",
             "area": f"{destination} riverside", "hotel_id": "mock_hotel_2", "lat": None, "lon": None},
            {"name": f"{destination} Budget Stay", "nightly_price": 45.0, "currency": "USD",
             "area": f"{destination} suburbs", "hotel_id": "mock_hotel_3", "lat": None, "lon": None},
        ]

        results = []
        for c in raw_candidates:
            nightly_price = int(c["nightly_price"])
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
                "supplier": "stayingapi",
                "reason": "Mock hotel data (StayingAPI not configured)",
            })

        results.sort(key=lambda item: item.get("price_per_night", 10**9))
        return results[:5]


# Singleton instance
_hotel_service: Optional[StayingAPIHotelService] = None


def get_stayingapi_hotel_service() -> StayingAPIHotelService:
    """
    Get singleton instance of StayingAPIHotelService
    """
    global _hotel_service
    if _hotel_service is None:
        _hotel_service = StayingAPIHotelService()
    return _hotel_service


if __name__ == "__main__":
    test_hotel_service = get_stayingapi_hotel_service()
    results = test_hotel_service.search_hotels(
        destination="Osaka",
        check_in_date="2026-09-10",
        check_out_date="2026-09-17",
        adults=2,
        room_quantity=1,
        max_price_per_night=300,
    )
    print(f"results: {results}")
