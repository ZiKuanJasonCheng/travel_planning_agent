from typing import TypedDict, List, Optional, Literal
from states.constraints import Constraints
from orchestration.tracability import DecisionTrace


def default_transport_options() -> dict:
    return {"railway": [], "flight": {"outbound": [], "inbound": []}}


class TripState(TypedDict, total=False):
    session_id: Optional[str]
    destination: str
    origin: str               # departure city / location
    num_people: int           # total number of travelers
    days: Optional[int]
    start_date: Optional[str]  # YYYY-MM-DD
    end_date: Optional[str]    # YYYY-MM-DD
    sub_destinations: List[str]
    preferences: List[str]

    transport_options: dict   # {"railway": list[dict], "flight": {"outbound": list[dict], "inbound": list[dict]}}
    accommodation_options: List[dict]
    itinerary: List[dict]

    feedback: Optional[str]
    constraints: Constraints          # Last round's AGREED (merged) constraints — not touched by apply_user_feedback
    new_constraints: Optional[dict]   # This round's raw parsed feedback, pre-merge

    #rerun_target: Optional[str]
    log_trace: bool
    traces: List[DecisionTrace]

    dirty_agents: list[str]

    status: Literal["planning", "is_waiting_for_feedback", "completed"]

    checker_retry_count: int
    checker_critique: Optional[str]