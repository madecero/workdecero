import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
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


def tree(root, skip=()):
    out = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root)
            if relative.split(os.sep)[0] in skip:
                continue
            with open(path, "rb") as handle:
                out[relative] = hashlib.sha1(handle.read()).hexdigest()
    return out


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.home = os.path.join(root, "config")
        self.cache = os.path.join(root, "cache")
        self.vault = os.path.join(root, "Notes")
        self.parent = root
        os.makedirs(os.path.join(self.vault, ".obsidian"))
        with open(os.path.join(self.vault, "Mine.md"), "w") as handle:
            handle.write("---\nstatus: mine\n---\nMy own note.\n")
        self.registry = os.path.join(root, "obsidian.json")
        with open(self.registry, "w") as handle:
            json.dump({"vaults": {"a": {"path": self.vault, "ts": 5, "open": True},
                                  "b": {"path": os.path.join(root, "gone"), "ts": 9}}}, handle)
        names = ("WORKDECERO_HOME", setup_tool.OBSIDIAN_CONFIG_ENV, setup_tool.DRAFT_DIR_ENV)
        self._env = dict((key, os.environ.get(key)) for key in names)
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ[setup_tool.OBSIDIAN_CONFIG_ENV] = self.registry
        os.environ[setup_tool.DRAFT_DIR_ENV] = self.cache
        self.addCleanup(self.restore_env)
        self.run_id = None

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def example(self, name="product-leader-denver", **changes):
        with open(os.path.join(EXAMPLES, name + ".yaml"), encoding="utf-8") as handle:
            profile = miniyaml.loads(handle.read())
        profile["storage"] = {"vault_path": self.vault, "vault_folder": "workdecero"}
        profile.update(changes)
        return profile

    def call(self, command):
        """Run one helper command exactly as the skill does: a bare subcommand, no arguments."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = setup_tool.main([command])
        return code, out.getvalue(), err.getvalue()

    def open_run(self):
        code, out, _ = self.call("status")
        match = re.search(r"RUN ID: ([0-9a-f]+)", out)
        self.run_id = match.group(1) if match else None
        return code, out

    def send(self, answers, run=None):
        """Write an answers file the way the assistant's Write tool does: a new file, default permissions."""
        if self.run_id is None and run is None:
            self.open_run()
        content = dict(answers)
        content["_run"] = run or self.run_id
        with open(setup_tool.answers_path(), "w", encoding="utf-8") as handle:
            json.dump(content, handle, ensure_ascii=False)

    def put(self, draft, run=None):
        """Send a whole draft as answers: resume values first, then every section, then the helper keys."""
        draft = dict(draft)
        resume = draft.pop("_from_resume", None)
        if resume:
            self.send({"set": resume, "from_resume": True}, run)
            self.call("save")
        sections = dict((key, value) for key, value in draft.items() if not key.startswith("_"))
        helpers = dict((key, value) for key, value in draft.items() if key.startswith("_"))
        self.send(dict(helpers, set=sections), run)

    def state(self):
        with open(setup_tool.draft_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def preview(self, draft):
        self.put(draft)
        return self.call("preview")

    def save(self, draft):
        self.put(draft)
        self.call("preview")
        code, out, err = self.call("write")
        self.run_id = None
        return code, out, err

    def disk(self, skip=("cache",)):
        return tree(self.tmp.name, skip)


class DraftFile(SetupCase):
    def test_status_prepares_a_private_folder_and_no_answers_file(self):
        code, out = self.open_run()
        self.assertEqual(code, 0)
        self.assertEqual(mode_of(self.cache), 0o700)
        self.assertEqual(mode_of(setup_tool.draft_path()), 0o600)      # the helper's own state
        self.assertEqual(self.state(), {"_run": self.run_id})
        self.assertFalse(os.path.exists(setup_tool.answers_path()))    # never pre-created: the Write tool makes it
        self.assertIn("ANSWERS FILE: %s" % setup_tool.answers_path(), out)
        self.assertIn("SAVED PROFILE: none. This is a first setup.", out)
        self.assertIn("  Notes   (", out)                              # the one vault that exists
        self.assertNotIn("gone", out)
        self.assertFalse(out.lstrip().startswith("{"))                 # plain words, not data

    def test_answers_are_absorbed_deleted_and_kept_private(self):
        self.put(self.example())
        self.assertEqual(mode_of(setup_tool.answers_path()), 0o644)    # as the Write tool leaves it, inside a 700 folder
        code, out, _ = self.call("save")
        self.assertEqual(code, 0)
        self.assertIn("Saved: schema_version, person, location, tracks", out)
        self.assertNotIn("Theo", out)                                  # names of settings, never their values
        self.assertFalse(os.path.exists(setup_tool.answers_path()))    # gone, so the next Write is a new file again
        self.assertEqual(mode_of(setup_tool.draft_path()), 0o600)
        self.assertEqual(self.state()["person"]["name"], "Theo Lindqvist")
        self.send({"set": {"digest.max_new_items": 5}})                # the next step: a few lines only
        self.assertEqual(self.call("preview")[0], 0)
        self.assertEqual(self.state()["digest"]["max_new_items"], 5)
        self.assertEqual(self.state()["person"]["name"], "Theo Lindqvist")   # nothing else was touched
        self.assertEqual(mode_of(os.path.join(self.cache, setup_tool.SHOWN_NAME)), 0o600)
        self.assertEqual(self.call("save")[1].strip(), "Nothing new to save.")

    def test_small_answers_set_append_and_remove(self):
        self.put(self.example())
        self.call("save")
        self.send({"set": {"jobs.salary_floor": 220000, "location.work_modes": ["remote"],
                           "person.website": None},
                   "append": {"jobs.titles": ["Chief Product Officer", "GM, Product"],
                              "exclusions": [{"name": "O'Brien Analytics", "kind": "employer"}]},
                   "remove": ["jobs.deal_breakers", "writing"],
                   "_confirmed": ["jobs.salary_floor"], "_dropped": ["person.summary"]})
        self.call("save")
        state = self.state()
        self.assertEqual(state["jobs"]["salary_floor"], 220000)
        self.assertEqual(state["location"]["work_modes"], ["remote"])
        self.assertNotIn("website", state["person"])
        self.assertEqual(state["jobs"]["titles"][-1], "GM, Product")
        self.assertEqual(state["jobs"]["titles"].count("Chief Product Officer"), 1)   # no duplicate
        self.assertEqual(state["exclusions"][-1]["name"], "O'Brien Analytics")
        self.assertNotIn("deal_breakers", state["jobs"])
        self.assertNotIn("writing", state)
        self.assertEqual((state["_confirmed"], state["_dropped"]), (["jobs.salary_floor"], ["person.summary"]))

    def test_default_answers_location_is_the_fixed_cache_path(self):
        saved = os.environ.pop(setup_tool.DRAFT_DIR_ENV)
        try:
            self.assertEqual(setup_tool.answers_path(),
                             os.path.join(os.path.expanduser("~"), ".cache", "workdecero", "setup-answers.json"))
        finally:
            os.environ[setup_tool.DRAFT_DIR_ENV] = saved

    def test_draft_is_deleted_after_a_save(self):
        code, out, _ = self.save(self.example())
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.cache))                   # file, checksum, run marker and folder

    def test_draft_is_deleted_on_cancel(self):
        self.put(self.example())
        self.call("preview")
        code, out, _ = self.call("cancel")
        self.assertEqual(code, 0)
        self.assertIn("The draft was deleted. Nothing was saved", out)
        self.assertFalse(os.path.exists(self.cache))
        self.assertFalse(os.path.exists(self.home))
        self.assertIn("There was no draft.", self.call("cancel")[1])

    def test_draft_never_reaches_backups_or_the_vault(self):
        self.save(self.example())
        draft = self.example()
        draft["digest"] = {"max_new_items": 5}
        self.save(draft)                                               # second save makes a profile backup
        for folder, _, files in os.walk(self.tmp.name):
            for name in files:
                for leftover in ("setup-state", "setup-answers", "setup-run", "setup-preview"):
                    self.assertNotIn(leftover, name)

    def test_a_killed_sessions_old_draft_is_deleted_by_the_next_status(self):
        self.put(self.example())
        self.call("save")
        old = time.time() - 30 * 3600
        for name in os.listdir(self.cache):
            os.utime(os.path.join(self.cache, name), (old, old))
        self.run_id = None
        code, out = self.open_run()
        self.assertEqual(code, 0)
        self.assertIn("too old to use and has been deleted", out)
        self.assertEqual(self.state(), {"_run": self.run_id})          # nothing of the old answers remains

    def test_a_recent_abandoned_draft_is_offered_once(self):
        self.put(self.example())
        self.call("save")
        first_run = self.run_id
        idle = time.time() - 3 * 3600                                  # three hours ago: not active, not stale
        for name in os.listdir(self.cache):
            os.utime(os.path.join(self.cache, name), (idle, idle))
        self.run_id = None
        code, out = self.open_run()
        self.assertEqual(code, 0)
        self.assertIn("UNFINISHED SETUP FOUND: started for Theo Lindqvist, last touched 3 hours ago.", out)
        self.assertNotEqual(self.run_id, first_run)
        kept = self.state()
        self.assertEqual(kept["_run"], self.run_id)                    # handed to the new run
        self.assertEqual(kept["person"]["name"], "Theo Lindqvist")
        self.assertEqual(mode_of(setup_tool.draft_path()), 0o600)


