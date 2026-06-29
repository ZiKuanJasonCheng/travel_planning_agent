import uuid
from typing import Optional
from states.trip_state import TripState
from db.repository import SessionRepository

_repository = SessionRepository()


def create_session(initial_state: TripState) -> str:
    session_id = str(uuid.uuid4())
    _repository.create(session_id, initial_state)
    return session_id


def get_session(session_id: str) -> Optional[TripState]:
    return _repository.get(session_id)


def update_session(session_id: str, state: TripState) -> None:
    _repository.update(session_id, state)
