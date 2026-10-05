import json
import logging
from typing import Optional
from states.accommodation_constraints import AccommodationConstraint
from states.constraints import Constraints
from states.trip_state import TripState

from services.currency import get_rates
from services.langfuse_client import observe, update_current_generation
from services.llm_retry import call_with_retry
from services.openai_client import get_openai_client

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

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


_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_constraints",
        "parameters": Constraints.model_json_schema(),
    },
}


@observe(as_type="generation", capture_input=False, capture_output=False)
def parse_feedback_with_llm(feedback: str) -> Optional[Constraints]:
    """Parse feedback with LLM into a constraint object. All price values are normalized to USD."""
    if not feedback:
        return None

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": feedback},
    ]

    try:
        response = call_with_retry(
            get_openai_client().chat.completions.create,
            model=_MODEL,
            messages=messages,
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "function", "function": {"name": "extract_constraints"}},
            timeout=60,
            temperature=0
        )
    except Exception as e:
        logger.error(f"parse_feedback_with_llm: error parsing feedback by LLM because of LLM service error: {e}")
        raise

    message = response.choices[0].message
    update_current_generation(
        model=response.model,
        input={"tools": [_EXTRACT_TOOL], "messages": messages},
        output={
            "role": message.role,
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
        },
        usage_details={
            "input": response.usage.prompt_tokens,
            "output": response.usage.completion_tokens,
        },
        model_parameters={
            "temperature": 0,
            "tool_choice": "extract_constraints",
        },
    )

    try:
        tool_call = message.tool_calls[0]
        args = tool_call.function.arguments
        logger.info(f"parse_feedback_with_llm(): args: {args}")

        return Constraints.model_validate_json(args)
    except Exception as e:
        logger.error(f"parse_feedback_with_llm: error parsing generated arguments by LLM: {e}")
        raise