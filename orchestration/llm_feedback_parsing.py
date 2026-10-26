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
from orchestration.strict_schema import to_strict_schema

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
        # strict: the schema is compiled into the decoding grammar, so a key
        # outside the schema (e.g. a price hoisted above `preference`) can't be
        # emitted at all. Requires every object closed and fully required —
        # see orchestration/strict_schema.py.
        "strict": True,
        "parameters": to_strict_schema(Constraints.model_json_schema()),
    },
}

# One initial call plus at most two re-asks when the returned arguments don't
# match the schema.
_MAX_PARSE_ATTEMPTS = 3

_REPAIR_INSTRUCTION = (
    "Your previous extract_constraints call was rejected: {error}\n"
    "Re-emit the call so it matches the schema exactly. Every field must sit at "
    "the nesting level the schema defines (for example a price limit goes under "
    "the category's `preference` object, never directly under the category), and "
    "no field outside the schema may be included."
)


def _repair_message(error: Exception) -> str:
    return _REPAIR_INSTRUCTION.format(error=error)


@observe(as_type="generation", capture_input=False, capture_output=False)
def parse_feedback_with_llm(feedback: str) -> Optional[Constraints]:
    """Parse feedback with LLM into a constraint object. All price values are normalized to USD.

    A response whose arguments don't validate (e.g. a price hoisted one level
    above `preference`) is re-asked with the validation error fed back, up to
    _MAX_PARSE_ATTEMPTS calls in total.
    """
    if not feedback:
        return None

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": feedback},
    ]
    prompt_tokens = 0
    completion_tokens = 0
    attempts = 0

    def record_generation(response):
        # `model` is on the response, not on the message. The messages list is
        # captured by reference, so this shows the repair turns on a re-ask.
        message = response.choices[0].message
        update_current_generation(
            model=response.model,
            input={"tools": [_EXTRACT_TOOL], "messages": messages},
            output={
                "role": message.role,
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
            },
            usage_details={"input": prompt_tokens, "output": completion_tokens},
            model_parameters={
                "temperature": 0,
                "tool_choice": "extract_constraints",
                "attempts": attempts,
            },
        )

    last_error: Optional[Exception] = None
    last_response = None

    for attempt in range(1, _MAX_PARSE_ATTEMPTS + 1):
        attempts = attempt
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
        last_response = response
        # Usage is summed across attempts: every call was billed, and the
        # observation should show the real cost of a re-ask.
        prompt_tokens += response.usage.prompt_tokens
        completion_tokens += response.usage.completion_tokens

        try:
            tool_calls = message.tool_calls or []
            args = tool_calls[0].function.arguments
            logger.info(f"parse_feedback_with_llm(): attempt {attempt} args: {args}")
            parsed = Constraints.model_validate_json(args)
        except Exception as e:
            last_error = e
            logger.warning(
                f"parse_feedback_with_llm: attempt {attempt}/{_MAX_PARSE_ATTEMPTS} "
                f"produced a response that doesn't match the schema: {e}"
            )
            if attempt == _MAX_PARSE_ATTEMPTS:
                break
            # Feed the bad call and the rejection back, so the next attempt can
            # correct the shape instead of replaying the same prompt.
            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_calls[0].id,
                    "content": _repair_message(e),
                })
            else:
                messages.append({"role": "user", "content": _repair_message(e)})
            continue

        record_generation(response)
        return parsed

    record_generation(last_response)
    logger.error(
        f"parse_feedback_with_llm: giving up after {attempts} attempts: {last_error}"
    )
    raise last_error