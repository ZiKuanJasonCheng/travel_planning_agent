import logging
from typing import Any, Optional
from states.trip_state import TripState
from states.trip_dates import derive_start_date, resolve_dates
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from orchestration.dependency import CONSTRAINT_AGENT_MAP, propagate_dirty_agents, topo_sort_agents

logger = logging.getLogger(__name__)

# Fields that describe the whole trip rather than one agent's scope. Every
# agent reads at least one of them (dates drive flights, hotels and the day
# plan; num_people prices and paces all three), so a change to any of them
# invalidates all three.
TRIP_FIELDS = ("start_date", "end_date", "days", "num_people")

ALL_AGENTS = {"transport_agent", "accommodation_agent", "attraction_agent"}


def _apply_trip_field_overrides(state: TripState, overrides: Optional[dict]) -> bool:
    """Apply optional date / party-size revisions to state.

    Returns True when at least one field actually changed. A value identical
    to what state already holds is not a change, so resubmitting the same
    overrides stays idempotent. Raises ValueError when the resulting dates are
    inconsistent (end_date not after start_date).
    """
    if not overrides:
        return False

    supplied = {k: v for k, v in overrides.items() if k in TRIP_FIELDS}
    if not any(v is not None for v in supplied.values()):
        return False

    # A partial override still has to be resolved against the rest of the
    # session's window. Precedence, most authoritative first:
    #   - start_date + end_date both supplied: they win outright and days
    #     follows from the gap, so a supplied days value is discarded.
    #   - end_date + days both supplied with no start_date: the pair pins the
    #     window, so start_date is derived by subtracting days from end_date.
    #   - start_date alone: validated against the existing end_date, which
    #     stays put — a later start shortens the trip rather than moving it.
    #   - days or end_date alone: measured from the existing start_date.
    anchor = supplied.get("start_date")
    if anchor is None:
        anchor = state.get("start_date")

    end_date = supplied.get("end_date")
    days = supplied.get("days")
    if end_date is None and days is None:
        end_date = state.get("end_date")
        if end_date is None:
            days = state.get("days")

    start_date, end_date, days = resolve_dates(anchor, end_date, days)

    # A session is always created with dates and a duration resolved by
    # RequestModel, so anything still missing here means the stored state is
    # incomplete — a technical fault rather than a bad user request. Refuse
    # rather than persisting a half-specified trip window.
    missing = [
        name for name, value in
        (("start_date", start_date), ("end_date", end_date), ("days", days))
        if value is None
    ]
    if missing:
        raise RuntimeError(
            "session state is missing trip dates/duration "
            f"({', '.join(missing)}); cannot apply a date revision"
        )

    # The pair pins the window, so start_date is derived from it rather than
    # resolved against the stored value. When the pair only restates what the
    # session holds, the derivation lands on the stored start_date anyway.
    if supplied.get("start_date") is None and supplied.get("end_date") is not None and supplied.get("days") is not None:
        start_date = derive_start_date(end_date, days)

    changed = False
    for field, value in (("start_date", start_date), ("end_date", end_date), ("days", days)):
        if supplied.get(field) is not None and state.get(field) != value:
            changed = True
        state[field] = value

    num_people = supplied.get("num_people")
    if num_people is not None:
        if state.get("num_people") != num_people:
            changed = True
        state["num_people"] = num_people

    return changed


def apply_user_feedback(state: TripState, feedback, overrides: Optional[dict] = None) -> bool:
    """
    Returns True if constraints changed, False otherwise
    """
    # Trip-wide revisions are independent of the LLM-extracted constraints, so
    # they apply even when the feedback text parses to nothing.
    trip_changed = _apply_trip_field_overrides(state, overrides)

    new_constraints = None
    try:
        new_constraints = parse_feedback_with_llm(feedback)
    except Exception as e:
        logger.error(f"apply_user_feedback(): an error occurred while parsing feedback by LLM. Error message: {e}")
        raise

    if not new_constraints:
        if not trip_changed:
            return False
        dict_new_constraints = {}
    else:
        dict_new_constraints = new_constraints.model_dump(exclude_none=True)

    state["feedback"] = feedback

    existing_constraints = state.get("constraints", {})
    # Constraints are no longer merged/persisted here — each agent merges
    # new_constraints into constraints for its own scope and decides for
    # itself whether real replanning is needed. state["constraints"] stays
    # untouched, holding the last round's agreed values.

    # Check if any constraint key changed
    dirty_agents = {  #changed_keys
        CONSTRAINT_AGENT_MAP.get(key) for key in dict_new_constraints.keys()
        if existing_constraints.get(key) != dict_new_constraints.get(key)
    }

    if trip_changed:
        dirty_agents |= ALL_AGENTS
        # TODO: Check its correctness and write tests
        for constraint_type in CONSTRAINT_AGENT_MAP:
            dict_new_constraints.setdefault(constraint_type, {})
            dict_new_constraints[constraint_type]["rerun_planning"] = True

    state["new_constraints"] = dict_new_constraints


    # Determine dirty agents based on dependency map
    # dirty_agents = set()
    # for key in changed_keys:
    #     agent = CONSTRAINT_AGENT_MAP.get(key, [])
    #     dirty_agents.update(agent)

    dirty_agents = propagate_dirty_agents(dirty_agents)  #propagate_agent_dependencies(dirty_agents)
    dirty_agents = topo_sort_agents(dirty_agents)  # Now dirty_agents is a list in topological order
    logger.info(f"After topological sort, dirty_agents: {dirty_agents}")

    state["dirty_agents"] = dirty_agents


    logger.info(f"apply_user_feedback(): state: {state}", extra={"to_terminal": False})

    return True



def human_feedback_checkpoint(state: TripState) -> TripState:
    """
    Change status to be 'is_waiting_for_feedback'
    """
    return {**state, "status": "is_waiting_for_feedback"}
    