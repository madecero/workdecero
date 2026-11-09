"""miniyaml: a small, strict YAML subset reader and writer.

Why this exists: the plugin must run with zero installs, and Python has no
YAML parser in its standard library. Profiles and Obsidian note properties
only need a small part of YAML, so this module supports exactly that part and
refuses everything else with a clear error instead of guessing.

Supported:
  * mappings and nested mappings (indentation with spaces)
  * block lists of scalars or of mappings ("- item")
  * inline lists of scalars ("[a, b, c]"), "[]" and "{}"
  * scalars: text (plain, 'single' or "double" quoted), whole numbers,
    decimals, true/false, null (null, ~ or empty)
  * comments (# ...) and an optional leading "---" line

Not supported (raises YamlError): tabs for indentation, anchors and aliases,
tags, multi-line text blocks (| and >), inline mappings with content, nested
inline lists, multiple documents, duplicate keys.

Dates stay text ("2026-11-09"), which is how Obsidian stores them too.
Standard library only, Python 3.9+.
"""

import re

__all__ = ["YamlError", "loads", "dumps", "split_front_matter", "join_front_matter"]

_INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$")
_TRUE = ("true", "True", "TRUE")
_FALSE = ("false", "False", "FALSE")
_NULL = ("null", "Null", "NULL", "~", "")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0", "r": "\r"}
_UNSUPPORTED_START = {
    "|": "multi-line text blocks (|) are not supported. Put the text on one line in quotes",
    ">": "multi-line text blocks (>) are not supported. Put the text on one line in quotes",
    "&": "anchors (&) are not supported",
    "*": "aliases (*) are not supported",
    "!": "tags (!) are not supported",
    "{": "inline mappings ({...}) are not supported. Use one 'key: value' per line",
    "%": "directives (%) are not supported",
    "@": "a value cannot start with @. Put it in quotes",
    "`": "a value cannot start with a backtick. Put it in quotes",
}


class YamlError(ValueError):
    """A problem in the YAML text. `line` is the 1-based line number, or None."""

    def __init__(self, message, line=None):
        self.problem = message
        self.line = line
        prefix = "line %d: " % line if line is not None else ""
        ValueError.__init__(self, prefix + message)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _strip_comment(text):
    """Remove a trailing comment that is outside quotes."""
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                quote = None
        elif ch in "\"'" and (i == 0 or text[i - 1] in " [,:-"):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] == " "):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _find_key_end(text):
    """Index of the colon that ends a mapping key, or -1 if this is not 'key: value'."""
    if text[:1] in "\"'":
        quote = text[0]
        i = 1
        while i < len(text):
            if quote == '"' and text[i] == "\\":
                i += 2
                continue
            if text[i] == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                rest = text[i + 1:]
                if rest == ":" or rest.startswith(": "):
                    return i + 1
                return -1
            i += 1
        return -1
    i = 0
    while True:
        i = text.find(":", i)
        if i == -1:
            return -1
        if i + 1 == len(text) or text[i + 1] == " ":
            return i
        i += 1


def _unquote(text, line):
    quote = text[0]
    if len(text) < 2 or text[-1] != quote:
        raise YamlError("a quoted value is missing its closing %s" % quote, line)
    body = text[1:-1]
    if quote == "'":
        if "'" in body.replace("''", ""):
            raise YamlError("a single quote inside '...' must be doubled ('')", line)
        return body.replace("''", "'")
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            raise YamlError('a double quote inside "..." must be written as \\"', line)
        if ch == "\\":
            i += 1
            nxt = body[i:i + 1]
            if nxt not in _ESCAPES:
                raise YamlError("unknown escape \\%s in a quoted value" % nxt, line)
            out.append(_ESCAPES[nxt])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_flow(body, line):
    """Split the inside of [a, b, c] on commas that are outside quotes."""
    items, current, quote = [], [], None
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            current.append(ch)
            if quote == '"' and ch == "\\":
                i += 1
                current.append(body[i:i + 1])
            elif ch == quote:
                quote = None
        elif ch in "\"'" and not "".join(current).strip():
            quote = ch
            current.append(ch)
        elif ch in "[{":
            raise YamlError("lists inside an inline list are not supported. Use a block list", line)
        elif ch == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if quote:
        raise YamlError("a quoted value is missing its closing %s" % quote, line)
    last = "".join(current).strip()
    if last or items:
        items.append(last)
    if any(item == "" for item in items):
        raise YamlError("an inline list has an empty item (check for a stray comma)", line)
    return items


