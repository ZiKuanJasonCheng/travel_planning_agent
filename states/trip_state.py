from typing import TypedDict, List, Optional
from states.accommodation_constraints import AccommodationConstraint


class TripState(TypedDict, total=False):
    destination: str
    sub_destinations: List[str]
    preferences: List[str]

    transport_options: List[dict]
    accommodation_options: List[dict]
    itinerary: List[dict]

    feedback: Optional[str]
    constraints: AccommodationConstraint

    rerun_target: Optional[str]

    status: str