"""
Amadeus Flight API Service
Wrapper for Amadeus flight search functionality
"""
import os
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


class AmadeusFlightService:
    """
    Service for querying flight information from Amadeus API
    """
    
    def __init__(self):
        """
        Initialize Amadeus client with API credentials from environment variables
        """
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            # Use test credentials if not provided (for development)
            # Note: These are test credentials and may have limited functionality
            self.client = None
            self.use_mock = True
            print("Warning: AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET not set. Using mock data.")
        else:
            self.client = Client(
                client_id=client_id,
                client_secret=client_secret
            )
            self.use_mock = False
    
    def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: Optional[str] = None,
        adults: int = 1,
        max_price: Optional[int] = None,
        preferred_airlines: Optional[List[str]] = None,
        flight_class: Optional[str] = None,
        excluded_airlines: Optional[List[str]] = None,
        accept_redeye_flights: bool = True,
        direct_flights_only: bool = False,
        preferred_departure_timeslots: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for flights using Amadeus API
        
        Args:
            origin: Origin airport code (e.g., "NYC", "JFK")
            destination: Destination airport code (e.g., "NRT", "TYO")
            departure_date: Departure date in YYYY-MM-DD format
            return_date: Return date in YYYY-MM-DD format (optional for one-way)
            adults: Number of adult passengers (default: 1)
            max_price: Maximum price filter (optional)
            preferred_airlines: List of preferred airline codes (optional)
            flight_class: Preferred flight class (e.g., "ECONOMY", "BUSINESS")
            excluded_airlines: List of airline codes to exclude
            accept_redeye_flights: Whether to accept red-eye flights (departure time to be from 23:30 to 23:59 or 0:00 to 5:29 on departure date)
            direct_flights_only: Whether to only accepts direct flights (default: False)
            preferred_departure_timeslots: List of preferred departure time slots in HH:MM~HH:MM format for flights
        Returns:
            List of flight options with details
        """

        print(f"search_flights(): use_mock: {self.use_mock}")
        print(f"search_flights(): preferred_airlines: {preferred_airlines}, excluded_airlines: {excluded_airlines}")
        if self.use_mock:
            return self._mock_flight_search(
                origin, destination, departure_date, return_date,
                max_price, preferred_airlines, excluded_airlines,
                accept_redeye_flights, direct_flights_only, preferred_departure_timeslots,
            )

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
            if max_price:
                search_params["maxPrice"] = max_price
            if flight_class:
                search_params["travelClass"] = flight_class.upper()

            print(f"search_flights(): search_params: {search_params}")

            # Search for flight offers
            response = self.client.shopping.flight_offers_search.get(**search_params)
            print(f"search_flights(): len(response.data): {len(response.data)}")

            flights = []
            skipped_counts = {
                "is_excluded_airlines": 0,
                "not_preferred_airlines": 0,
                "not_direct_flights": 0,
                "is_redeye_flights": 0,
                "not_in_preferred_departure_timeslots": 0
            }
            for i, offer in enumerate(response.data):
                if i < 3:
                    print(f"search_flights(): offer: {offer}")  # Temp
                # Parse and format results
                flight_option = self._parse_flight_offer(
                    offer,
                    preferred_airlines=preferred_airlines,
                    excluded_airlines=excluded_airlines,
                    accept_redeye_flights=accept_redeye_flights,
                    direct_flights_only=direct_flights_only,
                    preferred_departure_timeslots=preferred_departure_timeslots,
                    skipped_counts=skipped_counts
                )
                if flight_option:
                    flights.append(flight_option)
            
            # Sort by price
            flights.sort(key=lambda x: x.get("price", float("inf")))
            # Sort by arrival time from earliet to latest
            # ...

            return flights[:10]

        except ResponseError as error:
            print(f"Amadeus API Error: {error}")
            print(f"End of message of Amadeus API Error")
            return self._mock_flight_search(
                origin, destination, departure_date, return_date,
                max_price, preferred_airlines, excluded_airlines,
                accept_redeye_flights, direct_flights_only, preferred_departure_timeslots,
            )
        except Exception as e:
            print(f"Error searching flights: {e}")
            return self._mock_flight_search(
                origin, destination, departure_date, return_date,
                max_price, preferred_airlines, excluded_airlines,
                accept_redeye_flights, direct_flights_only, preferred_departure_timeslots,
            )
    
    def _parse_flight_offer(
        self,
        offer: Dict[str, Any],
        preferred_airlines: Optional[List[str]] = None,
        excluded_airlines: Optional[List[str]] = None,
        accept_redeye_flights: bool = True,
        direct_flights_only: bool = False,
        preferred_departure_timeslots: Optional[List[str]] = None,
        skipped_counts: Dict[str, Any] = {}
    ) -> Optional[Dict[str, Any]]:
        """
        Parse Amadeus flight offer into our standard format
        """
        try:
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                return None

            # Get first itinerary (outbound)
            outbound = itineraries[0]
            segments = outbound.get("segments", [])
            if not segments:
                return None

            # Extract outbound flight details
            first_segment = segments[0]
            last_segment = segments[-1]

            airline_code = first_segment.get("carrierCode", "")
            departure_time = first_segment.get("departure", {}).get("at", "")
            arrival_time = last_segment.get("arrival", {}).get("at", "")

            # Filter: excluded airlines
            if excluded_airlines and airline_code in excluded_airlines:
                skipped_counts["is_excluded_airlines"] = excluded_airlines.get("is_excluded_airlines", 0) + 1
                return None

            # Filter: preferred airlines whitelist
            if preferred_airlines and airline_code not in preferred_airlines:
                skipped_counts["not_preferred_airlines"] = excluded_airlines.get("not_preferred_airlines", 0) + 1
                return None

            # Filter: direct flights
            stops = len(segments) - 1
            if direct_flights_only and stops > 0:
                skipped_counts["not_direct_flights"] = excluded_airlines.get("not_direct_flights", 0) + 1
                return None

            # Filter: red-eye
            depart_time = departure_time.split("T")[1][:8] if "T" in departure_time else ""
            if not accept_redeye_flights and depart_time and _is_redeye(depart_time):
                skipped_counts["is_redeye_flights"] = excluded_airlines.get("is_redeye_flights", 0) + 1
                return None

            # Filter: preferred departure timeslots
            if preferred_departure_timeslots and depart_time and not _matches_timeslots(depart_time, preferred_departure_timeslots):
                skipped_counts["not_in_preferred_departure_timeslots"] = excluded_airlines.get("not_in_preferred_departure_timeslots", 0) + 1
                return None

            price_data = offer.get("price", {})
            total_price = float(price_data.get("total", 0))
            currency = price_data.get("currency", "USD")

            # Format arrival times
            arrival_time_formatted = arrival_time.split("T")[1][:8] if "T" in arrival_time else ""

            # Extract return leg departure time if this is a round-trip offer
            return_depart_time = ""
            if len(itineraries) > 1:
                return_segments = itineraries[1].get("segments", [])
                if return_segments:
                    return_dep_at = return_segments[0].get("departure", {}).get("at", "")
                    return_depart_time = return_dep_at.split("T")[1][:8] if "T" in return_dep_at else ""

            return {
                "type": "flight",
                "to": last_segment.get("arrival", {}).get("iataCode", ""),
                "airline": airline_code,
                "price": int(total_price),
                "currency": currency,
                "depart_time": depart_time,
                "arrival_time": arrival_time_formatted,
                "departure_date": departure_time.split("T")[0] if "T" in departure_time else "",
                "return_depart_time": return_depart_time,
                "stops": stops,
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