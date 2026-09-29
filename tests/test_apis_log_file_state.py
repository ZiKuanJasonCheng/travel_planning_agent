from unittest.mock import patch

from api.apis import start_trip, RequestModel


def test_start_trip_sets_log_file_in_state():
    captured_state = {}
    persisted_state = {}

    def fake_run_until_needing_feedback_or_finished(state):
        captured_state.update(state)
        state["status"] = "completed"
        state["final_output"] = {"session_id": "sess-123", "destination": "Tokyo", "reminder": ""}
        return state

    def fake_update_session(session_id, state):
        persisted_state.update(state)

    with patch("api.apis.check_city_granularity"), \
         patch("api.apis.setup_request_logging", return_value="/tmp/fake_log.log") as mock_setup, \
         patch("api.apis.run_until_needing_feedback_or_finished", side_effect=fake_run_until_needing_feedback_or_finished), \
         patch("api.apis.create_session", return_value="sess-123"), \
         patch("api.apis.update_session", side_effect=fake_update_session):
        param = RequestModel(
            destination="Tokyo",
            origin="San Francisco",
            num_people=1,
            days=3,
            preferences=[],
            start_date="2026-10-01",
        )
        response = start_trip(param)

    mock_setup.assert_called_once_with("new_trip")
    assert captured_state["log_file"] == "/tmp/fake_log.log"
    # The client-facing payload is returned, never persisted with the session.
    assert "final_output" not in persisted_state
    assert response == {"status": "completed", "session_id": "sess-123", "destination": "Tokyo", "reminder": ""}
