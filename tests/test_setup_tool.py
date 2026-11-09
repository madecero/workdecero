import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "setup", "scripts"))
import miniyaml  # noqa: E402
import setup_tool  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")
RESUMES = os.path.join(_paths.REPO_ROOT, "examples", "resumes")


def tree(root):
    out = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha1(handle.read()).hexdigest()
    return out


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.home = os.path.join(root, "config")
        self.vault = os.path.join(root, "Notes")
        self.parent = root
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        with open(os.path.join(self.vault, "Mine.md"), "w") as handle:
            handle.write("---\nstatus: mine\n---\nMy own note.\n")
        self.registry = os.path.join(root, "obsidian.json")
        with open(self.registry, "w") as handle:
            json.dump({"vaults": {"a": {"path": self.vault, "ts": 5, "open": True},
                                  "b": {"path": os.path.join(root, "gone"), "ts": 9}}}, handle)
        self._env = dict((key, os.environ.get(key)) for key in ("WORKDECERO_HOME", setup_tool.OBSIDIAN_CONFIG_ENV))
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ[setup_tool.OBSIDIAN_CONFIG_ENV] = self.registry
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def draft(self, name="product-leader-denver", **changes):
        with open(os.path.join(EXAMPLES, name + ".yaml"), encoding="utf-8") as handle:
            profile = miniyaml.loads(handle.read())
        profile["storage"] = {"vault_path": self.vault, "vault_folder": "workdecero"}
        profile.update(changes)
        return profile

    def run_tool(self, *args, draft=None):
        out, err = io.StringIO(), io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(draft) if draft is not None else "")
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = setup_tool.main(list(args))
        finally:
            sys.stdin = stdin
        return code, out.getvalue(), err.getvalue()

    def disk(self):
        return tree(self.tmp.name)


class ReadOnlyCommands(SetupCase):
    def test_status_on_a_fresh_machine(self):
        before = self.disk()
        code, out, _ = self.run_tool("status")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(report["profile_exists"])
        self.assertIsNone(report["profile"])
        self.assertEqual([item["name"] for item in report["known_vaults"]], ["Notes"])   # the missing one is dropped
        self.assertEqual(self.disk(), before)

    def test_known_vaults_are_read_only_and_survive_a_broken_list(self):
        with open(self.registry, "rb") as handle:
            before = handle.read()
        setup_tool.known_vaults()
        with open(self.registry, "rb") as handle:
            self.assertEqual(handle.read(), before)
        with open(self.registry, "w") as handle:
            handle.write("not json")
        self.assertEqual(setup_tool.known_vaults(), [])
        os.remove(self.registry)
        self.assertEqual(setup_tool.known_vaults(), [])

    def test_fields_lists_every_setting_in_plain_words(self):
        code, out, _ = self.run_tool("fields")
        self.assertEqual(code, 0)
        self.assertIn("jobs.salary_floor: Lowest base salary you would consider", out)
        self.assertIn("location.work_modes: The ways of working you accept. A list from: remote, hybrid, onsite.", out)
        self.assertIn("exclusions[].kind:", out)
        self.assertIn("mode:", out)

    def test_check_vault_refuses_non_vault_folders_with_a_reason(self):
        code, out, _ = self.run_tool("check-vault", self.parent)
        self.assertEqual(code, 1)
        self.assertIn("is not an Obsidian vault", out)
        self.assertIn("did you mean Notes?", out)                    # the parent of the real vault
        code, out, _ = self.run_tool("check-vault", os.path.join(self.parent, "typo"))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)
        code, out, _ = self.run_tool("check-vault", self.vault, "/abs/path")
        self.assertEqual(code, 1)
        self.assertIn("not a full path", out)
        code, out, _ = self.run_tool("check-vault", self.vault)
        self.assertEqual(code, 0)
        self.assertIn("A workdecero folder will be added, and nothing else is touched.", out)

    def test_read_resume_text_markdown_and_word(self):
        code, out, _ = self.run_tool("read-resume", os.path.join(RESUMES, "nurse-manager-texas.md"))
        self.assertEqual(code, 0)
        self.assertIn("Dana Whitfield", out)
        docx = os.path.join(self.tmp.name, "resume.docx")
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml",
                             "<w:document><w:body><w:p><w:r><w:t>Sam Example</w:t></w:r></w:p>"
                             "<w:p><w:r><w:t>Office &amp; Operations Manager</w:t></w:r></w:p></w:body></w:document>")
        code, out, _ = self.run_tool("read-resume", docx)
        self.assertEqual(out.strip(), "Sam Example\nOffice & Operations Manager")
        for bad, message in (("nope.txt", "No file at"), (os.path.join(self.tmp.name, "x.pdf"), "No file at")):
            code, _, err = self.run_tool("read-resume", bad)
            self.assertEqual(code, 2)
            self.assertIn(message, err)
        pdf = os.path.join(self.tmp.name, "resume.pdf")
        open(pdf, "wb").close()
        self.assertIn("Read tool", self.run_tool("read-resume", pdf)[2])

    def test_every_example_profile_has_a_resume(self):
        profiles = sorted(name[:-5] for name in os.listdir(EXAMPLES) if name.endswith(".yaml"))
        resumes = sorted(name[:-3] for name in os.listdir(RESUMES) if name.endswith(".md") and name != "README.md")
        self.assertEqual(profiles, resumes)
        for name in profiles:
            with open(os.path.join(EXAMPLES, name + ".yaml"), encoding="utf-8") as handle:
                person = miniyaml.loads(handle.read())["person"]
            with open(os.path.join(RESUMES, name + ".md"), encoding="utf-8") as handle:
                resume = handle.read()
            self.assertIn(person["name"], resume)
            if person.get("current_employer"):
                self.assertIn(person["current_employer"], resume)


