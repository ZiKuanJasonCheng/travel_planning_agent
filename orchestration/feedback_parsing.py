from typing import Optional
from states.accommodation_constraints import AccommodationConstraint
from states.trip_state import TripState

# Used by an obsolete function
def parse_feedback(feedback: str) -> AccommodationConstraint:
    """
        Parse feedback into a constraint object
    """
    feedback = feedback.lower()
    constraint: AccommodationConstraint = {}

    if "expensive" in feedback:
        constraint["budget"] = {"max_price_per_night": 70}

    if "city center" in feedback or "downtown" in feedback:
        constraint.setdefault("preference", {})
        constraint["preference"]["area"] = "city center"

    print(f"parse_feedback(): constraint: {constraint}")

    return constraint

# Obsolete
def parse_feedback_node(state: TripState):
    feedback = state.get("feedback")

    if feedback:
        state["constraints"] = parse_feedback(feedback)

        # Clean feedback
        state["feedback"] = None

    print(f"parse_feedback_node(): state: {state}")

    return state
    