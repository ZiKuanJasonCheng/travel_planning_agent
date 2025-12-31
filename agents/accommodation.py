from states.trip_state import TripState


def accommodation_agent(state: TripState) -> TripState:
    #feedback = state.get("feedback", "").lower()
    #print(f"accommodation_agent(): feedback: {feedback}")
    
    constraints = state.get("constraints", "")

    
    #if "expensive" in feedback:
    if "budget" in constraints:
        max_price_per_night = constraints["budget"]
        state["accommodation_options"] = [
            {
                "name": "Holiday Inn",
                "price_per_night": max_price_per_night,
                "area": "suburban area"
            }
        ]
        # Clean the 'budget' field from constraints
        state["constraints"].pop("budget")
    
    if "preference" in constraints:
        state["accommodation_options"] = [
            {
                "name": "Hotel Sakura",
                "price_per_night": 1500,
                "area": constraints["preference"]["area"]
            }
        ]
        # Clean the 'preference' field from constraints
        state["constraints"].pop("preference")

    if not state.get("accommodation_options"):
        state["accommodation_options"] = [
            {
                "name": "Artour Hotel",
                "price_per_night": 5000,
                "area": "near the station"
            }
        ]


    print(f"accommodation_agent(): state: {state}")

    return state