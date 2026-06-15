from typing import Any, Optional
from states.accommodation_constraints import AccommodationConstraint
from states.trip_state import TripState
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from orchestration.merge_constraints import merge_constraints
from orchestration.dependency import CONSTRAINT_AGENT_MAP, propagate_agent_dependencies, propagate_dirty_agents, topo_sort_agents


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
    #print(f"existing_constraints: {existing_constraints}")
    dict_new_constraints = new_constraints.model_dump(exclude_none=True)
    #print(f"dict_new_constraints: {dict_new_constraints}")
    merged_constraints = merge_constraints(existing_constraints, dict_new_constraints)

    print(f"merged_constraints: {merged_constraints}")

    # If merged constraints are the same as existing constraints, we don't need to update the constraints
    if merged_constraints == existing_constraints:
        print(f"apply_user_feedback(): merged_constraints == existing_constraints, state: {state}")
        return False
    # Else we update the constraints
    state["constraints"] = merged_constraints


    # Check if any constraint key changed
    dirty_agents = {  #changed_keys
        CONSTRAINT_AGENT_MAP.get(key) for key in dict_new_constraints.keys()
        if existing_constraints.get(key) != dict_new_constraints.get(key)
    }
    print(f"dirty_agents: {dirty_agents}")

    # Determine dirty agents based on dependency map
    # dirty_agents = set()
    # for key in changed_keys:
    #     agent = CONSTRAINT_AGENT_MAP.get(key, [])
    #     dirty_agents.update(agent)
    #print(f"Before propagating to downstream agents, dirty_agents: {dirty_agents}")

    dirty_agents = propagate_dirty_agents(dirty_agents)  #propagate_agent_dependencies(dirty_agents)
    dirty_agents = topo_sort_agents(dirty_agents)  # Now dirty_agents is a list in topological order
    print(f"After topological sort, dirty_agents: {dirty_agents}")

    state["dirty_agents"] = dirty_agents
    

    print(f"apply_user_feedback(): state: {state}")

    return True



def human_feedback_checkpoint(state: TripState) -> TripState:
    """
    Change status to be 'is_waiting_for_feedback'
    """
    return {**state, "status": "is_waiting_for_feedback"}
    