class TwoSessions(SetupCase):
    def test_second_session_is_warned_and_nothing_is_deleted(self):
        self.put(self.example())                                       # session A is mid-setup
        self.call("save")
        run_a = self.run_id
        with open(setup_tool.draft_path()) as handle:
            before = handle.read()
        code, out, _ = self.call("status")                             # session B starts
        self.assertEqual(code, 1)
        self.assertIn("ANOTHER SETUP IS IN PROGRESS (run %s, last activity 0 minutes ago)." % run_a, out)
        self.assertIn("run the takeover command", out)
        with open(setup_tool.draft_path()) as handle:
            self.assertEqual(handle.read(), before)                    # A's work is untouched
        self.assertEqual(setup_tool.current_run()["run"], run_a)

    def test_after_a_takeover_the_first_session_cannot_save(self):
        self.put(self.example())                                       # A
        self.call("preview")
        run_a = self.run_id
        code, out, _ = self.call("takeover")                           # B, with the person's say-so
        self.assertEqual(code, 0)
        run_b = re.search(r"RUN ID: ([0-9a-f]+)", out).group(1)
        self.assertNotEqual(run_a, run_b)
        b_draft = self.example("nurse-manager-texas")
        self.put(b_draft, run=run_b)                                   # B writes its answers
        self.put(self.example(), run=run_a)                            # A, unaware, writes its answers over them
        code, _, err = self.call("preview")
        self.assertEqual(code, 2)
        self.assertIn("was written for setup run %s, but the open run is %s" % (run_a, run_b), err)
        self.assertIn("do not save and do not cancel", err)
        self.assertNotIn("Theo", json.dumps(self.state()))             # A's answers never entered B's draft
        self.assertEqual(self.call("write")[0], 1)                     # and nothing can be saved without a preview
        self.assertFalse(os.path.exists(self.home))                    # nobody's answers were saved
        self.put(b_draft, run=run_b)                                   # B writes again and carries on
        self.assertEqual(self.call("preview")[0], 0)
        self.assertEqual(self.call("write")[0], 0)
        self.assertEqual(wdprofile.load()[0]["person"]["name"], "Dana Whitfield")

    def test_answers_without_a_run_id_are_refused(self):
        self.open_run()
        with open(setup_tool.answers_path(), "w") as handle:
            json.dump({"set": self.example()}, handle)
        code, _, err = self.call("preview")
        self.assertEqual(code, 2)
        self.assertIn('Every answers file must carry "_run"', err)
        self.assertEqual(self.state(), {"_run": self.run_id})

    def test_nothing_changes_outside_an_open_recent_run(self):
        """A stray helper call, outside a setup conversation, must do nothing."""
        for command in ("write", "save", "preview", "check-vault", "read-resume", "import-connections"):
            code, _, err = self.call(command)
            self.assertEqual(code, 2, command)
            self.assertIn("No setup run is open", err)
        self.assertFalse(os.path.exists(self.home))
        self.put(self.example())
        self.call("preview")
        old = time.time() - 30 * 3600                                  # a run left open since yesterday
        for name in os.listdir(self.cache):
            os.utime(os.path.join(self.cache, name), (old, old))
        code, _, err = self.call("write")
        self.assertEqual(code, 2)
        self.assertIn("No setup run is open", err)
        self.assertFalse(os.path.exists(self.home))

    def test_a_run_idle_for_over_thirty_minutes_no_longer_blocks(self):
        self.open_run()                                                # opened, then abandoned at the welcome
        old = time.time() - 45 * 60
        for name in os.listdir(self.cache):
            os.utime(os.path.join(self.cache, name), (old, old))
        first = self.run_id
        code, out = self.open_run()
        self.assertEqual(code, 0)
        self.assertNotIn("ANOTHER SETUP", out)
        self.assertNotEqual(self.run_id, first)


