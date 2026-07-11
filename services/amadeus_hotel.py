"""
Amadeus Hotel API Service
Primary supplier for hotel offers.
"""
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from amadeus import Client, ResponseError

from services.geocoding import fetch_coordinates
from services.currency import to_usd

REQUEST_BATCH = 20  # A maximum of 20 hotel IDs per request

class AmadeusHotelService:
    def __init__(self):
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        self.client: Optional[Client] = None
        if client_id and client_secret:
            self.client = Client(client_id=client_id, client_secret=client_secret)

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
        if not self.client:
            return []

        try:
            coords = fetch_coordinates(destination)
            print(f"coords: {coords}")
            if coords is None:
                return []
            lat, lon = coords
            hotel_ids = self._fetch_hotel_ids(lat, lon)
            print(f"hotel_ids: {hotel_ids}")
            if not hotel_ids:
                return []
            print(f"len(hotel_ids): {len(hotel_ids)}")

            check_in = check_in_date or _default_check_in_date()
            check_out = check_out_date or _default_check_out_date(check_in)

            base_params = {
                "adults": adults,
                "roomQuantity": room_quantity,
                "checkInDate": check_in,
                "checkOutDate": check_out,
                "currency": "USD",
            }
            print(f"search_hotels(): base_params: {base_params}")

            #raw_data = self._fetch_offers_resilient(hotel_ids[:20], base_params)
            raw_data = []
            for i in range(0, len(hotel_ids), REQUEST_BATCH):
                print(f"search_hotels(): i: {i}")
                raw_data.extend(self._fetch_offers_resilient(hotel_ids[i: i+REQUEST_BATCH], base_params))
            
            print(f"search_hotels(): raw_data: {raw_data}")
            return self._parse_offers(
                raw_data,
                check_in=check_in,
                check_out=check_out,
                max_price_per_night=max_price_per_night,
                preferred_area=preferred_area,
            )
        except ResponseError as error:
            print(f"Amadeus Hotel API error: {error}")
            return [{"reason": "Amadeus Hotel API error"}]
        except Exception as error:
            print(f"Unexpected Amadeus hotel error: {error}")
            return [{"reason": "Unknown error"}]


    def _fetch_offers_resilient(
        self, hotel_ids: List[str], base_params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Fetch hotel offers, skipping bad IDs by parsing errors and retrying without them."""
        remaining = list(hotel_ids)
        collected: List[Dict[str, Any]] = []

        while remaining:
            try:
                response = self.client.shopping.hotel_offers_search.get(
                    hotelIds=",".join(remaining), **base_params
                )
                collected.extend(response.data)
                break
            except ResponseError as error:
                bad_ids = _parse_bad_hotel_ids(str(error))
                #print(f"Hit ResponseError! remaining: {remaining}")
                bad_ids_in_remaining = [hid for hid in bad_ids if hid in remaining]
                if bad_ids_in_remaining:
                    print(f"Skipping hotels {bad_ids_in_remaining}: {error}")
                    remaining = [hid for hid in remaining if hid not in bad_ids_in_remaining]
                    print(f"remaining: {remaining}")
                else:
                    print(f"Amadeus Hotel API error (unparseable bad ID): {error}")
                    break
        print(f"collected: {collected}")

        return collected

    def _fetch_hotel_ids(self, latitude: float, longitude: float) -> List[str]:
        try:
            response = self.client.reference_data.locations.hotels.by_geocode.get(
                latitude=latitude, longitude=longitude, radius=5, radiusUnit="KM"
            )
            ids = [item.get("hotelId") for item in response.data if item.get("hotelId")]
            return ids
        except Exception as error:
            print(f"Failed fetching Amadeus hotel ids by geocode: {error}")
            return []

    def _parse_offers(
        self,
        data: List[Dict[str, Any]],
        check_in: str,
        check_out: str,
        max_price_per_night: Optional[int] = None,
        preferred_area: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        from datetime import date
        nights = max(1, (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days)
        hotels: List[Dict[str, Any]] = []

        for i, item in enumerate(data):
            if i < 3:
                print(f"Temp: i: {i}, item: {item}")  # Temp

            hotel = item.get("hotel") or {}
            hotel_offers = item.get("offers") or []
            if not hotel_offers:
                continue

            first_offer = hotel_offers[0]
            total_price = float((first_offer.get("price") or {}).get("total", 0) or 0)
            currency = (first_offer.get("price") or {}).get("currency", "USD")
            room = max(1, int((first_offer.get("roomQuantity") or 1)))
            nightly_price = int(total_price / (room * nights)) if total_price > 0 else 0

            address = hotel.get("address") or {}
            area = ", ".join(address.get("lines", [])).strip() or address.get("cityName", "")

            if max_price_per_night and to_usd(nightly_price, currency) > max_price_per_night:
                continue
            if preferred_area and preferred_area.lower() not in area.lower():
                continue

            hotels.append(
                {
                    "type": "hotel",
                    "name": hotel.get("name") or "Unknown Hotel",
                    "price_per_night": nightly_price,
                    "currency": currency,
                    "area": area or "unknown area",
                    "hotel_id": hotel.get("hotelId"),
                    "lat": hotel.get("latitude"),
                    "lon": hotel.get("longitude"),
                    "supplier": "amadeus",
                    "reason": "Amadeus hotel offer",
                }
            )

        hotels.sort(key=lambda item: item.get("price_per_night", 10**9))
        return hotels[:5]


def _parse_bad_hotel_ids(error_message: str) -> List[str]:
    """Extract bad hotel IDs from an Amadeus 400 error message.

    Handles formats like:
      [hotelIds=XKTYO78H] Provider Error - ...
      [hotelIds= MOTYOMOR] INVALID PROPERTY CODE
      [hotelIds=BWKIX525,NKKIX001] Provider Error - ...
    """
    match = re.search(r"\[hotelIds=\s*([A-Z0-9,\s]+)\]", error_message)
    if not match:
        return []
    return [hid.strip() for hid in match.group(1).split(",") if hid.strip()]


def _normalize_city_code(destination: str) -> str:
    destination_map = {
        "tokyo": "TYO",
        "kyoto": "KIX",
        "osaka": "OSA",
        "paris": "PAR",
        "london": "LON",
        "hong kong": "HKG",
        "singapore": "SIN",
        "bangkok": "BKK",
        "seoul": "SEL",
        "sydney": "SYD",
    }
    return destination_map.get(destination.lower().strip(), destination.upper()[:3])


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str) -> str:
    base = datetime.strptime(check_in_date, "%Y-%m-%d")
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


_hotel_service: Optional[AmadeusHotelService] = None


def get_amadeus_hotel_service() -> AmadeusHotelService:
    global _hotel_service
    if _hotel_service is None:
        _hotel_service = AmadeusHotelService()
    return _hotel_service



if __name__ == "__main__":
    # Test API:
    hotel_service = get_amadeus_hotel_service()
    hotels = hotel_service.search_hotels(
        destination="Tokyo",
        check_in_date="2026-07-05",
        check_out_date="2026-07-18",
        adults=1,
        room_quantity=1,
        max_price_per_night=None,
        preferred_area=None
    )
    print(f"Test: hotels: {hotels}")