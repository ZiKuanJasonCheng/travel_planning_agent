import unittest
from unittest.mock import MagicMock, patch


class AttractionTransportReadTests(unittest.TestCase):
    @patch("agents.attraction.get_llm_itinerary_service")
    def test_arrival_time_from_last_outbound_leg(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.generate_itinerary.return_value = [{"day": 1, "activities": []}]
        mock_get_svc.return_value = mock_svc

        from agents.attraction import attraction_agent
        state = {
            "destination": "Tokyo", "origin": "Hong Kong", "days": 3, "num_people": 2,
            "start_date": "2026-09-10", "constraints": {},
            "transport_options": {
                "railway": [],
                "flight": {
                    "outbound": [
                        {"airline": "CX", "arrival_time": "12:00:00"},
                        {"airline": "CX", "arrival_time": "14:00:00"},
                    ],
                    "inbound": [{"airline": "CX", "depart_time": "19:00:00"}],
                },
            },
            "accommodation_options": [{"area": "Shinjuku"}],
            "itinerary": None, "checker_critique": None,
            "log_trace": False, "traces": [], "dirty_agents": [],
        }
        attraction_agent(state)

        call_kwargs = mock_svc.generate_itinerary.call_args.kwargs
        self.assertEqual(call_kwargs["arrival_time"], "14:00:00")
        self.assertEqual(call_kwargs["return_depart_time"], "19:00:00")


if __name__ == "__main__":
    unittest.main()
