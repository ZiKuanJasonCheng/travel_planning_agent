from typing import Optional
from pydantic import BaseModel, Field


class PreferenceConstraint(BaseModel):
    area: Optional[str] = Field(None, description="Preferred area or location in a city/region")
    hotel_style: Optional[str] = Field(None, description="Hotel style or type")
    exclusions: Optional[list[str]] = Field(None, description="Hotel types, areas, or amenities to exclude e.g. hostel, shared bathroom, far from city center, etc.")
    max_price_per_night: Optional[int] = Field(None, description="Maximum acceptable price per night")


class AccommodationConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the accommodation search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )

