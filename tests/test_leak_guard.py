import os
import subprocess
import sys
import tempfile
import unittest

import _paths  # noqa: F401
import leak_guard

GUARD = os.path.join(_paths.REPO_ROOT, "dev", "leak_guard.py")

# Samples are assembled at run time so this file never trips the guard itself.
HOME = "/" + "Users" + "/" + "pat.rivera" + "/notes"
LINUX_HOME = "/" + "home" + "/" + "pat" + "/x"
EMAIL = "pat.rivera" + "@" + "realmail.test"
PHONE = "608" + "-" + "867" + "-" + "5309"
LONG_DASH = "a " + chr(0x2014) + " b"
SECRET = "Zorblatt"  # an invented word standing in for a private name


def rules(*patterns):
    import re
    return [(i, re.compile(p, re.IGNORECASE)) for i, p in enumerate(patterns, start=1)]


class GenericPatterns(unittest.TestCase):
    def kinds(self, line):
        return [rule for rule, _ in leak_guard.scan_generic(line)]

    def test_home_paths(self):
        self.assertEqual(self.kinds("open " + HOME), ["home-path"])
        self.assertEqual(self.kinds(LINUX_HOME), ["home-path"])

    def test_placeholder_home_path_is_fine(self):
        self.assertEqual(self.kinds("/" + "Users" + "/you/Documents"), [])

    def test_email(self):
        self.assertEqual(self.kinds("write to " + EMAIL), ["email"])

    def test_example_and_noreply_emails_are_fine(self):
        self.assertEqual(self.kinds("pat@example.com sam@mail.example.org"), [])
        self.assertEqual(self.kinds("123+someone@users.noreply.github.com"), [])
        self.assertEqual(self.kinds("git@github.com:owner/repo.git"), [])

    def test_phone(self):
        self.assertEqual(self.kinds("call " + PHONE), ["phone"])
        self.assertEqual(self.kinds("call (" + "608" + ") " + "867" + " " + "5309"), ["phone"])

    def test_fictional_phone_is_fine(self):
        self.assertEqual(self.kinds("call 512-555-0142"), [])

    def test_dates_versions_and_ids_are_not_phones(self):
        self.assertEqual(self.kinds("2026-11-09 v1.2.3 id 1234567890 sha 11d5960a3267"), [])

    def test_long_dash(self):
        self.assertEqual(self.kinds(LONG_DASH), ["long-dash"])
        self.assertEqual(self.kinds("a - b, follow-up"), [])

    def test_allow_marker_waives_generic_only(self):
        line = EMAIL + "  # " + leak_guard.ALLOW_MARKER
        self.assertEqual(self.kinds(line), [])
        found = leak_guard.scan_text("f", SECRET + " " + line, rules(SECRET))
        self.assertEqual([f.rule for f in found], ["denylist rule 1"])


class Denylist(unittest.TestCase):
    def test_case_insensitive_and_reports_rule_number_only(self):
        found = leak_guard.scan_text("notes.md", "ok\nmet " + SECRET.upper() + " today", rules("nomatch", SECRET))
        self.assertEqual(len(found), 1)
        rendered = found[0].render()
        self.assertIn("notes.md:2", rendered)
        self.assertIn("denylist rule 2", rendered)
        self.assertNotIn(SECRET.lower(), rendered.lower())

    def test_path_hit_does_not_print_the_path(self):
        found = leak_guard.scan_path_name("docs/" + SECRET + ".md", rules(SECRET))
        self.assertEqual(len(found), 1)
        self.assertNotIn(SECRET.lower(), found[0].render().lower())

    def test_load_skips_comments_and_blank_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("# comment\n\n" + SECRET + "\nfoo\\s+bar\n")
        try:
            loaded = leak_guard.load_denylist(handle.name)
        finally:
            os.unlink(handle.name)
        self.assertEqual([number for number, _ in loaded], [3, 4])

    def test_bad_rule_error_hides_rule_text(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(SECRET + "(\n")
        try:
            with self.assertRaises(ValueError) as caught:
                leak_guard.load_denylist(handle.name)
        finally:
            os.unlink(handle.name)
        self.assertIn("line 1", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))


