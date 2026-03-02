from states.trip_state import TripState
from orchestration.tracability import log_trace
from copy import deepcopy


def accommodation_agent(state: TripState) -> TripState:
    constraints = state.get("constraints", {})
    
    log_trace(
        state,
        node="accommodation_agent",
        action="execute",
        reason="Generating hotel recommendations",
        inputs={"constraints": deepcopy(constraints)}
    )


    if "budget" in constraints:
        max_price_per_night = constraints["budget"]
        default_option = {
            "name": "Holiday Inn",
            "price_per_night": max_price_per_night,
            "area": "suburban area"
        }

        if not state["accommodation_options"]:
            state["accommodation_options"] = [default_option]
        else:
            if isinstance(state["accommodation_options"][0], dict) and state["accommodation_options"][0]:
                state["accommodation_options"][0]["price_per_night"] = max_price_per_night
            else:
                state["accommodation_options"][0] = default_option
                
    
    if "preference" in constraints:
        default_option = {
            "name": "Hotel Sakura",
            "price_per_night": 1500,
            "area": constraints["preference"]["area"]
        }

        if not state["accommodation_options"]:
            state["accommodation_options"] = [default_option]
        else:
            if isinstance(state["accommodation_options"][0], dict) and state["accommodation_options"][0]:
                state["accommodation_options"][0]["area"] = constraints["preference"]["area"]
            else:
                state["accommodation_options"][0] = default_option
        
        

    if not state.get("accommodation_options"):
        state["accommodation_options"] = [
            {
                "name": "Artour Hotel",
                "price_per_night": 5000,
                "area": "near the station"
            }
        ]


    log_trace(
        state,
        node="accommodation_agent",
        action="complete recommendations",
        reason="Hotel recommendations generated",
        outputs={"accommodation_options": deepcopy(state["accommodation_options"])}  #"hotel_count": len(hotels)
    )

    print(f"accommodation_agent(): state: {state}")

    return state