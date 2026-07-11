import unittest

from states.accommodation_constraints import AccommodationConstraint


class AccommodationConstraintTests(unittest.TestCase):
    def test_rerun_planning_defaults_to_none(self):
        c = AccommodationConstraint()
        self.assertIsNone(c.rerun_planning)

    def test_rerun_planning_accepts_true(self):
        c = AccommodationConstraint(rerun_planning=True)
        self.assertTrue(c.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = AccommodationConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())
        self.assertIn("accommodation", description.lower())


if __name__ == "__main__":
    unittest.main()
