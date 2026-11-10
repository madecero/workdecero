"""Optional connections: what is on, what is refused, the caps, and the text the engine writes.

Nothing here reaches a browser, a calendar, a tracker or LinkedIn. The engine
never does. These tests check the gates and the words.
"""

import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "job-sweep", "scripts"))
sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "networking-sweep", "scripts"))
import integrations  # noqa: E402
import miniyaml  # noqa: E402
import networking_tool  # noqa: E402
import sources  # noqa: E402
import sweep  # noqa: E402
import sweep_tool  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")
TODAY = datetime.date(2026, 11, 9)
ALL_ON = {"browser": {"enabled": True, "linkedin_signed_in": True, "linkedin_risk_acknowledged": True},
          "calendar": "outlook",
          "task_tracker": {"kind": "linear", "jobs_issue": "ABC-12", "networking_issue": "ABC-14"}}


def profile(settings=None, quiet=False, mode="lite"):
    with open(os.path.join(EXAMPLES, "product-leader-denver.yaml"), encoding="utf-8") as handle:
        raw = miniyaml.loads(handle.read())
    raw["integrations"] = settings if settings is not None else {}
    raw["mode"] = mode
    raw["jobs"]["search_visibility"] = "quiet" if quiet else "open"
    return raw


class Gates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self._dry = os.environ.pop(integrations.DRY_RUN_ENV, None)
        self.addCleanup(self.restore)

    def restore(self):
        if self._dry is None:
            os.environ.pop(integrations.DRY_RUN_ENV, None)
        else:
            os.environ[integrations.DRY_RUN_ENV] = self._dry

    def test_everything_is_off_unless_turned_on(self):
        use = integrations.plan(profile())
        self.assertEqual((use["browser"], use["linkedin"], use["calendar"], use["tracker"]), (False, False, None, None))
        text = "\n".join(integrations.start_lines(profile(), "jobs", self.home, TODAY))
        self.assertIn("BROWSER PASS: OFF. Do not use any browser tool.", text)
        self.assertIn("SIGNED-IN LINKEDIN: OFF. Do not open LinkedIn while signed in.", text)
        self.assertIn("These tools are not pre-approved", text)

    def test_linkedin_needs_every_switch(self):
        for browser in ({"enabled": True, "linkedin_signed_in": True},                       # risk not acknowledged
                        {"enabled": True, "linkedin_risk_acknowledged": True},               # not turned on
                        {"enabled": False, "linkedin_signed_in": True, "linkedin_risk_acknowledged": True}):
            self.assertFalse(integrations.plan(profile({"browser": browser}))["linkedin"], browser)
        self.assertTrue(integrations.plan(profile(ALL_ON))["linkedin"])
        # The profile checker already refuses the half-set combinations when a profile is saved.
        raw = profile({"browser": {"enabled": True, "linkedin_signed_in": True}})
        self.assertIn("integrations.browser.linkedin_risk_acknowledged",
                      [problem.path for problem in wdprofile.check(raw)])

    def test_the_browser_rules_are_stated_in_full(self):
        text = "\n".join(integrations.start_lines(profile({"browser": {"enabled": True}}), "jobs", self.home, TODAY))
        for rule in ("BROWSER PASS: ON, for the places listed as needing a browser and nothing else. At most 15 pages.",
                     "Open ONE new tab of your own, work only in it, and close it when you finish.",
                     "Wait 4 to 12 seconds between page loads",
                     "Never type into a form",
                     "never click apply, save, follow, connect or message",
                     "If a site shows a CAPTCHA or asks you to prove you are human, stop on that site at once. Do not "
                     "try to solve it. Put the site's name in not_covered.",
                     "SIGNED-IN LINKEDIN: OFF."):
            self.assertIn(rule, text)
        full = "\n".join(integrations.start_lines(profile({"browser": {"enabled": True}}, mode="full"), "jobs",
                                                  self.home, TODAY))
        self.assertIn("At most 40 pages.", full)

    def test_linkedin_rules_caps_and_the_quiet_rule(self):
        text = "\n".join(integrations.start_lines(profile(ALL_ON), "jobs", self.home, TODAY))
        self.assertIn("SIGNED-IN LINKEDIN: ON, read-only. Left today: 20 pages, 5 searches.", text)
        self.assertIn("Never connect, message, follow, like, react, save, apply or endorse.", text)
        self.assertIn("1. Their 1st-degree connections at that employer", text)
        self.assertIn("2. The hiring team shown on the posting itself.", text)
        self.assertIn("3. One targeted people search", text)
        self.assertNotIn("QUIET SEARCH", text)
        quiet = "\n".join(integrations.start_lines(profile(ALL_ON, quiet=True), "jobs", self.home, TODAY))
        self.assertIn("QUIET SEARCH: never open anyone's profile, because a profile view can be shown to its owner.",
                      quiet)
        # The day's count is kept, shared by every sweep that day, and starts again the next day.
        integrations.record_linkedin(self.home, 12, 2, TODAY)
        integrations.record_linkedin(self.home, 6, 1, TODAY)
        self.assertEqual(integrations.linkedin_left(profile(ALL_ON), self.home, TODAY), (2, 2))
        self.assertEqual(integrations.linkedin_left(profile(ALL_ON), self.home, TODAY + datetime.timedelta(days=1)),
                         (20, 5))
        self.assertEqual(os.stat(os.path.join(self.home, "usage.json")).st_mode & 0o777, 0o600)
        later = "\n".join(integrations.start_lines(profile(ALL_ON), "networking", self.home, TODAY))
        self.assertIn("Left today: 2 pages, 2 searches.", later)
        integrations.record_linkedin(self.home, 1, 0, TODAY + datetime.timedelta(days=30))
        with open(os.path.join(self.home, "usage.json")) as handle:
            self.assertEqual(list(json.load(handle)["linkedin"]), ["2026-12-09"])     # old days are not kept

    def test_results_from_a_connection_that_is_off_are_refused(self):
        handoff = {"finds": [{"title": "VP of Product", "url": "https://jobs.example.com/1", "read_with": "browser"},
                             {"title": "Head of Product", "url": "https://jobs.example.com/2"}],
                   "warm": {"J-1": {"hiring_manager": {"name": "Marcus Oyelaran"},
                                    "connections": [{"name": "Priya Raman", "degree": "1st"}]}},
                   "linkedin_pages": 9, "linkedin_searches": 2}
        cleaned, lines = integrations.screen(handoff, profile(), self.home, TODAY)
        self.assertEqual([item["title"] for item in cleaned["finds"]], ["Head of Product"])
        self.assertNotIn("connections", cleaned["warm"]["J-1"])
        self.assertEqual(cleaned["warm"]["J-1"]["hiring_manager"], {"name": "Marcus Oyelaran"})   # public research stays
        self.assertIn("1 item read with a browser was left out: the browser pass is off in your profile.", lines)
        self.assertIn("People reported from signed-in LinkedIn were left out: that connection is off in your profile.",
                      lines)
        self.assertFalse(os.path.exists(os.path.join(self.home, "usage.json")))   # nothing is counted when it is off
        self.assertEqual(handoff["finds"][0]["read_with"], "browser")              # the caller's copy is untouched

    def test_results_are_kept_and_counted_when_the_connection_is_on(self):
        handoff = {"finds": [{"title": "VP of Product", "url": "https://jobs.example.com/1", "read_with": "browser"}],
                   "warm": {"J-1": {"connections": [{"name": "Priya Raman", "degree": "1st", "title": "VP Engineering"},
                                                    {"name": "No Degree Given"}, {"degree": "2nd"}]}},
                   "linkedin_pages": 19, "linkedin_searches": 2,
                   "integrations_skipped": ["calendar: no Outlook tool in this session"]}
        cleaned, lines = integrations.screen(handoff, profile(ALL_ON), self.home, TODAY)
        self.assertEqual(len(cleaned["finds"]), 1)
        self.assertIn("Browser pass: 1 item read in your browser.", lines)
        self.assertIn("Signed-in LinkedIn: 19 pages and 2 searches this step. Today so far: 19 of 20 pages, 2 of 5 "
                      "searches.", lines)
        self.assertIn("Skipped: calendar: no Outlook tool in this session.", lines)
        self.assertEqual(integrations.connections(cleaned["warm"]["J-1"]),
                         [{"name": "Priya Raman", "title": "VP Engineering", "company": "", "degree": "1st"}])
        _, again = integrations.screen({"linkedin_pages": 4}, profile(ALL_ON), self.home, TODAY)
        self.assertIn("The day's LinkedIn limit was passed. No more signed-in LinkedIn today.", again)

    def test_the_calendar_entry_is_written_by_the_engine(self):
        off = integrations.calendar_lines(profile(), "Founders night", "2026-11-18", "6:00 PM", "Example Hall",
                                          "https://events.example.com/1", "Founders in the room.")
        self.assertEqual(off, ["Calendar is off, so add it to your calendar yourself."])
        person = profile(ALL_ON)
        person["confidential"] = {"names": ["Alpenglow Freight Systems"]}
        lines = integrations.calendar_lines(person, "Alpenglow Freight Systems founders night", "2026-11-18", "6:00 PM",
                                            "Example Hall, Denver", "https://events.example.com/1",
                                            "Founders in the room.")
        text = "\n".join(lines)
        self.assertIn("CALENDAR (outlook). Do this now, in this order:", text)
        self.assertLess(text.index("1. Look at their calendar for 2026-11-18T18:00 to 2026-11-18T20:00."),
                        text.index("2. Add ONE event, with no attendees and no invitations:"))
        self.assertIn("tell the person what clashes and ask before adding", text)
        self.assertIn("       body:     https://events.example.com/1 Founders in the room.", text)
        self.assertIn("       timezone: America/Denver", text)
        self.assertNotIn("Alpenglow", text)                              # confidential names never reach the entry
        self.assertIn("If no outlook calendar tool is in this session, do not try another way.", text)
        self.assertNotIn("DRY RUN", text)
        all_day = "\n".join(integrations.calendar_lines(person, "Summit", "2026-12-03", None, None, None, None))
        self.assertIn("start:    2026-12-03  (all day)", all_day)
        self.assertEqual(integrations.calendar_lines(person, "No date", None, None, None, None, None),
                         ["The event has no date on file, so it was not offered to your calendar."])

    def test_the_tracker_comment_holds_counts_only(self):
        self.assertEqual(integrations.tracker_lines(profile(), "jobs", TODAY, [("new fits", 4)]), [])
        only_jobs = profile({"task_tracker": {"kind": "linear", "jobs_issue": "ABC-12"}})
        self.assertEqual(integrations.tracker_lines(only_jobs, "contracts", TODAY, [("new gigs", 2)]), [])
        lines = integrations.tracker_lines(profile(ALL_ON), "jobs", TODAY,
                                           [("new fits", 4), ("near misses", 8), ("closed", 0), ("left out", 3)], 2)
        self.assertEqual(lines[0], "TASK TRACKER (linear). Post exactly this as ONE comment on issue ABC-12, and change "
                                   "nothing else there:")
        self.assertEqual(lines[1], "    Job sweep, 2026-11-09: 4 new fits, 8 near misses, 0 closed, 3 left out. 2 "
                                   "sources not covered. The digest note in the tracker has the detail.")
        self.assertFalse(re.search(r"[A-Z][a-z]+ (Robotics|Health|Analytics)", lines[1]))

    def test_dry_run_calls_nothing(self):
        os.environ[integrations.DRY_RUN_ENV] = "1"
        person = profile(ALL_ON)
        start = "\n".join(integrations.start_lines(person, "jobs", self.home, TODAY))
        self.assertIn("DRY RUN: do not call any browser, calendar or tracker tool.", start)
        calendar = integrations.calendar_lines(person, "Summit", "2026-12-03", "9:00 AM", None, None, None)
        self.assertIn("DRY RUN: do not call the calendar tool.", calendar[1])
        tracker = integrations.tracker_lines(person, "jobs", TODAY, [("new fits", 1)])
        self.assertIn("DRY RUN: do not call the tracker tool.", tracker[1])


