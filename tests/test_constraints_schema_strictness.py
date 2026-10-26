import json
import unittest

from pydantic import ValidationError

from states.constraints import Constraints


class MisplacedFieldsMustNotBeSilentlyDropped(unittest.TestCase):
    """Real Langfuse traces showed the parser hoisting a price to the category
    level, e.g. {"accommodation": {"max_price_per_night": 5000}}, and the parse
    then discarding the 5000. A wrong level must be an error, not a quiet loss."""

    def test_max_price_per_night_under_accommodation_is_rejected(self):
        raw = json.dumps({"accommodation": {"max_price_per_night": 5000}})
        with self.assertRaises(ValidationError):
            Constraints.model_validate_json(raw)

    def test_max_price_per_ticket_under_attraction_is_rejected(self):
        raw = json.dumps({"attraction": {"max_price_per_ticket": 40}})
        with self.assertRaises(ValidationError):
            Constraints.model_validate_json(raw)

    def test_correct_nesting_round_trips(self):
        raw = json.dumps({
            "accommodation": {"preference": {"max_price_per_night": 5000}},
            "attraction": {"preference": {"max_price_per_ticket": 40}},
        })
        parsed = Constraints.model_validate_json(raw)
        self.assertEqual(parsed.accommodation.preference.max_price_per_night, 5000)
        self.assertEqual(parsed.attraction.preference.max_price_per_ticket, 40)

    def test_error_names_the_offending_field_and_location(self):
        raw = json.dumps({"accommodation": {"max_price_per_night": 5000}})
        with self.assertRaises(ValidationError) as caught:
            Constraints.model_validate_json(raw)
        self.assertIn("max_price_per_night", str(caught.exception))


class EveryObjectInTheSchemaIsClosed(unittest.TestCase):
    """`additionalProperties: false` on every object node is what communicates
    the nesting to the model; pydantic emits it for extra='forbid' models."""

    def test_no_object_node_accepts_additional_properties(self):
        def walk(node, path="$"):
            offenders = []
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    if node.get("additionalProperties") is not False:
                        offenders.append(path)
                for key, child in node.items():
                    offenders += walk(child, f"{path}.{key}")
            elif isinstance(node, list):
                for i, child in enumerate(node):
                    offenders += walk(child, f"{path}[{i}]")
            return offenders

        offenders = walk(Constraints.model_json_schema())
        self.assertEqual(offenders, [], f"open objects: {offenders}")


if __name__ == "__main__":
    unittest.main()
