from orchestration.graph import trip_graph


initial_state = {
    "destination": "Kyoto",
    "days": 5,
    "preferences": ["culture", "food"],
    "status": "planning",
    "feedback": "The hotel is so expensive."
}

final_state = trip_graph.invoke(initial_state)

print("=== FINAL TRIP PLAN ===")
print(f"final_state: {final_state}")