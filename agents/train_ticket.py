"""
Train Ticket Agent - Sub-agent for handling train ticket searches
Placeholder for future implementation
"""
from states.trip_state import TripState, default_transport_options
from orchestration.tracability import log_trace
from orchestration.merge_constraints import merge_constraints
from copy import deepcopy


def train_ticket_agent(state: TripState) -> TripState:
    """
    Sub-agent for searching and recommending train ticket options
    TODO: Integrate with train booking API (e.g., Rail Europe, local train APIs)
    """
    destination = state.get("destination", "")
    existing_transport = state.get("constraints", {}).get("transport") or {}
    new_transport = state.get("new_constraints", {}).get("transport") or {}
    merged_transport = merge_constraints(existing_transport, new_transport)

    existing_railway_pref = existing_transport.get("railway_ticket_preference") or {}
    merged_railway_pref = merged_transport.get("railway_ticket_preference") or {}

    # Check this round's raw signal (not merged_transport) for rerun_planning:
    # when transport_type is "both", air_ticket_agent runs first and may have
    # already reset rerun_planning to None in what it persisted — reading
    # new_transport directly keeps this check order-independent.
    rerun_requested = new_transport.get("rerun_planning") is True

    should_skip = (
        state.get("feedback") is not None
        and merged_railway_pref == existing_railway_pref
        and not rerun_requested
    )

    constraints = dict(state.get("constraints") or {})

    if should_skip:
        constraints["transport"] = merged_transport
        return {**state, "constraints": constraints}

    if state.get("log_trace"):
        log_trace(
            state, node="train_ticket_agent", action="execute",
            reason="Searching for train ticket options",
            inputs={"constraints": deepcopy(merged_transport), "destination": destination},
        )

    max_price = merged_railway_pref.get("max_price_per_ticket")

    train_option = {
        "type": "train", "to": destination,
        "price": max_price if max_price else 100,
        "depart_time": "09:00:00", "arrival_time": "14:30:00",
        "reason": "Placeholder train option (API not yet integrated)",
    }

    transport_options = state.get("transport_options") or default_transport_options()
    transport_options = {**transport_options, "railway": [train_option]}

    if rerun_requested:
        merged_transport = {**merged_transport, "rerun_planning": None}
    constraints["transport"] = merged_transport

    new_state = {**state, "transport_options": transport_options, "constraints": constraints}

    if state.get("log_trace"):
        log_trace(
            new_state, node="train_ticket_agent", action="complete recommendations",
            reason="Train ticket options generated",
            outputs={"transport_options": deepcopy(transport_options)},
        )

    print("train_ticket_agent(): Added train option")

    return new_state