def _scalar(text, line, in_flow=False):
    if text[:1] in "\"'":
        return _unquote(text, line)
    if text in _NULL:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    if text[0] in _UNSUPPORTED_START:
        raise YamlError(_UNSUPPORTED_START[text[0]], line)
    if text[0] == "[" and in_flow:
        raise YamlError("lists inside an inline list are not supported. Use a block list", line)
    return text


def _value(text, line):
    """Parse an inline value: a scalar, an inline list, [] or {}."""
    if text == "{}":
        return {}
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError("an inline list is missing its closing ]", line)
        return [_scalar(item, line, in_flow=True) for item in _split_flow(text[1:-1], line)]
    return _scalar(text, line)


class _Parser(object):
    def __init__(self, text):
        self.lines = []  # (indent, content, line_number)
        seen_content = False
        for number, raw in enumerate(text.splitlines(), start=1):
            if number == 1 and raw.startswith("\ufeff"):
                raw = raw[1:]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent_text = raw[:len(raw) - len(raw.lstrip())]
            if "\t" in indent_text:
                raise YamlError("indent with spaces, not tabs", number)
            if stripped == "---":
                if seen_content:
                    raise YamlError("more than one document in a file is not supported", number)
                continue
            if stripped == "...":
                raise YamlError("document end markers (...) are not supported", number)
            content = _strip_comment(raw.strip())
            if not content:
                continue
            seen_content = True
            self.lines.append((len(indent_text), content, number))
        self.pos = 0

    def peek(self):
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def parse_document(self):
        if not self.lines:
            return None
        indent, content, line = self.lines[0]
        if indent != 0:
            raise YamlError("the first line must not be indented", line)
        if not content.startswith("- ") and content != "-" and _find_key_end(content) == -1:
            if len(self.lines) > 1:
                raise YamlError("expected 'key: value' or '- item'", self.lines[1][2])
            self.pos = 1
            return _value(content, line)
        result = self.parse_block(0)
        if self.pos < len(self.lines):
            if isinstance(result, list):
                raise YamlError("expected a list item starting with '- '", self.lines[self.pos][2])
            raise YamlError("unexpected indentation", self.lines[self.pos][2])
        return result

    def parse_block(self, indent):
        content = self.lines[self.pos][1]
        if content == "-" or content.startswith("- "):
            return self.parse_sequence(indent)
        return self.parse_mapping(indent)

    def parse_child(self, parent_indent, line, allow_same_indent_list):
        """Parse whatever follows a 'key:' or '-' that has no inline value."""
        nxt = self.peek()
        if nxt is None:
            return None
        indent, content, _ = nxt
        if indent > parent_indent:
            return self.parse_block(indent)
        if allow_same_indent_list and indent == parent_indent and (content == "-" or content.startswith("- ")):
            return self.parse_sequence(indent)
        return None

    def parse_mapping(self, indent):
        result = {}
        while self.pos < len(self.lines):
            line_indent, content, line = self.lines[self.pos]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise YamlError("unexpected indentation", line)
            if content == "-" or content.startswith("- "):
                raise YamlError("a list item cannot appear here. Check the indentation", line)
            end = _find_key_end(content)
            if end == -1:
                raise YamlError("expected 'key: value' (a colon followed by a space)", line)
            key_text = content[:end].strip()
            if not key_text:
                raise YamlError("a key is missing before the colon", line)
            key = _unquote(key_text, line) if key_text[0] in "\"'" else key_text
            if key in result:
                raise YamlError("the key '%s' appears twice" % key, line)
            rest = content[end + 1:].strip()
            self.pos += 1
            if rest:
                result[key] = _value(rest, line)
            else:
                result[key] = self.parse_child(indent, line, allow_same_indent_list=True)
        return result

    def parse_sequence(self, indent):
        result = []
        while self.pos < len(self.lines):
            line_indent, content, line = self.lines[self.pos]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise YamlError("unexpected indentation", line)
            if not (content == "-" or content.startswith("- ")):
                break  # the list ended; the enclosing mapping continues
            rest = content[1:].strip()
            if not rest:
                self.pos += 1
                result.append(self.parse_child(indent, line, allow_same_indent_list=False))
            elif rest == "-" or rest.startswith("- "):
                raise YamlError("a list directly inside a list item is not supported", line)
            elif not rest.startswith("[") and _find_key_end(rest) != -1:
                # "- key: value" starts a mapping whose keys line up after the dash.
                item_indent = indent + (len(content) - len(rest))
                self.lines[self.pos] = (item_indent, rest, line)
                result.append(self.parse_mapping(item_indent))
            else:
                self.pos += 1
                result.append(_value(rest, line))
        return result


