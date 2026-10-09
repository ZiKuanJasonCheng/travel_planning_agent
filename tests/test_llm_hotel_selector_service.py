import json
import unittest
from unittest.mock import MagicMock, patch

from services.llm_hotel_selector_service import (
    _candidates_summary,
    _haversine_km,
    resolve_check_in_date,
    select_hotels,
)


def _candidate(name, price=120, distance=5.0, lat=35.0, lon=139.0, area="Shinjuku"):
    return {
        "type": "hotel",
        "name": name,
        "price_per_night": price,
        "currency": "USD",
        "area": area,
        "hotel_id": f"id_{name}",
        "lat": lat,
        "lon": lon,
        "supplier": "stayingapi",
        "reason": "StayingAPI offer",
        "distance_to_airport_km": distance,
    }


def _stay(index, check_in, check_out, reason="best fit"):
    return {
        "candidate_index": index,
        "check_in_date": check_in,
        "check_out_date": check_out,
        "reason": reason,
    }


def _tool_call(name, arguments, call_id="call-1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    tc.model_dump.return_value = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return tc


def _response_with_tool_calls(*tool_calls):
    message = MagicMock()
    message.tool_calls = list(tool_calls)
    message.content = None
    message.role = "assistant"
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.model = "gpt-4o"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 10
    return response


def _response_with_prose(content="I think the first one is best."):
    message = MagicMock()
    message.tool_calls = None
    message.content = content
    message.role = "assistant"
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    response.model = "gpt-4o"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 10
    return response


def _selection_response(stays):
    return _response_with_tool_calls(
        _tool_call("select_hotel_stays", json.dumps({"stays": stays}))
    )


class HaversineTests(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(_haversine_km(35.0, 139.0, 35.0, 139.0), 0.0)

    def test_known_distance_hong_kong_to_tokyo(self):
        # HKG (22.3089, 113.9145) to NRT (35.7647, 140.3864) is ~2,950 km.
        distance = _haversine_km(22.3089, 113.9145, 35.7647, 140.3864)
        self.assertAlmostEqual(distance, 2958, delta=25)

    def test_one_degree_of_longitude_at_equator(self):
        # ~111.19 km per degree of longitude at the equator.
        self.assertAlmostEqual(_haversine_km(0.0, 0.0, 0.0, 1.0), 111.19, delta=0.5)


class ResolveCheckInDateTests(unittest.TestCase):
    """Table-driven over the confirmed rule.

    A day-1 departure that lands on day 2 shifts check-in to day 2 — unless it lands
    in the 00:00–03:59 window, which keeps check-in on day 1.
    """

    def test_same_day_arrival_keeps_trip_start(self):
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-17", "18:25:00", "2026-11-17"),
            "2026-11-17",
        )

    def test_late_same_day_arrival_keeps_trip_start(self):
        # Lands 23:30 the same day it left: no night spent in the air.
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-17", "23:30:00", "2026-11-17"),
            "2026-11-17",
        )

    def test_next_day_arrival_at_0230_keeps_trip_start(self):
        # Small hours: the traveler still needs the room they are landing into.
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", "02:30:00", "2026-11-17"),
            "2026-11-17",
        )

    def test_next_day_arrival_at_0359_keeps_trip_start(self):
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", "03:59:00", "2026-11-17"),
            "2026-11-17",
        )

    def test_next_day_arrival_at_0400_shifts_to_arrival_date(self):
        # The boundary: from 04:00 the traveler is effectively arriving on day 2.
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", "04:00:00", "2026-11-17"),
            "2026-11-18",
        )

    def test_next_day_arrival_at_0600_shifts_to_arrival_date(self):
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", "06:00:00", "2026-11-17"),
            "2026-11-18",
        )

    def test_absent_arrival_date_keeps_trip_start(self):
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "", "06:00:00", "2026-11-17"),
            "2026-11-17",
        )

    def test_missing_arrival_time_still_shifts_on_a_next_day_arrival(self):
        # With no clock time the small-hours exception can't be detected, so a
        # next-day arrival date is taken at face value and the day-2 shift applies.
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", None, "2026-11-17"),
            "2026-11-18",
        )

    def test_malformed_arrival_time_behaves_like_missing(self):
        self.assertEqual(
            resolve_check_in_date("2026-11-17", "2026-11-18", "not-a-time", "2026-11-17"),
            "2026-11-18",
        )

    def test_missing_departure_date_keeps_trip_start(self):
        self.assertEqual(
            resolve_check_in_date(None, "2026-11-18", "06:00:00", "2026-11-17"),
            "2026-11-17",
        )


class CandidatesSummaryTests(unittest.TestCase):
    def test_includes_distance_and_price(self):
        summary = _candidates_summary([_candidate("Airport Inn", price=90, distance=1.2)])
        self.assertIn("[0] Airport Inn", summary)
        self.assertIn("distance_to_airport=1.2 km", summary)
        self.assertIn("price_per_night=90 USD", summary)

    def test_reports_unknown_distance_when_missing(self):
        summary = _candidates_summary([_candidate("No Coords", distance=None)])
        self.assertIn("distance_to_airport=unknown", summary)

    def test_empty_candidates(self):
        self.assertEqual(_candidates_summary([]), "(none)")


class SelectHotelsTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            _candidate("Airport Inn", price=95, distance=1.0, area="Airport"),
            _candidate("City Central", price=140, distance=28.0, area="Shinjuku"),
        ]

    def _select(self, response, **overrides):
        kwargs = {
            "candidates": self.candidates,
            "check_in_date": "2026-11-17",
            "check_out_date": "2026-11-21",
            "destination": "Tokyo",
            "airport_iata": "NRT",
            "arrival_time": "22:30:00",
            "arrival_date": "2026-11-17",
        }
        kwargs.update(overrides)
        with patch("services.llm_hotel_selector_service.get_openai_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = response
            return select_hotels(**kwargs)

    def test_single_stay_selection_returns_one_hotel(self):
        selected = self._select(_selection_response([_stay(1, "2026-11-17", "2026-11-21")]))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "City Central")
        self.assertEqual(selected[0]["check_in_date"], "2026-11-17")
        self.assertEqual(selected[0]["check_out_date"], "2026-11-21")

    def test_split_stay_returns_two_hotels_with_their_windows(self):
        selected = self._select(_selection_response([
            _stay(0, "2026-11-17", "2026-11-18", "near the airport for the landing night"),
            _stay(1, "2026-11-18", "2026-11-21", "central for the rest of the trip"),
        ]))

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["name"], "Airport Inn")
        self.assertEqual(selected[0]["check_in_date"], "2026-11-17")
        self.assertEqual(selected[0]["check_out_date"], "2026-11-18")
        self.assertEqual(selected[1]["name"], "City Central")
        self.assertEqual(selected[1]["check_in_date"], "2026-11-18")

    def test_selected_hotel_keeps_its_other_fields(self):
        selected = self._select(_selection_response([_stay(0, "2026-11-17", "2026-11-21")]))

        self.assertEqual(selected[0]["price_per_night"], 95)
        self.assertEqual(selected[0]["distance_to_airport_km"], 1.0)
        self.assertEqual(selected[0]["area"], "Airport")

    def test_out_of_range_index_is_dropped(self):
        selected = self._select(_selection_response([
            _stay(0, "2026-11-17", "2026-11-18"),
            _stay(7, "2026-11-18", "2026-11-21"),
        ]))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "Airport Inn")

    def test_non_integer_index_is_dropped(self):
        selected = self._select(_selection_response([
            {"candidate_index": "one", "check_in_date": "2026-11-17", "check_out_date": "2026-11-21"},
        ]))

        self.assertEqual(selected, [])

    def test_missing_stay_dates_fall_back_to_the_query_window(self):
        selected = self._select(_selection_response([
            {"candidate_index": 0, "reason": "closest to the airport"},
        ]))

        self.assertEqual(selected[0]["check_in_date"], "2026-11-17")
        self.assertEqual(selected[0]["check_out_date"], "2026-11-21")

    def test_prose_answer_then_terminal_call_still_resolves(self):
        prose = _response_with_prose()
        terminal = _selection_response([_stay(1, "2026-11-17", "2026-11-21")])

        with patch("services.llm_hotel_selector_service.get_openai_client") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = [prose, terminal]
            selected = select_hotels(
                candidates=self.candidates,
                check_in_date="2026-11-17",
                check_out_date="2026-11-21",
                destination="Tokyo",
            )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "City Central")

    def test_iteration_cap_still_yields_a_selection(self):
        # Three prose answers: the last iteration forces tool_choice, so the mocked
        # terminal response on the final call is what returns.
        prose = _response_with_prose()
        terminal = _selection_response([_stay(0, "2026-11-17", "2026-11-21")])

        with patch("services.llm_hotel_selector_service.get_openai_client") as mock_client:
            create = mock_client.return_value.chat.completions.create
            create.side_effect = [prose, prose, terminal]
            selected = select_hotels(
                candidates=self.candidates,
                check_in_date="2026-11-17",
                check_out_date="2026-11-21",
            )

        self.assertEqual(len(selected), 1)
        last_kwargs = create.call_args.kwargs
        self.assertEqual(
            last_kwargs["tool_choice"],
            {"type": "function", "function": {"name": "select_hotel_stays"}},
        )

    def test_no_terminal_call_returns_empty_list(self):
        with patch("services.llm_hotel_selector_service.get_openai_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = _response_with_prose()
            selected = select_hotels(
                candidates=self.candidates,
                check_in_date="2026-11-17",
                check_out_date="2026-11-21",
            )

        self.assertEqual(selected, [])

    def test_empty_candidates_returns_without_calling_the_model(self):
        with patch("services.llm_hotel_selector_service.get_openai_client") as mock_client:
            selected = select_hotels(candidates=[], check_in_date="2026-11-17", check_out_date="2026-11-21")

        self.assertEqual(selected, [])
        mock_client.return_value.chat.completions.create.assert_not_called()

    def test_invalid_arguments_json_returns_empty_list(self):
        selected = self._select(
            _response_with_tool_calls(_tool_call("select_hotel_stays", "{not json"))
        )
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
