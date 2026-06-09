"""
Booking.com Demand API Service
Fallback supplier for hotel offers.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib import error, request


class BookingHotelService:
    def __init__(self):
        self.api_key = os.getenv("BOOKING_API_KEY")
        self.affiliate_id = os.getenv("BOOKING_AFFILIATE_ID")
        use_sandbox = os.getenv("BOOKING_USE_SANDBOX", "true").lower() == "true"
        host = "https://demandapi-sandbox.booking.com" if use_sandbox else "https://demandapi.booking.com"
        self.search_url = f"{host}/3.1/accommodations/search"

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
        if not self.api_key or not self.affiliate_id:
            return []

        check_in = check_in_date or _default_check_in_date()
        check_out = check_out_date or _default_check_out_date(check_in)
        payload = {
            "booker": {"country": "us", "platform": "desktop"},
            "checkin": check_in,
            "checkout": check_out,
            "city": destination,
            "guests": {"number_of_rooms": room_quantity, "adults": adults},
        }

        raw_results = self._request_search(payload)
        return self._parse_offers(
            raw_results,
            max_price_per_night=max_price_per_night,
            preferred_area=preferred_area,
        )

    def _request_search(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        req = request.Request(
            self.search_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Affiliate-Id": self.affiliate_id,
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
                if isinstance(body, dict):
                    return body.get("data") or body.get("results") or []
                return []
        except error.HTTPError as http_error:
            print(f"Booking API HTTP error: {http_error}")
            return []
        except Exception as runtime_error:
            print(f"Booking API request error: {runtime_error}")
            return []

    def _parse_offers(
        self,
        data: List[Dict[str, Any]],
        max_price_per_night: Optional[int] = None,
        preferred_area: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        hotels: List[Dict[str, Any]] = []
        for item in data:
            name = item.get("name") or item.get("property", {}).get("name")
            if not name:
                continue

            amount = (
                item.get("price", {}).get("book")
                or item.get("price", {}).get("total")
                or item.get("price", {}).get("amount")
                or 0
            )
            try:
                nightly_price = int(float(amount))
            except (TypeError, ValueError):
                nightly_price = 0

            area = (
                item.get("district")
                or item.get("address", {}).get("city")
                or item.get("address", {}).get("street")
                or "unknown area"
            )
            currency = item.get("price", {}).get("currency") or "USD"

            if max_price_per_night and nightly_price > max_price_per_night:
                continue
            if preferred_area and preferred_area.lower() not in str(area).lower():
                continue

            hotels.append(
                {
                    "type": "hotel",
                    "name": name,
                    "price_per_night": nightly_price,
                    "currency": currency,
                    "area": area,
                    "supplier": "booking",
                    "reason": "Booking.com fallback offer",
                }
            )

        hotels.sort(key=lambda item: item.get("price_per_night", 10**9))
        return hotels[:5]


def _default_check_in_date() -> str:
    return (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")


def _default_check_out_date(check_in_date: str) -> str:
    base = datetime.strptime(check_in_date, "%Y-%m-%d")
    return (base + timedelta(days=1)).strftime("%Y-%m-%d")


_booking_service: Optional[BookingHotelService] = None


def get_booking_hotel_service() -> BookingHotelService:
    global _booking_service
    if _booking_service is None:
        _booking_service = BookingHotelService()
    return _booking_service
