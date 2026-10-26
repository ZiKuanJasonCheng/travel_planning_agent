"""Adapt a Pydantic JSON schema to OpenAI strict structured outputs.

Strict mode compiles the schema into a grammar, so the decoder can no longer
emit a key that isn't declared. That security comes with requirements our
models don't meet by default:

- every object must carry `additionalProperties: false`;
- every property must be listed in `required` (absence is expressed by a
  nullable type, not an omitted key);
- `default`, `title` and `format` are not accepted on the nodes we generate.

This transform adds those three edits and keeps descriptions intact — those are
the instructions the model reads to decide *which* value goes where.
"""
import copy

# Not part of the strict-subset grammar; the API rejects them outright.
_UNSUPPORTED_KEYS = ("default", "format")

# Kept on object nodes: `title` is unsupported, but `description` is the
# instruction text and must survive.
_STRIP_KEYS = _UNSUPPORTED_KEYS + ("title",)


def _has_properties(node):
    return node.get("type") == "object" and "properties" in node


def _strip(node):
    for key in _STRIP_KEYS:
        node.pop(key, None)
    for child in node.get("properties", {}).values():
        _visit(child)
    for child in node.get("anyOf", []):
        _visit(child)
    for child in node.get("items", {}) if isinstance(node.get("items"), dict) else []:
        _visit(child)


def _visit(node):
    if not isinstance(node, dict):
        return
    if _has_properties(node):
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())
    _strip(node)


def to_strict_schema(schema):
    strict = copy.deepcopy(schema)
    _visit(strict)
    for definition in strict.get("$defs", {}).values():
        _visit(definition)
    return strict
