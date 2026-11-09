"""A small JSON Schema checker with friendly messages.

The profile schema (profile.schema.json) is a normal JSON Schema file, so
editors and other tools can use it. Python has no JSON Schema validator in
its standard library, so this module checks the part of JSON Schema the
profile uses and words every problem for a non-technical reader: which
setting, what is wrong, and an example of a correct value.

Supported keywords are listed in SUPPORTED_KEYWORDS. `unsupported_keywords`
reports anything else, and a test fails if the schema ever uses a keyword
this module would silently ignore.

Standard library only, Python 3.9+.
"""

import difflib
import re

import miniyaml

SUPPORTED_KEYWORDS = frozenset([
    # checked
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "minimum", "maximum", "minLength", "minItems", "uniqueItems", "pattern",
    # used for messages and defaults
    "description", "examples", "default", "title", "patternHint",
    # ignored on purpose
    "$schema", "$id", "$comment",
])

_TYPE_WORDS = {
    "string": "text",
    "integer": "a whole number",
    "number": "a number",
    "boolean": "true or false",
    "array": "a list",
    "object": "a group of settings",
    "null": "empty",
}


class Problem(object):
    """One thing wrong with a file. `path` is a dotted setting name such as jobs.salary_floor."""

    def __init__(self, path, message, example=None):
        self.path = path
        self.message = message
        self.example = example

    def render(self):
        lines = ["%s: %s" % (self.path or "(whole file)", self.message)]
        if self.example:
            lines.append("  Example:")
            lines.extend("    " + line for line in self.example.splitlines())
        return "\n".join(lines)

    def __repr__(self):
        return "Problem(%r, %r)" % (self.path, self.message)


def _type_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value, wanted):
    actual = _type_of(value)
    if actual == wanted:
        return True
    return wanted == "number" and actual == "integer"


def _show(value):
    """A short, readable form of what the user wrote."""
    if isinstance(value, str):
        return '"%s"' % value
    if isinstance(value, (dict, list, tuple)):
        text = miniyaml.dumps(value).strip().replace("\n", " ")
        return text if len(text) <= 60 else text[:57] + "..."
    return miniyaml.dumps(value).strip()


def _join(path, key):
    return "%s.%s" % (path, key) if path else str(key)


def _leaf(path):
    name = path.rsplit(".", 1)[-1] if path else ""
    return re.sub(r"\[\d+\]$", "", name)


def example_for(path, schema):
    """YAML text showing a correct value for this setting, or None."""
    examples = schema.get("examples")
    if not examples:
        if "enum" in schema:
            examples = [schema["enum"][0]]
        elif "default" in schema:
            examples = [schema["default"]]
        else:
            return None
    if path.endswith("]"):
        return None  # one item of a list: the list's own example is the useful one
    key = _leaf(path)
    if not key:
        return miniyaml.dumps(examples[0]).rstrip("\n")
    return miniyaml.dumps({key: examples[0]}).rstrip("\n")


def _wanted_types(schema):
    wanted = schema.get("type")
    if wanted is None:
        return []
    return list(wanted) if isinstance(wanted, list) else [wanted]