class Preview(SetupCase):
    def test_preview_shows_a_plain_summary_and_writes_nothing(self):
        before = self.disk()
        draft = self.draft(_inferred=["person.name", "jobs.titles", "location.home_base", "person.current_employer"])
        code, out, _ = self.run_tool("preview", draft=draft)
        self.assertEqual(code, 0)
        for heading in ("YOU", "LOOKING FOR", "WHERE", "LEAVE OUT AND KEEP QUIET", "TIME AND TOOLS", "TRACKER"):
            self.assertIn("\n%s\n" % heading, "\n" + out)
        self.assertIn("  Theo Lindqvist (from your resume)", out)
        self.assertIn("  Pay floor: $210,000 a year, aiming for $260,000.", out)
        self.assertIn("  Home base: Denver, CO (from your resume)", out)
        self.assertIn("  Current employer: Granite Peak Software (from your resume). Always left out", out)
        self.assertIn("  Quiet search: yes.", out)
        self.assertIn("  Each digest shows up to 8 new items. Mode: Lite (sized for a Pro plan).", out)
        self.assertIn("  Browser: off. Signed-in LinkedIn: off.", out)
        self.assertIn("READY TO SAVE. Nothing has been written yet.", out)
        for jargon in ("vault_folder", "schema", "yaml", "wd_", "search_visibility"):
            self.assertNotIn(jargon, out.lower())
        self.assertEqual(self.disk(), before)

    def test_summary_shrinks_when_sweeps_are_off(self):
        _, out, _ = self.run_tool("preview", draft=self.draft("nurse-manager-texas"))
        self.assertIn("  Sweeps on: salaried jobs (off: contract work, networking)", out)
        self.assertNotIn("Hourly floor", out)
        self.assertNotIn("Networking, to meet", out)
        full = self.run_tool("preview", draft=self.draft())[1]
        self.assertLessEqual(len(out.splitlines()), len(full.splitlines()) - 6)

    def test_the_file_is_shown_only_when_asked(self):
        draft = self.draft()
        self.assertNotIn("schema_version", self.run_tool("preview", draft=draft)[1])
        _, out, _ = self.run_tool("preview", "--file", draft=draft)
        self.assertIn("profile file as it would be written", out)
        self.assertIn("schema_version: 1", out)

    def test_a_pay_floor_off_by_a_thousand_comes_back_as_a_question(self):
        draft = self.draft()
        draft["jobs"]["salary_floor"] = 210
        code, out, _ = self.run_tool("preview", draft=draft)
        self.assertEqual(code, 1)
        self.assertIn("QUESTIONS TO ASK BEFORE SAVING (1)", out)
        self.assertIn("jobs.salary_floor is 210", out)
        self.assertIn("Did you mean 210000?", out)
        self.assertNotIn("READY TO SAVE", out)

    def test_missing_answers_are_listed_as_things_to_fix(self):
        draft = self.draft()
        del draft["jobs"]["salary_floor"]
        draft["location"]["work_modes"] = []
        code, out, _ = self.run_tool("preview", draft=draft)
        self.assertEqual(code, 1)
        self.assertIn("TO FIX BEFORE THIS CAN BE SAVED (2)", out)
        self.assertIn("jobs.salary_floor: this setting is required but missing.", out)

    def test_helper_keys_and_empty_answers_are_never_saved(self):
        draft = self.draft(_inferred=["person.name"], _new_vault=False, _confirmed=[])
        draft["person"]["website"] = "   "
        draft["jobs"]["deal_breakers"] = []
        draft["writing"] = {}
        profile, inferred, _ = setup_tool.profile_from_draft(draft)
        text = setup_tool.profile_text(profile)
        for gone in ("_inferred", "_new_vault", "_confirmed", "website", "deal_breakers", "writing"):
            self.assertNotIn(gone, text)
        self.assertEqual(inferred, ["person.name"])
        self.assertEqual(list(profile)[:4], ["schema_version", "person", "location", "tracks"])
        self.assertIs(profile["tracks"]["networking"], True)
        nurse, _, _ = setup_tool.profile_from_draft(self.draft("nurse-manager-texas"))
        self.assertIs(nurse["tracks"]["contracts"], False)           # false is an answer, and is kept

    def test_paths_under_the_home_folder_are_saved_in_short_form(self):
        home = os.path.expanduser("~")
        draft = self.draft()
        draft["person"]["resume_path"] = os.path.join(home, "Documents", "resume.pdf")
        profile, _, _ = setup_tool.profile_from_draft(draft)
        self.assertEqual(profile["person"]["resume_path"], os.path.join("~", "Documents", "resume.pdf"))
        self.assertEqual(profile["storage"]["vault_path"], self.vault)      # not under home: left as given

    def test_draft_can_be_passed_on_the_command_line_with_escaped_apostrophes(self):
        draft = self.draft()
        draft["person"]["headline"] = "Leader who doesn't ship slideware"
        text = json.dumps(draft).replace("'", "\\u0027")
        self.assertNotIn("'", text)
        self.assertNotIn("\n", text)
        code, out, _ = self.run_tool("preview", "--draft", text)
        self.assertEqual(code, 0, out)
        self.assertIn("  Leader who doesn't ship slideware", out)
        code, _, err = self.run_tool("preview", "--draft")
        self.assertEqual(code, 2)
        self.assertIn("--draft needs the draft JSON", err)

    def test_broken_draft_is_reported_not_guessed_at(self):
        out, err = io.StringIO(), io.StringIO()
        stdin, sys.stdin = sys.stdin, io.StringIO("{ not json")
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = setup_tool.main(["preview"])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err.getvalue())


