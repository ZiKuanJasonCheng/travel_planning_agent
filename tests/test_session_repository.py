import pytest
from db.repository import SessionRepository


SAMPLE_STATE = {
    "destination": "Tokyo",
    "origin": "London",
    "num_people": 2,
    "start_date": "2026-09-01",
    "end_date": "2026-09-08",
    "status": "planning",
    "preferences": ["budget hotels", "museums"],
    "feedback": None,
    "checker_critique": None,
    "transport_options": [],
    "accommodation_options": [],
    "itinerary": [],
    "traces": [],
    "constraints": {},
    "dirty_agents": [],
    "log_trace": True,
    "checker_retry_count": 0,
}


@pytest.mark.integration
def test_create_and_get(clean_sessions):
    repo = SessionRepository()
    repo.create("sess-001", SAMPLE_STATE)
    result = repo.get("sess-001")
    assert result is not None
    assert result["destination"] == "Tokyo"
    assert result["num_people"] == 2
    assert result["preferences"] == ["budget hotels", "museums"]


@pytest.mark.integration
def test_get_nonexistent_returns_none(clean_sessions):
    repo = SessionRepository()
    result = repo.get("does-not-exist")
    assert result is None


@pytest.mark.integration
def test_update(clean_sessions):
    repo = SessionRepository()
    repo.create("sess-002", SAMPLE_STATE)
    updated = {**SAMPLE_STATE, "status": "completed", "feedback": "looks great"}
    repo.update("sess-002", updated)
    result = repo.get("sess-002")
    assert result["status"] == "completed"
    assert result["feedback"] == "looks great"


@pytest.mark.integration
def test_search_returns_matching_sessions(clean_sessions):
    repo = SessionRepository()
    repo.create("sess-003", SAMPLE_STATE)
    repo.create(
        "sess-004",
        {**SAMPLE_STATE, "destination": "Paris", "preferences": ["luxury spa"]},
    )
    results = repo.search("museums Tokyo")
    destinations = [r["destination"] for r in results]
    assert "Tokyo" in destinations
    assert "Paris" not in destinations


@pytest.mark.integration
def test_search_no_results_returns_empty_list(clean_sessions):
    repo = SessionRepository()
    repo.create("sess-005", SAMPLE_STATE)
    results = repo.search("zzznomatch")
    assert results == []
