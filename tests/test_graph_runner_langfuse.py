# tests/test_graph_runner_langfuse.py
from unittest.mock import patch

from orchestration.graph_runner import run_until_needing_feedback_or_finished


def test_run_until_needing_feedback_completes_without_langfuse_configured():
    """With no Langfuse keys set (default test env), the function must still
    run the graph and return terminal state — tracing must never break planning."""
    fake_state = {"status": "planning"}

    def fake_invoke(state, config=None):
        return {**state, "status": "completed"}

    with patch("orchestration.graph_runner.trip_graph") as mock_graph:
        mock_graph.invoke.side_effect = fake_invoke
        result = run_until_needing_feedback_or_finished(fake_state)

    assert result["status"] == "completed"


def test_run_until_needing_feedback_passes_callback_handler_when_configured():
    """When a callback handler is available, it must be passed into
    trip_graph.invoke() via config={'callbacks': [...]}."""
    fake_state = {"status": "planning", "session_id": "sess-1", "destination": "Tokyo", "origin": "SF", "num_people": 2}
    fake_handler = object()

    def fake_invoke(state, config=None):
        assert config is not None
        assert fake_handler in config["callbacks"]
        return {**state, "status": "completed"}

    with patch("orchestration.graph_runner.trip_graph") as mock_graph, \
         patch("orchestration.graph_runner.get_callback_handler", return_value=fake_handler):
        mock_graph.invoke.side_effect = fake_invoke
        result = run_until_needing_feedback_or_finished(fake_state)

    assert result["status"] == "completed"
