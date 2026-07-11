import unittest

from states.attraction_constraints import AttractionConstraint


class AttractionConstraintTests(unittest.TestCase):
    def test_rerun_planning_defaults_to_none(self):
        c = AttractionConstraint()
        self.assertIsNone(c.rerun_planning)

    def test_rerun_planning_accepts_true(self):
        c = AttractionConstraint(rerun_planning=True)
        self.assertTrue(c.rerun_planning)

    def test_rerun_planning_has_llm_facing_description(self):
        schema = AttractionConstraint.model_json_schema()
        description = schema["properties"]["rerun_planning"]["description"]
        self.assertIn("rerun", description.lower())
        self.assertIn("attraction", description.lower())


if __name__ == "__main__":
    unittest.main()
