"""The private hand-off folder and open runs."""

import datetime
import json
import os
import sys
import tempfile
import unittest

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "engine"))

import handoff  # noqa: E402


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = os.environ.get(handoff.DRAFT_DIR_ENV)
        self.folder = os.path.join(self.tmp.name, "cache")
        os.environ[handoff.DRAFT_DIR_ENV] = self.folder
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop(handoff.DRAFT_DIR_ENV, None)
        else:
            os.environ[handoff.DRAFT_DIR_ENV] = self._old

    def test_a_run_opens_a_private_folder_and_closing_removes_it(self):
        self.assertFalse(handoff.run_is_open("sources"))
        handoff.open_run("sources")
        self.assertEqual(os.stat(self.folder).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(self.folder, "sources-run.json")).st_mode & 0o777, 0o600)
        self.assertTrue(handoff.run_is_open("sources"))
        self.assertFalse(handoff.run_is_open("job-sweep"))            # one skill's run is not another's
        handoff.require_run("sources", "plan")
        handoff.close_run("sources")
        self.assertFalse(os.path.exists(self.folder))

    def test_a_loose_folder_is_closed_again(self):
        os.makedirs(self.folder, mode=0o755)
        os.chmod(self.folder, 0o755)
        handoff.open_run("sources")
        self.assertEqual(os.stat(self.folder).st_mode & 0o777, 0o700)

    def test_a_run_ends_by_itself(self):
        start = datetime.datetime(2026, 3, 2, 9, 0)
        handoff.open_run("sources", now=start)
        self.assertTrue(handoff.run_is_open("sources", now=start + datetime.timedelta(minutes=119)))
        self.assertFalse(handoff.run_is_open("sources", now=start + datetime.timedelta(minutes=121)))
        self.assertFalse(handoff.run_is_open("sources", now=start - datetime.timedelta(minutes=5)))
        with self.assertRaises(ValueError) as caught:
            handoff.require_run("sources", "plan", now=start + datetime.timedelta(hours=3))
        self.assertIn("nothing was changed", str(caught.exception))

    def test_a_damaged_run_file_is_not_a_run(self):
        handoff.ensure_folder()
        with open(os.path.join(self.folder, "sources-run.json"), "w") as handle:
            handle.write("not json")
        self.assertFalse(handoff.run_is_open("sources"))

    def test_links_are_refused(self):
        real = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(real)
        os.symlink(real, self.folder)
        with self.assertRaises(ValueError):
            handoff.open_run("sources")
        self.assertEqual(os.listdir(real), [])
        os.remove(self.folder)
        handoff.open_run("sources")
        target = os.path.join(real, "data.json")
        with open(target, "w") as handle:
            json.dump({"remove": "x"}, handle)
        os.symlink(target, os.path.join(self.folder, "sources-entries.json"))
        with self.assertRaises(ValueError):
            handoff.read("sources-entries.json")
        self.assertTrue(os.path.isfile(target))                       # the linked file is neither read nor removed

    def test_a_hand_off_file_is_read_once(self):
        handoff.open_run("sources")
        path = os.path.join(self.folder, "sources-entries.json")
        with open(path, "w") as handle:
            json.dump({"remove": "O'Brien & Sons"}, handle)
        self.assertEqual(handoff.read("sources-entries.json"), {"remove": "O'Brien & Sons"})
        self.assertFalse(os.path.exists(path))
        self.assertTrue(handoff.run_is_open("sources"))               # the run outlives each hand-off
        with open(path, "w") as handle:
            handle.write("[1]")
        with self.assertRaises(ValueError):
            handoff.read("sources-entries.json")


if __name__ == "__main__":
    unittest.main()