class ReadOnlyCommands(SetupCase):
    def test_status_changes_nothing_but_the_draft_folder(self):
        before = self.disk()
        self.open_run()
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
        code, out, _ = self.call("fields")
        self.assertEqual(code, 0)
        self.assertIn("jobs.salary_floor: Lowest base salary you would consider", out)
        self.assertIn("location.work_modes: The ways of working you accept. A list from: remote, hybrid, onsite.", out)
        self.assertIn("exclusions[].kind:", out)

    def test_check_vault_reads_the_folder_from_the_draft(self):
        self.send({"set": {"storage.vault_path": self.parent}})
        code, out, _ = self.call("check-vault")
        self.assertEqual(code, 1)
        self.assertIn("is not an Obsidian vault", out)
        self.assertIn("did you mean Notes?", out)                    # the parent of the real vault
        self.send({"set": {"storage.vault_path": os.path.join(self.parent, "typo")}})
        self.assertIn("does not exist", self.call("check-vault")[1])
        self.send({"set": {"storage": {"vault_path": self.vault, "vault_folder": "/abs/path"}}})
        self.assertIn("not a full path", self.call("check-vault")[1])
        self.send({"set": {"storage": {"vault_path": self.vault}}})
        code, out, _ = self.call("check-vault")
        self.assertEqual(code, 0)
        self.assertIn("A workdecero folder will be added, and nothing else is touched.", out)
        spaced = os.path.join(self.parent, "My Vault's \"copy\" $HOME")
        os.makedirs(os.path.join(spaced, ".obsidian"))
        self.send({"set": {"storage.vault_path": spaced}})
        self.assertEqual(self.call("check-vault")[0], 0)              # any characters, since no shell is involved

    def test_read_resume_takes_its_path_from_the_draft(self):
        self.put({"_resume_path": os.path.join(RESUMES, "nurse-manager-texas.md")})
        code, out, _ = self.call("read-resume")
        self.assertEqual(code, 0)
        self.assertIn("Dana Whitfield", out)
        docx = os.path.join(self.tmp.name, "O'Brien resume.docx")
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml",
                             "<w:document><w:body><w:p><w:r><w:t>Sam Example</w:t></w:r></w:p>"
                             "<w:p><w:r><w:t>Office &amp; Operations Manager</w:t></w:r></w:p></w:body></w:document>")
        self.put({"_resume_path": docx})
        self.assertEqual(self.call("read-resume")[1].strip(), "Sam Example\nOffice & Operations Manager")
        self.put({"_resume_path": os.path.join(self.tmp.name, "nope.txt")})
        code, _, err = self.call("read-resume")
        self.assertEqual(code, 2)
        self.assertIn("No file at", err)
        pdf = os.path.join(self.tmp.name, "resume.pdf")
        open(pdf, "wb").close()
        self.put({"_resume_path": pdf})
        self.assertIn("Read tool", self.call("read-resume")[2])
        self.call("cancel")
        self.run_id = None
        self.open_run()
        self.assertIn('"_resume_path"', self.call("read-resume")[2])

    def test_the_helper_takes_no_arguments_at_all(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = setup_tool.main(["check-vault", self.vault])
        self.assertEqual(code, 2)
        self.assertIn("takes no arguments", err.getvalue())
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(setup_tool.main(["preview", "--draft", "{}"]), 2)

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
    def test_preview_shows_a_short_plain_summary_and_saves_nothing(self):
        before = self.disk()
        draft = self.example(_from_resume={"person.name": "Theo Lindqvist", "jobs.titles": ["VP of Product"],
                                           "location.home_base": "Denver, CO"})
        code, out, _ = self.preview(draft)
        self.assertEqual(code, 0, out)
        card = out.split("\n\n")[0].splitlines()
        self.assertLessEqual(len(card), 20, "\n".join(card))
        self.assertIn("You          Theo Lindqvist. Product leader for B2B software", out)
        self.assertIn("(from your resume)", card[0])
        self.assertIn("Pay floor: $210,000 a year, aiming for $260,000.", out)
        self.assertIn("Where        Denver, CO (from your resume). remote, hybrid, within 30 miles.", out)
        self.assertIn("Current employer Granite Peak Software is always left out", out)
        self.assertIn("Quiet search: yes.", out)
        self.assertIn("Up to 8 new items in the digest. Lite mode.", out)
        self.assertIn("READY TO SAVE. Nothing has been saved yet.", out)
        for jargon in ("vault_folder", "schema", "yaml", "json", "wd_", "search_visibility", "_run", "{"):
            self.assertNotIn(jargon, out.lower())
        self.assertEqual(self.disk(), before)

    def test_summary_shrinks_when_sweeps_are_off(self):
        _, nurse, _ = self.preview(self.example("nurse-manager-texas"))
        self.assertIn("Sweeps       salaried jobs", nurse)
        self.assertNotIn("Contracts", nurse)
        self.assertNotIn("Networking", nurse)
        self.assertLessEqual(len(nurse.split("\n\n")[0].splitlines()), 14)

    def test_details_and_file_only_on_request(self):
        self.put(self.example())
        short = self.call("preview")[1]
        self.assertNotIn("schema_version", short)
        self.assertNotIn("Certifications:", short)
        details = self.call("preview-details")[1]
        self.assertIn("\nLOOKING FOR\n", details)
        self.assertIn("  Certifications: Certified Scrum Product Owner", details)
        shown = self.call("preview-file")[1]
        self.assertIn("profile file as it would be written", shown)
        self.assertIn("schema_version: 1", shown)

    def test_a_pay_floor_off_by_a_thousand_comes_back_as_a_question(self):
        draft = self.example()
        draft["jobs"]["salary_floor"] = 210
        code, out, _ = self.preview(draft)
        self.assertEqual(code, 1)
        self.assertIn("QUESTIONS TO ASK BEFORE SAVING (1)", out)
        self.assertIn("jobs.salary_floor is 210", out)
        self.assertIn("Did you mean 210000?", out)
        self.assertNotIn("READY TO SAVE", out)

    def test_missing_answers_are_listed_as_things_to_fix(self):
        draft = self.example()
        del draft["jobs"]["salary_floor"]
        draft["location"]["work_modes"] = []
        code, out, _ = self.preview(draft)
        self.assertEqual(code, 1)
        self.assertIn("TO FIX BEFORE THIS CAN BE SAVED (2)", out)
        self.assertIn("jobs.salary_floor: this setting is required but missing.", out)

    def test_a_resume_value_dropped_by_accident_is_caught(self):
        draft = self.example()
        read = {"person.summary": draft["person"]["summary"], "person.resume_path": "~/resume.pdf",
                "jobs.preferred_industries": list(draft["jobs"]["preferred_industries"]),
                "person.name": draft["person"]["name"]}
        draft["_from_resume"] = read
        del draft["person"]["summary"]                                # a rewrite that "kept only the changes"
        del draft["person"]["resume_path"]
        draft["jobs"]["preferred_industries"] = []
        code, out, _ = self.preview(draft)
        self.assertEqual(code, 1)
        self.assertIn("READ FROM THE RESUME BUT NO LONGER IN THE DRAFT (3)", out)
        self.assertIn("  person.summary: Sixteen years in product management", out)
        self.assertIn("  jobs.preferred_industries: B2B software, Climate technology", out)
        self.assertNotIn("READY TO SAVE", out)
        self.assertEqual(self.call("write")[0], 1)                    # and it cannot be saved like that
        self.assertFalse(os.path.exists(self.home))
        draft["person"]["summary"] = read["person.summary"]           # put two back, drop one on purpose
        draft["jobs"]["preferred_industries"] = read["jobs.preferred_industries"]
        draft["_dropped"] = ["person.resume_path"]
        code, out, _ = self.preview(draft)
        self.assertEqual(code, 0, out)
        self.assertIn("READY TO SAVE", out)

    def test_helper_keys_and_empty_answers_are_never_saved(self):
        draft = self.example(_from_resume={"person.name": "Theo Lindqvist"}, _new_vault=False, _confirmed=[],
                             _resume_path="/x/resume.pdf", _connections_path="/x/c.csv", _include_emails=True,
                             _dropped=[])
        draft["person"]["website"] = "   "
        draft["jobs"]["deal_breakers"] = []
        draft["writing"] = {}
        draft["_run"] = "abc12345"
        profile, inferred, _ = setup_tool.profile_from_draft(draft)
        text = setup_tool.profile_text(profile)
        for gone in ("_from_resume", "_new_vault", "_confirmed", "_run", "_resume_path", "/x/c.csv",
                     "_include_emails", "_dropped", "website", "deal_breakers", "writing", "abc12345"):
            self.assertNotIn(gone, text)
        self.assertEqual(inferred, ["person.name"])
        self.assertEqual(list(profile)[:4], ["schema_version", "person", "location", "tracks"])
        nurse, _, _ = setup_tool.profile_from_draft(self.example("nurse-manager-texas"))
        self.assertIs(nurse["tracks"]["contracts"], False)           # false is an answer, and is kept

    def test_paths_under_the_home_folder_are_saved_in_short_form(self):
        home = os.path.expanduser("~")
        draft = self.example()
        draft["person"]["resume_path"] = os.path.join(home, "Documents", "resume.pdf")
        profile, _, _ = setup_tool.profile_from_draft(draft)
        self.assertEqual(profile["person"]["resume_path"], os.path.join("~", "Documents", "resume.pdf"))
        self.assertEqual(profile["storage"]["vault_path"], self.vault)      # not under home: left as given

    def test_broken_answers_are_reported_not_guessed_at(self):
        self.open_run()
        with open(setup_tool.answers_path(), "w") as handle:
            handle.write("{ not json")
        code, _, err = self.call("preview")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)


class SpecialCharacters(SetupCase):
    def test_quotes_dollars_backticks_and_semicolons_round_trip_exactly(self):
        draft = self.example()
        draft["person"]["name"] = "Siobhán O'Brien-D'Arcy"
        draft["person"]["headline"] = 'VP "Growth" at $Co; `ops` & more \\ done'
        draft["person"]["current_employer"] = "Women's Health Partners"
        draft["exclusions"] = [{"name": "O'Brien Analytics; rm -rf $HOME", "kind": "employer",
                                "reason": 'Said "never" `again`'}]
        draft["confidential"] = {"names": ["L'Oréal $pecial `Projects`"]}
        code, out, _ = self.save(draft)
        self.assertEqual(code, 0, out)
        saved, problems = wdprofile.load()
        self.assertEqual(problems, [])
        self.assertEqual(saved["person"]["name"], "Siobhán O'Brien-D'Arcy")
        self.assertEqual(saved["person"]["headline"], 'VP "Growth" at $Co; `ops` & more \\ done')
        self.assertEqual(saved["person"]["current_employer"], "Women's Health Partners")
        self.assertEqual(saved["exclusions"][0]["name"], "O'Brien Analytics; rm -rf $HOME")
        self.assertEqual(saved["exclusions"][0]["reason"], 'Said "never" `again`')
        self.assertEqual(saved["confidential"]["names"], ["L'Oréal $pecial `Projects`"])
        self.assertIn("Women's Health Partners", setup_tool.short_card(saved))


