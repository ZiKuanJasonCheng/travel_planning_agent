import json
from typing import Optional
from sqlalchemy import text
from db.connection import get_engine
from states.trip_state import default_transport_options


def _state_to_json(state: dict) -> str:
    def _default(obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    return json.dumps(state, default=_default)


def _build_search_text(state: dict) -> str:
    parts = [
        state.get("destination") or "",
        state.get("origin") or "",
        state.get("feedback") or "",
        state.get("checker_critique") or "",
        " ".join(state.get("preferences") or []),
    ]
    return " ".join(p for p in parts if p)


class SessionRepository:
    def create(self, session_id: str, state: dict) -> None:
        search_text = _build_search_text(state)
        with get_engine().connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO sessions
                        (session_id, destination, origin, num_people,
                         start_date, end_date, status, state, search_vector)
                    VALUES
                        (:session_id, :destination, :origin, :num_people,
                         :start_date, :end_date, :status, cast(:state as jsonb),
                         to_tsvector('english', :search_text))
                    """
                ),
                {
                    "session_id": session_id,
                    "destination": state.get("destination", ""),
                    "origin": state.get("origin", ""),
                    "num_people": state.get("num_people", 1),
                    "start_date": state.get("start_date"),
                    "end_date": state.get("end_date"),
                    "status": state.get("status", "planning"),
                    "state": _state_to_json(state),
                    "search_text": search_text,
                },
            )
            conn.commit()

    def get(self, session_id: str) -> Optional[dict]:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT state FROM sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).fetchone()
        if row is None:
            return None
        state = dict(row[0])
        if not isinstance(state.get("transport_options"), dict):
            state["transport_options"] = default_transport_options()
        return state

    def update(self, session_id: str, state: dict) -> None:
        search_text = _build_search_text(state)
        with get_engine().connect() as conn:
            conn.execute(
                text(
                    """
                    UPDATE sessions SET
                        destination   = :destination,
                        origin        = :origin,
                        num_people    = :num_people,
                        start_date    = :start_date,
                        end_date      = :end_date,
                        status        = :status,
                        state         = cast(:state as jsonb),
                        search_vector = to_tsvector('english', :search_text),
                        updated_at    = NOW()
                    WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": session_id,
                    "destination": state.get("destination", ""),
                    "origin": state.get("origin", ""),
                    "num_people": state.get("num_people", 1),
                    "start_date": state.get("start_date"),
                    "end_date": state.get("end_date"),
                    "status": state.get("status", "planning"),
                    "state": _state_to_json(state),
                    "search_text": search_text,
                },
            )
            conn.commit()

    def search(self, query: str, filters: dict | None = None) -> list[dict]:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT session_id, destination, origin, num_people,
                           start_date, end_date, status, created_at
                    FROM sessions
                    WHERE search_vector @@ plainto_tsquery('english', :query)
                    ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC
                    """
                ),
                {"query": query},
            ).fetchall()
        return [row._asdict() for row in rows]
