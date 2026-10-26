from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PreferenceConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: Optional[str] = Field(None, description="Preferred area or location in a city/region")
    hotel_style: Optional[str] = Field(None, description="Hotel style or type")
    exclusions: Optional[list[str]] = Field(None, description="Hotel types, areas, or amenities to exclude e.g. hostel, shared bathroom, far from city center, etc.")
    max_price_per_night: Optional[int] = Field(
        None,
        description=(
            "Maximum acceptable price per night, in USD. No need to leave anything when the user simply "
            "hasn't mentioned a price or if the user explicitly says price doesn't matter or "
            "there's no budget limit (e.g. 'I don't care about the price', 'any price is fine')."
        ),
    )


class AccommodationConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: Optional[PreferenceConstraint] = None
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the accommodation search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )

