from langgraph.graph import StateGraph, END
from states.trip_state import TripState
from agents.transport import transport_agent
from agents.accommodation import accommodation_agent
from agents.attraction import attraction_agent
from orchestration.decision import determine_next_step
#from orchestration.feedback_parsing import parse_feedback_node
from orchestration.human_feedback import human_feedback_node

# Create a graph
builder = StateGraph(TripState)


# Add nodes/agents to graph
builder.add_node("transport", transport_agent)
builder.add_node("accommodation", accommodation_agent)
builder.add_node("attraction", attraction_agent)
#builder.add_node("parse_feedback", parse_feedback_node)
builder.add_node("human_feedback", human_feedback_node)


# Define workflow
builder.set_entry_point("transport")  # Entry point
#builder.add_edge("human_feedback", "transport")
builder.add_edge("transport", "accommodation")
builder.add_edge("accommodation", "attraction")
builder.add_edge("attraction", "human_feedback")
#builder.add_edge("attraction", END)

# Add conditional re-run
builder.add_conditional_edges("human_feedback", determine_next_step, {"redo_accommodation": "accommodation", "end": END})

# Compile graph
trip_graph = builder.compile()