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


class SplitCandidatesTests(unittest.TestCase):
    def test_empty_list(self):
        from agents.air_ticket import _split_candidates
        self.assertEqual(_split_candidates([]), ([], False, False))

    def test_all_valid(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API result"}, {"reason": "Amadeus API result"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(len(valid), 2)
        self.assertFalse(has_errors)
        self.assertFalse(all_errors)

    def test_all_errors(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API error"}, {"reason": "Unknown error"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(valid, [])
        self.assertTrue(has_errors)
        self.assertTrue(all_errors)

    def test_mixed(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Amadeus API result"}, {"reason": "Amadeus API error"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(len(valid), 1)
        self.assertTrue(has_errors)
        self.assertFalse(all_errors)


class ResolveOneWayDirectionTests(unittest.TestCase):
    def _candidate(self, price, airline="CX"):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": airline, "price": price, "depart_time": "10:00:00"}],
            "inbound_legs": None, "stops_outbound": 0, "stops_inbound": None,
            "reason": "Amadeus API result",
        }

    def test_no_candidates_returns_no_results_message(self):
        from agents.air_ticket import _resolve_one_way_direction
        result = _resolve_one_way_direction([], {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("change your", result[0]["reason"].lower())

    def test_all_errors_returns_error_template(self):
        from agents.air_ticket import _resolve_one_way_direction
        candidates = [{"reason": "Amadeus API error"}, {"reason": "Unknown error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("Amadeus API error (or unknown error)", result[0]["reason"])

    @patch("agents.air_ticket.select_flights")
    def test_normal_selection_uses_llm_index(self, mock_select):
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 1, "inbound_index": None, "reason": "Cheapest"}
        candidates = [self._candidate(500), self._candidate(300)]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["price"], 300)
        self.assertEqual(result[0]["reason"], "Cheapest")

    @patch("agents.air_ticket.select_flights")
    def test_partial_errors_appends_warning(self, mock_select):
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Best available"}
        candidates = [self._candidate(500), {"reason": "Amadeus API error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertIn("Best available", result[0]["reason"])
        self.assertIn("API errors during the run", result[0]["reason"])

    @patch("agents.air_ticket.select_flights")
    def test_inbound_direction_still_reads_outbound_legs_key(self, mock_select):
        """One-way search candidates always store their single leg list under "outbound_legs",
        even when the caller is searching the inbound direction (Amadeus doesn't know about our
        outbound/inbound relabeling for one-way calls). This locks in that behavior."""
        from agents.air_ticket import _resolve_one_way_direction
        mock_select.return_value = {"round_trip_index": None, "outbound_index": None, "inbound_index": 0, "reason": "Only option"}
        candidates = [self._candidate(300, airline="UO")]
        result = _resolve_one_way_direction(candidates, {}, "inbound")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["airline"], "UO")
        self.assertEqual(result[0]["reason"], "Only option")


class ResolveRoundTripTests(unittest.TestCase):
    def _round_trip_candidate(self, price):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "10:00:00"}],
            "inbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "19:00:00"}],
            "stops_outbound": 0, "stops_inbound": 0,
            "reason": "Amadeus API result",
        }

    def test_empty_candidates_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        self.assertIsNone(_resolve_round_trip([]))

    def test_all_errors_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        candidates = [{"reason": "Amadeus API error"}]
        self.assertIsNone(_resolve_round_trip(candidates))

    @patch("agents.air_ticket.select_flights")
    def test_normal_selection_splits_into_outbound_and_inbound(self, mock_select):
        from agents.air_ticket import _resolve_round_trip
        mock_select.return_value = {"round_trip_index": 0, "outbound_index": None, "inbound_index": None, "reason": "Good value"}
        candidates = [self._round_trip_candidate(600)]
        result = _resolve_round_trip(candidates)
        self.assertIsNotNone(result)
        outbound_legs, inbound_legs = result
        self.assertEqual(len(outbound_legs), 1)
        self.assertEqual(len(inbound_legs), 1)
        self.assertEqual(outbound_legs[0]["reason"], "Good value")
        self.assertEqual(inbound_legs[0]["reason"], "Good value")


if __name__ == "__main__":
    unittest.main()
