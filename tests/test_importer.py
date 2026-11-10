"""The spreadsheet import: engine, helper and skill text. Offline, with invented trackers only."""

import datetime
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "import", "scripts"))
import import_tool  # noqa: E402
import importer  # noqa: E402
import miniyaml  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")
TODAY = datetime.date(2026, 11, 9)
SECRET = "Hidden Harbor Foods"

JOBS = """Role,Employer,Low,High,Status,Applied,Link,Hiring manager,My notes,Priority
Brand Designer,Example Tea Company,"$85,000",95k,sent,11/02/2026,https://jobs.example.com/1,Imani Brooks,"Loved the team, follow up Friday",A
Packaging Lead,Example Brewing,90000,,New,,https://jobs.example.com/2,,Second choice,B
Art Director,Sample Roasters,,,ghosted,next week,,Raj Patel; Jane Doe,,C
Design Manager,%s,100000,,sent,10/20/2026,,,Under NDA,A
,Example Brewing,,,New,,,,A row with no role,
Brand Designer,Example Tea Company,,,sent,,,,The same role again,
""" % SECRET

PEOPLE = """Name,Company,Role,How we met,Follow up
Imani Brooks,Example Tea Company,Creative Director,Portland Brand Meetup,2026-11-16
Aaron Zed,,Recruiter,Cold email,
"""

GROUPS = """Group,Status,Site,Dues,Thoughts
Portland Brand Meetup,member,https://groups.example.org/brand,0,Good room
Oregon Design Association,New,https://groups.example.org/oda,$250,Pricey but worth a look
"""


class ImportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "config")
        self.cache = os.path.join(self.tmp.name, "cache")
        self.vault_dir = os.path.join(self.tmp.name, "Notes")
        self.exports = os.path.join(self.tmp.name, "Exports")
        os.makedirs(os.path.join(self.vault_dir, ".obsidian"))
        os.makedirs(self.home)
        os.makedirs(self.exports)
        self._env = dict((key, os.environ.get(key)) for key in ("WORKDECERO_HOME", import_tool.DRAFT_DIR_ENV))
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ[import_tool.DRAFT_DIR_ENV] = self.cache
        self.addCleanup(self.restore_env)
        with open(os.path.join(EXAMPLES, "freelance-designer-portland.yaml"), encoding="utf-8") as handle:
            raw = miniyaml.loads(handle.read())
        raw["storage"] = {"vault_path": self.vault_dir, "vault_folder": "workdecero"}
        raw["confidential"] = {"names": [SECRET]}
        with open(wdprofile.profile_path(), "w", encoding="utf-8") as handle:
            handle.write(miniyaml.dumps(raw))
        self.files = {}
        for name, text in (("Jobs.csv", JOBS), ("Contacts.csv", PEOPLE), ("Groups.csv", GROUPS)):
            self.files[name] = os.path.join(self.exports, name)
            with open(self.files[name], "w", encoding="utf-8") as handle:
                handle.write(text)

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def plan(self, statuses=None):
        return {"tables": [
            {"csv": self.files["Jobs.csv"], "type": "job", "title": "Role",
             "columns": {"Employer": "company", "Low": "pay_min", "High": "pay_max", "Status": "status",
                         "Applied": "applied_on", "Link": "apply_url", "Hiring manager": "contact"},
             "statuses": statuses if statuses is not None else {"sent": "Applied", "ghosted": "Closed"},
             "date_formats": ["%m/%d/%Y"]},
            {"csv": self.files["Contacts.csv"], "type": "person", "title": "Name",
             "columns": {"Company": "company", "Role": "role", "How we met": "met", "Follow up": "follow_up_by"}},
            {"csv": self.files["Groups.csv"], "type": "group", "title": "Group",
             "columns": {"Status": "status", "Site": "link", "Dues": "cost_per_year"},
             "statuses": {"member": "Joined"}, "keep_heading": "My old notes"}]}

    def hand_off(self, data):
        os.makedirs(self.cache, mode=0o700, exist_ok=True)
        with open(import_tool.handoff_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def command(self, name):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = import_tool.main([name])
        return code, out.getvalue(), err.getvalue()

    def check(self, plan=None):
        self.hand_off(plan if plan is not None else self.plan())
        out = io.StringIO()
        code = import_tool.check(out=out, today=TODAY)
        return code, out.getvalue()

    def run_import(self):
        out = io.StringIO()
        code = import_tool.run(out=out, today=TODAY)
        return code, out.getvalue()

    def store(self):
        return vault.VaultStore(self.vault_dir)

    def notes(self, kind):
        return dict((row.title, row) for row in self.store().rows(kind))


class Check(ImportCase):
    def test_a_first_look_shows_only_column_names_and_counts(self):
        code, out = self.check({"tables": [{"csv": self.files["Jobs.csv"], "type": "job"},
                                           {"csv": self.files["Groups.csv"], "type": "group"}]})
        self.assertEqual(code, 1)
        self.assertIn("  Jobs.csv (job, 6 rows): Role, Employer, Low, High, Status, Applied, Link, Hiring manager, "
                      "My notes, Priority", out)
        self.assertIn("  Groups.csv (group, 2 rows): Group, Status, Site, Dues, Thoughts", out)
        for private in ("Example Tea", "Imani", "85,000", SECRET, "Loved the team"):
            self.assertNotIn(private, out)

    def test_the_check_reports_everything_and_writes_nothing(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("IMPORT CHECK. Nothing has been written.", out)
        self.assertIn("Jobs.csv -> job notes", out)
        self.assertIn("  Rows: 6. Would be written: 3. Already in the vault: 0. Not imported: 3.", out)
        self.assertIn("1 at a confidential name in your profile (counted only, never written).", out)
        self.assertIn("1 with nothing in the title column.", out)
        self.assertIn("1 that repeat an earlier row of the same file.", out)
        self.assertIn("Columns kept word for word in each note's body: My notes, Priority.", out)
        self.assertIn('Dates in "Applied" that could not be read (1), kept as text in the note: "next week".', out)
        self.assertIn("Contacts.csv -> person notes", out)
        self.assertIn("  Rows: 2. Would be written: 2.", out)
        self.assertIn("Every row is accounted for: 10 rows, 10 written or already there or listed above", out)
        self.assertIn("READY.", out)
        self.assertFalse(os.path.isdir(os.path.join(self.vault_dir, "workdecero")))   # nothing in the vault yet
        # No row is named in what the conversation sees. The detail is in a private file.
        for private in ("Example Tea", "Imani", "Raj", SECRET, "Loved the team", "85"):
            self.assertNotIn(private, out)
        report = os.path.join(self.home, "import-report.txt")
        self.assertEqual(stat.S_IMODE(os.stat(report).st_mode), 0o600)
        with open(report, encoding="utf-8") as handle:
            detail = handle.read()
        self.assertIn("row 6: no Role, not imported", detail)
        self.assertIn("row 7: the same as row 2, not imported twice", detail)
        self.assertNotIn(SECRET, detail)                               # counted only, even there

    def test_a_status_word_is_never_guessed(self):
        code, out = self.check(self.plan(statuses={"sent": "Applied"}))
        self.assertEqual(code, 1)
        self.assertIn('STATUS WORDS WITH NO MEANING YET (the import cannot run until each is mapped): "ghosted" (1).', out)
        self.assertIn("A job note can be: New, Interested, Applied, Interviewing, Offer, Passed, Rejected, Closed.", out)
        self.assertIn("NOT READY.", out)
        with self.assertRaises(ValueError) as caught:
            self.run_import()
        self.assertIn("Some status words still have no meaning, so nothing was written.", str(caught.exception))
        self.assertFalse(os.path.isdir(os.path.join(self.vault_dir, "workdecero", "Jobs")))

    def test_a_bad_plan_is_refused_in_plain_words(self):
        bad = self.plan()
        bad["tables"][0]["columns"]["Priority"] = "priority"
        with self.assertRaises(ValueError) as caught:
            self.check(bad)
        self.assertIn('column "Priority" is mapped to "priority", which is not a property a job note takes', str(caught.exception))
        bad = self.plan()
        bad["tables"][0]["statuses"] = {"sent": "Sent off"}
        with self.assertRaises(ValueError) as caught:
            self.check(bad)
        self.assertIn('is not a status a job note has', str(caught.exception))
        bad = self.plan()
        bad["tables"][0]["columns"]["Nope"] = "location"
        with self.assertRaises(ValueError) as caught:
            self.check(bad)
        self.assertIn('Jobs.csv has no column named "Nope"', str(caught.exception))
        for path in ("relative.csv", os.path.join(self.exports, "missing.csv"), wdprofile.profile_path()):
            with self.assertRaises(ValueError):
                self.check({"tables": [{"csv": path, "type": "job", "title": "Role"}]})
        for target in ("id", "liveness", "near_miss", "text_hash"):
            self.assertNotIn(target, [field.name for field in importer.mappable("job")])


class Run(ImportCase):
    def test_run_needs_a_check_of_the_same_files(self):
        code, _, err = self.command("run")
        self.assertEqual(code, 2)
        self.assertIn("Nothing is in progress, so nothing was changed. Run `check` first", err)
        self.check()
        with open(self.files["Jobs.csv"], "a", encoding="utf-8") as handle:
            handle.write("Illustrator,Example Brewing,,,New,,,,,\n")
        with self.assertRaises(ValueError) as caught:
            self.run_import()
        self.assertIn("A CSV file changed after the check, so nothing was written.", str(caught.exception))

    def test_three_tables_become_notes_with_matching_counts(self):
        self.check()
        code, out = self.run_import()
        self.assertEqual(code, 0, out)
        self.assertIn("IMPORT DONE", out)
        self.assertIn("  Rows: 6. Written: 3. Already in the vault: 0. Not imported: 3.", out)
        jobs, people, groups, companies = (self.notes(kind) for kind in ("job", "person", "group", "company"))
        self.assertEqual(sorted(jobs), ["Art Director, Sample Roasters", "Brand Designer, Example Tea Company",
                                        "Packaging Lead, Example Brewing"])
        self.assertEqual(len(groups), 2)
        # People: two from the contacts table, two more named only in job rows. Imani is one note, not two.
        self.assertEqual(sorted(people), ["Aaron Zed", "Imani Brooks", "Jane Doe", "Raj Patel"])
        self.assertEqual(sorted(companies), ["Example Brewing", "Example Tea Company", "Sample Roasters"])
        job = jobs["Brand Designer, Example Tea Company"]
        self.assertEqual((job.values["status"], job.values["applied_on"], job.values["pay_min"], job.values["pay_max"]),
                         ("Applied", "2026-11-02", 85000, 95000))
        self.assertEqual(job.values["apply_url"], "https://jobs.example.com/1")
        self.assertIn("Companies/Example Tea Company", job.values["company"])
        self.assertIn("People/Imani Brooks", job.values["contact"])
        self.assertEqual(people["Imani Brooks"].values["role"], "Creative Director")      # the fuller note won
        self.assertEqual(people["Imani Brooks"].values["follow_up_by"], "2026-11-16")
        self.assertEqual(groups["Portland Brand Meetup"].values["status"], "Joined")
        self.assertEqual(groups["Oregon Design Association"].values["cost_per_year"], 250)
        # Nothing at the confidential name was written, anywhere.
        for root, _, files in os.walk(self.vault_dir):
            for name in files:
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as handle:
                    self.assertNotIn(SECRET, handle.read(), name)
                self.assertNotIn("Hidden Harbor", name)
        self.assertFalse(os.path.exists(import_tool.checked_path()))   # the run is over

    def test_unmapped_columns_and_unread_values_are_kept_in_the_body_as_their_own_text(self):
        self.check()
        self.run_import()
        job = self.notes("job")["Brand Designer, Example Tea Company"]
        own = job.body.split("## From my old tracker")[1]
        self.assertIn("- **My notes:** Loved the team, follow up Friday", own)
        self.assertIn("- **Priority:** A", own)
        self.assertIn("- **Status:** sent", own)                       # their own word, beside the plugin's status
        self.assertNotIn("## From my old tracker", job.section or "")  # outside the block the plugin rewrites
        self.assertIn("Imported on 2026-11-09 from row 2 of Jobs.csv.", job.section)
        late = self.notes("job")["Art Director, Sample Roasters"]
        self.assertNotIn("applied_on", late.values)
        self.assertIn("- **Applied:** next week", late.body)           # not a date, so kept as written
        self.assertIn("- **Hiring manager:** Raj Patel; Jane Doe", late.body)   # one link fits, the cell is kept whole
        group = self.notes("group")["Portland Brand Meetup"]
        self.assertIn("## My old notes", group.body)
        self.assertIn("- **Thoughts:** Good room", group.body)

    def test_a_second_run_adds_nothing_and_keeps_what_the_person_wrote(self):
        self.check()
        self.run_import()
        store = self.store()
        job = self.notes("job")["Brand Designer, Example Tea Company"]
        path = os.path.join(self.vault_dir, job.path)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace("Loved the team, follow up Friday", "Loved the team. Called Friday, second round booked.")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\nMy own line at the end.\n")
        store.upsert("job", job.id, job.title, {"status": "Interviewing"}, force=["status"])
        before = dict((kind, sorted(self.notes(kind))) for kind in ("job", "person", "group", "company"))
        code, out = self.check()
        self.assertIn("  Rows: 6. Would be written: 0. Already in the vault: 3. Not imported: 3.", out)
        self.run_import()
        self.assertEqual(dict((kind, sorted(self.notes(kind))) for kind in before), before)
        again = self.notes("job")["Brand Designer, Example Tea Company"]
        self.assertEqual(again.values["status"], "Interviewing")       # their change stands
        self.assertIn("Called Friday, second round booked.", again.body)
        self.assertIn("My own line at the end.", again.body)
        self.assertEqual(again.body.count("## From my old tracker"), 1)

    def test_a_sweep_style_write_leaves_the_imported_text_alone(self):
        self.check()
        self.run_import()
        store = self.store()
        job = self.notes("job")["Packaging Lead, Example Brewing"]
        store.upsert("job", job.id, job.title, {"fit": 71}, section="**Fit 71**: a later sweep rewrote this block.")
        after = self.notes("job")["Packaging Lead, Example Brewing"]
        self.assertIn("- **My notes:** Second choice", after.body)
        self.assertIn("a later sweep rewrote this block", after.section)

    def test_cancel_ends_it(self):
        self.check()
        code, out, _ = self.command("cancel")
        self.assertEqual(code, 0)
        code, _, err = self.command("run")
        self.assertEqual(code, 2)
        self.assertIn("Nothing is in progress", err)


class Reading(unittest.TestCase):
    def test_dates_and_numbers(self):
        self.assertEqual(importer.read_date("2026-11-02", []), "2026-11-02")
        self.assertEqual(importer.read_date("2026-11-02 09:30:00", []), "2026-11-02")
        self.assertEqual(importer.read_date("Nov 2, 2026", []), "2026-11-02")
        self.assertEqual(importer.read_date("02.11.2026", ["%d.%m.%Y"]), "2026-11-02")
        self.assertIsNone(importer.read_date("next week", []))
        self.assertIsNone(importer.read_date("13/45/2026", []))
        self.assertEqual(importer.read_number("$85,000"), 85000)
        self.assertEqual(importer.read_number("95k"), 95000)
        self.assertEqual(importer.read_number("$120/hr"), 120)
        self.assertEqual(importer.read_number("87.5"), 87.5)
        self.assertIsNone(importer.read_number("negotiable"))


class SkillText(unittest.TestCase):
    def test_the_skill_never_reads_the_files_and_asks_before_writing(self):
        with open(os.path.join(_paths.REPO_ROOT, "skills", "import", "SKILL.md"), encoding="utf-8") as handle:
            text = handle.read()
        front = text.split("---")[1]
        for tool in ("Read", "WebSearch", "WebFetch", "mcp__"):
            self.assertNotIn(tool, front)
        self.assertIn("Write(~/.cache/workdecero/import-plan.json)", front)
        for rule in ("**Never open, read or print the CSV files yourself.**", "**Never guess what a status word means.**",
                     "**Check first, and get a clear yes before `run`.**", "**Nothing is left behind.**"):
            self.assertIn(rule, text)
        self.assertEqual(import_tool.fields(out=io.StringIO()), 0)
        self.assertIn("job  (one row becomes one note in Jobs)", importer.fields_report())


if __name__ == "__main__":
    unittest.main()
