import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
