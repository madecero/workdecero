"""The release audit: it finds what it should, and never prints what it found. Offline, with an invented repository."""

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "dev"))
import release_audit  # noqa: E402

SECRET = "Hidden Harbor Foods"
RULES = [(7, re.compile("hidden harbor", re.IGNORECASE))]
ENV = dict(os.environ, GIT_AUTHOR_NAME="Sample Author", GIT_AUTHOR_EMAIL="author@example.com",
           GIT_COMMITTER_NAME="Sample Committer", GIT_COMMITTER_EMAIL="committer@example.com",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "A clean file.\n")
        self.commit("First commit")
        self.write("notes.txt", "line one\nA client called %s paid late.\n" % SECRET)
        self.commit("Add notes")
        self.write("notes.txt", "line one\nA client paid late.\n")              # removed, but still in history
        self.write("picture.bin", b"\x89PNG\x00\x01\x02")
        self.write(os.path.join("clients", "hidden harbor.md"), "Nothing private inside.\n")
        self.commit("Tidy up, with thanks to %s" % SECRET)

    def git(self, *args):
        subprocess.run(["git"] + list(args), cwd=self.repo, check=True, env=ENV, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)

    def write(self, name, content):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(content, bytes) else "w") as handle:
            handle.write(content)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_audit(self):
        audit = release_audit.Audit(RULES)
        binaries_dir = os.path.join(self.tmp.name, "binaries")
        previous = dict((key, os.environ.get(key)) for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"))
        os.environ.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        try:
            commits, binaries = release_audit.audit_history(audit, self.repo, ["--all"], binaries_dir)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        out = io.StringIO()
        release_audit.report(audit, binaries, binaries_dir, out=out)
        return audit, commits, binaries, binaries_dir, out.getvalue()

    def test_it_finds_a_name_in_an_old_version_a_message_and_a_path(self):
        audit, commits, binaries, _, text = self.run_audit()
        self.assertEqual(len(commits), 3)
        places = [place for _, place, _ in audit.findings]
        rules = set(rule for _, _, rule in audit.findings)
        self.assertEqual(rules, {"denylist line 7"})
        self.assertTrue(any(place.startswith("file notes.txt (blob ") and ", line 2, first in commit " in place
                            for place in places), places)             # gone from the file, still in history
        self.assertTrue(any(re.match(r"commit [0-9a-f]{10} message, line 1$", place) for place in places), places)
        self.assertTrue(any(place.startswith("a file path, first seen in ") for place in places), places)
        self.assertEqual(len(audit.findings), 3)
        self.assertIn("FINDINGS: 3", text)

    def test_it_never_prints_what_matched(self):
        _, _, _, _, text = self.run_audit()
        self.assertNotIn(SECRET, text)
        self.assertNotIn("Hidden", text)
        self.assertNotIn("hidden harbor", text)                        # the path that matched is not shown either
        self.assertNotIn("paid late", text)
        self.assertIn("denylist line 7", text)

    def test_identities_and_binary_files_are_listed(self):
        audit, _, binaries, binaries_dir, text = self.run_audit()
        self.assertIn(("commit author", "Sample Author", "author@example.com"), audit.identities)
        self.assertEqual(audit.identities[("committer", "Sample Committer", "committer@example.com")], 3)
        self.assertIn("commit author          Sample Author <author@example.com>  (3)", text)
        self.assertEqual([(path, size) for _, path, size in binaries], [("picture.bin", 7)])
        copied = os.listdir(binaries_dir)
        self.assertEqual(len(copied), 1)
        self.assertTrue(copied[0].endswith("-picture.bin"))
        self.assertIn("BINARY FILES IN HISTORY: 1", text)
        self.assertIn("WHAT THIS AUDIT CANNOT SEE", text)

    def test_generic_patterns_are_found_and_long_dashes_only_when_asked(self):
        address = "someone@" + "realcompany.io"                         # built here so this file holds no address
        self.write("contact.txt", "Write to " + address + " " + chr(0x2014) + " soon.\n")
        self.commit("Add a contact")
        audit, _, _, _, text = self.run_audit()
        self.assertIn("email", [rule for _, _, rule in audit.findings])
        self.assertNotIn("long-dash", [rule for _, _, rule in audit.findings])
        self.assertNotIn("realcompany", text)
        styled = release_audit.Audit(RULES, style=True)
        styled.scan("x", "somewhere", "a " + chr(0x2014) + " b")
        self.assertEqual([rule for _, _, rule in styled.findings], ["long-dash"])


class Threads(unittest.TestCase):
    def test_a_pull_request_is_read_with_its_history_and_nothing_matched_is_kept(self):
        audit = release_audit.Audit(RULES)
        node = {"number": 8, "title": "A clean title", "body": "Clean now.", "author": {"login": "sample-user"},
                "userContentEdits": {"pageInfo": {"hasNextPage": False},
                                     "nodes": [{"diff": "An earlier body naming %s." % SECRET, "editor": {"login": "sample-user"}}]},
                "comments": {"pageInfo": {"hasNextPage": True},
                             "nodes": [{"body": "fine", "author": {"login": "other-user"},
                                        "userContentEdits": {"nodes": [{"diff": "about %s" % SECRET}]}}]},
                "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": [
                    {"body": "ok", "author": {"login": "reviewer-one"}, "userContentEdits": {"nodes": []},
                     "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [{"body": SECRET, "userContentEdits": {"nodes": []}}]}}]},
                "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [
                    {"__typename": "RenamedTitleEvent", "previousTitle": "Work for %s" % SECRET, "currentTitle": "A clean title"},
                    {"__typename": "HeadRefForcePushedEvent", "beforeCommit": {"oid": "a" * 40}, "afterCommit": {"oid": "b" * 40}},
                    {"__typename": "HeadRefForcePushedEvent", "beforeCommit": None, "afterCommit": {"oid": "c" * 40}}]}}
        pushed = release_audit.audit_thread(audit, node, "pull request")
        self.assertEqual(pushed, [(8, "a" * 40)])
        places = sorted(place for _, place, _ in audit.findings)
        self.assertEqual(places, ["pull request #8 body, earlier version 1, line 1",
                                  "pull request #8 comment 1, earlier version 1, line 1",
                                  "pull request #8 review 1, comment 1, line 1",
                                  "pull request #8, an earlier title, line 1"])
        self.assertIn(("pull request author", "sample-user", ""), audit.identities)
        self.assertIn(("reviewer", "reviewer-one", ""), audit.identities)
        self.assertTrue(any("has more entries in comments than one query returns" in note for note in audit.notes))
        self.assertTrue(any("no longer names the commit that was replaced" in note for note in audit.notes))
        out = io.StringIO()
        release_audit.report(audit, [], None, out=out)
        self.assertNotIn(SECRET, out.getvalue())

    def test_it_only_ever_reads(self):
        with open(os.path.join(_paths.REPO_ROOT, "dev", "release_audit.py"), encoding="utf-8") as handle:
            source = handle.read()
        for word in ("mutation", "--method", "-X", "POST", "PATCH", "DELETE", "PUT", "git push", '"push"', "pr edit",
                     "pr close", "pr merge", "issue edit", "issue close", "repo edit", "repo delete"):
            self.assertNotIn(word, source, word)


if __name__ == "__main__":
    unittest.main()
