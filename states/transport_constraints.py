from typing import Optional
from pydantic import BaseModel, Field

from orchestration.merge_constraints import UNLIMITED_PRICE


class FlightPreferenceConstraint(BaseModel):
    airlines: Optional[list[str]] = Field(None, description="Preferred airline(s)")
    flight_class: Optional[str] = Field(None, description="Preferred flight class")
    excluded_airlines: Optional[list[str]] = Field(None, description="Airline(s) to be excluded")
    accept_redeye_flights: Optional[bool] = Field(None, description="Whether to accept red-eye flights (departure 23:30–05:29). Null means not explicitly stated; the agent infers False when preferred_departure_timeslots is set.")
    direct_flights_only: Optional[bool] = Field(None, description="Whether to only accept direct flights. Null means not explicitly stated.")
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
    max_price_per_ticket: Optional[int] = Field(
        None,
        description=(
            "Maximum acceptable price for a ticket, in USD. Leave null when the user simply "
            "hasn't mentioned a price. If the user explicitly says price doesn't matter or "
            "there's no budget limit (e.g. 'I don't care about the price', 'any price is fine'), "
            f"set this to {UNLIMITED_PRICE} instead of null."
        ),
    )


class RailwayTicketPreferenceConstraint(BaseModel):
    max_price_per_ticket: Optional[int] = Field(None, description="Maximum acceptable price for a ticket")


class TransportConstraint(BaseModel):
    outbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None,
        description=(
            "Flight preferences for the outbound (departure) leg. Decide direction PER FIELD, "
            "not per statement — a single message can mix general and directional preferences: "
            "for each field of FlightPreferenceConstraint (airlines, flight_class, "
            "excluded_airlines, accept_redeye_flights, direct_flights_only, "
            "preferred_departure_timeslots, max_price_per_ticket), if the user's wording for "
            "THAT field does not mention a direction ('way there'/'departure' vs 'way back'/"
            "'return'/'coming back'), copy the SAME value into that field in BOTH "
            "outbound_air_ticket_preference and inbound_air_ticket_preference. Only fields the "
            "user explicitly tied to one direction should differ between the two. "
            "Example: 'I prefer Cathay Pacific business class, no JetStar, no red-eye flights. "
            "Direct flights only on the way there, but stops are fine on the way back.' → "
            "outbound_air_ticket_preference={airlines:['Cathay Pacific'], flight_class:'business', "
            "excluded_airlines:['JetStar'], accept_redeye_flights:false, direct_flights_only:true}, "
            "inbound_air_ticket_preference={airlines:['Cathay Pacific'], flight_class:'business', "
            "excluded_airlines:['JetStar'], accept_redeye_flights:false, direct_flights_only:false}. "
            "Note airlines/flight_class/excluded_airlines/accept_redeye_flights (not tied to a "
            "direction) are copied to BOTH sides even though direct_flights_only (explicitly "
            "directional) differs — do not null out the other fields on inbound just because "
            "one field in the same message was directional."
        ),
    )
    inbound_air_ticket_preference: Optional[FlightPreferenceConstraint] = Field(
        None,
        description=(
            "Flight preferences for the inbound (return) leg. Decide direction PER FIELD, "
            "not per statement — a single message can mix general and directional preferences: "
            "for each field of FlightPreferenceConstraint (airlines, flight_class, "
            "excluded_airlines, accept_redeye_flights, direct_flights_only, "
            "preferred_departure_timeslots, max_price_per_ticket), if the user's wording for "
            "THAT field does not mention a direction ('way there'/'departure' vs 'way back'/"
            "'return'/'coming back'), copy the SAME value into that field in BOTH "
            "outbound_air_ticket_preference and inbound_air_ticket_preference. Only fields the "
            "user explicitly tied to one direction should differ between the two. "
            "Example: 'I prefer Cathay Pacific business class, no JetStar, no red-eye flights. "
            "Direct flights only on the way there, but stops are fine on the way back.' → "
            "outbound_air_ticket_preference={airlines:['Cathay Pacific'], flight_class:'business', "
            "excluded_airlines:['JetStar'], accept_redeye_flights:false, direct_flights_only:true}, "
            "inbound_air_ticket_preference={airlines:['Cathay Pacific'], flight_class:'business', "
            "excluded_airlines:['JetStar'], accept_redeye_flights:false, direct_flights_only:false}. "
            "Note airlines/flight_class/excluded_airlines/accept_redeye_flights (not tied to a "
            "direction) are copied to BOTH sides even though direct_flights_only (explicitly "
            "directional) differs — do not null out the other fields on outbound just because "
            "one field in the same message was directional."
        ),
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

