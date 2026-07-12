import json
from typing import Optional
from openai import OpenAI
from states.accommodation_constraints import AccommodationConstraint
from states.constraints import Constraints
from states.trip_state import TripState
import os

from services.currency import get_rates

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_SYSTEM_PROMPT_TEMPLATE = """
You are an experienced travel assistant that extracts structured constraints from user feedback.
You should only extract constraints that are explicitly implied.
If nothing applies, return an empty object.
All budget and price values in the output must be in USD.
If the user mentions a price in another currency, convert it to USD using the rates below (units of foreign currency per 1 USD):
{rates_json}
"""


def _build_system_prompt() -> str:
    rates = get_rates()
    return _SYSTEM_PROMPT_TEMPLATE.format(rates_json=json.dumps(rates, indent=2))


def parse_feedback_with_llm(feedback: str) -> Optional[Constraints]:
    """Parse feedback with LLM into a constraint object. All price values are normalized to USD."""
    if not feedback:
        return None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": feedback}
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "extract_constraints",
                    "parameters": Constraints.model_json_schema()
                }
            }],
            tool_choice={"type": "function", "function": {"name": "extract_constraints"}},
            timeout=60,
            temperature=0
        )

        # print(f"response: {response}")
        # print(f"response.choices: {response.choices}")
        # print(f"response.choices[0]: {response.choices[0]}")
        # print(f"response.choices[0].message: {response.choices[0].message}")
        # print(f"response.choices[0].message.tool_calls: {response.choices[0].message.tool_calls}")
        tool_call = response.choices[0].message.tool_calls[0]
        #print(f"tool_call: {tool_call}")
        #print(f"tool_call.function: {tool_call.function}")
        args = tool_call.function.arguments
        print(f"args: {args}")

        return Constraints.model_validate_json(args)
    except Exception as e:
        print(f"parse_feedback_with_llm: error parsing feedback by LLM: {e}")
        return