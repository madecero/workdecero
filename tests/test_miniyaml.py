import unittest

import _paths  # noqa: F401
import miniyaml
from miniyaml import YamlError, dumps, loads


class Reading(unittest.TestCase):
    def test_scalars(self):
        doc = loads(
            "text: hello world\n"
            "quoted: \"a: b # not a comment\"\n"
            "single: 'it''s fine'\n"
            "whole: 42\n"
            "negative: -7\n"
            "decimal: 3.5\n"
            "yes_flag: true\n"
            "no_flag: False\n"
            "nothing: null\n"
            "tilde: ~\n"
            "empty:\n"
            "date: 2026-11-09\n"
            "zip: \"02134\"\n"
            "version: 1.2.3\n"
            "url: https://example.com/a?b=c#frag\n"
            "word_yes: yes\n"
        )
        self.assertEqual(doc, {
            "text": "hello world", "quoted": "a: b # not a comment", "single": "it's fine",
            "whole": 42, "negative": -7, "decimal": 3.5, "yes_flag": True, "no_flag": False,
            "nothing": None, "tilde": None, "empty": None, "date": "2026-11-09", "zip": "02134",
            "version": "1.2.3", "url": "https://example.com/a?b=c#frag", "word_yes": "yes",
        })

    def test_leading_zero_number_stays_text(self):
        self.assertEqual(loads("zip: 02134"), {"zip": "02134"})

    def test_comments_and_blank_lines(self):
        doc = loads("# top\n\nname: Dana  # trailing\n\n  # indented comment\ncity: Austin\n")
        self.assertEqual(doc, {"name": "Dana", "city": "Austin"})

    def test_nested_mappings(self):
        doc = loads("a:\n  b:\n    c: 1\n  d: 2\ne: 3\n")
        self.assertEqual(doc, {"a": {"b": {"c": 1}, "d": 2}, "e": 3})

    def test_block_lists(self):
        doc = loads("titles:\n  - Nurse Manager\n  - \"Director, Nursing\"\nempty: []\n")
        self.assertEqual(doc, {"titles": ["Nurse Manager", "Director, Nursing"], "empty": []})

    def test_list_at_same_indent_as_key(self):
        self.assertEqual(loads("tags:\n- a\n- b\nnext: 1\n"), {"tags": ["a", "b"], "next": 1})

    def test_inline_lists(self):
        doc = loads("modes: [remote, hybrid]\nmixed: [1, 2.5, true, null, \"a, b\", 'c']\n")
        self.assertEqual(doc, {"modes": ["remote", "hybrid"], "mixed": [1, 2.5, True, None, "a, b", "c"]})

    def test_list_of_mappings(self):
        doc = loads(
            "channels:\n"
            "  - name: Upwork\n"
            "    status: active\n"
            "    tags: [a, b]\n"
            "  - name: Toptal\n"
            "    nested:\n"
            "      deep: 1\n"
            "after: done\n"
        )
        self.assertEqual(doc, {
            "channels": [{"name": "Upwork", "status": "active", "tags": ["a", "b"]},
                         {"name": "Toptal", "nested": {"deep": 1}}],
            "after": "done",
        })

    def test_top_level_list_and_scalar(self):
        self.assertEqual(loads("- a\n- b\n"), ["a", "b"])
        self.assertEqual(loads("just text\n"), "just text")
        self.assertIsNone(loads(""))
        self.assertIsNone(loads("# only a comment\n"))

    def test_leading_document_marker_and_bom(self):
        self.assertEqual(loads(chr(0xFEFF) + "---\na: 1\n"), {"a": 1})

    def test_quoted_keys_and_empty_mapping(self):
        self.assertEqual(loads('"odd: key": 1\nempty: {}\n'), {"odd: key": 1, "empty": {}})

    def test_double_quote_escapes(self):
        self.assertEqual(loads('a: "line\\nbreak \\"q\\" back\\\\slash"'), {"a": 'line\nbreak "q" back\\slash'})

    def test_apostrophe_in_plain_text(self):
        self.assertEqual(loads("note: don't call # really\n"), {"note": "don't call"})

    def test_windows_line_endings(self):
        self.assertEqual(loads("a: 1\r\nb:\r\n  - x\r\n"), {"a": 1, "b": ["x"]})


