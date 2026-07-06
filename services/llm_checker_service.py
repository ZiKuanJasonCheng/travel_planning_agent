import json
import os
from typing import Optional, TypedDict

from openai import OpenAI


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
   - Budget: no activity's estimated_cost significantly exceeds the max price per ticket \
(use your knowledge to convert local currency to USD for comparison).
   - Preferred styles: the overall mix of activities matches the stated travel styles.
Only flag a constraint violation if you are confident it is breached.
Be specific so the planner can act on each issue."""


def _build_constraints_lines(constraints: dict) -> str:
    parts = []
    budget = constraints.get("budget") or {}
    preference = constraints.get("preference") or {}
    if budget.get("max_price_per_ticket") is not None:
        parts.append(f"Max price per ticket/activity: {budget['max_price_per_ticket']} USD")
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

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        tools=[_EVALUATE_TOOL],
        tool_choice={"type": "function", "function": {"name": "evaluate_itinerary"}},
        timeout=60,
    )
    args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    return CheckerResult(
        passed=args["passed"],
        issues=args.get("issues", []),
        critique=args.get("critique", ""),
    )
