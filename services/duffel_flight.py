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


def _parse_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single Duffel slice segment into a flat leg dict (no price yet)."""
    origin = segment.get("origin", {}) or {}
    destination = segment.get("destination", {}) or {}
    operating_carrier = segment.get("operating_carrier", {}) or {}
    depart_at = segment.get("departing_at", "") or ""
    arrival_at = segment.get("arriving_at", "") or ""
    return {
        "airline": operating_carrier.get("iata_code", ""),
        "from": origin.get("iata_code", ""),
        "to": destination.get("iata_code", ""),
        "depart_time": depart_at.split("T")[1][:8] if "T" in depart_at else "",
        "arrival_time": arrival_at.split("T")[1][:8] if "T" in arrival_at else "",
        "departure_date": depart_at.split("T")[0] if "T" in depart_at else "",
    }


class DuffelFlightService:
    """
    Service for querying flight information from Duffel API
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

    def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        adults: int = 1,
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for flights using Duffel API.

        Returns:
            List of candidate dicts: {price, currency, outbound_legs, inbound_legs, stops_outbound,
            stops_inbound, reason}, or a single-item error list on failure.
        """
        cache_key = _cache_key(
            origin=origin, destination=destination, departure_date=departure_date,
            return_date=return_date, adults=adults,
            outbound_preference=outbound_preference, inbound_preference=inbound_preference,
        )
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        if self.use_mock:
            result = self._mock_flight_search(
                origin, destination, departure_date, return_date,
                outbound_preference, inbound_preference,
            )
            self._cache[cache_key] = (time.time(), result)
            return result

        try:
            slices = [{"origin": origin, "destination": destination, "departure_date": departure_date}]
            if return_date:
                slices.append({"origin": destination, "destination": origin, "departure_date": return_date})

            outbound_class = (outbound_preference or {}).get("flight_class")
            inbound_class = (inbound_preference or {}).get("flight_class")
            cabin_class = None
            if outbound_class and (not inbound_class or outbound_class == inbound_class):
                cabin_class = outbound_class.lower()
            elif inbound_class and not outbound_class:
                cabin_class = inbound_class.lower()
            # If both are set and differ, omit cabin_class — a single request can't express two cabins.

            payload: Dict[str, Any] = {
                "data": {
                    "slices": slices,
                    "passengers": [{"type": "adult"} for _ in range(adults)],
                }
            }
            if cabin_class:
                payload["data"]["cabin_class"] = cabin_class

            print(f"search_flights(): payload: {payload}")
            offers = self._request_offers(payload)
            print(f"search_flights(): len(offers): {len(offers)}")

            flights = []
            for i, offer in enumerate(offers):
                if i < 3:
                    print(f"search_flights(): offer: {offer}")  # Temp
                candidate = self._parse_offer(offer, outbound_preference, inbound_preference)
                if candidate:
                    flights.append(candidate)

            flights.sort(key=lambda x: x.get("price", float("inf")))
            result = flights
            self._cache[cache_key] = (time.time(), result)
            return result

        except error.HTTPError as http_error:
            print(f"Duffel API Error: {http_error}")
            return [{"type": "flight", "reason": "Duffel API error"}]
        except Exception as e:
            print(f"Error searching flights: {e}")
            return [{"type": "flight", "reason": "Unknown error"}]

    def _request_offers(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        req = request.Request(
            f"{DUFFEL_API_BASE_URL}/air/offer_requests?return_offers=true",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Duffel-Version": DUFFEL_API_VERSION,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with request.urlopen(req, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
        return ((body.get("data") or {}).get("offers")) or []

    def _parse_offer(
        self,
        offer: Dict[str, Any],
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse a Duffel offer into a candidate dict with per-leg detail and pricing."""
        try:
            slices = offer.get("slices", [])
            if not slices:
                return None

            outbound_segments = slices[0].get("segments", [])
            if not outbound_segments:
                return None
            outbound_legs_raw = [_parse_segment(s) for s in outbound_segments]

            inbound_legs_raw = None
            if len(slices) > 1:
                inbound_segments = slices[1].get("segments", [])
                if inbound_segments:
                    inbound_legs_raw = [_parse_segment(s) for s in inbound_segments]

            total_price = float(offer.get("total_amount", 0) or 0)
            currency = offer.get("total_currency", "USD")

            if inbound_legs_raw:
                outbound_total, inbound_total = total_price / 2, total_price / 2
            else:
                outbound_total, inbound_total = total_price, None

            outbound_legs = _apply_leg_prices(outbound_legs_raw, outbound_total)
            inbound_legs = _apply_leg_prices(inbound_legs_raw, inbound_total) if inbound_legs_raw else None

            if not _passes_preference(outbound_legs, outbound_preference):
                return None
            if inbound_legs is not None and not _passes_preference(inbound_legs, inbound_preference):
                return None

            return {
                "price": int(total_price),
                "currency": currency,
                "outbound_legs": outbound_legs,
                "inbound_legs": inbound_legs,
                "stops_outbound": len(outbound_legs) - 1,
                "stops_inbound": (len(inbound_legs) - 1) if inbound_legs else None,
                "reason": "Duffel API result",
            }
        except Exception as e:
            print(f"Error parsing flight offer: {e}")
            return None

    def _mock_flight_search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str],
        outbound_preference: Optional[Dict[str, Any]],
        inbound_preference: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Mock flight search for development/testing when API credentials are not available."""

        def _leg(airline, frm, to, depart, arrival, date):
            return {"airline": airline, "from": frm, "to": to, "depart_time": depart,
                     "arrival_time": arrival, "departure_date": date}

        raw_candidates = [
            {
                "price": 500.0, "currency": "USD",
                "outbound_legs_raw": [_leg("CX", origin, destination, "18:25:00", "22:00:00", departure_date)],
                "inbound_legs_raw": (
                    [_leg("CX", destination, origin, "19:00:00", "23:00:00", return_date)] if return_date else None
                ),
            },
            {
                "price": 400.0, "currency": "USD",
                "outbound_legs_raw": [
                    _leg("AA", origin, "XXX", "15:25:00", "16:30:00", departure_date),
                    _leg("AA", "XXX", destination, "17:00:00", "17:00:00", departure_date),
                ],
                "inbound_legs_raw": (
                    [_leg("AA", destination, origin, "10:00:00", "14:00:00", return_date)] if return_date else None
                ),
            },
        ]

        results = []
        for c in raw_candidates:
            outbound_legs_raw = c["outbound_legs_raw"]
            inbound_legs_raw = c["inbound_legs_raw"]
            total_price = c["price"]

            if inbound_legs_raw:
                outbound_total, inbound_total = total_price / 2, total_price / 2
            else:
                outbound_total, inbound_total = total_price, None

            outbound_legs = _apply_leg_prices(outbound_legs_raw, outbound_total)
            inbound_legs = _apply_leg_prices(inbound_legs_raw, inbound_total) if inbound_legs_raw else None

            if not _passes_preference(outbound_legs, outbound_preference):
                continue
            if inbound_legs is not None and not _passes_preference(inbound_legs, inbound_preference):
                continue

            results.append({
                "price": int(total_price),
                "currency": c["currency"],
                "outbound_legs": outbound_legs,
                "inbound_legs": inbound_legs,
                "stops_outbound": len(outbound_legs) - 1,
                "stops_inbound": (len(inbound_legs) - 1) if inbound_legs else None,
                "reason": "Mock flight data (Duffel API not configured)",
            })

        return results[:5]


# Singleton instance
_flight_service = None


def get_flight_service() -> DuffelFlightService:
    """
    Get singleton instance of DuffelFlightService
    """
    global _flight_service
    if _flight_service is None:
        _flight_service = DuffelFlightService()
    return _flight_service


if __name__ == "__main__":
    test_flight_service = get_flight_service()
    results = test_flight_service.search_flights(
        origin="HKG",
        destination="KIX",
        departure_date="2026-09-10",
        return_date="2026-09-17",
        adults=1,
        outbound_preference={
            "flight_class": "business",
            "accept_redeye_flights": False,
            "direct_flights_only": True,
            "max_price_per_ticket": 5000
        },
        inbound_preference={
            "accept_redeye_flights": False,
            "direct_flights_only": True,
            "max_price_per_ticket": 2500
        },
    )
    print(f"results: {results}")