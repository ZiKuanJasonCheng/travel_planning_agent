from typing import TypedDict, List, Optional, Literal
from pydantic import BaseModel, Field
from states.accommodation_constraints import AccommodationConstraint
from states.transport_constraints import TransportConstraint
from states.attraction_constraints import AttractionConstraint


class Constraints(BaseModel):
    accommodation: Optional[AccommodationConstraint] = None
    transport: Optional[TransportConstraint] = None
    attraction: Optional[AttractionConstraint] = None