class Write(SetupCase):
    def test_write_saves_a_valid_profile_and_sets_up_the_vault(self):
        code, out, _ = self.run_tool("write", draft=self.draft(_inferred=["person.name"]))
        self.assertEqual(code, 0, out)
        profile, problems = wdprofile.load()
        self.assertEqual(problems, [])
        self.assertEqual(profile["person"]["name"], "Theo Lindqvist")
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "workdecero", "Start here.md")))
        self.assertTrue(os.path.isdir(os.path.join(self.vault, "workdecero", "Jobs")))
        self.assertIn("workdecero is set up.", out)
        self.assertIn("  Tracker      Notes / workdecero / Start here   (8 files added)", out)
        self.assertIn('Next:   say "run the job sweep". The first one takes about 10 minutes in Lite mode.', out)
        self.assertIn("Open:   obsidian://open?path=", out)
        self.assertIn("Start%20here.md", out)
        self.assertIn("Change anything later:   /workdecero:setup", out)
        self.assertIn(setup_tool.PRIVACY, out)

    def test_saved_file_matches_the_example_it_came_from(self):
        self.run_tool("write", draft=self.draft())
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            saved = miniyaml.loads(handle.read())
        self.assertEqual(saved, self.draft())

    def test_quiet_search_leaves_no_trace_of_the_employer_in_the_vault(self):
        self.run_tool("write", draft=self.draft())
        for folder, _, files in os.walk(self.vault):
            for name in files:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    text = handle.read()
                for secret in ("Granite Peak", "Summit Ledger", "Alpenglow", "Theo", "Lindqvist"):
                    self.assertNotIn(secret, text, name)

    def test_nothing_outside_the_tracker_folder_and_config_folder_changes(self):
        before = self.disk()
        self.run_tool("write", draft=self.draft())
        after = self.disk()
        changed = sorted(path for path in after if before.get(path) != after[path])
        self.assertTrue(all(path.startswith(("config" + os.sep, os.path.join("Notes", "workdecero") + os.sep))
                            for path in changed), changed)
        self.assertEqual(after["obsidian.json"], before["obsidian.json"])
        self.assertEqual(after[os.path.join("Notes", "Mine.md")], before[os.path.join("Notes", "Mine.md")])

    def test_wrong_folder_is_refused_before_anything_is_written(self):
        before = self.disk()
        draft = self.draft()
        draft["storage"]["vault_path"] = self.parent
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 1)
        self.assertIn("VAULT PROBLEM", out)
        self.assertIn("is not an Obsidian vault", out)
        self.assertIn("Nothing was written.", out)
        self.assertEqual(self.disk(), before)                         # no profile either

    def test_problems_and_open_questions_block_the_save(self):
        before = self.disk()
        draft = self.draft()
        draft["jobs"]["salary_floor"] = 210
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 1)
        self.assertIn("Did you mean 210000?", out)
        self.assertIn("Nothing was written.", out)
        self.assertEqual(self.disk(), before)
        draft["_confirmed"] = ["jobs.salary_floor"]                   # the person was asked and kept it
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 0)
        self.assertEqual(wdprofile.load()[0]["jobs"]["salary_floor"], 210)

    def test_new_vault_needs_an_explicit_yes(self):
        target = os.path.join(self.tmp.name, "Brand New Vault")
        draft = self.draft()
        draft["storage"]["vault_path"] = target
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(target))
        draft["_new_vault"] = True
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(target, "workdecero", "Start here.md")))
        self.assertFalse(os.path.exists(os.path.join(target, ".obsidian")))
        self.assertIn('Open:   in Obsidian, choose "Open folder as vault"', out)   # Obsidian does not know it yet

    def test_first_sweep_named_on_the_done_screen_follows_what_is_on(self):
        _, out, _ = self.run_tool("write", draft=self.draft("freelance-designer-portland"))
        self.assertIn('say "run the contract sweep"', out)