def loads(text):
    """Parse YAML text and return dicts, lists, str, int, float, bool or None."""
    return _Parser(text).parse_document()


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_PLAIN_SAFE_RE = re.compile(r"^[A-Za-z0-9_./(][^\n\r\t]*$")


def _needs_quotes(text):
    if text == "" or text != text.strip():
        return True
    if not _PLAIN_SAFE_RE.match(text):
        return True
    if ": " in text or " #" in text or text.endswith(":"):
        return True
    if "," in text or "[" in text or "]" in text or "{" in text or "}" in text:
        return True  # keeps inline lists unambiguous
    if text in _TRUE or text in _FALSE or text in _NULL:
        return True
    if _INT_RE.match(text) or _FLOAT_RE.match(text):
        return True
    return False


def _quote(text):
    out = text.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r").replace("\0", "\\0")
    return '"' + out + '"'


def _dump_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if not _FLOAT_RE.match(text):
            raise YamlError("cannot write the number %r" % value)
        return text
    if isinstance(value, str):
        return _quote(value) if _needs_quotes(value) else value
    raise YamlError("cannot write a value of type %s" % type(value).__name__)


def _dump_key(key):
    if not isinstance(key, str):
        raise YamlError("keys must be text, got %s" % type(key).__name__)
    return _quote(key) if (_needs_quotes(key) or "'" in key or '"' in key) else key


def _is_scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _dump_node(value, indent, out):
    pad = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            _dump_entry(pad + _dump_key(key) + ":", item, indent, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict) and item:
                lines = []
                _dump_node(item, indent + 2, lines)
                out.append(pad + "- " + lines[0][indent + 2:])
                out.extend(lines[1:])
            elif isinstance(item, (list, tuple)):
                if not all(_is_scalar(sub) for sub in item):
                    raise YamlError("a list inside a list can only hold simple values")
                out.append(pad + "- [" + ", ".join(_dump_scalar(sub) for sub in item) + "]")
            elif isinstance(item, dict):
                out.append(pad + "- {}")
            else:
                out.append(pad + "- " + _dump_scalar(item))
    else:
        out.append(pad + _dump_scalar(value))


def _dump_entry(prefix, item, indent, out):
    if isinstance(item, dict):
        if not item:
            out.append(prefix + " {}")
        else:
            out.append(prefix)
            _dump_node(item, indent + 2, out)
    elif isinstance(item, (list, tuple)):
        if not item:
            out.append(prefix + " []")
        else:
            out.append(prefix)
            _dump_node(item, indent + 2, out)
    else:
        out.append(prefix + " " + _dump_scalar(item))


def dumps(value):
    """Write dicts, lists and scalars as YAML text that loads() reads back unchanged."""
    if isinstance(value, dict) and not value:
        return "{}\n"
    if isinstance(value, (list, tuple)) and not value:
        return "[]\n"
    out = []
    _dump_node(value, 0, out)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Markdown front matter (Obsidian note properties)
# --------------------------------------------------------------------------

def split_front_matter(text):
    """Return (properties, body) for a Markdown note.

    A note with no front matter returns ({}, text). The body is returned
    exactly as written, so a note can be rewritten without touching it.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            properties = loads("\n".join(lines[1:index]))
            if properties is None:
                properties = {}
            if not isinstance(properties, dict):
                raise YamlError("note properties must be 'key: value' lines", 2)
            return properties, "\n".join(lines[index + 1:])
    raise YamlError("the properties block that starts with --- is never closed", 1)


def join_front_matter(properties, body):
    """Build Markdown note text from properties and a body. Inverse of split_front_matter."""
    if not properties:
        return body
    return "---\n" + dumps(properties) + "---\n" + body
