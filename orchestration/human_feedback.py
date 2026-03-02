from typing import Any, Optional
from states.accommodation_constraints import AccommodationConstraint
from states.trip_state import TripState
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from orchestration.merge_constraints import merge_constraints
from orchestration.dependency import CONSTRAINT_AGENT_MAP


def apply_user_feedback(state: TripState, feedback) -> bool:
    """
    Returns True if constraints changed, False otherwise
    """
    
    new_constraints = parse_feedback_with_llm(feedback)
    if not new_constraints:
        return False
    
    # Clean feedback
    state["feedback"] = None

    existing_constraints = state.get("constraints", {})
    dict_new_constraints = new_constraints.model_dump(exclude_none=True)
    merged_constraints = merge_constraints(existing_constraints, dict_new_constraints)

    # If merged constraints are the same as existing constraints, we don't need to update the constraints
    if merged_constraints == existing_constraints:
        print(f"apply_user_feedback(): merged_constraints == existing_constraints, state: {state}")
        return False
    # Else we update the constraints
    state["constraints"] = merged_constraints


    # Check if any constraint key changed
    changed_keys = {
        key for key in dict_new_constraints.keys()
        if existing_constraints.get(key) != dict_new_constraints.get(key)
    }

    # Determine dirty agents based on dependency map
    dirty_agents = set()
    for key in changed_keys:
        agents = CONSTRAINT_AGENT_MAP.get(key, [])
        dirty_agents.update(agents)

    state["dirty_agents"] = list(dirty_agents)
    

    print(f"apply_user_feedback(): state: {state}")

    return True



def human_feedback_checkpoint(state: TripState) -> TripState:
    """
    Change status to be 'is_waiting_for_feedback'
    """
    state["status"] = "is_waiting_for_feedback"
    return state
    