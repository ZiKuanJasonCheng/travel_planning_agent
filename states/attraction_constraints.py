from typing import Optional
from pydantic import BaseModel, Field


class PreferenceConstraint(BaseModel):
    styles: Optional[list[str]] = Field(None, description="Preferred tourism styles e.g. natural scenery, historical sites, shopping, culture experiences, local food, etc.")
    exclusions: Optional[list[str]] = Field(None, description="Places, styles, or things to exclude e.g. Instagrammable spot, spicy food, etc.")
    must_go_places: Optional[list[str]] = Field(None, description="Some must-go places if specified")
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for an entrance ticket")


class AttractionConstraint(BaseModel):
    preference: Optional[PreferenceConstraint] = None
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the attraction search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )

