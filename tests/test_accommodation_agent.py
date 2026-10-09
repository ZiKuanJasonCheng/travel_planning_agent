import os
import unittest
from unittest.mock import patch, MagicMock

from agents.accommodation import accommodation_agent, _fill_unlimited_price
from orchestration.merge_constraints import UNLIMITED_PRICE


class FillUnlimitedPriceTests(unittest.TestCase):
    def test_fills_unlimited_when_price_unset(self):
        merged = {"preference": {"area": "Shinjuku"}}
        result = _fill_unlimited_price(merged)
        self.assertEqual(result["preference"]["max_price_per_night"], UNLIMITED_PRICE)

    def test_leaves_real_price_untouched(self):
        merged = {"preference": {"max_price_per_night": 150}}
        result = _fill_unlimited_price(merged)
        self.assertEqual(result["preference"]["max_price_per_night"], 150)

    def test_handles_missing_preference(self):
        result = _fill_unlimited_price({})
        self.assertEqual(result["preference"]["max_price_per_night"], UNLIMITED_PRICE)


class _StubSupplier:
    def __init__(self, options):
        self._options = options

    def search_hotels(self, **kwargs):
        return self._options


class AccommodationAgentTests(unittest.TestCase):
    def _base_state(self):
        return {
            "destination": "Tokyo",
            "days": 4,
            "constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_uses_duffel_results_as_primary(self, mock_duffel):
        mock_duffel.return_value = _StubSupplier(
            [
                {
                    "type": "hotel",
                    "name": "Duffel Grand Tokyo",
                    "price_per_night": 180,
                    "currency": "USD",
                    "area": "Shinjuku",
                    "supplier": "duffel",
                },
            ]
        )

        state = self._base_state()
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertEqual(new_state["accommodation_options"][0]["supplier"], "duffel")
        self.assertEqual(new_state["accommodation_options"][0]["name"], "Duffel Grand Tokyo")

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_uses_static_fallback_if_supplier_returns_nothing(self, mock_duffel):
        mock_duffel.return_value = _StubSupplier([])

        state = self._base_state()
        state["constraints"] = {
            "accommodation": {
                "preference": {"max_price_per_night": 150, "area": "Shinjuku"},
            }
        }

        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertEqual(new_state["accommodation_options"][0]["price_per_night"], 150)
        self.assertEqual(new_state["accommodation_options"][0]["area"], "Shinjuku")


class AccommodationAgentSkipLogicTests(unittest.TestCase):
    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "days": 4,
            "feedback": "some prior feedback",
            "constraints": {
                "accommodation": {"preference": {"max_price_per_night": 150}}
            },
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [
                {"type": "hotel", "name": "Existing Hotel", "reason": "Duffel Stays offer"}
            ],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_skips_when_constraints_unchanged(self, mock_duffel):
        state = self._base_state()
        new_state = accommodation_agent(state)

        mock_duffel.assert_not_called()
        self.assertEqual(new_state["accommodation_options"], state["accommodation_options"])
        self.assertEqual(
            new_state["constraints"]["accommodation"],
            {"preference": {"max_price_per_night": 150}},
        )

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_replans_when_new_constraints_add_something(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}}
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertEqual(
            new_state["constraints"]["accommodation"]["preference"]["area"], "Shibuya"
        )

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            constraints={
                "accommodation": {
                    "preference": {"max_price_per_night": 150},
                    "rerun_planning": True,
                }
            },
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertIsNone(new_state["constraints"]["accommodation"]["rerun_planning"])

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            accommodation_options=[
                {
                    "reason": (
                        "There's a StayingAPI Hotel API error (or unknown error) at the moment. "
                        "Please wait for a few minutes and submit a feedback saying "
                        "'Run accommodation service again'."
                    )
                }
            ],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_duffel.return_value = mock_service

        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, accommodation_options=[],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_real_api_error_produces_distinct_error_message(self, mock_duffel):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = [{"reason": "StayingAPI Hotel API error"}]
        mock_duffel.return_value = mock_service

        state = self._base_state(new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}})
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertIn(
            "StayingAPI Hotel API error (or unknown error)",
            new_state["accommodation_options"][0]["reason"],
        )


class HotelSelectionTests(unittest.TestCase):
    """accommodation_agent hands the shortlist to the LLM selector after search."""

    def setUp(self):
        # The real resolver geocodes over the network; the IATA code itself is not
        # what these tests are about.
        patcher = patch("agents.accommodation.resolve_city_iata_codes", return_value=["NRT"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "days": 4,
            "feedback": "some prior feedback",
            "constraints": {"accommodation": {"preference": {"area": "Tokyo"}}},
            "new_constraints": {"accommodation": {"preference": {"area": "Shinjuku"}}},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "accommodation_options": [],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
            "start_date": "2026-11-17",
            "end_date": "2026-11-21",
            "num_people": 2,
        }
        state.update(overrides)
        return state

    def _outbound(self, depart_date, depart_time, arrival_time, arrival_date):
        return {
            "railway": [],
            "flight": {
                "outbound": [{
                    "airline": "CX",
                    "from": "HKG",
                    "to": "NRT",
                    "depart_time": depart_time,
                    "arrival_time": arrival_time,
                    "departure_date": depart_date,
                    "arrival_date": arrival_date,
                }],
                "inbound": [],
            },
        }

    def _two_hotels(self):
        return [
            {
                "type": "hotel", "name": "Airport Inn", "price_per_night": 95,
                "currency": "USD", "area": "Narita", "hotel_id": "h1",
                "lat": 35.7, "lon": 140.3, "supplier": "stayingapi",
                "reason": "StayingAPI offer", "distance_to_airport_km": 1.0,
            },
            {
                "type": "hotel", "name": "City Central", "price_per_night": 140,
                "currency": "USD", "area": "Shinjuku", "hotel_id": "h2",
                "lat": 35.69, "lon": 139.7, "supplier": "stayingapi",
                "reason": "StayingAPI offer", "distance_to_airport_km": 60.0,
            },
        ]

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_selector_choice_is_persisted(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier(self._two_hotels())
        mock_select.return_value = [
            {**self._two_hotels()[0], "check_in_date": "2026-11-17", "check_out_date": "2026-11-18"},
            {**self._two_hotels()[1], "check_in_date": "2026-11-18", "check_out_date": "2026-11-21"},
        ]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            state = accommodation_agent(self._state())

        self.assertEqual([h["name"] for h in state["accommodation_options"]], ["Airport Inn", "City Central"])
        self.assertEqual(state["accommodation_options"][0]["check_out_date"], "2026-11-18")

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_selector_receives_arrival_context(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier(self._two_hotels())
        mock_select.return_value = []

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            accommodation_agent(self._state(
                transport_options=self._outbound("2026-11-17", "23:00:00", "06:30:00", "2026-11-18"),
            ))

        kwargs = mock_select.call_args.kwargs
        self.assertEqual(kwargs["arrival_time"], "06:30:00")
        self.assertEqual(kwargs["arrival_date"], "2026-11-18")

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_single_candidate_skips_the_selector(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier([self._two_hotels()[0]])

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            state = accommodation_agent(self._state())

        mock_select.assert_not_called()
        self.assertEqual(len(state["accommodation_options"]), 1)
        self.assertEqual(state["accommodation_options"][0]["check_in_date"], "2026-11-17")

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_missing_api_key_skips_the_selector(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier(self._two_hotels())

        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            state = accommodation_agent(self._state())

        mock_select.assert_not_called()
        # Fail-safe: the first (cheapest) hotel, covering the whole stay.
        self.assertEqual([h["name"] for h in state["accommodation_options"]], ["Airport Inn"])
        self.assertEqual(state["accommodation_options"][0]["check_in_date"], "2026-11-17")
        self.assertEqual(state["accommodation_options"][0]["check_out_date"], "2026-11-21")

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_selector_failure_falls_back_to_the_first_hotel(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier(self._two_hotels())
        mock_select.side_effect = ValueError("model never returned a selection")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            state = accommodation_agent(self._state())

        # The run survives and degrades to one bookable hotel for the whole stay,
        # rather than a half-applied split that could leave a night uncovered.
        self.assertEqual([h["name"] for h in state["accommodation_options"]], ["Airport Inn"])
        self.assertEqual(state["accommodation_options"][0]["check_out_date"], "2026-11-21")

    @patch("agents.accommodation.select_hotels")
    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_empty_selection_falls_back_to_the_first_hotel(self, mock_service, mock_select):
        mock_service.return_value = _StubSupplier(self._two_hotels())
        mock_select.return_value = []

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            state = accommodation_agent(self._state())

        self.assertEqual([h["name"] for h in state["accommodation_options"]], ["Airport Inn"])

    @patch("agents.accommodation.get_stayingapi_hotel_service")
    def test_airport_iata_is_passed_to_the_search(self, mock_service):
        mock_service.return_value = MagicMock()
        mock_service.return_value.search_hotels.return_value = []

        accommodation_agent(self._state())

        self.assertTrue(mock_service.return_value.search_hotels.call_args.kwargs["airport_iata"])


class CheckInDateTests(unittest.TestCase):
    """Search window follows the confirmed arrival rule for a day-1 departure."""

    def setUp(self):
        patcher = patch("agents.accommodation.resolve_city_iata_codes", return_value=["NRT"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, outbound):
        service = MagicMock()
        service.search_hotels.return_value = []
        state = {
            "destination": "Tokyo",
            "days": 4,
            "constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": outbound, "inbound": []}},
            "accommodation_options": [],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
            "start_date": "2026-11-17",
            "end_date": "2026-11-21",
        }
        with patch("agents.accommodation.get_stayingapi_hotel_service", return_value=service):
            accommodation_agent(state)
        return service.search_hotels.call_args.kwargs

    def _leg(self, arrival_time, arrival_date):
        return {
            "depart_time": "23:00:00",
            "arrival_time": arrival_time,
            "departure_date": "2026-11-17",
            "arrival_date": arrival_date,
        }

    def test_small_hours_arrival_keeps_day_1_check_in(self):
        kwargs = self._run([self._leg("02:30:00", "2026-11-18")])
        self.assertEqual(kwargs["check_in_date"], "2026-11-17")

    def test_morning_arrival_shifts_check_in_to_day_2(self):
        kwargs = self._run([self._leg("06:30:00", "2026-11-18")])
        self.assertEqual(kwargs["check_in_date"], "2026-11-18")

    def test_check_out_stays_on_the_trip_end_date(self):
        kwargs = self._run([self._leg("06:30:00", "2026-11-18")])
        self.assertEqual(kwargs["check_out_date"], "2026-11-21")


if __name__ == "__main__":
    unittest.main()
