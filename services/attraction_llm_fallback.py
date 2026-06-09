import json
import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = "You are a travel itinerary expert. Generate realistic, specific attraction recommendations for the given destination."

ITINERARY_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_itinerary",
        "description": "Generate a list of attraction activities for a travel itinerary.",
        "parameters": {
            "type": "object",
            "properties": {
                "activities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Name of the attraction or activity"
                            },
                            "type": {
                                "type": "string",
                                "description": "Type of attraction (e.g., museum, park, temple, restaurant)"
                            },
                            "estimated_cost": {
                                "type": ["integer", "null"],
                                "description": "Estimated ticket or entry cost in local currency, null if free"
                            },
                            "currency": {
                                "type": "string",
                                "description": "Currency code (e.g., USD, JPY, EUR)"
                            },
                            "time_slot": {
                                "type": "string",
                                "enum": ["morning", "afternoon", "evening"],
                                "description": "Recommended time slot for visiting"
                            },
                            "area": {
                                "type": "string",
                                "description": "Area or district of the attraction within the destination"
                            },
                            "reason": {
                                "type": "string",
                                "description": "Why this attraction is recommended, including estimated price note"
                            }
                        },
                        "required": ["name", "type", "estimated_cost", "currency", "time_slot", "area", "reason"]
                    }
                }
            },
            "required": ["activities"]
        }
    }
}


class AttractionLLMFallbackService:
    def generate_itinerary(
        self,
        destination: str,
        days: int,
        hotel_area: str | None,
        arrival_time: str | None,
        styles: list[str] | None,
        must_go_places: list[str] | None,
        max_price_per_ticket: int | None,
    ) -> list[dict]:
        try:
            user_prompt_parts = [f"Destination: {destination}", f"Number of days: {days}"]

            if hotel_area:
                user_prompt_parts.append(f"Hotel area: {hotel_area}")
            if arrival_time:
                user_prompt_parts.append(f"Arrival time: {arrival_time}")
            if styles:
                user_prompt_parts.append(f"Travel styles/preferences: {', '.join(styles)}")
            if must_go_places:
                user_prompt_parts.append(f"Must-visit places: {', '.join(must_go_places)}")
            if max_price_per_ticket is not None:
                user_prompt_parts.append(f"Maximum price per ticket: {max_price_per_ticket}")

            user_prompt = "\n".join(user_prompt_parts)
            user_prompt += (
                f"\n\nPlease generate a realistic itinerary with activities spread across {days} day(s). "
                "For each activity, include the estimated price in the reason field to indicate it is not a confirmed real price."
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                tools=[ITINERARY_TOOL],
                tool_choice={"type": "function", "function": {"name": "generate_itinerary"}},
                timeout=60
            )

            tool_call = response.choices[0].message.tool_calls[0]
            args = tool_call.function.arguments
            parsed = json.loads(args)
            activities = parsed.get("activities", [])

            result = []
            for activity in activities:
                result.append({
                    "name": activity.get("name", ""),
                    "type": activity.get("type", ""),
                    "estimated_cost": activity.get("estimated_cost"),
                    "currency": activity.get("currency", ""),
                    "time_slot": activity.get("time_slot", ""),
                    "area": activity.get("area", ""),
                    "supplier": "llm_fallback",
                    "reason": activity.get("reason", ""),
                })

            return result

        except Exception as e:
            print(f"AttractionLLMFallbackService error: {e}")
            return []


_llm_fallback_service = None


def get_attraction_llm_fallback_service() -> AttractionLLMFallbackService:
    global _llm_fallback_service
    if _llm_fallback_service is None:
        _llm_fallback_service = AttractionLLMFallbackService()
    return _llm_fallback_service
