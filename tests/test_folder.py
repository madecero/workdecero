"""The private hand-off folder is there before the assistant's first write, in every skill.

Claude Code lets the assistant write a hand-off file without asking only while
the folder exists and is private. In a live run the folder had been removed,
and Claude Code asked about the hand-off file three times.
"""

import importlib
import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "hooks"))
import allow_helper  # noqa: E402

# The commands a skill can begin with, and the ones that come before a hand-off file is written.
FIRST = {
    "setup": ("status", "fields"),
    "sources": ("plan", "plan-refresh", "open", "show"),
    "job-sweep": ("start", "restart", "list", "open"),
    "contract-sweep": ("start", "restart", "list", "open"),
    "networking-sweep": ("start", "restart", "list", "open"),
}


class FolderBeforeTheFirstWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache", "workdecero")
        self.home = os.path.join(self.tmp.name, "config")
        os.makedirs(self.home, mode=0o700)
        self._env = dict((key, os.environ.get(key)) for key in ("WORKDECERO_HOME", "WORKDECERO_DRAFT_DIR"))
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ["WORKDECERO_DRAFT_DIR"] = self.cache
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def module(self, skill):
        scripts = os.path.join(_paths.REPO_ROOT, "skills", skill, "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        return importlib.import_module(allow_helper.HELPERS[skill][0][:-3])

    def call(self, module, command):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return module.main([command])

    def test_every_skill_is_covered(self):
        self.assertEqual(sorted(FIRST), sorted(allow_helper.HELPERS))

    def test_an_empty_cache_then_the_first_command_leaves_a_private_folder(self):
        for skill, commands in sorted(FIRST.items()):
            module = self.module(skill)
            for command in commands:
                self.assertIn(command, module.COMMANDS, skill)
                self.assertFalse(os.path.exists(self.cache), "%s %s" % (skill, command))
                self.call(module, command)                             # whatever it answers, even "no profile yet"
                info = os.lstat(self.cache)
                self.assertTrue(stat.S_ISDIR(info.st_mode), "%s %s" % (skill, command))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700, "%s %s" % (skill, command))
                # And that is exactly what the hook needs to approve the skill's hand-off file.
                original = allow_helper.HANDOFF_DIR
                allow_helper.HANDOFF_DIR = self.cache
                try:
                    self.assertTrue(allow_helper.write_allowed(os.path.join(self.cache, allow_helper.HELPERS[skill][2])),
                                    "%s %s" % (skill, command))
                finally:
                    allow_helper.HANDOFF_DIR = original
                for name in os.listdir(self.cache):
                    os.remove(os.path.join(self.cache, name))
                os.rmdir(self.cache)

    def test_a_command_that_ends_the_work_leaves_nothing_behind(self):
        for skill in sorted(FIRST):
            module = self.module(skill)
            for command in module.ENDS:
                self.assertIn(command, module.COMMANDS, skill)
        module = self.module("setup")
        self.call(module, "fields")
        self.assertTrue(os.path.isdir(self.cache))
        self.assertEqual(self.call(module, "cancel"), 0)
        self.assertFalse(os.path.exists(self.cache))

    def test_a_link_in_place_of_the_folder_is_never_followed(self):
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(os.path.dirname(self.cache))
        os.symlink(elsewhere, self.cache)
        for skill in sorted(FIRST):
            self.call(self.module(skill), FIRST[skill][0])
        self.assertTrue(os.path.islink(self.cache))
        self.assertEqual(os.listdir(elsewhere), [])


if __name__ == "__main__":
    unittest.main()
