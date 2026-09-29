"""
Reminder/advisor LLM.

Reads the last itinerary-checker critique and the `reason` fields left by the
flight and accommodation agents, then writes a short, plain-language note for
the traveler: what might be imperfect, and what they can do about it. In
"closing" mode it skips all of that and just writes a warm wrap-up line.
"""
import json
import logging
import os
from typing import Optional

from openai import OpenAI

from services.langfuse_client import observe, update_current_generation

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_LEAVE_REMINDER_TOOL = {
    "type": "function",
    "function": {
        "name": "leave_reminder",
        "description": "Leave a short, plain-language note for the traveler about the final trip plan",
        "parameters": {
            "type": "object",
            "properties": {
                "reminder": {
                    "type": "string",
                    "description": (
                        "The note shown to the traveler. Empty string when nothing needs "
                        "their attention."
                    ),
                },
            },
            "required": ["reminder"],
        },
    },
}

_SYSTEM_PROMPT = """\
You are a travel assistant writing a short closing note that accompanies a finished \
trip plan. You write in plain, non-technical language — the traveler has not seen any \
of the internal review notes, error logs, or raw data.

You will be given up to three things:
1. A critique from the itinerary quality reviewer, if the latest refined itinerary still \
has a known imperfection. If present, tell the traveler plainly that the latest itinerary \
may still have an issue, say briefly what it is in everyday words, and note it's worth a \
closer look. Do not mention reviewers, agents, or that any automated check exists.
2. The "reason" field of each flight and accommodation item. Most are routine ("Duffel API \
result", "Mock flight data") and need no mention. Flag only the ones that need the \
traveler's attention — an API error, a "no suitable flights/hotel found" result, or a \
fallback/degraded result — and tell them what to do next. When the reason itself already \
contains instructions, such as submitting feedback saying "Run transport/flight service \
again", pass that suggestion along in your own words.
3. The itinerary and trip context for anything else worth a brief mention.

Keep it short and friendly — a few sentences. If nothing needs the traveler's attention, \
return an empty or near-empty message rather than inventing a concern. Never say the plan \
is perfect if the critique or a reason field says otherwise.

Call leave_reminder with your note."""

_CLOSING_PROMPT = """\
You are a travel assistant writing a short, warm closing line that accompanies a \
finished trip plan the traveler has just confirmed they are happy with.

They made no changes this round, so there is nothing to flag, review, or warn about. \
Just wrap up: their final trip plan is ready, here it is, and wish them a good trip. \
One or two friendly sentences in plain language — no mention of agents, reviewers, \
internal checks, itineraries being refined, or anything technical.

Call leave_reminder with your note."""


def _summarize_reasons(items: Optional[list]) -> str:
    """Collect the distinct non-empty `reason` values from a list of items."""
    reasons = []
    for item in items or []:
        reason = (item or {}).get("reason")
        if reason and reason not in reasons:
            reasons.append(reason)
    return "\n".join(f"- {r}" for r in reasons) if reasons else "(none)"


def _flight_items(transport_options: Optional[dict]) -> list:
    flight = (transport_options or {}).get("flight") or {}
    return list(flight.get("outbound") or []) + list(flight.get("inbound") or [])


@observe(as_type="generation", capture_input=False, capture_output=False)
def generate_reminder(
    checker_critique: Optional[str] = None,
    flight_items: Optional[list] = None,
    accommodation_options: Optional[list] = None,
    itinerary: Optional[list] = None,
    destination: Optional[str] = None,
    closing: bool = False,
) -> str:
    """Return a short traveler-facing note, or "" when nothing needs attention.

    Never raises: any failure degrades to an empty reminder so the final
    response is never broken by the advisory step.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    if closing:
        system_prompt = _CLOSING_PROMPT
        user_content = (
            f"Trip destination: {destination or 'unknown'}\n"
            f"Trip length: {len(itinerary or [])} day(s)\n\n"
            "The traveler confirmed they are happy with this plan."
        )
    else:
        system_prompt = _SYSTEM_PROMPT
        user_content = (
            f"Trip destination: {destination or 'unknown'}\n\n"
            f"Itinerary reviewer critique (may be empty):\n{checker_critique or '(none)'}\n\n"
            f"Flight item reasons:\n{_summarize_reasons(flight_items)}\n\n"
            f"Accommodation item reasons:\n{_summarize_reasons(accommodation_options)}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=[_LEAVE_REMINDER_TOOL],
            tool_choice={"type": "function", "function": {"name": "leave_reminder"}},
            timeout=60,
        )
        message = response.choices[0].message
        update_current_generation(
            model=response.model,
            input={"tools": [_LEAVE_REMINDER_TOOL], "messages": messages},
            output={
                "role": message.role,
                "content": message.content,
                "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
            },
            usage_details={
                "input": response.usage.prompt_tokens,
                "output": response.usage.completion_tokens,
            },
            model_parameters={"tool_choice": "leave_reminder"},
        )
        args = json.loads(message.tool_calls[0].function.arguments)
        return args.get("reminder", "")
    except Exception as e:
        logger.error(f"generate_reminder(): failed, returning empty reminder: {e}")
        return ""


__all__ = ["generate_reminder"]