class Write(SetupCase):
    def test_write_saves_a_valid_profile_and_sets_up_the_vault(self):
        code, out, _ = self.save(self.example())
        self.assertEqual(code, 0, out)
        profile, problems = wdprofile.load()
        self.assertEqual(problems, [])
        self.assertEqual(profile["person"]["name"], "Theo Lindqvist")
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "workdecero", "Start here.md")))
        self.assertIn("workdecero is set up.", out)
        self.assertIn("  Tracker      Notes / workdecero / Start here   (8 files added)", out)
        self.assertIn('Next:   say "run the job sweep". The first one takes a few minutes in Lite mode.', out)
        self.assertIn("Open:   obsidian://open?path=", out)
        self.assertIn("Start%20here.md", out)
        self.assertIn('Say "build my local map"', out)
        self.assertIn("Change anything later:   /workdecero:setup", out)
        self.assertIn(setup_tool.PRIVACY, out)

    def test_saved_file_matches_the_draft(self):
        self.save(self.example())
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            saved = miniyaml.loads(handle.read())
        self.assertEqual(saved, self.example())

    def test_write_is_refused_without_a_preview(self):
        self.put(self.example())
        code, out, _ = self.call("write")
        self.assertEqual(code, 1)
        self.assertIn("no summary was shown yet", out)
        self.assertIn("Nothing was saved.", out)
        self.assertFalse(os.path.exists(self.home))

    def test_a_correction_after_the_summary_forces_a_new_preview(self):
        draft = self.example()
        self.put(draft)
        self.assertEqual(self.call("preview")[0], 0)
        draft["jobs"]["salary_floor"] = 190000                        # changed after the person saw the card
        self.put(draft)
        code, out, _ = self.call("write")
        self.assertEqual(code, 1)
        self.assertIn("The draft has changed since the person last saw the summary", out)
        self.assertFalse(os.path.exists(self.home))
        self.assertEqual(self.call("preview")[0], 0)                  # shown again
        self.assertEqual(self.call("write")[0], 0)
        self.assertEqual(wdprofile.load()[0]["jobs"]["salary_floor"], 190000)

    def test_quiet_search_leaves_no_trace_of_the_employer_in_the_vault(self):
        self.save(self.example())
        for folder, _, files in os.walk(self.vault):
            for name in files:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    text = handle.read()
                for secret in ("Granite Peak", "Summit Ledger", "Alpenglow", "Theo", "Lindqvist"):
                    self.assertNotIn(secret, text, name)

    def test_nothing_outside_the_tracker_folder_and_config_folder_changes(self):
        before = self.disk()
        self.save(self.example())
        after = tree(self.tmp.name)                                    # the cache folder is gone too
        changed = sorted(path for path in after if before.get(path) != after[path])
        self.assertTrue(all(path.startswith(("config" + os.sep, os.path.join("Notes", "workdecero") + os.sep))
                            for path in changed), changed)
        self.assertEqual(after["obsidian.json"], before["obsidian.json"])

    def test_wrong_folder_is_refused_before_anything_is_saved(self):
        before = self.disk()
        draft = self.example()
        draft["storage"]["vault_path"] = self.parent
        code, out, _ = self.save(draft)
        self.assertEqual(code, 1)
        self.assertIn("VAULT PROBLEM", out)
        self.assertIn("Nothing was saved.", out)
        self.assertEqual(self.disk(), before)

    def test_an_open_question_blocks_the_save_until_the_person_answers(self):
        before = self.disk()
        draft = self.example()
        draft["jobs"]["salary_floor"] = 210
        code, out, _ = self.save(draft)
        self.assertEqual(code, 1)
        self.assertIn("Did you mean 210000?", out)
        self.assertEqual(self.disk(), before)
        self.open_run() if setup_tool.current_run() is None else None
        self.run_id = setup_tool.current_run()["run"]
        draft["_confirmed"] = ["jobs.salary_floor"]                   # the person was asked and kept it
        code, out, _ = self.save(draft)
        self.assertEqual(code, 0, out)
        self.assertEqual(wdprofile.load()[0]["jobs"]["salary_floor"], 210)

    def test_new_vault_needs_an_explicit_yes(self):
        target = os.path.join(self.tmp.name, "Brand New Vault")
        draft = self.example()
        draft["storage"]["vault_path"] = target
        self.assertEqual(self.preview(draft)[0], 1)
        self.assertFalse(os.path.exists(target))
        draft["_new_vault"] = True
        code, out, _ = self.save(draft)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(target, "workdecero", "Start here.md")))
        self.assertFalse(os.path.exists(os.path.join(target, ".obsidian")))
        self.assertIn('Open:   in Obsidian, choose "Open folder as vault"', out)

    def test_first_sweep_named_on_the_done_screen_follows_what_is_on(self):
        _, out, _ = self.save(self.example("freelance-designer-portland"))
        self.assertIn('say "run the contract sweep"', out)


