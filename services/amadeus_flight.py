"""
Amadeus Flight API Service
Wrapper for Amadeus flight search functionality
"""
import json
import os
import time
from typing import Optional, List, Dict, Any
from amadeus import Client, ResponseError


def _is_redeye(depart_time_str: str) -> bool:
    """Return True if departure time falls in the red-eye window (23:30–05:29)."""
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
        return True  # Unparseable timeslot → Don't filter


def _parse_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a single Amadeus itinerary segment into a flat leg dict (no price yet)."""
    departure = segment.get("departure", {})
    arrival = segment.get("arrival", {})
    depart_at = departure.get("at", "")
    arrival_at = arrival.get("at", "")
    return {
        "airline": segment.get("carrierCode", ""),
        "from": departure.get("iataCode", ""),
        "to": arrival.get("iataCode", ""),
        "depart_time": depart_at.split("T")[1][:8] if "T" in depart_at else "",
        "arrival_time": arrival_at.split("T")[1][:8] if "T" in arrival_at else "",
        "departure_date": depart_at.split("T")[0] if "T" in depart_at else "",
    }


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


class AmadeusFlightService:
    """
    Service for querying flight information from Amadeus API
    """

    _CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")

        self._cache: Dict[str, Any] = {}
        self._cache_ttl_seconds = self._CACHE_TTL_SECONDS

        if not client_id or not client_secret:
            # Use test credentials if not provided (for development)
            # Note: These are test credentials and may have limited functionality
            self.client = None
            self.use_mock = True
            print("Warning: AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET not set. Using mock data.")
        else:
            self.client = Client(client_id=client_id, client_secret=client_secret)
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
        Search for flights using Amadeus API.

        Args:
            origin: Origin airport code
            destination: Destination airport code
            departure_date: Departure date in YYYY-MM-DD format
            return_date: Return date in YYYY-MM-DD format (round-trip) or None (one-way)
            adults: Number of adult passengers
            outbound_preference: Preference dict applied to the outbound leg (see module docstring)
            inbound_preference: Preference dict applied to the inbound leg; ignored when return_date is None
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
            # Build search parameters
            search_params: Dict[str, Any] = {
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date,
                "adults": adults,
            }
            if return_date:
                search_params["returnDate"] = return_date

            outbound_class = (outbound_preference or {}).get("flight_class")
            inbound_class = (inbound_preference or {}).get("flight_class")
            if outbound_class and (not inbound_class or outbound_class == inbound_class):
                search_params["travelClass"] = outbound_class.upper()
            elif inbound_class and not outbound_class:
                search_params["travelClass"] = inbound_class.upper()
            # If both are set and differ, omit travelClass — a single request can't express two cabins.

            response = self.client.shopping.flight_offers_search.get(**search_params)
            print(f"search_flights(): len(response.data): {len(response.data)}")

            flights = []
            for i, offer in enumerate(response.data):
                if i < 3:
                    print(f"search_flights(): offer: {offer}")  # Temp                
                candidate = self._parse_flight_offer(offer, outbound_preference, inbound_preference)
                if candidate:
                    flights.append(candidate)

            # Sort by price
            flights.sort(key=lambda x: x.get("price", float("inf")))
            result = flights[:10]
            self._cache[cache_key] = (time.time(), result)
            return result

        except ResponseError as error:
            print(f"Amadeus API Error: {error}")
            print(f"End of message of Amadeus API Error")
            return [{"type": "flight", "reason": "Amadeus API error"}]
        except Exception as e:
            print(f"Error searching flights: {e}")
            return [{"type": "flight", "reason": "Unknown error"}]

    def _parse_flight_offer(
        self,
        offer: Dict[str, Any],
        outbound_preference: Optional[Dict[str, Any]] = None,
        inbound_preference: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse an Amadeus flight offer into a candidate dict with per-leg detail and pricing."""
        try:
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                return None

            outbound_segments = itineraries[0].get("segments", [])
            if not outbound_segments:
                return None
            outbound_legs_raw = [_parse_segment(s) for s in outbound_segments]

            inbound_legs_raw = None
            if len(itineraries) > 1:
                inbound_segments = itineraries[1].get("segments", [])
                if inbound_segments:
                    inbound_legs_raw = [_parse_segment(s) for s in inbound_segments]

            price_data = offer.get("price", {})
            total_price = float(price_data.get("total", 0))
            currency = price_data.get("currency", "USD")

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
                "reason": "Amadeus API result",
            }
        except Exception as e:
            print(f"Error parsing flight offer: {e}")
            return None

    def _mock_flight_search(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        max_price: Optional[int] = None,
        preferred_airlines: Optional[List[str]] = None,
        excluded_airlines: Optional[List[str]] = None,
        accept_redeye_flights: bool = True,
        direct_flights_only: bool = False,
        preferred_departure_timeslots: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Mock flight search for development/testing when API credentials are not available
        """
        # Generate mock flight options
        mock_flights = [
            {
                "type": "flight",
                "to": destination,
                "airline": "CX" if not preferred_airlines else preferred_airlines[0],
                "price": max_price if max_price else 500,
                "currency": "USD",
                "depart_time": "18:25:00",
                "arrival_time": "22:00:00",
                "departure_date": departure_date,
                "return_depart_time": "19:00:00" if return_date else "",
                "stops": 0,
                "reason": "Mock flight data (Amadeus API not configured)"
            },
            {
                "type": "flight",
                "to": destination,
                "airline": "AA",
                "price": max_price - 100 if max_price and max_price > 100 else 400,
                "currency": "USD",
                "depart_time": "15:25:00",
                "arrival_time": "17:00:00",
                "departure_date": departure_date,
                "return_depart_time": "10:00:00" if return_date else "",
                "stops": 1,
                "reason": "Mock flight data - cheaper option"
            }
        ]
        
        if max_price:
            mock_flights = [f for f in mock_flights if f["price"] <= max_price]
        if excluded_airlines:
            mock_flights = [f for f in mock_flights if f["airline"] not in excluded_airlines]
        if preferred_airlines:
            mock_flights = [f for f in mock_flights if f["airline"] in preferred_airlines]
        if direct_flights_only:
            mock_flights = [f for f in mock_flights if f["stops"] == 0]
        if not accept_redeye_flights:
            mock_flights = [f for f in mock_flights if not _is_redeye(f["depart_time"])]
        if preferred_departure_timeslots:
            mock_flights = [f for f in mock_flights if _matches_timeslots(f["depart_time"], preferred_departure_timeslots)]

        return mock_flights[:5]


# Singleton instance
_flight_service = None

def get_flight_service() -> AmadeusFlightService:
    """
    Get singleton instance of AmadeusFlightService
    """
    global _flight_service
    if _flight_service is None:
        _flight_service = AmadeusFlightService()
    return _flight_service


if __name__ == "__main__":
    # Test API:
    flight_service = get_flight_service()
    flight_service.search_flights(
        origin="HKG",
        destination="KIX",
        departure_date="2026-04-13",
        return_date="2026-04-18",
        adults=1,
        max_price=None,
        preferred_airlines=["UO"],
        flight_class=None
    )