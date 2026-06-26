from fastapi import Query, FastAPI, APIRouter, Response, status, HTTPException
from session import create_session, get_session, update_session
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import date, timedelta

from orchestration.human_feedback import apply_user_feedback
from orchestration.graph_runner import run_until_needing_feedback_or_finished
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from services.geocoding import check_city_granularity

router = APIRouter()


class RequestModel(BaseModel):
    destination: str = Field(min_length=1, max_length=100)
    origin: str = Field(min_length=1, max_length=100)
    num_people: int = Field(default=1, ge=1, le=50)
    days: Optional[int] = Field(default=None, ge=1, le=365)
    preferences: Optional[List[str]] = Field(default_factory=list)
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD

    @field_validator("destination", "origin")
    @classmethod
    def validate_city_granularity(cls, v: str) -> str:
        check_city_granularity(v)
        return v

    @model_validator(mode="after")
    def resolve_and_validate_dates(self) -> "RequestModel":
        start = self._parse_iso_date(self.start_date, "start_date")
        end = self._parse_iso_date(self.end_date, "end_date")

        if start and end:
            if end < start:
                raise ValueError("end_date must be on or after start_date")
            self.days = (end - start).days or 1
        elif start and self.days is not None:
            self.end_date = (start + timedelta(days=self.days)).strftime("%Y-%m-%d")
        elif self.days is None:
            self.days = 1  # default

        return self

    @staticmethod
    def _parse_iso_date(value: Optional[str], field: str) -> Optional[date]:
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} must be a valid ISO date (YYYY-MM-DD)")

class FeedbackModel(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    feedback: str = Field(min_length=1, max_length=2000)


@router.post("/trip/start")
def start_trip(param: RequestModel):
    constraints = None
    if param.preferences:
        constraints = parse_feedback_with_llm(", ".join(param.preferences))
    
    if constraints:
        constraints = constraints.model_dump()
        for agent, cons in constraints.items():
            if not cons:
                constraints[agent] = {}
    else:
        constraints = {}

    state = {
        "destination": param.destination,
        "origin": param.origin,
        "num_people": param.num_people,
        "days": param.days,
        "start_date": param.start_date,
        "end_date": param.end_date,
        "preferences": param.preferences,
        "constraints": constraints,
        "status": "planning",
        "log_trace": True,
        "dirty_agents": ["transport_agent", "accommodation_agent", "attraction_agent"],
        "traces": [],
    }
    print(f"start_trip(): state: {state}")

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
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
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