class InTheSweeps(unittest.TestCase):
    """The same gates, seen from the helpers a person actually runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "config")
        self.cache = os.path.join(self.tmp.name, "cache")
        self.vault_dir = os.path.join(self.tmp.name, "Notes")
        os.makedirs(os.path.join(self.vault_dir, ".obsidian"))
        os.makedirs(self.home)
        keys = ("WORKDECERO_HOME", sweep_tool.DRAFT_DIR_ENV, integrations.DRY_RUN_ENV)
        self._env = dict((key, os.environ.get(key)) for key in keys)
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ[sweep_tool.DRAFT_DIR_ENV] = self.cache
        os.environ[integrations.DRY_RUN_ENV] = "1"                    # nothing real is ever added or posted
        self.addCleanup(self.restore_env)
        sources.save(sources.empty_map())

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def use(self, settings, name="product-leader-denver"):
        with open(os.path.join(EXAMPLES, name + ".yaml"), encoding="utf-8") as handle:
            raw = miniyaml.loads(handle.read())
        raw["storage"] = {"vault_path": self.vault_dir, "vault_folder": "workdecero"}
        raw["integrations"] = settings
        with open(wdprofile.profile_path(), "w", encoding="utf-8") as handle:
            handle.write(miniyaml.dumps(raw))
        return wdprofile.load()[0]

    def hand_off(self, name, data):
        os.makedirs(self.cache, exist_ok=True)
        with open(os.path.join(self.cache, name), "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def run_step(self, function, **kwargs):
        out = io.StringIO()
        code = function(out=out, **kwargs)
        return code, out.getvalue()

    class NoWeb(object):
        def get(self, url, accept=None):
            raise AssertionError("no request expected: %s" % url)

        post_json = get

    def job_sweep(self, settings, finds, warm=None):
        self.use(settings)
        _, start = self.run_step(sweep_tool.start, fetcher=self.NoWeb(), today=TODAY)
        self.hand_off("sweep-handoff.json", {"finds": finds, "searches": 9})
        self.run_step(sweep_tool.intake, fetcher=self.NoWeb(), today=TODAY)
        written = ""
        if warm is not None:
            state = sweep.load_state(sweep_tool.work_dir())
            self.hand_off("sweep-handoff.json", {"warm": dict((entry["id"], warm) for entry in state["new"]),
                                                 "linkedin_pages": 3, "linkedin_searches": 1})
            _, written = self.run_step(sweep_tool.warm, today=TODAY)
        _, done = self.run_step(sweep_tool.finish, today=TODAY)
        store = vault.VaultStore(self.vault_dir)
        digest = [row for row in store.rows("digest")][0].section
        return start, written, done, store, digest

    FIND = {"title": "VP of Product", "company": "Front Range Robotics", "fit_assessment": 32, "work_mode": "remote",
            "url": "https://careers.example.com/frontrange/vp-product", "read_with": "browser"}
    WARM = {"hiring_manager": {"name": "Marcus Oyelaran", "title": "CTO"},
            "connections": [{"name": "Priya Raman", "title": "VP Engineering", "degree": "1st"}]}

    def test_with_everything_off_a_browser_find_and_linkedin_people_are_refused(self):
        start, written, done, store, digest = self.job_sweep({}, [self.FIND], self.WARM)
        self.assertIn("BROWSER PASS: OFF.", start)
        self.assertEqual(store.rows("job"), [])
        self.assertEqual([row.title for row in store.rows("person")], [])
        connections = digest.split("## Connections")[1].split("##")[0]
        self.assertIn("read with a browser was left out: the browser pass is off in your profile", connections)
        self.assertNotIn("TASK TRACKER", done)
        self.assertFalse(os.path.exists(os.path.join(self.home, "usage.json")))

    def test_with_everything_on_they_are_used_counted_and_reported(self):
        start, written, done, store, digest = self.job_sweep(ALL_ON, [self.FIND], self.WARM)
        self.assertIn("BROWSER PASS: ON", start)
        self.assertIn("SIGNED-IN LINKEDIN: ON, read-only. Left today: 20 pages, 5 searches.", start)
        self.assertEqual(len(store.rows("job")), 1)
        self.assertIn("People added from LinkedIn: 1.", written)
        people = dict((row.title, row) for row in store.rows("person"))
        self.assertEqual(people["Priya Raman"].values["relationship"], "1st")
        note = store.rows("job")[0].section
        self.assertIn("Priya Raman", note)                              # the warm-path engine used the connection
        connections = digest.split("## Connections")[1].split("##")[0]
        self.assertIn("Browser pass: 1 item read in your browser.", connections)
        self.assertIn("Signed-in LinkedIn: 3 pages and 1 search this step. Today so far: 3 of 20 pages, 1 of 5 "
                      "searches.", connections)
        self.assertIn("TASK TRACKER (linear). Post exactly this as ONE comment on issue ABC-12", done)
        self.assertIn("Job sweep, 2026-11-09: 1 new fits, 0 near misses, 0 closed, 0 left out.", done)
        self.assertIn("DRY RUN: do not call the tracker tool.", done)
        comment = [line for line in done.splitlines() if line.startswith("    Job sweep")][0]
        for private in ("Front Range", "Priya", "Marcus", "VP of Product"):
            self.assertNotIn(private, comment)                          # counts only

    def test_going_to_an_event_offers_the_calendar_only_when_one_is_set(self):
        os.environ[networking_tool.DRAFT_DIR_ENV] = self.cache
        for settings, expected in (({}, "Calendar is off, so add it to your calendar yourself."),
                                   ({"calendar": "google"}, "CALENDAR (google). Do this now, in this order:")):
            self.use(settings)
            store = vault.VaultStore(self.vault_dir)
            store.ensure_layout()
            store.upsert("event", "E-1", "Founders night", {"date": "2026-11-18", "time": "6:00 PM",
                                                            "where": "Example Hall, Denver",
                                                            "link": "https://events.example.com/1"})
            self.run_step(networking_tool.open_change)
            self.hand_off("networking-handoff.json", {"updates": [{"id": "E-1", "status": "Registered"}]})
            _, out = self.run_step(networking_tool.update, today=TODAY)
            self.run_step(networking_tool.close_change)
            self.assertIn(expected, out)
            if settings:
                self.assertIn("DRY RUN: do not call the calendar tool.", out)
                self.assertIn("no attendees and no invitations", out)
                self.assertIn("2026-11-18T18:00 to 2026-11-18T20:00", out)


class Words(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(_paths.REPO_ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_no_skill_or_hook_gives_standing_permission_for_these_tools(self):
        hook = self.read("hooks", "allow_helper.py")
        for word in ("mcp__", "chrome", "linear", "calendar", "WebSearch", "WebFetch"):
            self.assertNotIn(word, hook.lower() if word.islower() else hook)
        for skill in ("job-sweep", "contract-sweep", "networking-sweep"):
            text = self.read("skills", skill, "SKILL.md")
            front = text.split("---")[1]
            self.assertNotIn("mcp__", front)
            self.assertNotIn("chrome", front.lower())
            for rule in ("Off means off.", "Never try to solve one.", "These tools are not pre-approved.",
                         "do not look for another way", "Never add titles, employers or names to it."):
                self.assertIn(rule, text, "%s: %s" % (skill, rule))
        self.assertIn("look for a clash first", self.read("skills", "networking-sweep", "SKILL.md"))

    def test_the_linkedin_warning_is_shown_before_it_is_turned_on(self):
        setup = self.read("skills", "setup", "SKILL.md")
        self.assertIn("automated activity can get a LinkedIn account restricted or closed", setup)
        self.assertIn("only after they say they accept it", setup)
        readme = self.read("README.md")
        self.assertIn("Automated activity can get a LinkedIn account restricted or closed.", readme)
        self.assertIn("No standing permission for these.", readme)


if __name__ == "__main__":
    unittest.main()
