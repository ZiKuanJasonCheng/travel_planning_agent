import unittest
from unittest.mock import patch, MagicMock

from agents.accommodation import accommodation_agent


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

    @patch("agents.accommodation.get_booking_hotel_service")
    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_uses_amadeus_results_as_primary(self, mock_amadeus, mock_booking):
        mock_amadeus.return_value = _StubSupplier(
            [
                {
                    "type": "hotel",
                    "name": "Amadeus Grand Tokyo",
                    "price_per_night": 180,
                    "currency": "USD",
                    "area": "Shinjuku",
                    "supplier": "amadeus",
                },
                # {
                #     "type": "hotel",
                #     "name": "Amadeus Grand Kyotp",
                #     "price_per_night": 900,
                #     "currency": "USD",
                #     "area": "Kyoto",
                #     "supplier": "amadeus",
                # }
            ]
        )
        mock_booking.return_value = _StubSupplier([])

        state = self._base_state()
        new_state = accommodation_agent(state)
        print(f"new_state: {new_state}")

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertEqual(new_state["accommodation_options"][0]["supplier"], "amadeus")
        self.assertEqual(new_state["accommodation_options"][0]["name"], "Amadeus Grand Tokyo")

    # Outdated: Booking.com fallback is currently disabled in accommodation_agent.
#     @patch("agents.accommodation.get_booking_hotel_service")
#     @patch("agents.accommodation.get_amadeus_hotel_service")
#     def test_falls_back_to_booking_when_amadeus_has_no_results(self, mock_amadeus, mock_booking):
#         mock_amadeus.return_value = _StubSupplier([])
#         mock_booking.return_value = _StubSupplier(
#             [
#                 {
#                     "type": "hotel",
#                     "name": "Booking Central Tokyo",
#                     "price_per_night": 160,
#                     "currency": "USD",
#                     "area": "Ueno",
#                     "supplier": "booking",
#                 }
#             ]
#         )
#
#         state = self._base_state()
#         new_state = accommodation_agent(state)
#
#         self.assertEqual(len(new_state["accommodation_options"]), 1)
#         self.assertEqual(new_state["accommodation_options"][0]["supplier"], "booking")
#         self.assertEqual(new_state["accommodation_options"][0]["name"], "Booking Central Tokyo")

    @patch("agents.accommodation.get_booking_hotel_service")
    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_uses_static_fallback_if_all_suppliers_fail(self, mock_amadeus, mock_booking):
        mock_amadeus.return_value = _StubSupplier([])
        mock_booking.return_value = _StubSupplier([])

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
                {"type": "hotel", "name": "Existing Hotel", "reason": "Amadeus hotel offer"}
            ],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_skips_when_constraints_unchanged(self, mock_amadeus):
        from agents.accommodation import accommodation_agent
        state = self._base_state()
        new_state = accommodation_agent(state)

        mock_amadeus.assert_not_called()
        self.assertEqual(new_state["accommodation_options"], state["accommodation_options"])
        self.assertEqual(
            new_state["constraints"]["accommodation"],
            {"preference": {"max_price_per_night": 150}},
        )

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_new_constraints_add_something(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}}
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()
        self.assertEqual(
            new_state["constraints"]["accommodation"]["preference"]["area"], "Shibuya"
        )

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_rerun_planning_true_even_if_unchanged(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
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

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_replans_when_last_run_had_errors_even_if_unchanged(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            accommodation_options=[
                {
                    "reason": (
                        "There's an Amadeus Hotel API error (or unknown error) at the moment. "
                        "Please wait for a few minutes and submit a feedback saying "
                        "'Run accommodation service again'."
                    )
                }
            ],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_new_trip_always_plans_even_with_no_preferences(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = []
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(
            feedback=None, constraints={}, new_constraints={}, accommodation_options=[],
        )
        new_state = accommodation_agent(state)

        mock_service.search_hotels.assert_called_once()

    @patch("agents.accommodation.get_amadeus_hotel_service")
    def test_real_api_error_produces_distinct_error_message(self, mock_amadeus):
        mock_service = MagicMock()
        mock_service.search_hotels.return_value = [{"reason": "Amadeus Hotel API error"}]
        mock_amadeus.return_value = mock_service

        from agents.accommodation import accommodation_agent
        state = self._base_state(new_constraints={"accommodation": {"preference": {"area": "Shibuya"}}})
        new_state = accommodation_agent(state)

        self.assertEqual(len(new_state["accommodation_options"]), 1)
        self.assertIn(
            "Amadeus Hotel API error (or unknown error)",
            new_state["accommodation_options"][0]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
