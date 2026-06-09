import unittest
from unittest.mock import patch, MagicMock

from agents.attraction import attraction_agent


class _StubAmadeus:
    def __init__(self, activities):
        self._activities = activities

    def search_activities(self, **kwargs):
        return self._activities


class _StubLLM:
    def __init__(self, activities):
        self._activities = activities

    def generate_itinerary(self, **kwargs):
        return self._activities


def _make_activity(name="Test Activity", cost=30):
    return {
        "name": name, "type": "cultural", "estimated_cost": cost,
        "currency": "USD", "time_slot": "morning", "area": "Tokyo",
        "supplier": "amadeus", "reason": "test"
    }


class AttractionAgentTests(unittest.TestCase):
    def _base_state(self):
        return {
            "destination": "Tokyo",
            "days": 3,
            "constraints": {},
            "transport_options": [{"arrival_time": "18:00:00"}],
            "accommodation_options": [{"area": "Shinjuku"}],
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_generates_multi_day_itinerary(self, mock_amadeus, mock_llm):
        activities = [_make_activity(f"Activity {i}") for i in range(6)]
        mock_amadeus.return_value = _StubAmadeus(activities)
        mock_llm.return_value = _StubLLM([])

        state = self._base_state()
        state["days"] = 3
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        self.assertEqual(len(itinerary), 3)
        for entry in itinerary:
            self.assertIn("day", entry)
            self.assertIn("activities", entry)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_respects_must_go_places(self, mock_amadeus, mock_llm):
        activities = [
            _make_activity("Senso-ji Temple", cost=0),
            _make_activity("Tokyo Tower", cost=20),
            _make_activity("Shibuya Crossing", cost=0),
        ]
        mock_amadeus.return_value = _StubAmadeus(activities)
        mock_llm.return_value = _StubLLM([])

        state = self._base_state()
        state["constraints"] = {
            "attraction": {
                "preference": {"must_go_places": ["Senso-ji"], "styles": None}
            }
        }
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        all_names = [
            act["name"]
            for day in itinerary
            for act in day["activities"]
        ]
        self.assertIn("Senso-ji Temple", all_names)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_respects_budget_constraint(self, mock_amadeus, mock_llm):
        # Stub returns only the $20 activity (as if the service filtered out $100)
        activities = [_make_activity("Cheap Activity", cost=20)]
        mock_amadeus.return_value = _StubAmadeus(activities)
        mock_llm.return_value = _StubLLM([])

        state = self._base_state()
        state["constraints"] = {
            "attraction": {
                "budget": {"max_price_per_ticket": 50}
            }
        }
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        all_costs = [
            act["estimated_cost"]
            for day in itinerary
            for act in day["activities"]
            if act["estimated_cost"] is not None
        ]
        for cost in all_costs:
            self.assertLessEqual(cost, 50)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_late_arrival_adjusts_day1(self, mock_amadeus, mock_llm):
        activities = [_make_activity(f"Activity {i}") for i in range(8)]
        mock_amadeus.return_value = _StubAmadeus(activities)
        mock_llm.return_value = _StubLLM([])

        state = self._base_state()
        state["days"] = 3
        state["transport_options"] = [{"arrival_time": "22:00:00"}]
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        day1 = itinerary[0]
        self.assertLessEqual(len(day1["activities"]), 2)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_falls_back_to_llm_when_amadeus_empty(self, mock_amadeus, mock_llm):
        llm_activities = [_make_activity(f"LLM Activity {i}") for i in range(4)]
        mock_amadeus.return_value = _StubAmadeus([])
        llm_stub = MagicMock()
        llm_stub.generate_itinerary.return_value = llm_activities
        mock_llm.return_value = llm_stub

        state = self._base_state()
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        self.assertGreater(len(itinerary), 0)
        self.assertEqual(llm_stub.generate_itinerary.call_count, 1)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_falls_back_to_static_when_both_fail(self, mock_amadeus, mock_llm):
        mock_amadeus.return_value = _StubAmadeus([])
        llm_stub = MagicMock()
        llm_stub.generate_itinerary.return_value = []
        mock_llm.return_value = llm_stub

        state = self._base_state()
        state["days"] = 2
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        self.assertEqual(len(itinerary), 2)
        for day in itinerary:
            self.assertGreaterEqual(len(day["activities"]), 1)

    @patch("agents.attraction.get_attraction_llm_fallback_service")
    @patch("agents.attraction.get_amadeus_attraction_service")
    def test_uses_hotel_area(self, mock_amadeus, mock_llm):
        activities = [_make_activity(f"Activity {i}") for i in range(4)]
        amadeus_stub = MagicMock()
        amadeus_stub.search_activities.return_value = activities
        mock_amadeus.return_value = amadeus_stub
        mock_llm.return_value = _StubLLM([])

        state = self._base_state()
        state["accommodation_options"] = [{"area": "Shinjuku"}]
        result = attraction_agent(state)
        itinerary = result["itinerary"]

        self.assertGreater(len(itinerary), 0)
        call_kwargs = amadeus_stub.search_activities.call_args.kwargs
        self.assertEqual(call_kwargs.get("hotel_area"), "Shinjuku")


if __name__ == "__main__":
    unittest.main()
