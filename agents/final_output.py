"""
Final Output Agent

Formats the finished trip into the stable payload the API returns to the client,
plus a plain-language reminder/advisor note about anything that needs attention.
"""
import logging
from copy import deepcopy

from states.trip_state import TripState
from orchestration.tracability import log_trace
from services.llm_reminder_service import generate_reminder

logger = logging.getLogger(__name__)


def _flight_items(transport_options: dict) -> list:
    flight = (transport_options or {}).get("flight") or {}
    return list(flight.get("outbound") or []) + list(flight.get("inbound") or [])


def final_output_node(state: TripState, closing_reminder: bool = False) -> TripState:
    """Build the client-facing payload and store it under state["final_output"].

    `closing_reminder=True` is used on the no-change feedback path, where the
    reminder LLM writes a wrap-up line instead of reading the critique and the
    error `reason` fields.
    """
    transport_options = state.get("transport_options") or {}
    accommodation_options = state.get("accommodation_options") or []
    itinerary = state.get("itinerary") or []

    if state.get("log_trace"):
        log_trace(
            state,
            node="final_output",
            action="execute",
            reason="Formatting final output",
            inputs={"closing_reminder": closing_reminder},
        )

    reminder = generate_reminder(
        checker_critique=state.get("checker_critique"),
        flight_items=_flight_items(transport_options),
        accommodation_options=accommodation_options,
        itinerary=itinerary,
        destination=state.get("destination"),
        closing=closing_reminder,
    )

    final_output = {
        "session_id": state.get("session_id"),
        "destination": state.get("destination"),
        "origin": state.get("origin"),
        "num_people": state.get("num_people"),
        "days": state.get("days"),
        "start_date": state.get("start_date"),
        "end_date": state.get("end_date"),
        "transport_options": transport_options,
        "accommodation_options": accommodation_options,
        "itinerary": itinerary,
        "reminder": reminder,
    }

    logger.info(f"LLM reminder output: {reminder}", extra={"to_terminal": False})

    if state.get("log_trace"):
        log_trace(
            state,
            node="final_output",
            action="complete",
            reason="Final output formatted",
            outputs={"has_reminder": bool(reminder)},
        )

    return {**state, "final_output": final_output}
