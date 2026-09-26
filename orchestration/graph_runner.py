from states.trip_state import TripState
from orchestration.graph import trip_graph
from services.langfuse_client import observe, propagate_attributes, get_callback_handler


def _run_graph_loop(state: TripState, config: dict | None) -> TripState:
    while True:
        state = trip_graph.invoke(state, config=config)

        if state["status"] == "is_waiting_for_feedback" or state["status"] == "completed":
            return state


@observe()
def run_until_needing_feedback_or_finished(state: TripState) -> TripState:
    """
    Run the graph until needing user to submit feedback or planning is finished
    """
    session_id = state.get("session_id")
    handler = get_callback_handler()
    config = {"callbacks": [handler]} if handler else None

    if session_id:
        with propagate_attributes(
            session_id=session_id,
            metadata={
                "destination": state.get("destination"),
                "origin": state.get("origin"),
                "num_people": state.get("num_people"),
                "log_file": state.get("log_file"),
            },
        ):
            return _run_graph_loop(state, config)

    return _run_graph_loop(state, config)
