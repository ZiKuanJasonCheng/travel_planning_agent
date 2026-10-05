from unittest.mock import patch

from api.apis import submit_feedback, FeedbackModel


def _session_state(**overrides):
    state = {
        "session_id": "sess-1",
        "status": "is_waiting_for_feedback",
        "destination": "Tokyo",
        "origin": "San Francisco",
        "num_people": 2,
        "days": 5,
        "start_date": "2026-10-01",
        "end_date": "2026-10-06",
        "constraints": {},
        "dirty_agents": [],
    }
    state.update(overrides)
    return state


def _run_feedback(param, state):
    """Drive submit_feedback with a stubbed session store and graph."""
    captured = {}
    persisted = {}

    def fake_apply(state, feedback, overrides=None):
        from orchestration.human_feedback import apply_user_feedback
        with patch("orchestration.human_feedback.parse_feedback_with_llm", return_value=None):
            return apply_user_feedback(state, feedback, overrides)

    def fake_run(state):
        captured.update(state)
        state["status"] = "completed"
        state["final_output"] = {"session_id": "sess-1", "destination": "Tokyo", "reminder": ""}
        return state

    with patch("api.apis.setup_request_logging", return_value="/tmp/fake.log"), \
         patch("api.apis.get_session", return_value=state), \
         patch("api.apis.update_session", side_effect=lambda sid, s: persisted.update(s)), \
         patch("api.apis.apply_user_feedback", side_effect=fake_apply), \
         patch("api.apis.run_until_needing_feedback_or_finished", side_effect=fake_run):
        response = submit_feedback(param)

    return response, captured, persisted


def test_start_date_override_within_window_keeps_existing_end_date():
    """start_date alone is checked against the session's end_date, not shifted."""
    param = FeedbackModel(
        session_id="sess-1",
        feedback="starting a couple of days later",
        start_date="2026-10-03",
    )

    response, captured, persisted = _run_feedback(param, _session_state())

    assert captured["start_date"] == "2026-10-03"
    assert captured["end_date"] == "2026-10-06"  # unchanged
    assert captured["days"] == 3                 # recomputed from the pair
    assert captured["dirty_agents"] == [
        "transport_agent", "accommodation_agent", "attraction_agent"
    ]
    assert persisted["start_date"] == "2026-10-03"
    assert response["status"] == "completed"


def test_start_date_after_existing_end_date_is_rejected():
    """Session ends 2026-10-06, so a bare start_date later than that is invalid."""
    param = FeedbackModel(
        session_id="sess-1",
        feedback="push it way back",
        start_date="2026-12-01",
    )

    response, captured, _ = _run_feedback(param, _session_state())

    assert captured == {}  # the graph never ran
    assert response["status"] == "is_waiting_for_feedback"
    assert "Invalid date revision" in response["message"]


def test_start_date_with_explicit_consistent_end_date_succeeds():
    param = FeedbackModel(
        session_id="sess-1",
        feedback="moving the whole trip",
        start_date="2026-12-01",
        end_date="2026-12-04",
    )

    _, captured, _ = _run_feedback(param, _session_state())

    assert captured["start_date"] == "2026-12-01"
    assert captured["end_date"] == "2026-12-04"
    assert captured["days"] == 3


def test_days_only_override_recomputes_end_date_from_session_start():
    param = FeedbackModel(session_id="sess-1", feedback="make it longer", days=8)

    _, captured, persisted = _run_feedback(param, _session_state())

    assert captured["start_date"] == "2026-10-01"
    assert captured["days"] == 8
    assert captured["end_date"] == "2026-10-09"
    assert persisted["end_date"] == "2026-10-09"


def test_end_date_only_override_recomputes_days():
    param = FeedbackModel(session_id="sess-1", feedback="cut it short", end_date="2026-10-04")

    _, captured, _ = _run_feedback(param, _session_state())

    assert captured["end_date"] == "2026-10-04"
    assert captured["days"] == 3
    assert captured["start_date"] == "2026-10-01"


def test_num_people_override_is_forwarded():
    param = FeedbackModel(session_id="sess-1", feedback="four of us", num_people=4)

    _, captured, persisted = _run_feedback(param, _session_state())

    assert captured["num_people"] == 4
    assert persisted["num_people"] == 4


def test_no_overrides_leaves_dates_untouched_and_skips_graph():
    """A neutral feedback round that parses to nothing reports no change."""
    param = FeedbackModel(session_id="sess-1", feedback="looks good")

    response, captured, persisted = _run_feedback(param, _session_state())

    assert captured == {}  # the graph never ran
    assert persisted["start_date"] == "2026-10-01"
    assert persisted["end_date"] == "2026-10-06"
    assert response["status"] == "completed"
