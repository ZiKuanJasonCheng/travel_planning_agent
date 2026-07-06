from typing import Optional
from pydantic import BaseModel, Field


class FlightPreferenceConstraint(BaseModel):
    airlines: Optional[list[str]] = Field(None, description="Preferred airline(s)")
    flight_class: Optional[str] = Field(None, description="Preferred flight class")
    excluded_airlines: Optional[list[str]] = Field(None, description="Airline(s) to be excluded")
    accept_redeye_flights: Optional[bool] = Field(None, description="Whether to accept red-eye flights (departure 23:30–05:29). Null means not explicitly stated; the agent infers False when preferred_departure_timeslots is set.")
    direct_flights_only: Optional[bool] = Field(False, description="Whether to only accept direct flights")
    preferred_departure_timeslots: Optional[list[str]] = Field(
        None,
        description=(
            "Preferred departure time ranges in HH:MM~HH:MM format. "
            "Named periods: early morning=05:30~08:59, morning=09:00~11:59, "
            "noon=12:00~12:59, afternoon=13:00~16:59, evening=17:00~19:59, night=20:00~23:29. "
            "When the user says they DON'T want a period, fill in ALL OTHER slots as the complement "
            "(excluding red-eye 23:30~05:29 unless accept_redeye_flights is True). "
            "Example: 'no morning flights' → ['05:30~08:59','12:00~12:59','13:00~16:59','17:00~19:59','20:00~23:29']. "
            "When the user says they DO want a period, list only those slots."
        ),
    )
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class RailwayTicketPreferenceConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class TransportConstraint(BaseModel):
    outbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None, description="Flight preferences for the outbound (departure) leg only."
    )
    inbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None, description="Flight preferences for the inbound (return) leg only."
    )
    railway_ticket_preference: Optional[RailwayTicketPreferenceConstraint] = Field(
        None, description="Preferences for train/railway tickets, if applicable."
    )
    transport_type: Optional[str] = Field(
        None, description="Preferred transport type: 'flight', 'train', or 'both'"
    )
    rerun_planning: Optional[bool] = Field(
        None,
        description=(
            "True if the user wants the transport/flight search rerun from scratch, "
            "regardless of whether they provided any new preferences — e.g. after "
            "receiving an API error last time, or simply wanting to try again."
        ),
    )

