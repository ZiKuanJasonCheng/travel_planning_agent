import unittest

from orchestration.llm_feedback_parsing import _EXTRACT_TOOL


def _walk(node, path="$"):
    """Yield (path, node) for every dict in the schema."""
    if isinstance(node, dict):
        yield path, node
        for key, child in node.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from _walk(child, f"{path}[{i}]")


def _object_nodes():
    for path, node in _walk(_EXTRACT_TOOL["function"]["parameters"]):
        if node.get("type") == "object" and "properties" in node:
            yield path, node


class StrictToolDefinitionTests(unittest.TestCase):
    """The suite fakes the OpenAI client, so nothing here can see the request.
    These pin the request itself: a regression here is a live 400, not a red
    test, which is exactly what makes the assertions worth having."""

    def test_strict_mode_is_on(self):
        self.assertIs(_EXTRACT_TOOL["function"].get("strict"), True)

    def test_every_object_is_closed(self):
        open_nodes = [path for path, node in _object_nodes()
                      if node.get("additionalProperties") is not False]
        self.assertEqual(open_nodes, [])

    def test_every_property_is_required(self):
        problems = []
        for path, node in _object_nodes():
            declared = set(node["properties"])
            required = set(node.get("required") or [])
            if declared != required:
                problems.append(f"{path}: missing {sorted(declared - required)}")
        self.assertEqual(problems, [])

    def test_no_strictly_unsupported_keywords_remain(self):
        problems = []
        for path, node in _walk(_EXTRACT_TOOL["function"]["parameters"]):
            for key in ("default", "title", "format"):
                if key in node:
                    problems.append(f"{path}.{key}")
        self.assertEqual(problems, [])

    def test_descriptions_survive_the_transform(self):
        """`title` is stripped; `description` carries the instructions that make
        the model put a value at the right level, so it must not be collateral."""
        defs = _EXTRACT_TOOL["function"]["parameters"]["$defs"]
        price_desc = defs["states__accommodation_constraints__PreferenceConstraint"][
            "properties"]["max_price_per_night"].get("description")
        self.assertIn("Maximum acceptable price per night", price_desc or "")

    def test_nested_objects_stay_nested(self):
        """The whole point: the price path goes through `preference`."""
        defs = _EXTRACT_TOOL["function"]["parameters"]["$defs"]
        pref_ref = defs["AccommodationConstraint"]["properties"]["preference"]
        self.assertIn(
            "#/$defs/states__accommodation_constraints__PreferenceConstraint",
            str(pref_ref),
        )
        self.assertNotIn(
            "max_price_per_night",
            defs["AccommodationConstraint"]["properties"],
        )


if __name__ == "__main__":
    unittest.main()
