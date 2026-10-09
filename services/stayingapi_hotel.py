"""
StayingAPI Hotel Service
Wrapper for StayingAPI hotel search functionality (Duffel Stays backup/replacement)
"""
import json
import logging
import os
import time
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib import error, request

from services.city_iata_resolver import get_airport_coords
from services.currency import to_usd
from services.geocoding import fetch_coordinates
from services.langfuse_client import observe
from services.llm_hotel_selector_service import _haversine_km

logger = logging.getLogger(__name__)

STAYINGAPI_BASE_URL = "https://api.stayingapi.com/v1"
_JOB_POLL_INTERVAL_SECONDS = 1
_JOB_POLL_TIMEOUT_SECONDS = 150

# How far a hotel may sit from the centre of the traveler's preferred area and still
# count as being in it.
_PREFERRED_AREA_RADIUS_KM = 5.0


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str) -> str:
    base = datetime.strptime(check_in_date, "%Y-%m-%d")
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


def _nights(check_in_date: str, check_out_date: str) -> int:
    """Return the number of nights between two ISO dates, minimum 1."""
    return max(1, (date.fromisoformat(check_out_date) - date.fromisoformat(check_in_date)).days)


def _within_preferred_area(
    hotel_lat,
    hotel_lon,
    area: str,
    preferred_area: Optional[str],
    area_coords: Optional[tuple[float, float]],
) -> bool:
    """Whether a hotel can be considered inside the traveler's preferred area.

    Nominatim geocodes the free-text area ("Shinjuku", "Ueno, Tokyo") into a
    representative centre point, and a hotel is judged against that centre by
    distance. String-matching the hotel's own `area` field — the previous approach —
    silently dropped every airbnb and google listing, because those platforms return
    no city or region at all.

    Name matching survives as the fallback for when the area could not be
    geocoded, which keeps a typo'd or unlisted area from emptying the result set, and
    as a last resort beyond the radius: a large area can hold a hotel that is
    genuinely inside it yet sits far from the geocoded centre point.
    """
    if not preferred_area:
        return True

    name_match = preferred_area.lower() in (area or "").lower()

    if not area_coords:
        return name_match

    if hotel_lat is None or hotel_lon is None:
        # Unknown coordinates can't be measured, so they pass: dropping them would
        # discard every listing from a provider that omits location data.
        return True

    try:
        distance = _haversine_km(
            float(hotel_lat), float(hotel_lon), area_coords[0], area_coords[1]
        )
    except (TypeError, ValueError) as e:
        logger.error(f"Could not measure hotel distance from preferred area: {e}", extra={"to_terminal": False})
        return True

    if distance <= _PREFERRED_AREA_RADIUS_KM:
        return True

    # Too far from the centre to count by distance alone, but the hotel may still
    # name the area — a large area extends well past its centre point.
    return name_match


def _passes_hotel_filters(
    nightly_price_usd: float,
    max_price_per_night: Optional[int],
) -> bool:
    if max_price_per_night and nightly_price_usd > max_price_per_night:
        return False
    return True


def _cache_key(**kwargs) -> str:
    return json.dumps(kwargs, sort_keys=True, default=str)


