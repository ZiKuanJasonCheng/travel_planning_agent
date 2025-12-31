from states.trip_state import TripState


def determine_next_step(state: TripState):
    # Get a feedback from a user
    #feedback = state.get("feedback")
    print(f"determine_next_step(): state: {state}")

    constraints = state.get("constraints", {})
    print(f"determine_next_step(): constraints: {constraints}")

    if not constraints:  #feedback:
        return "end"

    #feedback = feedback.lower()

    # Dummy example
    # if "expensive" in feedback:
    #     state["rerun_target"] = "accommodation"
    #     return "redo_accommodation"
    if "budget" in constraints or "preference" in constraints:
        state["rerun_target"] = "accommodation"
        return "redo_accommodation"

    return "end"