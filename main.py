from orchestration.graph import trip_graph


initial_state = {
    "destination": "Kyoto",
    "days": 5,
    "preferences": ["culture", "food"],
    "status": "planning",
    "feedback": "The hotel is so expensive. Is there any other hotel that costs less than 200 per night?"
    #The hotel is so expensive. I would like to stay at its half price or less. In addition, I don't want live in the city center.
}

final_state = trip_graph.invoke(initial_state)

print("=== FINAL TRIP PLAN ===")
print(f"final_state: {final_state}")