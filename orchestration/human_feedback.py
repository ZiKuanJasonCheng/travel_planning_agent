from typing import Optional
from states.accommodation_constraints import AccommodationConstraint
from states.trip_state import TripState
from orchestration.llm_feedback_parsing import parse_feedback_with_llm


def human_feedback_node(state: TripState):
    feedback = state.get("feedback")

    if not feedback:
        return state
    
    
    constraints = parse_feedback_with_llm(feedback)

    if constraints:
        state["constraints"] = constraints.model_dump(exclude_none=True)

    # Clean feedback
    state["feedback"] = None

    print(f"human_feedback_node(): state: {state}")

    return state
    