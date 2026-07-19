from typing import Optional
from pydantic import BaseModel, Field

from orchestration.merge_constraints import UNLIMITED_PRICE


class PreferenceConstraint(BaseModel):
    styles: Optional[list[str]] = Field(None, description="Preferred tourism styles e.g. natural scenery, historical sites, shopping, culture experiences, local food, etc.")
    exclusions: Optional[list[str]] = Field(None, description="Places, styles, or things to exclude e.g. Instagrammable spot, spicy food, etc.")
    must_go_places: Optional[list[str]] = Field(None, description="Some must-go places if specified")
    max_price_per_ticket: Optional[int] = Field(
        None,
        description=(
            "Maximum acceptable price for an entrance ticket, in USD. Leave null when the user "
            "simply hasn't mentioned a price. If the user explicitly says price doesn't matter or "
            "there's no budget limit (e.g. 'I don't care about the price', 'any price is fine'), "
            f"set this to {UNLIMITED_PRICE} instead of null."
        ),
    )


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

