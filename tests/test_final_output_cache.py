import time
import unittest
from unittest.mock import patch

from agents.final_output import final_output_node
from api.apis import RequestModel, start_trip
from orchestration import final_output_cache


def _trip_state(**overrides):
    """A state shaped like the one /trip/start hands to the graph."""
    state = {
        "destination": "Tokyo",
        "origin": "San Francisco",
        "num_people": 2,
        "days": 3,
        "start_date": "2026-11-01",
        "end_date": "2026-11-04",
        "preferences": ["food tours", "museums"],
        "new_constraints": {"attraction": {"preference": {"styles": ["food"]}}},
        "constraints": {},
        "transport_options": {
            "railway": [],
            "flight": {
                "outbound": [{"airline": "CX", "reason": "Duffel API result"}],
                "inbound": [{"airline": "CX", "reason": "Duffel API result"}],
            },
        },
        "accommodation_options": [{"name": "Hotel Sakura", "reason": "Mock hotel data"}],
        "itinerary": [
            {"day": 1, "reason": "LLM generated itinerary",
             "activities": [{"name": "Senso-ji", "reason": "LLM generated activity"}]},
        ],
        "checker_critique": None,
        "log_trace": False,
    }
    state.update(overrides)
    return state


class FinalOutputCacheKeyTests(unittest.TestCase):

    def test_different_preference_wording_with_same_constraints_shares_a_key(self):
        """Preferences are only a means to constraints; two phrasings that parse
        to the same constraints describe the same trip."""
        constraints = {"accommodation": {"preference": {"max_price_per_night": 120}}}
        a = final_output_cache.build_cache_key(
            _trip_state(preferences=["cheap hotels"], new_constraints=constraints)
        )
        b = final_output_cache.build_cache_key(
            _trip_state(preferences=["budget-friendly accommodation"], new_constraints=constraints)
        )
        self.assertEqual(a, b)

    def test_constraint_key_order_does_not_change_the_key(self):
        a = final_output_cache.build_cache_key(
            _trip_state(new_constraints={"attraction": {"styles": ["food"]}, "transport": {"x": 1}})
        )
        b = final_output_cache.build_cache_key(
            _trip_state(new_constraints={"transport": {"x": 1}, "attraction": {"styles": ["food"]}})
        )
        self.assertEqual(a, b)

    def test_any_trip_input_changes_the_key(self):
        base = final_output_cache.build_cache_key(_trip_state())
        for field, value in (
            ("destination", "Kyoto"), ("origin", "Los Angeles"), ("num_people", 3),
            ("days", 4), ("start_date", "2026-12-01"), ("end_date", "2026-12-05"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(base, final_output_cache.build_cache_key(_trip_state(**{field: value})))

    def test_different_constraints_change_the_key(self):
        base = final_output_cache.build_cache_key(_trip_state())
        changed = final_output_cache.build_cache_key(
            _trip_state(new_constraints={"accommodation": {"preference": {"max_price_per_night": 90}}})
        )
        self.assertNotEqual(base, changed)


class CacheabilityGateTests(unittest.TestCase):

    def test_clean_new_trip_is_cacheable(self):
        self.assertTrue(final_output_cache.is_cacheable(_trip_state()))

    def test_feedback_round_is_not_cacheable(self):
        self.assertFalse(final_output_cache.is_cacheable(_trip_state(feedback=["change hotel"])))

    def test_closing_reminder_is_not_cacheable_even_without_feedback(self):
        # A first feedback round that parses to nothing leaves feedback unset
        # but still builds a wrap-up payload.
        self.assertFalse(final_output_cache.is_cacheable(_trip_state(), closing_reminder=True))

    def test_unresolved_checker_critique_is_not_cacheable(self):
        self.assertFalse(final_output_cache.is_cacheable(_trip_state(checker_critique="Fix day 2")))

    def test_checker_llm_failure_marker_is_not_cacheable(self):
        from agents.checker import LLM_FAILED_CRITIQUE
        self.assertFalse(
            final_output_cache.is_cacheable(_trip_state(checker_critique=LLM_FAILED_CRITIQUE))
        )

    def test_degraded_reasons_block_caching(self):
        degraded_states = {
            "flight_api_error": _trip_state(transport_options={
                "railway": [],
                "flight": {"outbound": [{"reason": "Duffel API timeout"}], "inbound": []},
            }),
            "partial_flight_warning": _trip_state(transport_options={
                "railway": [],
                "flight": {"outbound": [{"reason": "Selected. There were a few API errors during the run."}],
                           "inbound": []},
            }),
            "fallback_hotel": _trip_state(accommodation_options=[
                {"name": "Fallback Hotel Option", "reason": "No supplier inventory returned"},
            ]),
            "hotel_api_error": _trip_state(accommodation_options=[{"reason": "StayingAPI Hotel API error"}]),
            "static_itinerary": _trip_state(itinerary=[
                {"day": 1, "activities": [{"name": "Walk", "reason": "Static fallback"}]},
            ]),
            "itinerary_llm_error": _trip_state(itinerary=[{"day": None, "activities": [], "reason": "LLM API error"}]),
        }
        for label, state in degraded_states.items():
            with self.subTest(case=label):
                self.assertFalse(final_output_cache.is_cacheable(state))

    def test_true_empties_are_cacheable(self):
        state = _trip_state(
            transport_options={"railway": [], "flight": {"outbound": [], "inbound": []}},
            accommodation_options=[],
            itinerary=[],
        )
        self.assertTrue(final_output_cache.is_cacheable(state))

    def test_no_results_are_cacheable(self):
        """A search that ran and found nothing is a valid result, not an error.
        The reason tells the traveler to change preferences, which yields a
        different cache key."""
        state = _trip_state(transport_options={
            "railway": [],
            "flight": {
                "outbound": [{"reason": "No suitable flights were found. "
                                        "Please change your flight preferences and submit feedback again."}],
                "inbound": [],
            },
        })
        self.assertTrue(final_output_cache.is_cacheable(state))


class FinalOutputNodeStoreTests(unittest.TestCase):

    def setUp(self):
        final_output_cache.clear_cache()

    def tearDown(self):
        final_output_cache.clear_cache()

    @patch("agents.final_output.generate_reminder", return_value="Enjoy Tokyo!")
    def test_clean_run_stores_the_payload(self, mock_reminder):
        state = _trip_state(session_id="sess-1")

        result = final_output_node(state)

        cached = final_output_cache.get_cached_final_output(state)
        self.assertEqual(cached, result["final_output"])

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_failed_or_empty_reminder_still_stores(self, mock_reminder):
        state = _trip_state(session_id="sess-1")

        final_output_node(state)

        cached = final_output_cache.get_cached_final_output(state)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["reminder"], "")

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_checker_failure_does_not_store(self, mock_reminder):
        from agents.checker import LLM_FAILED_CRITIQUE
        state = _trip_state(checker_critique=LLM_FAILED_CRITIQUE)

        final_output_node(state)

        self.assertIsNone(final_output_cache.get_cached_final_output(state))

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_degraded_item_does_not_store(self, mock_reminder):
        state = _trip_state(
            accommodation_options=[{"name": "Fallback Hotel Option", "reason": "No supplier inventory returned"}]
        )

        final_output_node(state)

        self.assertIsNone(final_output_cache.get_cached_final_output(state))

    @patch("agents.final_output.generate_reminder", return_value="Wrap-up!")
    def test_closing_reminder_does_not_store(self, mock_reminder):
        state = _trip_state()

        final_output_node(state, closing_reminder=True)

        self.assertIsNone(final_output_cache.get_cached_final_output(state))

    @patch("agents.final_output.generate_reminder", return_value="")
    def test_feedback_round_does_not_store(self, mock_reminder):
        state = _trip_state(feedback=["make it cheaper"])

        final_output_node(state)

        self.assertIsNone(final_output_cache.get_cached_final_output(state))

    @patch("agents.final_output.generate_reminder", return_value="Enjoy!")
    def test_cached_payload_is_isolated_from_later_mutation(self, mock_reminder):
        state = _trip_state()
        result = final_output_node(state)

        first_read = final_output_cache.get_cached_final_output(state)
        first_read["itinerary"].append({"day": 99})
        first_read["transport_options"]["flight"]["outbound"].clear()

        second_read = final_output_cache.get_cached_final_output(state)
        self.assertEqual(second_read["itinerary"], result["final_output"]["itinerary"])
        self.assertEqual(
            second_read["transport_options"]["flight"]["outbound"],
            result["final_output"]["transport_options"]["flight"]["outbound"],
        )


class CacheExpiryTests(unittest.TestCase):

    def setUp(self):
        final_output_cache.clear_cache()
        self.state = _trip_state()
        self.state.update({"session_id": "sess-1", "reminder": "hi"})

    def tearDown(self):
        final_output_cache.clear_cache()

    def test_entry_expires_after_ttl(self):
        final_output_cache.store_final_output(self.state, {"session_id": "sess-1", "reminder": "hi"})
        key = final_output_cache.build_cache_key(self.state)

        with patch.object(final_output_cache.time, "time",
                          return_value=time.time() + final_output_cache._TTL_SECONDS + 1):
            self.assertIsNone(final_output_cache.get_cached_final_output(self.state))

        self.assertNotIn(key, final_output_cache._cache)

    def test_entry_is_retained_within_ttl(self):
        final_output_cache.store_final_output(self.state, {"session_id": "sess-1", "reminder": "hi"})

        with patch.object(final_output_cache.time, "time",
                          return_value=time.time() + final_output_cache._TTL_SECONDS - 1):
            self.assertIsNotNone(final_output_cache.get_cached_final_output(self.state))


class StartTripCacheHitTests(unittest.TestCase):

    def setUp(self):
        final_output_cache.clear_cache()

    def tearDown(self):
        final_output_cache.clear_cache()

    def _param(self, **overrides):
        params = dict(
            destination="Tokyo",
            origin="San Francisco",
            num_people=2,
            days=3,
            preferences=["food tours", "museums"],
            start_date="2026-11-01",
        )
        params.update(overrides)
        # City-granularity validation is a separate concern; the live
        # geocoder rejects "Tokyo" as a province rather than a city.
        with patch("api.apis.check_city_granularity"):
            return RequestModel(**params)

    def _seed(self, param):
        """Run a clean final_output_node over the state start_trip would build."""
        state = _trip_state(
            destination=param.destination,
            origin=param.origin,
            num_people=param.num_people,
            days=param.days,
            start_date=param.start_date,
            end_date=param.end_date,
            preferences=param.preferences,
            constraints={},
            new_constraints={},
            session_id="original-session",
        )
        with patch("agents.final_output.generate_reminder", return_value="Enjoy!"):
            final_output_node(state)
        return state

    def test_matching_new_trip_skips_the_graph_and_renews_session_id(self):
        param = self._param()
        self._seed(param)

        persisted = {}

        with patch("api.apis.check_city_granularity"), \
             patch("api.apis.setup_request_logging", return_value="/tmp/fake.log"), \
             patch("api.apis.create_session", return_value="fresh-session"), \
             patch("api.apis.update_session", side_effect=lambda sid, s: persisted.update(s)), \
             patch("api.apis.run_until_needing_feedback_or_finished") as mock_run, \
             patch("api.apis.parse_feedback_with_llm", return_value=None):
            response = start_trip(param)

        mock_run.assert_not_called()
        self.assertEqual(response["session_id"], "fresh-session")
        self.assertEqual(response["status"], "is_waiting_for_feedback")
        self.assertEqual(response["destination"], "Tokyo")
        # The session really holds the plan, so a feedback round can diff it.
        self.assertEqual(persisted["session_id"], "fresh-session")
        self.assertEqual(persisted["status"], "is_waiting_for_feedback")
        self.assertEqual(persisted["dirty_agents"], [])
        self.assertEqual(persisted["constraints"], {})
        self.assertTrue(persisted["itinerary"])
        self.assertTrue(persisted["accommodation_options"])
        self.assertTrue(persisted["transport_options"]["flight"]["outbound"])

    def test_different_trip_is_not_served_from_cache(self):
        param = self._param()
        self._seed(param)

        other = self._param(destination="Kyoto")

        def fake_run(state):
            state["status"] = "completed"
            state["final_output"] = {"session_id": "new", "destination": "Kyoto", "reminder": ""}
            return state

        with patch("api.apis.check_city_granularity"), \
             patch("api.apis.setup_request_logging", return_value="/tmp/fake.log"), \
             patch("api.apis.create_session", return_value="new"), \
             patch("api.apis.update_session"), \
             patch("api.apis.parse_feedback_with_llm", return_value=None), \
             patch("api.apis.run_until_needing_feedback_or_finished", side_effect=fake_run) as mock_run:
            response = start_trip(other)

        mock_run.assert_called_once()
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["destination"], "Kyoto")


if __name__ == "__main__":
    unittest.main()
