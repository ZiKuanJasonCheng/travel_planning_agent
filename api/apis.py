import logging
from fastapi import Query, FastAPI, APIRouter, Response, status, HTTPException
from session import create_session, get_session, update_session
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Annotated, List, Optional
from datetime import timedelta
from states.trip_state import TripState, default_transport_options
from states.trip_dates import parse_iso_date
from agents.final_output import final_output_node
from orchestration.final_output_cache import get_cached_final_output
from orchestration.human_feedback import apply_user_feedback
from orchestration.graph_runner import run_until_needing_feedback_or_finished
from orchestration.llm_feedback_parsing import parse_feedback_with_llm
from copy import deepcopy
from services.geocoding import check_city_granularity
from logging_config import setup_request_logging

router = APIRouter()
logger = logging.getLogger(__name__)


class RequestModel(BaseModel):
    destination: str = Field(min_length=1, max_length=100)
    origin: str = Field(min_length=1, max_length=100)
    num_people: int = Field(default=1, ge=1, le=50)
    days: Optional[int] = Field(default=None, ge=1, le=365)
    preferences: Optional[List[str]] = Field(default_factory=list)
    start_date: str  # YYYY-MM-DD, required
    end_date: Optional[str] = None    # YYYY-MM-DD

    @field_validator("destination", "origin")
    @classmethod
    def validate_city_granularity(cls, v: str) -> str:
        check_city_granularity(v)
        return v

    @model_validator(mode="after")
    def resolve_and_validate_dates(self) -> "RequestModel":
        start = parse_iso_date(self.start_date, "start_date")
        end = parse_iso_date(self.end_date, "end_date")

        if end is not None:
            if end <= start:
                raise ValueError("end_date must be at least one day after start_date")
            self.days = (end - start).days
        elif self.days is not None:
            self.end_date = (start + timedelta(days=self.days)).strftime("%Y-%m-%d")
        else:
            self.days = 5  # default
            self.end_date = (start + timedelta(days=self.days)).strftime("%Y-%m-%d")

        return self


class FeedbackModel(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    # One entry per message the user submitted this round. Only this round's
    # messages are held — earlier rounds are not accumulated.
    feedback: List[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(min_length=1)

    # Optional trip revisions. Unlike RequestModel these are never derived
    # here: resolving days against a start_date needs the session's existing
    # start_date as the anchor, which only the handler has.
    num_people: Optional[int] = Field(default=None, ge=1, le=50)
    days: Optional[int] = Field(default=None, ge=1, le=365)
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "FeedbackModel":
        start = parse_iso_date(self.start_date, "start_date")
        end = parse_iso_date(self.end_date, "end_date")
        if start is not None and end is not None and end <= start:
            raise ValueError("end_date must be at least one day after start_date")
        return self


def run_from_cached_plan(state: dict, cached_output: dict) -> dict:
    """Turn a request state plus a cached payload into a persisted session.

    The session must hold the plan itself, not just the client payload: a later
    feedback round diffs the cached options against the agreed constraints, and
    `_build_search_text`/the agents read `constraints`. `dirty_agents` is empty
    so nothing replans until the traveler asks for a change.
    """
    return {
        **state,
        "constraints": deepcopy(state.get("new_constraints") or {}),
        "transport_options": deepcopy(cached_output.get("transport_options") or default_transport_options()),
        "accommodation_options": deepcopy(cached_output.get("accommodation_options") or []),
        "itinerary": deepcopy(cached_output.get("itinerary") or []),
        "dirty_agents": [],
        "status": "is_waiting_for_feedback",
    }


@router.post("/trip/start")
def start_trip(param: RequestModel):
    log_file = setup_request_logging("new_trip")
    logger.info(f"start_trip(): received request for destination={param.destination}, origin={param.origin}")

    constraints = None

    if param.preferences:
        try:
            constraints = parse_feedback_with_llm("; ".join(param.preferences))
        except Exception as e:
            logger.error(f"start_trip(): failed to parse user constraints. {e}")
            return {
                "status": "failed",
                "message": "An error occcurred while processing preferences. It might be an LLM service issue. Please wait and try again.",
            }
    
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
        "constraints": {},
        "new_constraints": constraints,
        "transport_options": default_transport_options(),
        "status": "planning",
        "log_trace": True,
        "dirty_agents": ["transport_agent", "accommodation_agent", "attraction_agent"],
        "traces": [],
        "log_file": log_file,
    }
    logger.info(f"start_trip(): state: {state}", extra={"to_terminal": False})

    # A brand-new trip that matches a recently verified run can skip the whole
    # pipeline. The cached plan still becomes a real session so the traveler can
    # submit feedback against it; only the client-facing session_id is renewed.
    cached_output = get_cached_final_output(state)
    if cached_output is not None:
        cached_state = run_from_cached_plan(state, cached_output)
        session_id = create_session(cached_state)
        cached_state["session_id"] = session_id  # Agents might use session_id
        update_session(session_id, cached_state)

        logger.info(
            f"start_trip(): served from cache with status={cached_state['status']}"
        )
        return {"status": cached_state["status"], **cached_output, "session_id": session_id}

    session_id = create_session(state)
    state["session_id"] = session_id  # Agents might use session_id
    state = run_until_needing_feedback_or_finished(state)

    final_output = state.pop("final_output")  # client-facing payload — never persisted
    update_session(session_id, state)

    logger.info(f"start_trip(): completed with status={state['status']}")
    return {"status": state["status"], **final_output}


@router.post("/trip/feedback")  # /trip/{session_id}/feedback
def submit_feedback(param: FeedbackModel):
    log_file = setup_request_logging("feedback")
    logger.info(f"submit_feedback(): received feedback for session_id={param.session_id}")

    state = get_session(param.session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state["session_id"] = state.get("session_id", param.session_id)  # Agents might use session_id
    state["log_file"] = log_file
    logger.info(f"Retrieved state! state: {state}", extra={"to_terminal": False})

    changed = False
    try:
        changed = apply_user_feedback(state, param.feedback, {
            "start_date": param.start_date,
            "end_date": param.end_date,
            "days": param.days,
            "num_people": param.num_people,
        })
    except ValueError as e:
        # The resulting dates were internally inconsistent (end_date not after
        # start_date). Nothing was persisted — the session still holds its
        # previous state — so the client can simply resubmit a valid range.
        logger.error(f"submit_feedback(): invalid date revision: {e}")
        return {
            "session_id": param.session_id,
            "status": state["status"],
            "message": f"Invalid date revision: {e}",
        }
    except Exception as e:
        logger.error(f"submit_feedback(): failed to parse user feedback to know if a constraint has been changed. Error message: {e}")
        return {
            "session_id": param.session_id,
            "status": state["status"],
            "message": "An error occcurred while processing sumbitted feedback. It might be an LLM service issue. Please wait and try again.",
        }


    if not changed:
        state["status"] = "completed"
        # The graph didn't run this round and the previous response popped
        # final_output before persisting, so rebuild it. Nothing changed, so the
        # reminder LLM writes a closing line instead of scanning for problems.
        final_output = final_output_node(state, closing_reminder=True)["final_output"]
        update_session(param.session_id, state)
        logger.info(f"submit_feedback(): completed with status={state['status']}")
        return {"status": state["status"], **final_output}

    state["status"] = "planning"
    # Keep running the graph until needing feedback or finished
    state = run_until_needing_feedback_or_finished(state)

    final_output = state.pop("final_output")  # client-facing payload — never persisted
    update_session(param.session_id, state)

    logger.info(f"submit_feedback(): completed with status={state['status']}")
    return {"status": state["status"], **final_output}