def _parse_search_result(
    result: Dict[str, Any],
    airport_coords: Optional[tuple[float, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Parse a single StayingAPI Property into a flat hotel dict (no filtering yet).

    `airport_coords` is the destination airport's (lat, lon) when known; it enables
    `distance_to_airport_km`, which the hotel selector needs to judge whether a
    candidate suits the traveler's landing night. None when either side is missing
    coordinates — vrbo listings in particular often carry none.
    """
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

    lat = location.get("lat")
    lon = location.get("lng")

    distance_to_airport_km = None
    if airport_coords and lat is not None and lon is not None:
        try:
            distance_to_airport_km = round(
                _haversine_km(float(lat), float(lon), airport_coords[0], airport_coords[1]), 1
            )
        except (TypeError, ValueError) as e:
            logger.error(f"An error occurred while calculating distance between an airport and a hotel. Error message: {e}", extra={"to_terminal": False})
            distance_to_airport_km = None

    return {
        "type": "hotel",
        "name": result.get("name") or "Unknown Hotel",
        "price_per_night": int(nightly_price),
        "currency": currency,
        "area": area,
        "hotel_id": result.get("id"),
        "lat": lat,
        "lon": lon,
        "supplier": "stayingapi",
        "reason": "StayingAPI offer",
        "distance_to_airport_km": distance_to_airport_km,
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
            logger.warning("Warning: STAYINGAPI_API_KEY not set. Using mock data.")
        else:
            self.api_key = api_key
            self.use_mock = False

    @observe()
    def search_hotels(
        self,
        destination: str,
        check_in_date: Optional[str] = None,
        check_out_date: Optional[str] = None,
        adults: int = 2,
        room_quantity: int = 1,
        max_price_per_night: Optional[int] = None,
        preferred_area: Optional[str] = None,
        airport_iata: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for hotels using StayingAPI's Search API.

        `airport_iata` is optional and only affects the returned data: when given, each
        hotel carries `distance_to_airport_km` for the hotel selector to reason about.

        Returns:
            List of hotel dicts: {type, name, price_per_night, currency, area, hotel_id,
            lat, lon, supplier, reason, distance_to_airport_km}, or a single-item error
            list on failure.
        """
        check_in = check_in_date or _default_check_in_date()
        check_out = check_out_date or _default_check_out_date(check_in)

        cache_key = _cache_key(
            destination=destination, check_in_date=check_in, check_out_date=check_out,
            adults=adults, room_quantity=room_quantity,
            max_price_per_night=max_price_per_night, preferred_area=preferred_area,
            airport_iata=airport_iata,
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        if self.use_mock:
            result = self._mock_hotel_search(destination, check_in, check_out, max_price_per_night, preferred_area)
            self._cache[cache_key] = (time.time(), result)
            return result

        airport_coords = get_airport_coords(airport_iata) if airport_iata else None

        # Geocode the preferred area once so hotels can be judged by distance; a
        # lookup failure just means the area filter can't apply.
        area_coords = fetch_coordinates(preferred_area) if preferred_area else None
        if preferred_area and not area_coords:
            logger.warning(
                f"search_hotels(): could not geocode preferred_area '{preferred_area}' — "
                f"area filtering falls back to name matching"
            )

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

            logger.info(f"search_hotels(): params: {params}")
            raw_results = self._request_search(params)
            logger.info(f"search_hotels(): len(raw_results): {len(raw_results)}")
            logger.info(f"search_hotels(): raw_results: {raw_results}", extra={"to_terminal": False})

            hotels = []
            for item in raw_results:
                hotel = _parse_search_result(item, airport_coords)
                if hotel is None:
                    continue
                nightly_usd = to_usd(hotel["price_per_night"], hotel["currency"])
                if not _passes_hotel_filters(nightly_usd, max_price_per_night):
                    continue
                if not _within_preferred_area(
                    hotel["lat"], hotel["lon"], hotel["area"], preferred_area, area_coords
                ):
                    continue
                hotels.append(hotel)

            hotels.sort(key=lambda item: item.get("price_per_night", 10**9))
            result = hotels[:5]
            self._cache[cache_key] = (time.time(), result)
            return result

        except error.HTTPError as http_error:
            logger.error(f"StayingAPI Hotel API Error: {http_error}, Response body: {http_error.read().decode('utf-8')}")
            return [{"reason": "StayingAPI Hotel API error"}]
        except Exception as e:
            logger.error(f"Error searching hotels: {e}")
            return [{"reason": "Unknown error"}]

    def _request_search(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = f"{STAYINGAPI_BASE_URL}/search?{urllib.parse.urlencode(params)}"
        body = self._get_json(url)

        # A live API key can return 202 + a job to poll instead of results
        # synchronously; sandbox (stay_test_) keys always respond inline.
        data = body.get("data")
        logger.info(f"Hotel search data: {data}", extra={"to_terminal": False})
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
        #logger.info(f"deadline: {deadline}", extra={"to_terminal": False})
        url = f"https://api.stayingapi.com{poll_url}" if poll_url.startswith("/") else poll_url
        #logger.info(f"url: {url}", extra={"to_terminal": False})
        while time.time() < deadline:
            body = self._get_json(url)
            #logger.info(f"body: {body}", extra={"to_terminal": False})
            status = (body.get("data") or {}).get("status")
            #logger.info(f"status: {status}", extra={"to_terminal": False})
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
            if not _passes_hotel_filters(nightly_usd, max_price_per_night):
                continue
            if not _within_preferred_area(c["lat"], c["lon"], c["area"], preferred_area, None):
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
                "distance_to_airport_km": None,
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
