import inspect

from services.llm_itinerary_service import LLMItineraryService
from services.llm_flight_selector_service import select_flights
from orchestration.llm_feedback_parsing import parse_feedback_with_llm


def _is_observed(func):
    # langfuse's @observe wraps via functools.wraps, so the original
    # function is reachable at __wrapped__ when decorated.
    return hasattr(func, "__wrapped__")


def test_call_llm_is_observed():
    assert _is_observed(LLMItineraryService._call_llm)


def test_select_flights_is_observed():
    assert _is_observed(select_flights)


def test_parse_feedback_with_llm_is_observed():
    assert _is_observed(parse_feedback_with_llm)
