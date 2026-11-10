from contextlib import redirect_stdout
import io
import shutil
import stat
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "hooks"))
sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "engine"))
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
        # The session marks and the settings folder are temp ones too: a test never touches the real ones.
        self.sessions = os.path.join(self.tmp.name, "workdecero-sessions")
        self.addCleanup(setattr, allow_helper, "SESSIONS_DIR", allow_helper.SESSIONS_DIR)
        allow_helper.SESSIONS_DIR = self.sessions
        self.home = os.path.join(self.tmp.name, "config")
        os.mkdir(self.home, 0o700)
        previous = os.environ.get("WORKDECERO_HOME")
        os.environ["WORKDECERO_HOME"] = self.home
        self.addCleanup(lambda: os.environ.pop("WORKDECERO_HOME", None) if previous is None
                        else os.environ.__setitem__("WORKDECERO_HOME", previous))


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

    def test_every_helper_is_listed_exactly_and_shares_no_file(self):
        import importlib
        for skill, (script, commands, handoff) in allow_helper.HELPERS.items():
            scripts = os.path.join(ROOT, "skills", skill, "scripts")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            module = importlib.import_module(script[:-3])
            self.assertEqual(sorted(commands), sorted(module.COMMANDS), skill)
            with open(os.path.join(ROOT, "skills", skill, "SKILL.md"), encoding="utf-8") as handle:
                front = handle.read().split("---")[1]
            listed = sorted(rule.rsplit(" ", 1)[1] for rule in re.findall(r"Bash\(([^)]*)\)", front))
            self.assertEqual(listed, sorted(commands), skill)
            self.assertIn("Write(~/.cache/workdecero/%s)" % handoff, front)
            for subcommand in commands:
                line = 'python3 "%s" %s' % (os.path.join(scripts, script), subcommand)
                self.assertTrue(allow_helper.decide({"tool_name": "Bash", "tool_input": {"command": line}}), line)
                self.assertFalse(allow_helper.decide({"tool_name": "Bash", "tool_input": {"command": line + " x"}}))
        names = [name for _, _, name in allow_helper.HELPERS.values()]
        self.assertEqual(len(names), len(set(names)))
        skills = os.path.join(ROOT, "skills")
        with_helpers = [name for name in os.listdir(skills) if os.path.isfile(os.path.join(skills, name, "SKILL.md"))]
        self.assertEqual(sorted(with_helpers), sorted(allow_helper.HELPERS))

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

    def test_it_refuses_only_through_the_one_browser_rule(self):
        with open(HOOK, encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count('"deny"'), 1)
        self.assertNotIn('"ask"', source)
        # Nothing but a browser tool can ever be refused, even in a marked session with browser use off.
        self.assertTrue(allow_helper.mark_session(SESSION))
        for event in (bash("ls"), bash(SETUP + "status"), write("/tmp/x"),
                      {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
                      {"tool_name": "mcp__linear__save_comment", "tool_input": {}},
                      {"tool_name": "Skill", "tool_input": {"skill": "workdecero:job-sweep"}},
                      {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}):
            self.assertIsNone(allow_helper.refuse(dict(event, session_id=SESSION)), event)

    def test_hook_file_runs_it_for_bash_write_and_connected_tools(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as handle:
            hooks = json.load(handle)["hooks"]
        self.assertEqual(sorted(hooks), ["PreToolUse", "UserPromptSubmit"])
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Bash|Write|Skill|mcp__.*")
        self.assertNotIn("matcher", hooks["UserPromptSubmit"][0])
        self.assertEqual(hooks["PreToolUse"][0]["hooks"][0]["command"],
                         'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/allow_helper.py"')



SESSION, OTHER = "11111111-2222-3333-4444-555555555555", "99999999-8888-7777-6666-555555555555"
CHROME = {"hook_event_name": "PreToolUse", "tool_name": "mcp__claude-in-chrome__tabs_create_mcp", "tool_input": {},
          "session_id": SESSION}


def slash(text, session=SESSION):
    return {"hook_event_name": "UserPromptSubmit", "prompt": text, "session_id": session}


def skill(name, session=SESSION):
    return {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": name}, "session_id": session}


class BrowserInAWorkdeceroSession(PrivateFolder):
    """workdecero must never open the person's browser unless they turned that on."""

    def through_main(self, event):
        out = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(event))
        try:
            with redirect_stdout(out):
                code = allow_helper.main()
        finally:
            sys.stdin = stdin
        return code, out.getvalue().strip()

    def flags(self, value):
        path = os.path.join(self.home, "flags.json")
        with open(path, "w") as handle:
            json.dump(value, handle)
        os.chmod(path, 0o600)
        return path

    def refused(self, event=CHROME):
        code, out = self.through_main(event)
        self.assertEqual(code, 0)
        if not out:
            return False
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertTrue(decision["permissionDecisionReason"].startswith("workdecero: browser use is off in your profile."))
        return True

    # ---- the session is marked the moment a skill starts, before any tool runs
    def test_a_browser_call_before_the_helper_has_run_is_refused(self):
        self.flags({"browser_enabled": False})
        self.assertFalse(self.refused())                               # an ordinary conversation: left alone
        self.assertEqual(self.through_main(slash("/workdecero:networking-sweep")), (0, ""))
        self.assertTrue(self.refused())                                # no run file exists yet, and it is refused
        self.assertEqual(os.listdir(self.folder), [])
        for tool in ("mcp__claude-in-chrome__navigate", "mcp__claude-in-chrome__computer",
                     "mcp__playwright__browser_navigate", "mcp__puppeteer__goto", "mcp__some_server__open_browser",
                     "mcp__browserbase__session"):
            self.assertTrue(self.refused(dict(CHROME, tool_name=tool)), tool)

    def test_a_skill_started_in_plain_words_marks_the_session_too(self):
        self.assertEqual(self.through_main(skill("workdecero:job-sweep")), (0, ""))   # says nothing: the prompt decides
        self.assertTrue(self.refused())

    def test_a_helper_command_marks_the_session_as_well(self):
        code, out = self.through_main(dict(bash(SETUP + "status"), session_id=SESSION))
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertTrue(self.refused())

    def test_other_prompts_and_other_skills_mark_nothing(self):
        for event in (slash("run /workdecero:job-sweep later"), slash("/other:thing"), slash("tell me about workdecero"),
                      skill("other:workdecero"), skill("docs"), dict(bash("ls"), session_id=SESSION),
                      {"hook_event_name": "UserPromptSubmit", "prompt": None, "session_id": SESSION}):
            self.through_main(event)
        self.assertFalse(os.path.exists(self.sessions))
        self.assertFalse(self.refused())

    # ---- only that conversation
    def test_another_session_is_untouched(self):
        self.through_main(slash("/workdecero:job-sweep"))
        self.assertTrue(self.refused())
        self.assertFalse(self.refused(dict(CHROME, session_id=OTHER)))
        self.assertFalse(self.refused(dict((key, value) for key, value in CHROME.items() if key != "session_id")))

    def test_a_mark_lasts_a_day_and_old_ones_are_cleared(self):
        self.through_main(slash("/workdecero:job-sweep"))
        mark = os.path.join(self.sessions, SESSION)
        self.assertEqual(stat.S_IMODE(os.stat(mark).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.sessions).st_mode), 0o700)
        self.assertEqual(os.path.getsize(mark), 0)                     # an empty file: the id is all it holds
        old = time.time() - allow_helper.MARK_KEPT_SECONDS - 60
        os.utime(mark, (old, old))
        self.assertFalse(self.refused())
        self.through_main(slash("/workdecero:setup", session=OTHER))
        self.assertEqual(os.listdir(self.sessions), [OTHER])

    def test_a_session_id_that_is_not_a_plain_id_never_becomes_a_file(self):
        for bad in ("../../escape", "a/b/c/d/e/f/g/h", "short", "x" * 200, "", None, 12345678, "has space-in-it-xx"):
            self.assertFalse(allow_helper.mark_session(bad), bad)
            self.assertFalse(allow_helper.session_marked(bad), bad)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape")))

    # ---- the person's choice
    def test_a_normal_prompt_when_browser_use_is_on(self):
        self.through_main(slash("/workdecero:job-sweep"))
        self.flags({"browser_enabled": True})
        self.assertEqual(self.through_main(CHROME), (0, ""))           # nothing said: Claude Code asks as usual
        self.assertFalse(allow_helper.decide(CHROME))                  # and it is never approved for them

    def test_an_unknown_setting_is_refused_inside_a_marked_session(self):
        self.through_main(slash("/workdecero:job-sweep"))
        self.assertTrue(self.refused())                                # no flags file at all
        for value in ({}, {"browser_enabled": "true"}, {"browser_enabled": 1}, ["x"], {"browser_enabled": None}):
            self.flags(value)
            self.assertTrue(self.refused(), value)
        with open(os.path.join(self.home, "flags.json"), "w") as handle:
            handle.write("not json")
        self.assertTrue(self.refused())

    def test_a_flags_file_that_cannot_be_trusted_counts_as_off(self):
        self.through_main(slash("/workdecero:job-sweep"))
        path = self.flags({"browser_enabled": True})
        self.assertFalse(self.refused())
        os.chmod(path, 0o666)                                          # anyone could have written it
        self.assertTrue(self.refused())
        os.chmod(path, 0o600)
        os.chmod(self.home, 0o777)                                     # anyone could have replaced it
        self.assertTrue(self.refused())
        os.chmod(self.home, 0o700)
        os.remove(path)
        real = os.path.join(self.tmp.name, "elsewhere.json")
        with open(real, "w") as handle:
            json.dump({"browser_enabled": True}, handle)
        os.symlink(real, path)                                         # a link is not their settings file
        self.assertTrue(self.refused())

    def test_a_mark_that_cannot_be_trusted_is_ignored(self):
        self.flags({"browser_enabled": False})
        self.through_main(slash("/workdecero:job-sweep"))
        os.chmod(self.sessions, 0o755)                                 # the folder is not private
        self.assertFalse(self.refused())
        self.assertFalse(allow_helper.mark_session(OTHER))             # and nothing is written into it
        os.chmod(self.sessions, 0o700)
        os.remove(os.path.join(self.sessions, SESSION))
        os.symlink(os.path.join(self.home, "flags.json"), os.path.join(self.sessions, SESSION))
        self.assertFalse(self.refused())                               # a link in place of the mark
        self.assertFalse(allow_helper.mark_session(SESSION))           # is not followed either
        shutil.rmtree(self.sessions)
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.mkdir(elsewhere, 0o700)
        os.symlink(elsewhere, self.sessions)                           # a link in place of the folder
        self.assertFalse(allow_helper.mark_session(SESSION))
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertFalse(self.refused())

    # ---- the settings side
    def test_reading_or_saving_the_profile_keeps_the_flag_in_step(self):
        import wdprofile
        self.assertIs(wdprofile.write_flags({}), False)
        path = os.path.join(self.home, "flags.json")
        with open(path) as handle:
            self.assertEqual(json.load(handle), {"browser_enabled": False})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertIs(wdprofile.write_flags({"integrations": {"browser": {"enabled": True}}}), True)
        self.assertTrue(allow_helper.browser_enabled())
        self.assertIs(wdprofile.write_flags({"integrations": {"browser": {"enabled": "yes"}}}), False)
        self.assertFalse(allow_helper.browser_enabled())
        # Every helper reads the profile before it does anything, and that read writes the flag.
        os.remove(path)
        shutil.copy(os.path.join(ROOT, "examples", "profiles", "product-leader-denver.yaml"),
                    os.path.join(self.home, "profile.yaml"))
        profile, problems = wdprofile.load()
        self.assertEqual(problems, [])
        with open(path) as handle:
            self.assertEqual(json.load(handle), {"browser_enabled": False})
        # Checking an example profile somewhere else writes nothing.
        os.remove(path)
        wdprofile.load(os.path.join(ROOT, "examples", "profiles", "product-leader-denver.yaml"))
        self.assertFalse(os.path.exists(path))

    def test_every_web_skill_says_never_open_a_browser(self):
        for name in ("job-sweep", "contract-sweep", "sources"):
            with open(os.path.join(ROOT, "skills", name, "SKILL.md"), encoding="utf-8") as handle:
                self.assertIn("**Never open a browser", handle.read(), name)


if __name__ == "__main__":
    unittest.main()
