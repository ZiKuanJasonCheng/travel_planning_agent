from states.trip_state import TripState
from services.llm_checker_service import evaluate_itinerary

_MAX_RETRIES = 2


def checker_agent(state: TripState) -> TripState:
    destination = state.get("destination", "")
    days = state.get("days") or 1
    num_people = state.get("num_people") or 1
    itinerary = state.get("itinerary", [])
    retry_count = state.get("checker_retry_count") or 0
    prior_critique = state.get("checker_critique")

    try:
        result = evaluate_itinerary(
            destination=destination,
            days=days,
            num_people=num_people,
            itinerary=itinerary,
            prior_critique=prior_critique,
        )
    except Exception as e:
        print(f"checker_agent(): LLM call failed ({e}), treating as pass")
        return {**state, "checker_retry_count": 0, "checker_critique": None}

    if result["passed"]:
        print("checker_agent(): itinerary passed quality check")
        return {**state, "checker_retry_count": 0, "checker_critique": None}

    print(f"checker_agent(): issues found: {result['issues']}")

    if retry_count < _MAX_RETRIES:
        dirty_agents = list(state.get("dirty_agents", []))
        dirty_agents.append("attraction_agent")
        return {
            **state,
            "checker_retry_count": retry_count + 1,
            "checker_critique": result["critique"],
            "dirty_agents": dirty_agents,
        }

    # Max retries exhausted — surface issues to the user via checker_critique
    flagged = "Checker flagged unresolved issues:\n" + "\n".join(
        f"- {issue}" for issue in result["issues"]
    )
    return {**state, "checker_retry_count": 0, "checker_critique": flagged}