def validate(value, schema, path=""):
    """Return a list of Problem for `value` checked against `schema`."""
    problems = []
    example = example_for(path, schema)

    wanted = _wanted_types(schema)
    if wanted and not any(_type_matches(value, name) for name in wanted):
        words = " or ".join(_TYPE_WORDS.get(name, name) for name in wanted if name != "null")
        if value is None:
            message = "is empty, but it needs %s." % words
        elif "string" in wanted and isinstance(value, dict) and len(value) == 1:
            key, rest = list(value.items())[0]
            written = "%s: %s" % (key, "" if rest is None else miniyaml.dumps(rest).strip())
            message = ("contains a colon followed by a space, so it was read as a setting instead of text. "
                       "Put the whole value in quotes.")
            return [Problem(path, message, '"%s"' % written.strip().replace('"', "'"))]
        elif "array" in wanted and _type_of(value) != "object":
            message = "must be a list, but you wrote a single value (%s). Put it in a list." % _show(value)
        else:
            message = "must be %s, but you wrote %s." % (words, _show(value))
        return [Problem(path, message, example)]

    if value is None:
        return problems

    if "enum" in schema and value not in schema["enum"]:
        choices = ", ".join(str(choice) for choice in schema["enum"])
        hint = ""
        if isinstance(value, str):
            close = difflib.get_close_matches(value, [str(c) for c in schema["enum"]], n=1, cutoff=0.6)
            if close:
                hint = " Did you mean %s?" % close[0]
        problems.append(Problem(path, "must be one of: %s. You wrote %s.%s" % (choices, _show(value), hint), example))

    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            problems.append(Problem(path, "must not be blank.", example))
        elif "pattern" in schema and not re.search(schema["pattern"], value):
            hint = schema.get("patternHint", "is not in the expected form")
            problems.append(Problem(path, "%s. You wrote %s." % (hint, _show(value)), example))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(Problem(path, "must be %s or more, but you wrote %s." % (schema["minimum"], value), example))
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(Problem(path, "must be %s or less, but you wrote %s." % (schema["maximum"], value), example))

    if isinstance(value, (list, tuple)):
        if len(value) < schema.get("minItems", 0):
            need = schema["minItems"]
            have = "it is empty" if not value else "it has %d" % len(value)
            problems.append(Problem(path, "needs at least %s, but %s."
                                    % ("one item" if need == 1 else "%d items" % need, have), example))
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                if item in seen:
                    problems.append(Problem(path, "lists %s more than once." % _show(item), None))
                    break
                seen.append(item)
        if "items" in schema:
            for index, item in enumerate(value):
                problems.extend(validate(item, schema["items"], "%s[%d]" % (path, index + 1)))

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value or value[key] is None:
                child = properties.get(key, {})
                child_path = _join(path, key)
                what = "section" if "object" in _wanted_types(child) else "setting"
                problems.append(Problem(child_path, "this %s is required but missing." % what,
                                        example_for(child_path, child)))
        for key, item in value.items():
            child_path = _join(path, key)
            if key in properties:
                if item is None and key in schema.get("required", []):
                    continue  # already reported as missing
                if item is None and "default" in properties[key]:
                    continue  # an empty optional setting falls back to its default
                problems.extend(validate(item, properties[key], child_path))
            elif schema.get("additionalProperties") is False:
                close = difflib.get_close_matches(str(key), list(properties), n=1, cutoff=0.6)
                hint = " Did you mean %s?" % close[0] if close else " Remove it or check the spelling."
                problems.append(Problem(child_path, "is not a setting workdecero knows.%s" % hint, None))
    return problems


def apply_defaults(value, schema):
    """Return a copy of `value` with schema defaults filled in for missing or empty settings.

    Optional sections with no required settings (digest, schedule, integrations)
    are created so callers can read their defaults without checking first.
    Sections that have required settings (jobs, contracts, networking) are not.
    """
    if isinstance(value, dict):
        result = dict(value)
        for key, child in schema.get("properties", {}).items():
            if result.get(key) is None:
                result.pop(key, None)
                if "default" in child:
                    result[key] = _copy(child["default"])
                elif child.get("type") == "object" and not child.get("required"):
                    result[key] = {}  # an optional section: fill it with its own defaults
            if key in result:
                result[key] = apply_defaults(result[key], child)
        return result
    if isinstance(value, list) and "items" in schema:
        return [apply_defaults(item, schema["items"]) for item in value]
    return value


def _copy(value):
    if isinstance(value, dict):
        return dict((key, _copy(item)) for key, item in value.items())
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def unsupported_keywords(schema, path="#"):
    """List every keyword in `schema` that this module does not understand."""
    found = []
    if not isinstance(schema, dict):
        return found
    for key, item in schema.items():
        if key not in SUPPORTED_KEYWORDS:
            found.append("%s/%s" % (path, key))
        if key == "properties" and isinstance(item, dict):
            for name, child in item.items():
                found.extend(unsupported_keywords(child, "%s/properties/%s" % (path, name)))
        elif key == "items":
            found.extend(unsupported_keywords(item, path + "/items"))
    return found
