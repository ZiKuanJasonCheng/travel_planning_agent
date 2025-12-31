from states.trip_state import TripState


def transport_agent(state: TripState) -> TripState:
    destination = state["destination"]

    state["transport_options"] = [
        {
            "type": "flight",
            "to": destination,
            "price": 300,
            "reason": "cheapest option"
        }
    ]

    print(f"transport_agent(): state: {state}")
    
    return state