import unittest


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


if __name__ == "__main__":
    unittest.main()
