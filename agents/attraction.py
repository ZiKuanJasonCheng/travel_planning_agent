from states.trip_state import TripState


def attraction_agent(state: TripState) -> TripState:
    state["itinerary"] = [
        {
            "day": 1,
            "activities": ["Local market", "Historic temple", "Surrounding mountains"]
        }
    ]

    print(f"attraction_agent(): state: {state}")
    
    return state