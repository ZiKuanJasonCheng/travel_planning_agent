from typing import TypedDict, List, Optional, Literal
from pydantic import BaseModel, Field

class BudgetConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class PreferenceConstraint(BaseModel):
    airlines: Optional[list[str]] = Field(None, description="Preferred airline(s)")
    flight_class: Optional[str] = Field(None, description="Preferred flight class")
    transport_type: Optional[str] = Field(None, description="Preferred transport type: 'flight', 'train', or 'both'")


class TransportConstraint(BaseModel):
    budget: Optional[BudgetConstraint] = None
    preference: Optional[PreferenceConstraint] = None

