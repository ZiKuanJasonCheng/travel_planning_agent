# We use in-memory first. We can change to use Redis or other DB in the future
from typing import Dict
from states.trip_state import TripState
import uuid

SESSIONS: Dict[str, TripState] = {}


def create_session(initial_state: TripState) -> str:
    session_id = str(uuid.uuid4())
    global SESSIONS
    SESSIONS[session_id] = initial_state
    return session_id
    

def get_session(session_id: str) -> TripState:
    global SESSIONS
    return SESSIONS[session_id]


def update_session(session_id: str, state: TripState):
    global SESSIONS
    SESSIONS[session_id] = state