class RunAgain(SetupCase):
    def test_second_run_starts_from_the_saved_profile(self):
        self.run_tool("write", draft=self.draft())
        code, out, _ = self.run_tool("status")
        report = json.loads(out)
        self.assertTrue(report["profile_exists"])
        self.assertEqual(report["profile"], self.draft())
        self.assertIn("  Theo Lindqvist", report["summary"])
        self.assertEqual(report["profile_problems"], [])

    def test_changing_one_thing_changes_only_that_and_keeps_a_backup(self):
        self.run_tool("write", draft=self.draft())
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            first = handle.read()
        vault_before = tree(self.vault)
        draft = json.loads(self.run_tool("status")[1])["profile"]
        draft["digest"]["max_new_items"] = 5
        code, out, _ = self.run_tool("write", draft=draft)
        self.assertEqual(code, 0)
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            second = handle.read()
        differing = [(a, b) for a, b in zip(first.splitlines(), second.splitlines()) if a != b]
        self.assertEqual(differing, [("  max_new_items: 8", "  max_new_items: 5")])
        self.assertEqual(tree(self.vault), vault_before)              # the tracker is untouched
        self.assertIn("already in place, nothing overwritten", out)
        backups = os.listdir(os.path.join(self.home, "backups"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.home, "backups", backups[0]), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)

    def test_saving_the_same_answers_again_writes_nothing(self):
        self.run_tool("write", draft=self.draft())
        before = self.disk()
        code, out, _ = self.run_tool("write", draft=self.draft())
        self.assertEqual(code, 0)
        self.assertIn("unchanged", out)
        self.assertEqual(self.disk(), before)

    def test_a_hand_broken_profile_is_reported(self):
        os.makedirs(self.home)
        with open(wdprofile.profile_path(), "w") as handle:
            handle.write("schema_version: 1\nperson:\n\tname: Sam\n")
        report = json.loads(self.run_tool("status")[1])
        self.assertTrue(report["profile_exists"])
        self.assertIn("cannot be read", report["profile_problems"][0])


