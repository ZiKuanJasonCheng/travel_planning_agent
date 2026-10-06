"""
LLM-powered itinerary service.
Generates granular day-by-day travel plans (sightseeing, meals, activities)
directly from an LLM, without relying on packaged-tour APIs.
"""
import json
import logging
import os
from typing import Optional

from openai import APIError

from services.langfuse_client import observe, update_current_generation
from services.llm_retry import call_with_retry
from services.openai_client import get_openai_client
from services.ticketmaster_events import SEARCH_LOCAL_EVENTS_TOOL, execute_tool_call

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_MAX_TOOL_ITERATIONS = 3


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


def _available_tools() -> Optional[list[dict]]:
    """The tool list to offer the model, or None when event search isn't configured.

    With no TICKETMASTER_API_KEY the request carries no tools at all, which is
    the pre-tool behavior — dev environments and the existing tests are
    unaffected.
    """
    if not os.getenv("TICKETMASTER_API_KEY"):
        return None
    return [SEARCH_LOCAL_EVENTS_TOOL]


def _build_context_lines(
    destination: str,
    days: int,
    hotel_area: Optional[str],
    arrival_time: Optional[str],
    start_date: Optional[str],
    styles: Optional[list[str]],
    exclusions: Optional[list[str]],
    must_go_places: Optional[list[str]],
    max_price_per_ticket: Optional[float],
    num_people: int = 1,
    origin: Optional[str] = None,
    return_depart_time: Optional[str] = None,
    hotel_lat: Optional[float] = None,
    hotel_lon: Optional[float] = None,
    end_date: Optional[str] = None,
) -> str:
    parts = [f"Destination: {destination}", f"Trip duration: {days} day(s)", f"Number of travelers: {num_people}"]
    if origin:
        parts.append(f"Departing from: {origin}")
    if hotel_area:
        parts.append(f"Hotel location: {hotel_area}")
    if hotel_lat is not None and hotel_lon is not None:
        parts.append(f"Hotel coordinates: ({hotel_lat:.5f}, {hotel_lon:.5f})")
    # The end date matters for event search: the planner must query the whole
    # visit window, not just its first day.
    if start_date and end_date:
        parts.append(f"Trip dates: {start_date} to {end_date}")
    elif start_date:
        parts.append(f"Start date: {start_date}")
    if arrival_time:
        parts.append(f"Arrival time on day 1: {arrival_time}")
    if return_depart_time:
        parts.append(f"Return flight departure time on day {days}: {return_depart_time}")
    if styles:
        parts.append(f"Travel styles: {', '.join(styles)}")
    if exclusions:
        parts.append(f"Exclusions (DO NOT include these): {', '.join(exclusions)}")
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
        self.client = get_openai_client() if api_key else None

    def generate_itinerary(
        self,
        destination: str,
        days: int,
        hotel_area: Optional[str] = None,
        arrival_time: Optional[str] = None,
        start_date: Optional[str] = None,
        styles: Optional[list[str]] = None,
        exclusions: Optional[list[str]] = None,
        must_go_places: Optional[list[str]] = None,
        max_price_per_ticket: Optional[float] = None,
        num_people: int = 1,
        origin: Optional[str] = None,
        return_depart_time: Optional[str] = None,
        critique: Optional[str] = None,
        hotel_lat: Optional[float] = None,
        hotel_lon: Optional[float] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        if not self.client:
            return []

        context = _build_context_lines(
            destination, days, hotel_area, arrival_time, start_date,
            styles, exclusions, must_go_places, max_price_per_ticket,
            num_people=num_people, origin=origin, return_depart_time=return_depart_time,
            hotel_lat=hotel_lat, hotel_lon=hotel_lon,
            end_date=end_date,
        )
        flight_rules = _day_rules(arrival_time, return_depart_time, days)

        prompt = f"""You are an expert travel planner. Create a detailed, realistic {days}-day itinerary.

{context}

Plan each day at a granular level — specific places to visit, where to have lunch, afternoon spots, and dinner restaurants. Mix sightseeing, local food, and experiences that match the travel styles.

If a tool is available for finding local events, you may use it to include something happening during the trip dates.

Rules:
{_SHARED_RULES}
{flight_rules}

Respond ONLY with valid JSON matching this structure:
{_ACTIVITY_SCHEMA}"""

        if critique:
            prompt += f"\n\nIMPORTANT — a quality review found these issues in a previous version. You MUST fix them:\n{critique}"

        return self._call_llm(prompt, context="generate_itinerary", tools=_available_tools())

    def update_itinerary(
        self,
        existing_itinerary: list[dict],
        destination: str,
        days: int,
        hotel_area: Optional[str] = None,
        arrival_time: Optional[str] = None,
        styles: Optional[list[str]] = None,
        exclusions: Optional[list[str]] = None,
        must_go_places: Optional[list[str]] = None,
        max_price_per_ticket: Optional[float] = None,
        num_people: int = 1,
        origin: Optional[str] = None,
        return_depart_time: Optional[str] = None,
        critique: Optional[str] = None,
        hotel_lat: Optional[float] = None,
        hotel_lon: Optional[float] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
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
            destination, days, hotel_area, arrival_time, start_date,
            styles, exclusions, must_go_places, max_price_per_ticket,
            num_people=num_people, origin=origin, return_depart_time=return_depart_time,
            hotel_lat=hotel_lat, hotel_lon=hotel_lon,
            end_date=end_date,
        )
        flight_rules = _day_rules(arrival_time, return_depart_time, days)

        prompt = f"""You are an expert travel planner revising an existing itinerary based on updated traveler preferences.

Trip context:
{context}

Current itinerary:
{json.dumps(existing_summary, ensure_ascii=False, indent=2)}

Revise the itinerary to better match the updated preferences. You may keep, replace, or reorder entries within each day. Introduce new specific places or restaurants where needed.

The trip is exactly {days} day(s) long. Return exactly {days} day entries numbered 1 through {days}. If the current itinerary has a different number of days, add or remove days so the total matches — this matters more than preserving the existing day-by-day structure.

If a tool is available for finding local events, you may use it to include something happening during the trip dates.

Rules:
{_SHARED_RULES}
{flight_rules}

Respond ONLY with valid JSON matching this structure:
{_ACTIVITY_SCHEMA}"""

        if critique:
            prompt += f"\n\nIMPORTANT — a quality review found these issues in a previous version. You MUST fix them:\n{critique}"

        return self._call_llm(prompt, context="update_itinerary", tools=_available_tools())

    @observe(as_type="generation", capture_input=False, capture_output=False)
    def _call_llm(self, prompt: str, context: str, tools: Optional[list[dict]] = None) -> list[dict]:
        """Run the planning prompt, letting the model call tools before it answers.

        Up to `_MAX_TOOL_ROUNDS` tool rounds run; once a response arrives with
        no tool_calls, its content is the itinerary JSON and is parsed. If the
        cap is reached the model is asked once more with tools withheld, so a
        usable itinerary always comes back.
        """
        try:
            messages = [{"role": "user", "content": prompt}]
            request = {
                "model": _MODEL,
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
                "timeout": 180,
            }
            if tools:
                request["tools"] = tools

            total_prompt_tokens = 0
            total_completion_tokens = 0
            last_model = _MODEL
            final_message = None

            for iteration in range(_MAX_TOOL_ITERATIONS):
                # On the last iteration, withhold tools so the model must answer
                # with the itinerary itself rather than another lookup.
                last_chance = iteration == _MAX_TOOL_ITERATIONS - 1
                call_kwargs = dict(request)
                if last_chance and tools:
                    call_kwargs.pop("tools", None)

                response = call_with_retry(
                    self.client.chat.completions.create, messages=messages, **call_kwargs
                )
                message = response.choices[0].message
                last_model = response.model
                total_prompt_tokens += response.usage.prompt_tokens
                total_completion_tokens += response.usage.completion_tokens
                final_message = message

                tool_calls = message.tool_calls or []
                if not tool_calls:
                    # The answer turn holds the itinerary JSON. A tool-calling
                    # turn never reaches here — it carries tool_calls with
                    # content=None, which json.loads would reject.
                    break

                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                })
                for tc in tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": execute_tool_call(tc.function.name, tc.function.arguments),
                    })

            update_current_generation(
                model=last_model,
                input={"tools": tools, "messages": messages},
                output={
                    "role": final_message.role,
                    "content": final_message.content,
                    "tool_calls": [tc.model_dump() for tc in (final_message.tool_calls or [])],
                },
                usage_details={
                    "input": total_prompt_tokens,
                    "output": total_completion_tokens,
                },
                model_parameters={
                    "temperature": 0.7,
                    "response_format": "json_object",
                },
            )
            parsed = json.loads(final_message.content)
            return self._normalize(parsed.get("itinerary", []))
        except APIError as e:
            logger.error(f"LLMItineraryService.{context}() OpenAI API error: {e}")
            return [{"day": None, "activities": [], "reason": "LLM API error"}]
        except Exception as e:
            logger.error(f"LLMItineraryService.{context}() error: {e}")
            return [{"day": None, "activities": [], "reason": "Unknown error"}]

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
