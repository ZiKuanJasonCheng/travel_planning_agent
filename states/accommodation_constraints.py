from typing import TypedDict, List, Optional, Literal
from pydantic import BaseModel, Field

class BudgetConstraint(BaseModel):
    max_price_per_night: Optional[int] = Field(None, description="Maximum acceptable price per night")


class PreferenceConstraint(BaseModel):
    area: Optional[str] = Field(None, description="Preferred area or location in a city/region")
    hotel_style: Optional[str] = Field(None, description="Hotel style or type")



class AccommodationConstraint(BaseModel):
    budget: Optional[BudgetConstraint] = None
    preference: Optional[PreferenceConstraint] = None

