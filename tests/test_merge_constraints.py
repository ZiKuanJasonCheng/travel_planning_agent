import unittest


class MergeConstraintsNoneHandlingTests(unittest.TestCase):
    """A None value in new_constraints means the user gave no constraint for
    that field this round — it must not blank out an existing value."""

    def test_none_new_value_does_not_overwrite_existing_value(self):
        from orchestration.merge_constraints import merge_constraints
        old = {"direct_flights_only": True, "flight_class": "business"}
        new = {"direct_flights_only": None, "accept_redeye_flights": False}
        merged = merge_constraints(old, new)
        self.assertEqual(merged, {
            "direct_flights_only": True,
            "flight_class": "business",
            "accept_redeye_flights": False,
        })

    def test_none_new_value_over_absent_old_value_stays_absent(self):
        from orchestration.merge_constraints import merge_constraints
        old = {"flight_class": "business"}
        new = {"direct_flights_only": None}
        merged = merge_constraints(old, new)
        self.assertNotIn("direct_flights_only", merged)

    def test_non_none_new_value_still_overwrites(self):
        from orchestration.merge_constraints import merge_constraints
        old = {"direct_flights_only": True}
        new = {"direct_flights_only": False}
        merged = merge_constraints(old, new)
        self.assertFalse(merged["direct_flights_only"])

    def test_reported_bug_scenario_outbound_direct_flights_preserved(self):
        """Regression for a reported bug: round 1 sets direct_flights_only=True
        for outbound only. Round 2's feedback only mentions red-eye flights and
        flight class; the LLM parser leaves direct_flights_only null for both
        legs since it wasn't restated. The merge must not reset outbound's
        True back to a false-y value just because this round's parse carried
        an explicit null for it."""
        from orchestration.merge_constraints import merge_constraints
        existing_outbound = {
            "airlines": ["Cathay Airlines"], "flight_class": "business",
            "excluded_airlines": ["JetStar"], "direct_flights_only": True,
            "max_price_per_ticket": None, "accept_redeye_flights": False,
            "preferred_departure_timeslots": None,
        }
        new_outbound = {
            "flight_class": "business", "accept_redeye_flights": False,
            "direct_flights_only": None,
        }
        merged = merge_constraints(existing_outbound, new_outbound)
        self.assertTrue(merged["direct_flights_only"])


class ResolveCategoryConstraintsTests(unittest.TestCase):
    def test_new_trip_always_unchanged_false(self):
        """feedback is None (brand-new trip) always forces unchanged=False,
        even when both existing and new are empty."""
        from orchestration.merge_constraints import resolve_category_constraints
        state = {"feedback": None, "constraints": {}, "new_constraints": {}}
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {})
        self.assertFalse(unchanged)

    def test_replanning_with_identical_merge_is_unchanged(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {
            "feedback": "business class please",
            "constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
            "new_constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
        }
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {"preference": {"max_price_per_night": 200}})
        self.assertTrue(unchanged)

    def test_replanning_with_differing_merge_is_changed(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {
            "feedback": "actually make it Shibuya",
            "constraints": {"accommodation": {"preference": {"max_price_per_night": 200}}},
            "new_constraints": {"accommodation": {"preference": {"area": "Shibuya"}}},
        }
        merged, unchanged = resolve_category_constraints(state, "accommodation")
        self.assertEqual(merged, {"preference": {"max_price_per_night": 200, "area": "Shibuya"}})
        self.assertFalse(unchanged)

    def test_missing_category_defaults_to_empty_dicts(self):
        from orchestration.merge_constraints import resolve_category_constraints
        state = {"feedback": "some feedback", "constraints": {}, "new_constraints": {}}
        merged, unchanged = resolve_category_constraints(state, "attraction")
        self.assertEqual(merged, {})
        self.assertTrue(unchanged)

    def test_explicit_none_constraints_does_not_crash(self):
        """A key present but set to None (as opposed to absent) must not crash —
        .get(key, default) only substitutes the default when the key is missing,
        not when its value is falsy, so this must be handled explicitly."""
        from orchestration.merge_constraints import resolve_category_constraints
        state = {"feedback": "some feedback", "constraints": None, "new_constraints": None}
        merged, unchanged = resolve_category_constraints(state, "attraction")
        self.assertEqual(merged, {})
        self.assertTrue(unchanged)


if __name__ == "__main__":
    unittest.main()
