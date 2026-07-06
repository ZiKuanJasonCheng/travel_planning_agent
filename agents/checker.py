from copy import deepcopy

from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.llm_checker_service import evaluate_itinerary

_MAX_RETRIES = 2


def checker_agent(state: TripState) -> TripState:
    destination = state.get("destination", "")
    days = state.get("days") or 1
    num_people = state.get("num_people") or 1
    itinerary = state.get("itinerary", [])
    retry_count = state.get("checker_retry_count") or 0
    prior_critique = state.get("checker_critique")
    attraction_constraints = state.get("constraints", {}).get("attraction") or {}

    if state.get("log_trace"):
        log_trace(
            state,
            node="checker_agent",
            action="execute",
            reason="Evaluating itinerary quality",
            inputs={"retry_count": retry_count, "has_prior_critique": prior_critique is not None},
        )

    try:
        result = evaluate_itinerary(
            destination=destination,
            days=days,
            num_people=num_people,
            itinerary=itinerary,
            prior_critique=prior_critique,
            constraints=attraction_constraints,
        )
    except Exception as e:
        print(f"checker_agent(): LLM call failed ({e}), treating as pass")
        next_state = {**state, "checker_retry_count": 0, "checker_critique": None}
        if state.get("log_trace"):
            log_trace(
                next_state,
                node="checker_agent",
                action="complete",
                reason=f"LLM call failed ({e}), treated as pass",
                outputs={"passed": True},
            )
        return next_state

    if result["passed"]:
        print("checker_agent(): itinerary passed quality check")
        next_state = {**state, "checker_retry_count": 0, "checker_critique": None}
        if state.get("log_trace"):
            log_trace(
                next_state,
                node="checker_agent",
                action="complete",
                reason="Itinerary passed quality check",
                outputs={"passed": True},
            )
        return next_state

    print(f"checker_agent(): issues found: {result['issues']}")

    if retry_count < _MAX_RETRIES:
        dirty_agents = list(state.get("dirty_agents", []))
        dirty_agents.append("attraction_agent")
        next_state = {
            **state,
            "checker_retry_count": retry_count + 1,
            "checker_critique": result["critique"],
            "dirty_agents": dirty_agents,
        }
        if state.get("log_trace"):
            log_trace(
                next_state,
                node="checker_agent",
                action="retry",
                reason=f"Issues found, queuing retry {retry_count + 1}/{_MAX_RETRIES}",
                outputs={"issues": deepcopy(result["issues"]), "critique": result["critique"]},
            )
        return next_state

    # Max retries exhausted — surface issues to the user via checker_critique
    flagged = "Checker flagged unresolved issues:\n" + "\n".join(
        f"- {issue}" for issue in result["issues"]
    )
    next_state = {**state, "checker_retry_count": 0, "checker_critique": flagged}
    if state.get("log_trace"):
        log_trace(
            next_state,
            node="checker_agent",
            action="complete",
            reason="Max retries exhausted, flagging issues for user",
            outputs={"issues": deepcopy(result["issues"])},
        )
    return next_state
