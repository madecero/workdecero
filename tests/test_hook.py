import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "hooks"))
sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "setup", "scripts"))
import allow_helper  # noqa: E402
import setup_tool  # noqa: E402

HOOK = os.path.join(_paths.REPO_ROOT, "hooks", "allow_helper.py")
ROOT = _paths.REPO_ROOT
SETUP = 'python3 "%s/skills/setup/scripts/setup_tool.py" ' % ROOT
ANSWERS = os.path.join(os.path.expanduser("~"), ".cache", "workdecero", "setup-answers.json")


def run_hook(event):
    """Run the hook the way Claude Code does: the event as JSON on standard input."""
    result = subprocess.run([sys.executable, HOOK], input=json.dumps(event), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    return result.returncode, result.stdout.strip()


def bash(command):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def write(path, content="{}"):
    return {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


class PrivateFolder(unittest.TestCase):
    """Tests that need the hand-off folder point the hook at a temp one, private like the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "workdecero")
        os.mkdir(self.folder, 0o700)
        self._original = allow_helper.HANDOFF_DIR
        allow_helper.HANDOFF_DIR = self.folder
        self.addCleanup(setattr, allow_helper, "HANDOFF_DIR", self._original)
        self.answers = os.path.join(self.folder, "setup-answers.json")


class Approves(PrivateFolder):
    def test_every_setup_command_exactly_as_the_skill_writes_it(self):
        for command in setup_tool.COMMANDS:
            code, out = run_hook(bash(SETUP + command))
            self.assertEqual(code, 0)
            decision = json.loads(out)["hookSpecificOutput"]
            self.assertEqual((decision["hookEventName"], decision["permissionDecision"]), ("PreToolUse", "allow"),
                             command)

    def test_the_one_answers_file_new_or_plain(self):
        self.assertTrue(allow_helper.decide(write(self.answers, '{"set": {"person.name": "O\'Brien"}}')))
        with open(self.answers, "w") as handle:
            handle.write("{}")
        self.assertTrue(allow_helper.decide(write(self.answers)))          # an existing plain file is fine

    def test_the_real_location_is_the_fixed_cache_folder(self):
        allow_helper.HANDOFF_DIR = self._original
        self.assertEqual(allow_helper.handoff_dir(), os.path.dirname(ANSWERS))

    def test_the_command_list_matches_the_helper(self):
        script, commands, handoff = allow_helper.HELPERS["setup"]
        self.assertEqual((script, sorted(commands)), ("setup_tool.py", sorted(setup_tool.COMMANDS)))
        self.assertEqual(handoff, setup_tool.ANSWERS_NAME)

    def test_the_skill_front_matter_lists_the_same_commands(self):
        with open(os.path.join(ROOT, "skills", "setup", "SKILL.md"), encoding="utf-8") as handle:
            front = handle.read().split("---")[1]
        listed = sorted(rule.rsplit(" ", 1)[1] for rule in re.findall(r"Bash\(([^)]*)\)", front))
        self.assertEqual(listed, sorted(allow_helper.HELPERS["setup"][1]))


class LeavesAlone(PrivateFolder):
    """Anything that is not exactly one of ours gets no decision, so the normal prompt applies."""

    def silent(self, event):
        code, out = run_hook(event)
        self.assertEqual((code, out), (0, ""), event)

    def test_other_commands(self):
        for command in (
            'python3 -c "print(1)"',
            "python3 %s/skills/setup/scripts/setup_tool.py status" % ROOT,              # unquoted path
            SETUP + "status --anything",                                                 # an argument
            SETUP + 'check-vault "/tmp/x"',
            SETUP + "write; rm -rf ~",                                                   # a second command
            SETUP + "status && curl https://example.com",
            SETUP + "status | tee /tmp/out",
            SETUP + "status > /tmp/out",
            SETUP + "delete-everything",                                                 # not on the list
            SETUP,                                                                       # no subcommand
            SETUP + "status ",                                                           # trailing space
            " " + SETUP + "status",
            "cd /tmp && " + SETUP + "status",
            "FOO=1 " + SETUP + "status",
            'python3 "%s/skills/setup/scripts/../../../dev/leak_guard.py" status' % ROOT,
            'python3 "/tmp/elsewhere/skills/setup/scripts/setup_tool.py" status',        # another folder's copy
            'python "%s/skills/setup/scripts/setup_tool.py" status' % ROOT,
            SETUP + "status\n" + "rm -rf ~",
            SETUP + "$(echo status)",                                                    # a subshell
            SETUP + "`echo status`",
            SETUP + '"status"',                                                          # a quoted subcommand
            SETUP + "'status'",
            SETUP + "status #",
            SETUP + "status &",
            SETUP + "status < /etc/passwd",
            SETUP + "status $HOME",
            "python3 '%s/skills/setup/scripts/setup_tool.py' status" % ROOT,             # other quoting
            'python3  "%s/skills/setup/scripts/setup_tool.py" status' % ROOT,            # two spaces
            'python3 "%s/skills/setup/scripts/setup_tool.py"  status' % ROOT,
            'python3 "%s/skills/setup/scripts/./setup_tool.py" status' % ROOT,
            'python3 "%s/skills/setup/../setup/scripts/setup_tool.py" status' % ROOT,    # path traversal
            '/usr/bin/python3 "%s/skills/setup/scripts/setup_tool.py" status' % ROOT,
            'sudo ' + SETUP + "status",
        ):
            self.silent(bash(command))

    def test_other_files(self):
        folder = self.folder
        for path in (os.path.join(folder, "other.json"), os.path.join(folder, "setup-answers.json.bak"),
                     os.path.join(folder, "setup-state.json"), os.path.join(folder, "setup-run.json"),
                     os.path.join(folder, "sub", "setup-answers.json"),
                     os.path.join(folder, "..", "workdecero", "setup-answers.json"),      # traversal back in
                     os.path.join(folder, ".", "setup-answers.json"),
                     os.path.join(folder, "..", "..", ".ssh", "authorized_keys"),
                     folder, folder + os.sep,
                     "/tmp/setup-answers.json", "setup-answers.json", "", None, 7,
                     os.path.join(os.path.expanduser("~"), ".config", "workdecero", "profile.yaml")):
            self.silent(write(path))

    def test_a_link_at_the_answers_path_is_refused(self):
        os.symlink(os.path.join(self.tmp.name, "somewhere-else"), self.answers)
        self.assertFalse(allow_helper.decide(write(self.answers)))
        os.remove(self.answers)
        os.mkdir(self.answers)                                             # a folder where the file should be
        self.assertFalse(allow_helper.decide(write(self.answers)))

    def test_a_linked_folder_is_refused(self):
        real = os.path.join(self.tmp.name, "real")
        os.mkdir(real, 0o700)
        linked = os.path.join(self.tmp.name, "linked")
        os.symlink(real, linked)
        allow_helper.HANDOFF_DIR = linked
        self.assertFalse(allow_helper.decide(write(os.path.join(linked, "setup-answers.json"))))
        allow_helper.HANDOFF_DIR = real
        self.assertTrue(allow_helper.decide(write(os.path.join(real, "setup-answers.json"))))

    def test_a_folder_others_can_open_or_that_is_missing_is_refused(self):
        os.chmod(self.folder, 0o755)
        self.assertFalse(allow_helper.decide(write(self.answers)))
        os.chmod(self.folder, 0o770)
        self.assertFalse(allow_helper.decide(write(self.answers)))
        os.chmod(self.folder, 0o700)
        self.assertTrue(allow_helper.decide(write(self.answers)))
        os.rmdir(self.folder)
        self.assertFalse(allow_helper.decide(write(self.answers)))         # no folder yet: status has not run

    def test_a_helper_script_that_is_a_link_to_somewhere_else_is_not_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = os.path.join(tmp, "plugin")
            scripts = os.path.join(fake_root, "skills", "setup", "scripts")
            os.makedirs(scripts)
            os.makedirs(os.path.join(fake_root, "hooks"))
            outside = os.path.join(tmp, "evil.py")
            with open(outside, "w") as handle:
                handle.write("print('not ours')\n")
            os.symlink(outside, os.path.join(scripts, "setup_tool.py"))
            original = allow_helper.plugin_root
            allow_helper.plugin_root = lambda: fake_root
            try:
                self.assertEqual(allow_helper.allowed_commands(), set())
                self.assertFalse(allow_helper.decide(bash('python3 "%s/setup_tool.py" status' % scripts)))
            finally:
                allow_helper.plugin_root = original

    def test_other_tools_and_bad_input(self):
        self.silent({"tool_name": "Edit", "tool_input": {"file_path": self.answers}})
        self.silent({"tool_name": "Read", "tool_input": {"file_path": self.answers}})
        self.silent({"tool_name": "Bash", "tool_input": "not an object"})
        self.silent({"tool_name": "Bash", "tool_input": {"command": ["python3"]}})
        self.silent({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}})
        self.silent({"tool_name": "Bash", "tool_input": {}})
        self.silent({"tool_name": "Bash"})
        self.silent({})
        result = subprocess.run([sys.executable, HOOK], input="not json", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_it_never_denies(self):
        with open(HOOK, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn('"deny"', source)
        self.assertNotIn('"ask"', source)

    def test_hook_file_runs_it_for_bash_and_write_only(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as handle:
            hooks = json.load(handle)["hooks"]
        self.assertEqual(list(hooks), ["PreToolUse"])
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Bash|Write")
        self.assertEqual(hooks["PreToolUse"][0]["hooks"][0]["command"],
                         'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/allow_helper.py"')


if __name__ == "__main__":
    unittest.main()