class Refusing(unittest.TestCase):
    def fails(self, text, line, fragment):
        with self.assertRaises(YamlError) as caught:
            loads(text)
        self.assertEqual(caught.exception.line, line, str(caught.exception))
        self.assertIn(fragment, str(caught.exception))

    def test_tabs(self):
        self.fails("a:\n\tb: 1\n", 2, "spaces, not tabs")

    def test_duplicate_key(self):
        self.fails("a: 1\nb: 2\na: 3\n", 3, "appears twice")

    def test_missing_space_after_colon(self):
        self.fails("a: 1\nb:2\n", 2, "colon followed by a space")

    def test_bad_indentation(self):
        self.fails("a: 1\n  b: 2\n", 2, "unexpected indentation")
        self.fails("a:\n    b: 1\n  c: 2\n", 3, "unexpected indentation")

    def test_unsupported_features_say_so(self):
        self.fails("a: |\n  text\n", 1, "multi-line text blocks")
        self.fails("a: &x 1\n", 1, "anchors")
        self.fails("a: *x\n", 1, "aliases")
        self.fails("a: !!str 1\n", 1, "tags")
        self.fails("a: {b: 1}\n", 1, "inline mappings")
        self.fails("a: [1, [2]]\n", 1, "inside an inline list")
        self.fails("a: 1\n---\nb: 2\n", 2, "more than one document")
        self.fails("- - a\n", 1, "list directly inside a list")

    def test_unclosed_things(self):
        self.fails('a: "open\n', 1, "closing")
        self.fails("a: [1, 2\n", 1, "closing ]")
        self.fails("a: [1,, 2]\n", 1, "empty item")

    def test_mapping_line_after_top_level_list(self):
        self.fails("- a\nb: 1\n", 2, "expected a list item")

    def test_list_item_in_mapping(self):
        self.fails("a: 1\n- b\n", 2, "list item cannot appear here")


class Writing(unittest.TestCase):
    def test_layout(self):
        text = dumps({
            "name": "Dana Whitfield",
            "tracks": {"jobs": True, "contracts": False},
            "titles": ["Nurse Manager", "Director, Nursing"],
            "channels": [{"name": "Upwork", "status": "active"}],
            "none": None, "empty_list": [], "empty_map": {},
        })
        self.assertEqual(text, (
            "name: Dana Whitfield\n"
            "tracks:\n"
            "  jobs: true\n"
            "  contracts: false\n"
            "titles:\n"
            "  - Nurse Manager\n"
            "  - \"Director, Nursing\"\n"
            "channels:\n"
            "  - name: Upwork\n"
            "    status: active\n"
            "none: null\n"
            "empty_list: []\n"
            "empty_map: {}\n"
        ))

    def test_round_trip_of_awkward_values(self):
        awkward = [
            "", " lead", "trail ", "true", "False", "null", "~", "42", "-7", "3.5", "02134",
            "a: b", "ends:", "has # hash", "#start", "- dash", "-dash", "?q", "[x]", "{x}", "a, b",
            "it's", 'say "hi"', '"quoted"', "'single'", "back\\slash", "line\nbreak", "tab\there",
            "@handle", "`tick`", "*star", "&amp", "!bang", "|pipe", ">gt", "%pct",
            "2026-11-09", "https://example.com/a?b=c#frag", "C:\\path", "caf\u00e9", "(paren)", "50% off",
        ]
        doc = {
            "values": awkward,
            "as_keys": dict((value, index) for index, value in enumerate(awkward) if value),
            "numbers": [0, -1, 10, 2.5, -0.25],
            "flags": [True, False, None],
            "nested": {"list_of_maps": [{"a": 1, "b": [1, 2], "c": {"d": "e"}}, {}, {"f": None}],
                       "list_of_lists": [["x", "y, z"], [1, True]]},
        }
        self.assertEqual(loads(dumps(doc)), doc)

    def test_round_trip_top_level_shapes(self):
        for value in ({}, [], ["a", {"b": 1}], "text", 5, None, True):
            self.assertEqual(loads(dumps(value)), value)

    def test_refuses_what_it_cannot_write(self):
        with self.assertRaises(YamlError):
            dumps({"a": object()})
        with self.assertRaises(YamlError):
            dumps({1: "a"})


class FrontMatter(unittest.TestCase):
    def test_split_and_join_keep_the_body_exactly(self):
        note = "---\ntype: posting\ntags:\n  - job\n---\n# Title\n\nBody with --- inside\n"
        properties, body = miniyaml.split_front_matter(note)
        self.assertEqual(properties, {"type": "posting", "tags": ["job"]})
        self.assertEqual(body, "# Title\n\nBody with --- inside\n")
        self.assertEqual(miniyaml.split_front_matter(miniyaml.join_front_matter(properties, body)), (properties, body))

    def test_note_without_properties(self):
        self.assertEqual(miniyaml.split_front_matter("# Just a note\n"), ({}, "# Just a note\n"))
        self.assertEqual(miniyaml.join_front_matter({}, "# Just a note\n"), "# Just a note\n")

    def test_empty_properties_block(self):
        self.assertEqual(miniyaml.split_front_matter("---\n---\nbody"), ({}, "body"))

    def test_unclosed_block(self):
        with self.assertRaises(YamlError):
            miniyaml.split_front_matter("---\ntype: posting\nno end\n")


if __name__ == "__main__":
    unittest.main()