class RunAgain(SetupCase):
    def test_second_run_starts_from_the_saved_profile(self):
        self.save(self.example())
        code, out = self.open_run()
        self.assertEqual(code, 0)
        self.assertIn("SAVED PROFILE: yes", out)
        self.assertIn("The saved profile is loaded as the starting point. Send only what the person wants changed.", out)
        self.assertIn("You          Theo Lindqvist.", out)
        seeded = self.state()
        self.assertEqual(seeded.pop("_run"), self.run_id)
        self.assertEqual(seeded, self.example())
        self.assertEqual(mode_of(setup_tool.draft_path()), 0o600)

    def test_changing_one_thing_changes_only_that_and_keeps_a_backup(self):
        self.save(self.example())
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            first = handle.read()
        vault_before = tree(self.vault)
        self.open_run()
        self.send({"set": {"digest.max_new_items": 5}})                # the only thing the assistant sends
        self.assertEqual(self.call("preview")[0], 0)
        code, out, _ = self.call("write")
        self.run_id = None
        self.assertEqual(code, 0, out)
        with open(wdprofile.profile_path(), encoding="utf-8") as handle:
            second = handle.read()
        differing = [(a, b) for a, b in zip(first.splitlines(), second.splitlines()) if a != b]
        self.assertEqual(differing, [("  max_new_items: 8", "  max_new_items: 5")])
        self.assertEqual(tree(self.vault), vault_before)
        self.assertIn("already in place, nothing overwritten", out)
        backups = os.listdir(os.path.join(self.home, "backups"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.home, "backups", backups[0]), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)

    def test_looking_without_changing_leaves_nothing_behind(self):
        self.save(self.example())
        before = tree(self.tmp.name)
        self.open_run()
        self.call("cancel")
        self.assertEqual(tree(self.tmp.name), before)

    def test_a_hand_broken_profile_is_reported(self):
        os.makedirs(self.home)
        with open(wdprofile.profile_path(), "w") as handle:
            handle.write("schema_version: 1\nperson:\n\tname: Sam\n")
        _, out = self.open_run()
        self.assertIn("has problems:", out)
        self.assertIn("cannot be read", out)


