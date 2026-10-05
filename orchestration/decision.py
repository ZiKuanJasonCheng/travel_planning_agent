import logging
from states.trip_state import TripState
from orchestration.tracability import log_trace
from copy import deepcopy

logger = logging.getLogger(__name__)


def determine_next_step(state: TripState):
    # Get a feedback from a user
    dirty_agents = state.get("dirty_agents", [])
    logger.info(f"dirty_agents: {dirty_agents}")

    if not dirty_agents:
        return "final_output"

    next_agent = dirty_agents.pop(0)
    #state["dirty_agents"] = dirty_agents
    logger.info(f"next_agent: {next_agent}")
    
    return next_agent


def buffer_step(state: TripState):
    return state