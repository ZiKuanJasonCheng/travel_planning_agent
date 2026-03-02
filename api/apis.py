from fastapi import Query, FastAPI, APIRouter, Response, status
from session import create_session, get_session, update_session
from pydantic import BaseModel
from typing import Optional, List

from orchestration.human_feedback import apply_user_feedback
from orchestration.graph_runner import run_until_needing_feedback_or_finished

router = APIRouter()


class RequestModel(BaseModel):
    destination: str
    days: int
    preferences: Optional[List] = []

class FeedbackModel(BaseModel):
    session_id: str
    feedback: str


@router.post("/trip/start")
def start_trip(param: RequestModel):
    state = {
        "destination": param.destination,
        "days": param.days,
        "preferences": param.preferences,
        "status": "planning",
        "dirty_agents": ["transport_agent", "accommodation_agent", "attraction_agent"],
        "traces": [],
    }
    
    session_id = create_session(state)
    state = run_until_needing_feedback_or_finished(state)
    update_session(session_id, state)

    return {
        "session_id": session_id,
        "status": state["status"],
        "state": state
    }


@router.post("/trip/feedback")  # /trip/{session_id}/feedback
def submit_feedback(param: FeedbackModel):
    state = get_session(param.session_id)
    print(f"Retrieved state! state: {state}")
    
    changed = apply_user_feedback(state, param.feedback)

    if not changed:
        state["status"] = "completed"
        update_session(param.session_id, state)
        return {
            "session_id": param.session_id,
            "status": state["status"],
            "final_state": state,
        }

    state["status"] = "planning"
    # Keep running the graph until needing feedback or finished
    state = run_until_needing_feedback_or_finished(state)
    update_session(param.session_id, state)

    return {
        "session_id": param.session_id,
        "status": state["status"],
        "state": state
    }