"""
Amadeus Tours & Activities API Service
Primary supplier for attraction/activity search with geocoding support.
"""
import os
import logging
from typing import Optional, List, Dict, Tuple

from amadeus import Client, ResponseError, Location
from openai import OpenAI

from services.geocoding import fetch_coordinates
from services.currency import to_usd

openai_api_key = os.getenv("OPENAI_API_KEY")

class AmadeusAttractionService:
    def __init__(self):
        client_id = os.getenv("AMADEUS_CLIENT_ID")
        client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
        self.client: Optional[Client] = None
        if client_id and client_secret:
            self.client = Client(client_id=client_id, client_secret=client_secret)

        self.openai_client: Optional[OpenAI] = OpenAI(api_key=openai_api_key) if openai_api_key else None

    def search_activities(
        self,
        destination: str,
        days: int,
        hotel_area: str,
        arrival_time: str,
        styles: List[str],
        must_go_places: List[str],
        max_price_per_ticket: Optional[float],
    ) -> List[Dict]:
        if not self.client:
            return []

        try:
            coords = self._resolve_coordinates(destination)
            print(f"coords: {coords}")
            if coords is None:
                return []

            lat, lon = coords
            response = self.client.shopping.activities.get(
                latitude=lat, longitude=lon, radius=20
            )
            return self._parse_activities(
                response.data, styles, must_go_places, max_price_per_ticket, destination
            )
        except ResponseError as error:
            print(f"Amadeus Attraction API error: {error}")
            return []
        except Exception as error:
            print(f"Unexpected Amadeus attraction error: {error}")
            return []

    def _resolve_coordinates(self, city_name: str) -> Optional[Tuple[float, float]]:
        try:
            print(f"_resolve_coordinates(): city_name: {city_name}")
            response = self.client.reference_data.locations.get(
                keyword=city_name, subType=Location.ANY,  #"CITY"
                #max=200
            )
            print(f"_resolve_coordinates(): response: {response}")
            print(f"_resolve_coordinates(): response.data: {response.data}")
            if response.data:
                geo = response.data[0].get("geoCode", {})
                lat = geo.get("latitude")
                lon = geo.get("longitude")
                print(f"lat: {lat}, lon: {lon}")
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
        except Exception as error:
            print(f"Amadeus location lookup failed for {city_name}: {error}")

        return fetch_coordinates(city_name)

    def _style_matches_activity(self, name: str, styles: List[str], description: str) -> bool:
        """Return True if any style semantically matches the activity, using LLM when available."""
        combined = (name + " " + description).strip()
        #print(f"combined: {combined}, styles: {styles}")
        if self.openai_client:
            try:
                styles_str = ", ".join(styles)
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"Activity: {combined}\n"
                                f"Travel styles: {styles_str}\n\n"
                                "Does this activity semantically match any of the listed travel styles? "
                                "Reply with only 'yes' or 'no'."
                            ),
                        }
                    ],
                    max_tokens=5,
                    temperature=0,
                )
                answer = response.choices[0].message.content.strip().lower()
                print(f"answer: {answer}")
                return answer.startswith("yes")
            except Exception as error:
                print(f"LLM style matching failed, falling back to keyword match: {error}")

        combined_lower = combined.lower()
        return any(style.lower() in combined_lower for style in styles)

    def _parse_activities(
        self,
        data: List[Dict],
        styles: Optional[List[str]],
        must_go_places: Optional[List[str]],
        max_price_per_ticket: Optional[float],
        destination: str,
    ) -> List[Dict]:
        #time_slots = ["morning", "afternoon", "evening"]
        results = []
        print(f"_parse_activities(): styles: {styles}")
        print(f"_parse_activities(): len(data): {len(data)}")
        print(f"_parse_activities(): data[:3]: {data[:3]}")

        for idx, activity in enumerate(data):
            #print(f"idx: {idx}, activity: {activity}")
            name = activity.get("name", "")
            description = activity.get("description", "") or ""  #shortDescription
            minimum_duration = activity.get("minimumDuration", "30 minutes")
            print(f"idx: {idx}, name: {name}")

            price_amount = None
            currency = "USD"
            price_info = activity.get("price", {})
            if price_info:
                try:
                    price_amount = int(float(price_info.get("amount", 0)))
                    currency = price_info.get("currencyCode", "USD")
                except (TypeError, ValueError):
                    price_amount = None

            is_must_go = False
            if must_go_places:
                name_lower = name.lower()
                for place in must_go_places:
                    if place.lower() in name_lower:
                        is_must_go = True
                        break

            if not is_must_go:
                if max_price_per_ticket is not None and price_amount is not None:
                    if to_usd(price_amount, currency) > max_price_per_ticket:
                        continue

                if styles:
                    if not self._style_matches_activity(name, styles, description=""):
                        continue

            activity_type = "activity"
            if activity.get("categories"):
                activity_type = activity["categories"][0]

            results.append(
                {
                    "name": name,
                    "type": activity_type,
                    "description": description,
                    "estimated_cost": price_amount,
                    "currency": currency,
                    #"time_slot": time_slots[len(results) % 3],
                    "minimum_duration": minimum_duration,
                    "area": destination,
                    "supplier": "amadeus",
                    "reason": "Amadeus Tours & Activities API result",
                }
            )

        return results


_attraction_service: Optional[AmadeusAttractionService] = None


def get_amadeus_attraction_service() -> AmadeusAttractionService:
    global _attraction_service
    if _attraction_service is None:
        _attraction_service = AmadeusAttractionService()
    return _attraction_service


if __name__ == "__main__":
    # Test API:
    amadeus_service = get_amadeus_attraction_service()
    all_activities = amadeus_service.search_activities(
        destination="kyoto",
        days=5,
        hotel_area="city center",
        arrival_time="10:00:00",
        styles=['local food'],  #'culture experiences'
        must_go_places=None,
        max_price_per_ticket=None,
    )
    print(f"Test: all_activities: {all_activities}")