CSV = """Notes:
"Some email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Priya,Raman,https://www.linkedin.com/in/example-priya,priya@raman.example.com,Front Range Robotics,VP Engineering,09 Nov 2024
Tom,Becker,https://www.linkedin.com/in/example-tom,,Granite Peak Software,Engineer,15 Mar 2023
"""


class Connections(SetupCase):
    def csv_path(self):
        path = os.path.join(self.tmp.name, "My Connections (1).csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(CSV)
        return path

    def test_import_needs_a_saved_profile(self):
        self.put({"_connections_path": self.csv_path()})
        code, out, _ = self.call("import-connections")
        self.assertEqual(code, 1)
        self.assertIn("Finish setup first", out)

    def test_import_adds_people_without_emails_by_default_and_cleans_up(self):
        self.save(self.example())
        self.put({"_connections_path": self.csv_path()})
        code, out, _ = self.call("import-connections")
        self.assertEqual(code, 0, out)
        self.assertIn("Connections imported: 1 added, 0 already known and refreshed.", out)
        self.assertIn("1 left out because they work at an employer you keep confidential.", out)
        self.assertIn("Email addresses: not included.", out)
        people = vault.VaultStore(self.vault).rows("person")
        self.assertEqual([row.title for row in people], ["Priya Raman"])
        self.assertNotIn("email", people[0].values)
        self.assertFalse(os.path.exists(self.cache))

    def test_import_includes_emails_only_when_the_draft_says_so(self):
        self.save(self.example())
        self.put({"_connections_path": self.csv_path(), "_include_emails": True})
        code, out, _ = self.call("import-connections")
        self.assertIn("Email addresses: included, as you asked.", out)
        self.assertEqual(vault.VaultStore(self.vault).rows("person")[0].values["email"], "priya@raman.example.com")

    def test_missing_file_explains_where_to_get_it(self):
        self.save(self.example())
        self.put({"_connections_path": os.path.join(self.tmp.name, "nope.csv")})
        code, out, _ = self.call("import-connections")
        self.assertEqual(code, 1)
        self.assertIn("Get a copy of your data", out)


class SkillText(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_paths.REPO_ROOT, "skills", "setup", "SKILL.md"), encoding="utf-8") as handle:
            self.text = handle.read()
        self.front = self.text.split("---")[1]

    def test_lists_are_written_out_and_the_takeover_question_uses_the_picker(self):
        for phrase in ("is written out in full in the same message as the question, or offered as picker options",
                       'Never point at it with words such as "above"',
                       "always with the AskUserQuestion picker, never as a typed question",
                       "write out every title and level you drafted, one per line, in the message itself"):
            self.assertIn(phrase, self.text)

    def test_it_states_the_promises_and_the_privacy_line(self):
        for phrase in ("Nothing is saved until the person confirms the summary",
                       "Never put the person's words on a command line",
                       "Your answers stay in a file on this computer. Sweeps search the public web. "
                       "Nothing is ever applied for, sent or posted for you.",
                       "Never guess pay", "Always confirm the current employer explicitly",
                       "Step 1 of 5", "Step 5 of 5", "What would you like to change?",
                       "The person has only seen what you said to them", "Saving your answers...",
                       "Never ask them to confirm something that appeared only there"):
            self.assertIn(phrase, self.text)

    def test_every_helper_command_is_an_exact_rule_with_no_wildcard(self):
        rules = re.findall(r'Bash\(([^)]*)\)', self.front)
        prefix = 'python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" '
        commands = sorted(rule[len(prefix):] for rule in rules)
        self.assertTrue(all(rule.startswith(prefix) for rule in rules), rules)
        self.assertEqual(commands, sorted(setup_tool.COMMANDS))
        self.assertNotIn("*", self.front)                             # no wildcard anywhere in the permissions
        self.assertNotIn("Bash(python3:", self.text)

    def test_only_the_one_draft_file_may_be_written(self):
        self.assertEqual(re.findall(r'(?:Write|Edit|Read)\(([^)]*)\)', self.front),
                         ["~/.cache/workdecero/setup-answers.json"])
        self.assertNotIn("Edit", self.front)                          # the answers file is always written new

    def test_every_command_line_in_the_instructions_is_one_of_the_fixed_lines(self):
        lines = [line.strip() for line in self.text.splitlines() if line.strip().startswith("python3 ")]
        prefix = 'python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" '
        self.assertEqual(sorted(line[len(prefix):] for line in lines), sorted(setup_tool.COMMANDS))
        named = set(re.findall(r"`TOOL ([a-z-]+)`", self.text))
        self.assertTrue(named <= set(setup_tool.COMMANDS), named)

    def test_no_user_data_can_reach_a_command_line(self):
        for banned in ("--draft", "<<", "&apos;", "\\u0027", "CLAUDE_PLUGIN_ROOT", "--include-emails"):
            self.assertNotIn(banned, self.text)

    def test_picker_guidance(self):
        self.assertIn('end its label with "(Recommended)"', self.text)
        self.assertNotIn("re-select", self.text.lower())
        self.assertIn("no asterisks, backticks or other markup", self.text)


if __name__ == "__main__":
    unittest.main()
