from states.trip_state import TripState
from orchestration.tracability import log_trace
from copy import deepcopy


def determine_next_step(state: TripState):
    # Get a feedback from a user
    #print(f"determine_next_step(): state: {state}")
    dirty_agents = state.get("dirty_agents", [])
    print(f"dirty_agents: {dirty_agents}")

    if not dirty_agents:
        return "human_feedback"

    next_agent = dirty_agents.pop(0)
    #state["dirty_agents"] = dirty_agents
    print(f"next_agent: {next_agent}")
    
    return next_agent


def buffer_step(state: TripState):
    return state