from typing import TypedDict, List, Optional, Literal
from states.accommodation_constraints import AccommodationConstraint
from orchestration.tracability import DecisionTrace


class TripState(TypedDict, total=False):
    destination: str
    days: Optional[int]
    sub_destinations: List[str]
    preferences: List[str]

    transport_options: List[dict]
    accommodation_options: List[dict]
    itinerary: List[dict]

    feedback: Optional[str]
    constraints: AccommodationConstraint

    #rerun_target: Optional[str]
    traces: List[DecisionTrace]

    dirty_agents: list[str]

    status: Literal["planning", "is_waiting_for_feedback", "completed"]