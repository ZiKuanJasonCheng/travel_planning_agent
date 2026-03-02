from typing import TypedDict, List, Optional, Literal


class BudgetConstraint(TypedDict, total=False):
    max_price_per_night: int


class PreferenceConstraint(TypedDict, total=False):
    area: Optional[str]
    hotel_style: Optional[str]



class AccommodationConstraint(TypedDict, total=False):
    budget: Optional[BudgetConstraint]
    preference: Optional[PreferenceConstraint]

