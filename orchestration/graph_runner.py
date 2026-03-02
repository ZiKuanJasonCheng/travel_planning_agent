from states.trip_state import TripState
from orchestration.graph import trip_graph


def run_until_needing_feedback_or_finished(state: TripState) -> TripState:
    """
    Run the graph until needing user to submit feedback or planning is finished
    """
    while True:
        state = trip_graph.invoke(state)

        if state["status"] == "is_waiting_for_feedback" or state["status"] == "completed":
            return state
