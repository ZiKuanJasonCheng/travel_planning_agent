import unittest
from unittest.mock import MagicMock, patch


class ResolvePreferenceTests(unittest.TestCase):
    @patch("agents.air_ticket.resolve_airline_iata_codes", side_effect=lambda names: [n.upper()[:2] for n in names])
    def test_resolves_airlines_and_defaults(self, mock_resolve):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({
            "airlines": ["cathay"], "max_price_per_ticket": 500,
            "direct_flights_only": True,
        })
        self.assertEqual(result["airlines"], ["CA"])
        self.assertEqual(result["max_price_per_ticket"], 500)
        self.assertTrue(result["direct_flights_only"])
        self.assertTrue(result["accept_redeye_flights"])  # no timeslots set -> defaults True

    def test_none_input_returns_empty_dict(self):
        from agents.air_ticket import _resolve_preference
        self.assertEqual(_resolve_preference(None), {})

    def test_redeye_defaults_false_when_timeslots_set(self):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({"preferred_departure_timeslots": ["09:00~11:59"]})
        self.assertFalse(result["accept_redeye_flights"])

    def test_explicit_redeye_flag_is_respected(self):
        from agents.air_ticket import _resolve_preference
        result = _resolve_preference({
            "preferred_departure_timeslots": ["09:00~11:59"], "accept_redeye_flights": True,
        })
        self.assertTrue(result["accept_redeye_flights"])


class SearchModeTests(unittest.TestCase):
    def test_new_trip_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": None}
        self.assertEqual(_search_mode(state, {}), "full")

    def test_rerun_planning_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": "please try again"}
        self.assertEqual(_search_mode(state, {"rerun_planning": True}), "full")

    def test_outbound_only_when_only_outbound_preference_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers on the way there",
            "last_feedback_constraints": {"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
        }
        self.assertEqual(_search_mode(state, {}), "outbound_only")

    def test_inbound_only_when_only_inbound_preference_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class on the way back",
            "last_feedback_constraints": {"transport": {"inbound_air_ticket_preference": {"flight_class": "BUSINESS"}}},
        }
        self.assertEqual(_search_mode(state, {}), "inbound_only")

    def test_both_one_way_when_both_preferences_changed(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers either way",
            "last_feedback_constraints": {"transport": {
                "outbound_air_ticket_preference": {"direct_flights_only": True},
                "inbound_air_ticket_preference": {"direct_flights_only": True},
            }},
        }
        self.assertEqual(_search_mode(state, {}), "both_one_way")

    def test_none_when_feedback_unrelated_to_transport(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "last_feedback_constraints": {"attraction": {"preference": {"styles": ["museum"]}}},
        }
        self.assertEqual(_search_mode(state, {}), "none")


if __name__ == "__main__":
    unittest.main()
