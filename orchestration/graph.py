from langgraph.graph import StateGraph, END
from states.trip_state import TripState
from agents.transport import transport_agent
from agents.accommodation import accommodation_agent
from agents.attraction import attraction_agent
from orchestration.decision import determine_next_step, buffer_step
from orchestration.human_feedback import human_feedback_checkpoint  #human_feedback_node


def build_graph():
    # Create a graph
    builder = StateGraph(TripState)

    # Add nodes/agents to graph
    builder.add_node("transport", transport_agent)
    builder.add_node("accommodation", accommodation_agent)
    builder.add_node("attraction", attraction_agent)
    builder.add_node("human_feedback", human_feedback_checkpoint)
    builder.add_node("determine_next_step", determine_next_step)
    builder.add_node("buffer_step", buffer_step)

    # Define workflow
    builder.set_entry_point("buffer_step")  # Entry point

    # Add conditional re-run
    builder.add_conditional_edges(
        "buffer_step", 
        determine_next_step, 
        {
            "transport_agent": "transport",
            "accommodation_agent": "accommodation",
            "attraction_agent": "attraction",
            "human_feedback": "human_feedback"
        }
    )

    # After each agent completes, return to determine_next_step
    builder.add_edge("transport", "buffer_step")
    builder.add_edge("accommodation", "buffer_step")
    builder.add_edge("attraction", "buffer_step")

    builder.add_edge("human_feedback", END)

    # Compile graph
    trip_graph = builder.compile()

    return trip_graph


trip_graph = build_graph()