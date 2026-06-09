"""
LLM-powered itinerary service.
Generates granular day-by-day travel plans (sightseeing, meals, activities)
directly from an LLM, without relying on packaged-tour APIs.
"""
import json
import os
from typing import Optional

from openai import OpenAI


_ACTIVITY_SCHEMA = """{
  "itinerary": [
    {
      "day": 1,
      "activities": [
        {
          "name": "<specific place or restaurant name>",
          "type": "<sightseeing|restaurant|museum|temple|park|market|shopping|activity|other>",
          "short_desc": "<1-2 sentences describing what to do/eat/see>",
          "time_slot": "<morning|afternoon|evening>",
          "estimated_cost": <integer in local currency, or null if free>,
          "currency": "<local currency code, e.g. JPY>",
          "minimum_duration": "<e.g. 1 hour, 30 minutes, 2-3 hours>",
          "area": "<neighbourhood or district>"
        }
      ]
    }
  ]
}"""

_SHARED_RULES = """\
- Each day should have 3-5 entries covering morning sightseeing, lunch, afternoon activities, and dinner.
- Do NOT repeat the same location across different days.
- Must-visit places must appear in the itinerary.
- Respect the budget constraint if provided.
- time_slot must be one of: "morning", "afternoon", "evening".
- type must be one of: "sightseeing", "restaurant", "museum", "temple", "park", "market", "shopping", "activity", "other".
- estimated_cost is an integer in local currency, or null if free.
- short_desc is 1-2 sentences describing what to do/eat/see there.\
"""


def _build_context_lines(
    destination: str,
    days: int,
    hotel_area: Optional[str],
    arrival_time: Optional[str],
    start_date: Optional[str],
    styles: Optional[list[str]],
    must_go_places: Optional[list[str]],
    max_price_per_ticket: Optional[float],
    num_people: int = 1,
    origin: Optional[str] = None,
    return_depart_time: Optional[str] = None,
) -> str:
    parts = [f"Destination: {destination}", f"Trip duration: {days} day(s)", f"Number of travelers: {num_people}"]
    if origin:
        parts.append(f"Departing from: {origin}")
    if hotel_area:
        parts.append(f"Hotel location: {hotel_area}")
    if start_date:
        parts.append(f"Start date: {start_date}")
    if arrival_time:
        parts.append(f"Arrival time on day 1: {arrival_time}")
    if return_depart_time:
        parts.append(f"Return flight departure time on day {days}: {return_depart_time}")
    if styles:
        parts.append(f"Travel styles: {', '.join(styles)}")
    if must_go_places:
        parts.append(f"Must-visit places: {', '.join(must_go_places)}")
    if max_price_per_ticket is not None:
        parts.append(f"Max budget per activity/ticket: {max_price_per_ticket} USD (convert to local currency as needed)")
    return "\n".join(parts)


def _day_rules(arrival_time: Optional[str], return_depart_time: Optional[str], days: int) -> str:
    """Build prompt rules for day 1 and last day based on flight times."""
    rules = []

    if arrival_time:
        hour = int(arrival_time[:2])
        if hour < 12:
            rules.append("Day 1 (arrival before noon): plan 2-4 activities, including meals.")
        elif hour < 17:
            rules.append("Day 1 (arrival noon-5pm): plan at most 2 activities plus dinner.")
        else:
            rules.append("Day 1 (arrival after 5pm): plan only dinner or 1 light evening activity.")

    if return_depart_time and days > 1:
        hour = int(return_depart_time[:2])
        if hour < 12:
            rules.append(f"Day {days} (return flight before noon): plan no activities, or airport duty-free shopping only.")
        elif hour < 18:
            rules.append(f"Day {days} (return flight noon-6pm): plan at most 1 activity.")
        else:
            rules.append(f"Day {days} (return flight after 6pm): plan 1-3 activities including meals.")

    return "\n".join(f"- {r}" for r in rules)


class LLMItineraryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self.client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

    def generate_itinerary(
        self,
        destination: str,
        days: int,
        hotel_area: Optional[str] = None,
        arrival_time: Optional[str] = None,
        start_date: Optional[str] = None,
        styles: Optional[list[str]] = None,
        must_go_places: Optional[list[str]] = None,
        max_price_per_ticket: Optional[float] = None,
        num_people: int = 1,
        origin: Optional[str] = None,
        return_depart_time: Optional[str] = None,
    ) -> list[dict]:
        if not self.client:
            return []

        context = _build_context_lines(
            destination, days, hotel_area, arrival_time, start_date,
            styles, must_go_places, max_price_per_ticket,
            num_people=num_people, origin=origin, return_depart_time=return_depart_time,
        )
        flight_rules = _day_rules(arrival_time, return_depart_time, days)

        prompt = f"""You are an expert travel planner. Create a detailed, realistic {days}-day itinerary.

{context}

Plan each day at a granular level — specific places to visit, where to have lunch, afternoon spots, and dinner restaurants. Mix sightseeing, local food, and experiences that match the travel styles.

Rules:
{_SHARED_RULES}
{flight_rules}

Respond ONLY with valid JSON matching this structure:
{_ACTIVITY_SCHEMA}"""

        return self._call_llm(prompt, context="generate_itinerary")

    def update_itinerary(
        self,
        existing_itinerary: list[dict],
        destination: str,
        days: int,
        hotel_area: Optional[str] = None,
        arrival_time: Optional[str] = None,
        styles: Optional[list[str]] = None,
        must_go_places: Optional[list[str]] = None,
        max_price_per_ticket: Optional[float] = None,
        num_people: int = 1,
        origin: Optional[str] = None,
        return_depart_time: Optional[str] = None,
    ) -> list[dict]:
        if not self.client:
            return []

        existing_summary = [
            {
                "day": day_plan.get("day"),
                "activities": [
                    {
                        "name": act.get("name", ""),
                        "type": act.get("type", ""),
                        "time_slot": act.get("time_slot", ""),
                        "short_desc": act.get("short_desc", ""),
                    }
                    for act in day_plan.get("activities", [])
                ],
            }
            for day_plan in existing_itinerary
        ]

        context = _build_context_lines(
            destination, days, hotel_area, arrival_time, None,
            styles, must_go_places, max_price_per_ticket,
            num_people=num_people, origin=origin, return_depart_time=return_depart_time,
        )
        flight_rules = _day_rules(arrival_time, return_depart_time, days)

        prompt = f"""You are an expert travel planner revising an existing itinerary based on updated traveler preferences.

Trip context:
{context}

Current itinerary:
{json.dumps(existing_summary, ensure_ascii=False, indent=2)}

Revise the itinerary to better match the updated preferences. You may keep, replace, or reorder entries within each day. Introduce new specific places or restaurants where needed.

Rules:
{_SHARED_RULES}
{flight_rules}

Respond ONLY with valid JSON matching this structure:
{_ACTIVITY_SCHEMA}"""

        return self._call_llm(prompt, context="update_itinerary")

    def _call_llm(self, prompt: str, context: str) -> list[dict]:
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"},
                timeout=180,
            )
            parsed = json.loads(response.choices[0].message.content)
            return self._normalize(parsed.get("itinerary", []))
        except Exception as e:
            print(f"LLMItineraryService.{context}() error: {e}")
            return []

    def _normalize(self, llm_itinerary: list[dict]) -> list[dict]:
        result = []
        for day_plan in llm_itinerary:
            day_acts = []
            for act in day_plan.get("activities", []):
                if not act.get("name"):
                    continue
                day_acts.append({
                    "name": act.get("name", ""),
                    "type": act.get("type", "activity"),
                    "short_desc": act.get("short_desc", ""),
                    "time_slot": act.get("time_slot", ""),
                    "estimated_cost": act.get("estimated_cost"),
                    "currency": act.get("currency", ""),
                    "minimum_duration": act.get("minimum_duration", ""),
                    "area": act.get("area", ""),
                    "supplier": "llm",
                    "reason": "LLM generated activity",
                })
            result.append({"day": day_plan.get("day"), "activities": day_acts})
        return result


_llm_itinerary_service: Optional[LLMItineraryService] = None


def get_llm_itinerary_service() -> LLMItineraryService:
    global _llm_itinerary_service
    if _llm_itinerary_service is None:
        _llm_itinerary_service = LLMItineraryService()
    return _llm_itinerary_service