CSV = """Notes:
"Some email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Priya,Raman,https://www.linkedin.com/in/example-priya,priya@raman.example.com,Front Range Robotics,VP Engineering,09 Nov 2024
Tom,Becker,https://www.linkedin.com/in/example-tom,,Granite Peak Software,Engineer,15 Mar 2023
"""


class Connections(SetupCase):
    def csv_path(self):
        path = os.path.join(self.tmp.name, "Connections.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(CSV)
        return path

    def test_import_needs_a_saved_profile(self):
        code, out, _ = self.run_tool("import-connections", self.csv_path())
        self.assertEqual(code, 1)
        self.assertIn("Finish setup first", out)

    def test_import_adds_people_without_emails_by_default(self):
        self.run_tool("write", draft=self.draft())
        code, out, _ = self.run_tool("import-connections", self.csv_path())
        self.assertEqual(code, 0)
        self.assertIn("Connections imported: 1 added, 0 already known and refreshed.", out)
        self.assertIn("1 left out because they work at an employer you keep confidential.", out)
        self.assertIn("Email addresses: not included.", out)
        store = vault.VaultStore(self.vault)
        people = store.rows("person")
        self.assertEqual([row.title for row in people], ["Priya Raman"])
        self.assertNotIn("email", people[0].values)

    def test_import_includes_emails_only_with_the_flag(self):
        self.run_tool("write", draft=self.draft())
        code, out, _ = self.run_tool("import-connections", self.csv_path(), "--include-emails")
        self.assertIn("Email addresses: included, as you asked.", out)
        self.assertEqual(vault.VaultStore(self.vault).rows("person")[0].values["email"], "priya@raman.example.com")

    def test_missing_file_explains_where_to_get_it(self):
        self.run_tool("write", draft=self.draft())
        code, out, _ = self.run_tool("import-connections", os.path.join(self.tmp.name, "nope.csv"))
        self.assertEqual(code, 1)
        self.assertIn("Get a copy of your data", out)


class SkillText(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_paths.REPO_ROOT, "skills", "setup", "SKILL.md"), encoding="utf-8") as handle:
            self.text = handle.read()

    def test_it_states_the_promises_and_the_privacy_line(self):
        for phrase in ("Nothing is written until the person confirms the summary",
                       "Your answers stay in a file on this computer. Sweeps search the public web. "
                       "Nothing is ever applied for, sent or posted for you.",
                       "Never guess pay", "Always confirm the current employer explicitly",
                       "Step 1 of 5", "Step 5 of 5", "What would you like to change?"):
            self.assertIn(phrase, self.text)

    def test_every_command_it_names_exists(self):
        import re
        named = set(re.findall(r"`TOOL ([a-z-]+)", self.text))
        self.assertEqual(named, {"status", "fields", "read-resume", "check-vault", "preview", "write",
                                 "import-connections"})
        for command in named:
            self.assertIn('command == "%s"' % command, open(setup_tool.__file__, encoding="utf-8").read())

    def test_only_the_helper_may_run_without_a_prompt(self):
        front = self.text.split("---")[1]
        self.assertIn('allowed-tools: Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" *), Read, '
                      'AskUserQuestion', front)
        self.assertNotIn("Bash(python3:*)", self.text)
        self.assertNotIn("Bash(python3 *)", self.text)
        # Every helper call in the instructions uses the exact form the rule allows.
        self.assertIn('python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview --draft', self.text)
        self.assertNotIn("<<", self.text)
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", self.text)


if __name__ == "__main__":
    unittest.main()
