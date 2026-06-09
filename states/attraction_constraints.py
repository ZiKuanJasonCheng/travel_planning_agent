from typing import TypedDict, List, Optional, Literal
from pydantic import BaseModel, Field

class BudgetConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for an entrance ticket")


class PreferenceConstraint(BaseModel):
    styles: Optional[list[str]] = Field(None, description="Preferred tourism styles e.g. natural scenery, historical sites, shopping, culture experiences, local food, etc.")
    must_go_places: Optional[list[str]] = Field(None, description="Some must-go places if specified")


class AttractionConstraint(BaseModel):
    budget: Optional[BudgetConstraint] = None
    preference: Optional[PreferenceConstraint] = None

