import unittest
from unittest.mock import MagicMock, patch

from orchestration.merge_constraints import UNLIMITED_PRICE


class FillUnlimitedPriceTests(unittest.TestCase):
    def test_fills_unlimited_when_direction_preference_exists_but_price_unset(self):
        from agents.air_ticket import _fill_unlimited_price
        merged_transport = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        result = _fill_unlimited_price(merged_transport)
        self.assertEqual(
            result["outbound_air_ticket_preference"]["max_price_per_ticket"], UNLIMITED_PRICE
        )

    def test_leaves_real_price_untouched(self):
        from agents.air_ticket import _fill_unlimited_price
        merged_transport = {"outbound_air_ticket_preference": {"max_price_per_ticket": 500}}
        result = _fill_unlimited_price(merged_transport)
        self.assertEqual(result["outbound_air_ticket_preference"]["max_price_per_ticket"], 500)

    def test_leaves_absent_direction_preference_untouched(self):
        """A direction with no preference at all (None) must stay None — filling
        in a price-only preference dict out of nothing would falsely register
        as a change for that direction in _search_mode's per-direction diff."""
        from agents.air_ticket import _fill_unlimited_price
        merged_transport = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        result = _fill_unlimited_price(merged_transport)
        self.assertIsNone(result.get("inbound_air_ticket_preference"))

    def test_fills_both_directions_independently(self):
        from agents.air_ticket import _fill_unlimited_price
        merged_transport = {
            "outbound_air_ticket_preference": {"max_price_per_ticket": 500},
            "inbound_air_ticket_preference": {"flight_class": "business"},
        }
        result = _fill_unlimited_price(merged_transport)
        self.assertEqual(result["outbound_air_ticket_preference"]["max_price_per_ticket"], 500)
        self.assertEqual(
            result["inbound_air_ticket_preference"]["max_price_per_ticket"], UNLIMITED_PRICE
        )


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
        self.assertEqual(_search_mode(state, {}, {}), "full")

    def test_rerun_planning_returns_full(self):
        from agents.air_ticket import _search_mode
        state = {"feedback": "please try again"}
        self.assertEqual(_search_mode(state, {}, {"rerun_planning": True}), "full")

    def test_outbound_only_when_merged_outbound_preference_differs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers on the way there",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {"outbound_air_ticket_preference": {"direct_flights_only": True}}
        self.assertEqual(_search_mode(state, existing, merged), "outbound_only")

    def test_inbound_only_when_merged_inbound_preference_differs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class on the way back",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {"inbound_air_ticket_preference": {"flight_class": "BUSINESS"}}
        self.assertEqual(_search_mode(state, existing, merged), "inbound_only")

    def test_full_when_both_directions_differ(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "no layovers either way",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {}
        merged = {
            "outbound_air_ticket_preference": {"direct_flights_only": True},
            "inbound_air_ticket_preference": {"direct_flights_only": True},
        }
        self.assertEqual(_search_mode(state, existing, merged), "full")

    def test_none_when_restating_an_already_satisfied_preference(self):
        """The core bug this upgrade fixes: mentioning a preference whose merged
        value is identical to what's already there must NOT trigger a re-search."""
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "business class please",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        merged = {"outbound_air_ticket_preference": {"flight_class": "business"}}
        self.assertEqual(_search_mode(state, existing, merged), "none")

    def test_none_when_feedback_unrelated_to_transport(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        self.assertEqual(_search_mode(state, {}, {}), "none")

    def test_full_when_transport_type_switched_to_flight_with_no_existing_legs(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "actually let's fly instead",
            "transport_options": {"flight": {"outbound": [], "inbound": []}},
        }
        existing = {"transport_type": "train"}
        merged = {"transport_type": "flight"}
        self.assertEqual(_search_mode(state, existing, merged), "full")

    def test_none_when_transport_type_switched_but_flights_already_exist(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "actually let's fly instead",
            "transport_options": {"flight": {"outbound": [{"airline": "CX"}], "inbound": []}},
        }
        existing = {"transport_type": "train"}
        merged = {"transport_type": "flight"}
        self.assertEqual(_search_mode(state, existing, merged), "none")

    def test_outbound_only_when_last_run_had_full_error_on_outbound(self):
        from agents.air_ticket import _search_mode, _ERROR_MESSAGE
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {
                "flight": {
                    "outbound": [dict(_ERROR_MESSAGE)],
                    "inbound": [{"airline": "CX", "reason": "Good option"}],
                },
            },
        }
        self.assertEqual(_search_mode(state, {}, {}), "outbound_only")

    def test_inbound_only_when_last_run_had_partial_error_on_inbound(self):
        from agents.air_ticket import _search_mode
        state = {
            "feedback": "add a museum on day 2",
            "transport_options": {
                "flight": {
                    "outbound": [{"airline": "CX", "reason": "Good option"}],
                    "inbound": [{
                        "airline": "UO",
                        "reason": (
                            "Best available. There were a few API errors during the run. "
                            "Therefore, the selected flight might not be the best option. "
                            "You can wait for a few minutes and submit feedback saying "
                            "'Run transport/flight service again'."
                        ),
                    }],
                },
            },
        }
        self.assertEqual(_search_mode(state, {}, {}), "inbound_only")


class SplitCandidatesTests(unittest.TestCase):
    def test_empty_list(self):
        from agents.air_ticket import _split_candidates
        self.assertEqual(_split_candidates([]), ([], False, False))

    def test_all_valid(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Duffel API result"}, {"reason": "Duffel API result"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(len(valid), 2)
        self.assertFalse(has_errors)
        self.assertFalse(all_errors)

    def test_all_errors(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Duffel API error"}, {"reason": "Unknown error"}]
        valid, has_errors, all_errors = _split_candidates(candidates)
        self.assertEqual(valid, [])
        self.assertTrue(has_errors)
        self.assertTrue(all_errors)

    def test_mixed(self):
        from agents.air_ticket import _split_candidates
        candidates = [{"reason": "Duffel API result"}, {"reason": "Duffel API error"}]
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
            "reason": "Duffel API result",
        }

    def test_no_candidates_returns_no_results_message(self):
        from agents.air_ticket import _resolve_one_way_direction
        result = _resolve_one_way_direction([], {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("change your", result[0]["reason"].lower())

    def test_all_errors_returns_error_template(self):
        from agents.air_ticket import _resolve_one_way_direction
        candidates = [{"reason": "Duffel API error"}, {"reason": "Unknown error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertEqual(len(result), 1)
        self.assertIn("Duffel API error (or unknown error)", result[0]["reason"])

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
        candidates = [self._candidate(500), {"reason": "Duffel API error"}]
        result = _resolve_one_way_direction(candidates, {}, "outbound")
        self.assertIn("Best available", result[0]["reason"])
        self.assertIn("API errors during the run", result[0]["reason"])

    @patch("agents.air_ticket.select_flights")
    def test_inbound_direction_still_reads_outbound_legs_key(self, mock_select):
        """One-way search candidates always store their single leg list under "outbound_legs",
        even when the caller is searching the inbound direction (Duffel doesn't know about our
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
            "reason": "Duffel API result",
        }

    def test_empty_candidates_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        self.assertIsNone(_resolve_round_trip([]))

    def test_all_errors_returns_none(self):
        from agents.air_ticket import _resolve_round_trip
        candidates = [{"reason": "Duffel API error"}]
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


class AirTicketAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "agents.air_ticket.resolve_city_iata_codes",
            side_effect=lambda name: {"Hong Kong": ["HKG"], "Tokyo": ["NRT"]}.get(name, [name.upper()[:3]])
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _base_state(self, **overrides):
        state = {
            "destination": "Tokyo",
            "origin": "Hong Kong",
            "num_people": 1,
            "days": 5,
            "start_date": "2026-09-10",
            "end_date": "2026-09-16",
            "constraints": {},
            "new_constraints": {},
            "transport_options": {"railway": [], "flight": {"outbound": [], "inbound": []}},
            "feedback": None,
            "log_trace": False,
            "traces": [],
            "dirty_agents": [],
            "status": "planning",
        }
        state.update(overrides)
        return state

    def _round_trip_candidate(self, price=600):
        return {
            "price": price, "currency": "USD",
            "outbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "10:00:00", "arrival_time": "14:00:00"}],
            "inbound_legs": [{"airline": "CX", "price": price / 2, "depart_time": "19:00:00", "arrival_time": "23:00:00"}],
            "stops_outbound": 0, "stops_inbound": 0,
            "reason": "Duffel API result",
        }

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_new_trip_uses_round_trip_when_available(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = [self._round_trip_candidate()]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": 0, "outbound_index": None, "inbound_index": None, "reason": "Good option"}

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        mock_svc.search_flights.assert_called_once()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(len(flight["inbound"]), 1)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_new_trip_falls_back_to_one_way_pair_when_round_trip_empty(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        outbound_candidate = {**self._round_trip_candidate(400), "inbound_legs": None, "stops_inbound": None}
        inbound_candidate = {**self._round_trip_candidate(300), "outbound_legs": self._round_trip_candidate(300)["inbound_legs"], "inbound_legs": None, "stops_inbound": None}
        mock_svc.search_flights.side_effect = [[], [outbound_candidate], [inbound_candidate]]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Only option"}

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        self.assertEqual(mock_svc.search_flights.call_count, 3)
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(len(flight["inbound"]), 1)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_replanning_outbound_only_leaves_inbound_untouched(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        outbound_candidate = {**self._round_trip_candidate(400), "inbound_legs": None, "stops_inbound": None}
        mock_svc.search_flights.return_value = [outbound_candidate]
        mock_get_svc.return_value = mock_svc
        mock_select.return_value = {"round_trip_index": None, "outbound_index": 0, "inbound_index": None, "reason": "Better outbound"}

        existing_inbound = [{"airline": "UO", "price": 250, "depart_time": "19:00:00", "reason": "kept from before"}]
        state = self._base_state(
            feedback="no layovers on the way there",
            new_constraints={"transport": {"outbound_air_ticket_preference": {"direct_flights_only": True}}},
            transport_options={"railway": [], "flight": {"outbound": [], "inbound": existing_inbound}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_called_once()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(flight["inbound"], existing_inbound)

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_all_error_tier_produces_templated_message(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = [
            [{"type": "flight", "reason": "Duffel API error"}],  # round trip
            [{"type": "flight", "reason": "Duffel API error"}],  # outbound fallback
            [{"type": "flight", "reason": "Unknown error"}],      # inbound fallback
        ]
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        mock_select.assert_not_called()
        flight = new_state["transport_options"]["flight"]
        self.assertIn("Duffel API error (or unknown error)", flight["outbound"][0]["reason"])
        self.assertIn("Duffel API error (or unknown error)", flight["inbound"][0]["reason"])

    @patch("agents.air_ticket.get_flight_service")
    def test_unhandled_exception_produces_symmetric_error_message(self, mock_get_svc):
        """A raised (not tiered-candidate) exception during the search itself
        must not silently leave one direction empty while the other gets an
        explanatory message — both should get the same error-tier message."""
        mock_svc = MagicMock()
        mock_svc.search_flights.side_effect = RuntimeError("boom")
        mock_get_svc.return_value = mock_svc

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(self._base_state())

        flight = new_state["transport_options"]["flight"]
        self.assertEqual(len(flight["outbound"]), 1)
        self.assertEqual(len(flight["inbound"]), 1)
        self.assertIn("Duffel API error (or unknown error)", flight["outbound"][0]["reason"])
        self.assertIn("Duffel API error (or unknown error)", flight["inbound"][0]["reason"])

    @patch("agents.air_ticket.get_flight_service")
    def test_rerun_planning_is_reset_after_use(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_svc.search_flights.return_value = []
        mock_get_svc.return_value = mock_svc

        state = self._base_state(
            feedback="please try again",
            constraints={"transport": {"rerun_planning": True}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        self.assertIsNone(new_state["constraints"]["transport"]["rerun_planning"])

    @patch("agents.air_ticket.select_flights")
    @patch("agents.air_ticket.get_flight_service")
    def test_replanning_skips_when_restated_preference_already_satisfied(self, mock_get_svc, mock_select):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # "existing" already carries the unlimited-price sentinel, as a real prior
        # round's air_ticket_agent call would have persisted it via _fill_unlimited_price.
        existing_outbound = [{"airline": "CX", "flight_class": "business", "reason": "Good option"}]
        existing_inbound = [{"airline": "CX", "flight_class": "business", "reason": "Good option"}]
        state = self._base_state(
            feedback="business class please",
            constraints={"transport": {
                "outbound_air_ticket_preference": {"flight_class": "business", "max_price_per_ticket": 1_000_000_000},
                "inbound_air_ticket_preference": {"flight_class": "business", "max_price_per_ticket": 1_000_000_000},
            }},
            new_constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
            transport_options={"railway": [], "flight": {"outbound": existing_outbound, "inbound": existing_inbound}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_not_called()
        mock_select.assert_not_called()
        flight = new_state["transport_options"]["flight"]
        self.assertEqual(flight["outbound"], existing_outbound)
        self.assertEqual(flight["inbound"], existing_inbound)

    @patch("agents.air_ticket.get_flight_service")
    def test_skip_still_persists_merged_constraints(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # "existing" already carries the unlimited-price sentinel, as a real prior
        # round's air_ticket_agent call would have persisted it via _fill_unlimited_price.
        state = self._base_state(
            feedback="business class please",
            constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business", "max_price_per_ticket": 1_000_000_000}}},
            new_constraints={"transport": {"outbound_air_ticket_preference": {"flight_class": "business"}}},
        )

        from agents.air_ticket import air_ticket_agent
        new_state = air_ticket_agent(state)

        mock_svc.search_flights.assert_not_called()
        self.assertEqual(
            new_state["constraints"]["transport"]["outbound_air_ticket_preference"],
            {"flight_class": "business", "max_price_per_ticket": 1_000_000_000},
        )


if __name__ == "__main__":
    unittest.main()