class EndToEnd(unittest.TestCase):
    """Run the real script against a throwaway git repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.repo)
        self.denylist = os.path.join(self.tmp.name, "deny.txt")
        with open(self.denylist, "w") as handle:
            handle.write("# private\n" + SECRET + "\n")
        self.git("init", "-q")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git"] + list(args), cwd=self.repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def guard(self, *args, **env_overrides):
        env = dict(os.environ)
        env[leak_guard.DENYLIST_ENV] = self.denylist
        env.update(env_overrides)
        return subprocess.run([sys.executable, GUARD] + list(args), cwd=self.repo, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def test_clean_staged_file_passes(self):
        self.write("a.md", "nothing private here\n")
        self.git("add", "a.md")
        result = self.guard("--staged")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staged_denylisted_name_is_blocked_without_echo(self):
        self.write("a.md", "line one\nlunch with " + SECRET.lower() + "\n")
        self.git("add", "a.md")
        result = self.guard("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.md:2", result.stderr)
        self.assertIn("denylist rule 2", result.stderr)
        self.assertNotIn(SECRET.lower(), (result.stdout + result.stderr).lower())

    def test_staged_scan_reads_the_index_not_the_work_tree(self):
        self.write("a.md", SECRET + "\n")
        self.git("add", "a.md")
        self.write("a.md", "cleaned up but not re-staged\n")
        self.assertEqual(self.guard("--staged").returncode, 1)

    def test_tree_scan_and_generic_patterns(self):
        self.write("a.md", "reach me at " + EMAIL + "\n")
        self.git("add", "a.md")
        result = self.guard("--tree", "--no-denylist")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[email]", result.stderr)

    def test_no_denylist_flag_skips_private_rules(self):
        self.write("a.md", SECRET + "\n")
        self.git("add", "a.md")
        self.assertEqual(self.guard("--tree", "--no-denylist").returncode, 0)
        self.assertEqual(self.guard("--tree").returncode, 1)

    def test_missing_denylist_warns_and_still_runs_generic(self):
        self.write("a.md", "fine\n")
        self.git("add", "a.md")
        missing = os.path.join(self.tmp.name, "nope.txt")
        result = self.guard("--staged", **{leak_guard.DENYLIST_ENV: missing})
        self.assertEqual(result.returncode, 0)
        self.assertIn("WARNING", result.stderr)
        self.assertEqual(self.guard("--staged", "--require-denylist", **{leak_guard.DENYLIST_ENV: missing}).returncode, 2)

    def test_commit_message_scan(self):
        message = os.path.join(self.tmp.name, "MSG")
        with open(message, "w") as handle:
            handle.write("fix thing for " + SECRET + "\n# a git comment naming " + SECRET + "\n")
        result = self.guard("--message", message)
        self.assertEqual(result.returncode, 1)
        self.assertIn("commit message:1", result.stderr)
        with open(message, "w") as handle:
            handle.write("fix thing\n")
        self.assertEqual(self.guard("--message", message).returncode, 0)

    def test_branch_name_scan(self):
        self.write("a.md", "fine\n")
        self.git("add", "a.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "one")
        self.git("checkout", "-q", "-b", "feature-" + SECRET.lower())
        result = self.guard("--branch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("branch name", result.stderr)
        self.assertNotIn(SECRET.lower(), result.stderr.lower())
        self.git("checkout", "-q", "-b", "feature-fine")
        self.assertEqual(self.guard("--branch").returncode, 0)

    def test_binary_file_is_skipped_with_a_note(self):
        with open(os.path.join(self.repo, "blob.bin"), "wb") as handle:
            handle.write(b"\x00\x01" + SECRET.encode())
        self.git("add", "blob.bin")
        result = self.guard("--staged")
        self.assertEqual(result.returncode, 0)
        self.assertIn("binary file not scanned", result.stderr)


if __name__ == "__main__":
    unittest.main()
