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


if __name__ == "__main__":
    unittest.main()
