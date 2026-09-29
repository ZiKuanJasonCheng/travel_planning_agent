import json
import os
from typing import Optional, TypedDict

from openai import OpenAI

from services.currency import CONVERT_CURRENCY_TOOL, execute_tool_call

_MAX_TOOL_ITERATIONS = 3


class CheckerResult(TypedDict):
    passed: bool
    issues: list[str]
    critique: str


_EVALUATE_TOOL = {
    "type": "function",
    "function": {
        "name": "evaluate_itinerary",
        "description": "Report quality issues found in the itinerary",
        "parameters": {
            "type": "object",
            "properties": {
                "passed": {
                    "type": "boolean",
                    "description": "True if no issues found",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific problems found in the itinerary",
                },
                "critique": {
                    "type": "string",
                    "description": "Actionable summary the planner must address on retry",
                },
            },
            "required": ["passed", "issues", "critique"],
        },
    },
}

_SYSTEM_PROMPT = """\
You are a strict travel itinerary quality reviewer. Check for exactly these types of issue:
1. Repeated venues — the same attraction appearing more than once across all days.
2. Unreasonable travel — consecutive activities on the same day requiring excessive \
travel given the character of the destination. Use your knowledge of the destination \
to judge what is reasonable (2-hour drives are normal in Iceland, not in central Kyoto).
3. Constraint violations — if traveler constraints are provided, verify:
   - Exclusions: no excluded venues, styles, or activity types appear.
   - Must-visit places: every listed must-visit place appears at least once.
   - Budget: no activity's estimated_cost significantly exceeds the max price per ticket. \
Activity costs are in their own local currency while the max price per ticket is in USD — \
call convert_currency to convert a cost to USD before comparing it, never compare the raw \
local-currency number against the USD limit.
   - Preferred styles: the overall mix of activities matches the stated travel styles.
Only flag a constraint violation if you are confident it is breached.
Be specific so the planner can act on each issue.
When you have finished reviewing, call evaluate_itinerary with your verdict."""


def _evaluate_with_tools(client: OpenAI, messages: list) -> dict:
    """Run a bounded tool loop until the model calls evaluate_itinerary.

    The model may call convert_currency one or more times before deciding; on the
    final allowed iteration the terminal tool is forced so the call can never
    end without a verdict.
    """
    tools = [CONVERT_CURRENCY_TOOL, _EVALUATE_TOOL]

    for iteration in range(_MAX_TOOL_ITERATIONS):
        last_chance = iteration == _MAX_TOOL_ITERATIONS - 1
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice=(
                {"type": "function", "function": {"name": "evaluate_itinerary"}}
                if last_chance else "auto"
            ),
            timeout=60,
        )
        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        for tc in tool_calls:
            if tc.function.name == "evaluate_itinerary":
                return json.loads(tc.function.arguments)

        if not tool_calls:
            messages.append({"role": "user", "content": "Call evaluate_itinerary with your verdict."})
            continue

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

    raise ValueError("evaluate_itinerary: model did not return a verdict tool call")


def _build_constraints_lines(constraints: dict) -> str:
    parts = []
    preference = constraints.get("preference") or {}
    if preference.get("max_price_per_ticket") is not None:
        parts.append(f"Max price per ticket/activity: {preference['max_price_per_ticket']} USD")
    if preference.get("styles"):
        parts.append(f"Preferred styles: {', '.join(preference['styles'])}")
    if preference.get("exclusions"):
        parts.append(f"Exclusions (must NOT appear): {', '.join(preference['exclusions'])}")
    if preference.get("must_go_places"):
        parts.append(f"Must-visit places (must ALL appear): {', '.join(preference['must_go_places'])}")
    return "\n".join(parts)


def evaluate_itinerary(
    destination: str,
    days: int,
    num_people: int,
    itinerary: list[dict],
    prior_critique: Optional[str] = None,
    constraints: Optional[dict] = None,
) -> CheckerResult:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    itinerary_text = json.dumps(itinerary, ensure_ascii=False, indent=2)
    user_content = (
        f"Destination: {destination}\nDays: {days}\nTravelers: {num_people}\n\n"
        f"Itinerary:\n{itinerary_text}"
    )
    if constraints:
        constraint_lines = _build_constraints_lines(constraints)
        if constraint_lines:
            user_content += f"\n\nTraveler constraints:\n{constraint_lines}"
    if prior_critique:
        user_content += f"\n\nPrevious critique to verify is now resolved:\n{prior_critique}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    args = _evaluate_with_tools(client, messages)
    return CheckerResult(
        passed=args["passed"],
        issues=args.get("issues", []),
        critique=args.get("critique", ""),
    )
