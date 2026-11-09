import datetime
import os
import tempfile
import unittest

import _paths
import vault
import warmpath
import wdprofile

TODAY = datetime.date(2026, 11, 9)
EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")

TWO = {"domain": "frontrange.example.com", "pattern": "first.last", "evidence": "two-current"}


def fmt(evidence, pattern="first.last"):
    return {"domain": "frontrange.example.com", "pattern": pattern, "evidence": evidence}


class EmailConfidence(unittest.TestCase):
    def test_published_address_is_ninety_and_wins_over_any_format(self):
        person = {"name": "Marcus Oyelaran", "email_published": "m.oyelaran@frontrange.example.com"}
        self.assertEqual(warmpath.email_for(person, fmt("guess")),
                         ("m.oyelaran@frontrange.example.com", 90, "exact address published"))

    def test_scale_for_a_plain_name(self):
        person = {"name": "Marcus Oyelaran"}
        got = [(warmpath.email_for(person, fmt(evidence))[1]) for evidence in
               ("two-current", "one-example", "aggregator", "guess")]
        self.assertEqual(got, [75, 60, 40, 20])
        self.assertEqual(warmpath.email_for(person, fmt("two-current"))[0], "marcus.oyelaran@frontrange.example.com")

    def test_a_name_with_more_than_one_reading_drops_a_step(self):
        for name in ("Ines Carvalho-Reyes", "Mary Anne Van Der Berg", "Zoë O'Neil", "J. Smith", "Renée Dubois"):
            address, percent, basis = warmpath.email_for({"name": name}, fmt("two-current"))
            self.assertEqual(percent, 60, name)
            self.assertIn("more than one way", basis)
            self.assertTrue(address.endswith("@frontrange.example.com"))
        self.assertEqual(warmpath.email_for({"name": "Ines Carvalho-Reyes"}, fmt("one-example"))[1], 40)
        self.assertEqual(warmpath.email_for({"name": "Ines Carvalho-Reyes"}, fmt("guess"))[1], 20)

    def test_no_address_without_a_name_a_format_or_evidence(self):
        nothing = (None, None, None)
        self.assertEqual(warmpath.email_for({"title": "VP of Product"}, TWO), nothing)
        self.assertEqual(warmpath.email_for({"name": "Marcus Oyelaran"}, None), nothing)
        self.assertEqual(warmpath.email_for({"name": "Marcus Oyelaran"}, fmt(None)), nothing)
        self.assertEqual(warmpath.email_for({"name": "Marcus Oyelaran"}, fmt("two-current", "made.up")), nothing)
        self.assertEqual(warmpath.email_for({"name": "Cher"}, TWO), nothing)
        self.assertEqual(warmpath.email_for(None, TWO), nothing)

    def test_formats(self):
        expected = {"first.last": "marcus.oyelaran", "firstlast": "marcusoyelaran", "first_last": "marcus_oyelaran",
                    "flast": "moyelaran", "f.last": "m.oyelaran", "firstl": "marcuso", "first": "marcus",
                    "last.first": "oyelaran.marcus", "lastf": "oyelaranm"}
        for pattern, local in expected.items():
            self.assertEqual(warmpath.build_address("Marcus Oyelaran", fmt("guess", pattern)),
                             local + "@frontrange.example.com")

    def test_titles_suffixes_and_accents_are_handled(self):
        self.assertEqual(warmpath.name_parts("Dr. Marcus Oyelaran, PhD"), ("marcus", "oyelaran", False))
        self.assertEqual(warmpath.name_parts("Renée Dubois"), ("renee", "dubois", True))
        self.assertEqual(warmpath.name_parts('Robert "Bob" Smith'), ("robert", "smith", False))


class WarmCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "vault"))
        self.store = vault.VaultStore(os.path.join(self.tmp.name, "vault"), today=TODAY)
        self.profile, problems = wdprofile.load(os.path.join(EXAMPLES, "product-leader-denver.yaml"))
        self.assertEqual(problems, [])
        link = self.store.link
        self.store.upsert("company", "O-1", "Front Range Robotics", {})
        self.store.upsert("job", "J-1", "VP of Product, Front Range Robotics",
                          {"role": "VP of Product", "company": link("company", "Front Range Robotics")},
                          section="**Fit 87**: background 30/40.")
        self.store.upsert("job", "J-2", "Head of AI, Front Range Robotics",
                          {"role": "Head of AI", "company": link("company", "Front Range Robotics")})
        self.store.upsert("person", "P-1", "Priya Raman",
                          {"relationship": "1st", "company": link("company", "Front Range Robotics"),
                           "role": "VP Engineering", "why": "Worked together in 2019", "met": "Old team"})
        self.store.upsert("person", "P-2", "Tom Becker",
                          {"relationship": "1st", "company": link("company", "Granite Peak Software"),
                           "role": "Engineer"})
        self.store.upsert("person", "P-3", "Aisha Karim",
                          {"relationship": "1st", "company": link("company", "Mile High Analytics")})

    def full_research(self):
        return {
            "hiring_manager": {"name": "Marcus Oyelaran", "title": "Chief Technology Officer",
                               "source_url": "https://frontrange.example.com/team"},
            "skip_level": {"title": "Chief Executive Officer"},
            "recruiter": {"name": "Dana Whitcombe", "title": "Senior Recruiter", "scope": "function"},
            "email_format": dict(TWO, source_url="https://frontrange.example.com/press"),
            "insiders": [{"name": "Ines Carvalho-Reyes", "title": "Director of Design"}],
            "warm_paths": [{"via": "Aisha Karim", "to": "Marcus Oyelaran", "how": "same team at a past company."},
                           {"via": "Tom Becker", "to": "Dana Whitcombe"},
                           {"via": "Somebody Unknown", "to": "Marcus Oyelaran"}],
            "note": "The team doubled this year.",
            "next_step": "Ask Priya for a short call before you apply.",
        }

    def build(self, research, job_id="J-1", profile=None):
        row = self.store.get("job", job_id)
        return warmpath.build(row, research, self.store.rows("person"), self.store, profile or self.profile)

    def lines(self, warm):
        return warm.text.split("\n")


class Found(WarmCase):
    def test_facts_come_one_per_bullet_in_the_agreed_order(self):
        labels = [line.split(":**")[0].replace("- **", "") for line in self.lines(self.build(self.full_research()))]
        self.assertEqual(labels, ["Contact first", "Hiring manager", "Skip-level", "Recruiter", "Email format",
                                  "1st-degree connections", "Insiders", "Warm paths", "Note", "Next step"])

    def test_named_people_are_linked_with_an_address_percent_and_basis(self):
        text = self.build(self.full_research()).text
        self.assertIn("- **Hiring manager:** [[workdecero/People/Marcus Oyelaran|Marcus Oyelaran]], Chief Technology "
                      "Officer ([source](https://frontrange.example.com/team)). Email: "
                      "marcus.oyelaran@frontrange.example.com (email confidence 75%: format confirmed by two or more "
                      "current addresses).", text)
        self.assertIn("- **Email format:** first.last@frontrange.example.com (format confirmed by two or more "
                      "current addresses) ([source](https://frontrange.example.com/press)).", text)

    def test_a_role_with_no_name_is_never_given_a_name_or_an_address(self):
        text = self.build(self.full_research()).text
        self.assertIn("- **Skip-level:** Likely Chief Executive Officer (name not found).", text)
        skip_line = [line for line in text.split("\n") if "Skip-level" in line][0]
        self.assertNotIn("@", skip_line)

    def test_function_level_recruiter_is_marked_low_confidence(self):
        text = self.build(self.full_research()).text
        recruiter = [line for line in text.split("\n") if line.startswith("- **Recruiter")][0]
        # The address and the role match are two separate judgements, each labelled.
        self.assertIn("(email confidence 75%: format confirmed by two or more current addresses).", recruiter)
        self.assertTrue(recruiter.endswith("Role match: low confidence. Recruits for this kind of role, not "
                                           "confirmed on this posting."))
        research = self.full_research()
        research["recruiter"]["scope"] = "posting"
        confirmed = [line for line in self.build(research).text.split("\n") if line.startswith("- **Recruiter")][0]
        self.assertTrue(confirmed.endswith("Role match: named on this posting."))

    def test_connections_and_paths_come_from_the_network_only(self):
        text = self.build(self.full_research()).text
        self.assertIn("- **1st-degree connections:** [[workdecero/People/Priya Raman|Priya Raman]], VP Engineering.",
                      text)
        self.assertIn("[[workdecero/People/Aisha Karim|Aisha Karim (1st)]] knows Marcus Oyelaran (same team at a "
                      "past company)", text)
        self.assertNotIn("Somebody Unknown", text)       # not in the network, so cannot introduce

    def test_someone_you_know_at_the_company_is_a_warm_path_everywhere(self):
        """The bullet, the property behind the table column and the first line must agree."""
        research = {"hiring_manager": {"name": "Marcus Oyelaran", "title": "CTO"}}
        warm = self.build(research)
        lines = dict((line.split(":**")[0][4:], line) for line in self.lines(warm))
        self.assertEqual(lines["Warm paths"],
                         "- **Warm paths:** [[workdecero/People/Priya Raman|Priya Raman (1st)]] works there.")
        self.assertEqual(lines["1st-degree connections"],
                         "- **1st-degree connections:** [[workdecero/People/Priya Raman|Priya Raman]], VP Engineering.")
        job_values = warm.operations[-1]["values"]
        self.assertEqual(job_values["warm_path"], ["[[workdecero/People/Priya Raman|Priya Raman (1st)]]"])
        self.assertTrue(lines["Contact first"].startswith("- **Contact first:** Priya Raman (1st), for a warm "
                                                          "introduction."))
        self.assertEqual(job_values["approach"], lines["Contact first"][len("- **Contact first:** "):])
        self.assertEqual(warm.warm, "1st")
        # And the other way round: nobody known means no warm path in any of the three places.
        self.store.upsert("person", "P-1", "Priya Raman", {"relationship": "Not connected"})
        cold = self.build(research)
        self.assertIn("- **Warm paths:** none found.", self.lines(cold))
        self.assertEqual(cold.operations[-1]["values"]["warm_path"], [])
        self.assertNotIn("warm introduction", cold.operations[-1]["values"]["approach"])

    def test_second_degree_at_the_company_shows_its_degree(self):
        self.store.upsert("person", "P-1", "Priya Raman", {"relationship": "2nd"})
        warm = self.build({})
        self.assertEqual(warm.operations[-1]["values"]["warm_path"],
                         ["[[workdecero/People/Priya Raman|Priya Raman (2nd)]]"])
        self.assertIn("Priya Raman (2nd)]] works there, one introduction away.", warm.text)
        self.assertEqual(warm.warm, "2nd")

    def test_contact_first_line_for_each_situation(self):
        def first(research, **changes):
            for name, values in changes.items():
                self.store.upsert("person", {"priya": "P-1"}[name], "Priya Raman", values)
            return self.lines(self.build(research))[0][len("- **Contact first:** "):]

        manager = {"name": "Marcus Oyelaran", "title": "CTO"}
        self.assertEqual(first({"hiring_manager": {"name": "Priya Raman", "title": "VP Engineering"}}),
                         "Priya Raman, directly. A 1st-degree connection who is on the hiring team.")
        self.assertEqual(first({"hiring_manager": manager}),
                         "Priya Raman (1st), for a warm introduction. A connection of yours who works there.")
        nobody = {"relationship": "Not connected"}
        self.assertEqual(first({"hiring_manager": manager,
                                "warm_paths": [{"via": "Aisha Karim", "to": "Marcus Oyelaran"}]}, priya=nobody),
                         "Aisha Karim (1st), for a warm introduction to Marcus Oyelaran.")
        self.assertEqual(first({"hiring_manager": manager, "email_format": TWO}),
                         "Marcus Oyelaran, the hiring manager, directly by email (email confidence 75%). No one in "
                         "your network works there.")
        self.assertEqual(first({"recruiter": {"name": "Dana Whitcombe", "scope": "posting"}, "email_format": TWO}),
                         "Dana Whitcombe, the recruiter on this posting, directly by email (email confidence 75%).")
        self.assertEqual(first({"hiring_manager": manager}),
                         "Marcus Oyelaran, the hiring manager, directly. No email was found, so use LinkedIn or the "
                         "application.")
        guessed = dict(manager, inferred=True)                # worked out from a leadership page, not named on the posting
        self.assertEqual(first({"hiring_manager": guessed}),
                         "Marcus Oyelaran, likely the hiring manager (not named on the posting), directly. No email "
                         "was found, so use LinkedIn or the application.")
        self.assertEqual(first({"hiring_manager": guessed, "email_format": TWO}),
                         "Marcus Oyelaran, likely the hiring manager (not named on the posting), directly by email "
                         "(email confidence 75%). No one in your network works there.")
        self.assertEqual(first({"hiring_manager": {"title": "VP of Product (likely)"}}),
                         "No one found yet. Apply directly; the hiring manager is likely VP of Product.")
        self.assertEqual(first({"hiring_manager": {"title": "VP of Product"}}),
                         "No one found yet. Apply directly; the hiring manager is likely VP of Product.")
        self.assertEqual(first({}), "No one found yet. Apply directly.")
        # A recruiter who only recruits for this kind of role is not who to contact first.
        self.assertEqual(first({"recruiter": {"name": "Dana Whitcombe", "scope": "function"}, "email_format": TWO}),
                         "No one found yet. Apply directly.")

    def test_warm_tier_for_scoring(self):
        self.assertEqual(self.build(self.full_research()).warm, "1st")
        research = self.full_research()
        research["hiring_manager"]["name"] = "Priya Raman"
        self.assertEqual(self.build(research).warm, "1st-hiring-team")
        self.store.upsert("person", "P-1", "Priya Raman", {"relationship": "Not connected"})
        self.assertEqual(self.build(self.full_research()).warm, "2nd-hiring-team")
        research = self.full_research()
        research["warm_paths"] = [{"via": "Aisha Karim", "to": "someone in finance"}]
        self.assertEqual(self.build(research).warm, "2nd")
        self.assertEqual(self.build({"shared_background": "both worked at Example Corp"}).warm, "shared-background")
        self.assertEqual(self.build({"hiring_manager": {"name": "Marcus Oyelaran"}}).warm, "named-contact")
        self.assertIsNone(self.build({"hiring_manager": {"title": "CTO"}}).warm)

    def test_applied_operations_write_the_part_people_and_links(self):
        operations, results = warmpath.plan_warm_paths({"J-1": self.full_research(), "J-404": {}}, self.store,
                                                       self.profile)
        self.assertEqual(list(results), ["J-1"])
        self.assertEqual(self.store.changed[-1:], self.store.changed[-1:])
        self.store.apply(operations)
        job = self.store.get("job", "J-1")
        self.assertTrue(job.section.startswith("**Fit 87**: background 30/40.\n\n### Warm path\n\n- **Contact first:**"))
        self.assertEqual(job.values["warm_path"], ["[[workdecero/People/Priya Raman|Priya Raman (1st)]]",
                                                   "[[workdecero/People/Aisha Karim|Aisha Karim (1st)]]"])
        self.assertEqual(job.values["approach"],
                         "Priya Raman (1st), for a warm introduction. A connection of yours who works there.")
        people = dict((row.title, row) for row in self.store.rows("person"))
        self.assertEqual(sorted(people), ["Aisha Karim", "Dana Whitcombe", "Ines Carvalho-Reyes", "Marcus Oyelaran",
                                          "Priya Raman", "Tom Becker"])
        marcus = people["Marcus Oyelaran"]
        self.assertEqual((marcus.id, marcus.values["relationship"], marcus.values["confidence"]),
                         ("P-4", "Not connected", 75))
        self.assertEqual(marcus.values["email"], "marcus.oyelaran@frontrange.example.com")
        self.assertEqual(marcus.values["linked_roles"],
                         ["[[workdecero/Jobs/VP of Product, Front Range Robotics|VP of Product, Front Range Robotics]]"])
        self.assertEqual(people["Ines Carvalho-Reyes"].values["confidence"], 60)     # a name with two readings
        self.assertEqual(people["Priya Raman"].values["why"], "Worked together in 2019")   # the network's own facts stay
        self.assertEqual(people["Priya Raman"].values["relationship"], "1st")

    def test_same_person_on_two_postings_gets_one_note_with_both_roles(self):
        research = {"hiring_manager": {"name": "Marcus Oyelaran", "title": "CTO"}}
        operations, _ = warmpath.plan_warm_paths({"J-1": research, "J-2": research}, self.store, self.profile)
        self.store.apply(operations)
        rows = [row for row in self.store.rows("person") if row.title == "Marcus Oyelaran"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0].values["linked_roles"]), 2)

    def test_running_twice_changes_nothing_and_keeps_log_lines(self):
        operations, _ = warmpath.plan_warm_paths({"J-1": self.full_research()}, self.store, self.profile)
        self.store.apply(operations)
        self.store.apply([{"type": "job", "id": "J-1", "values": {}, "append": "2026-11-09: posting may be closed."}])
        store = vault.VaultStore(self.store.vault, today=TODAY)
        operations, _ = warmpath.plan_warm_paths({"J-1": self.full_research()}, store, self.profile)
        store.apply(operations)
        self.assertEqual(store.changed, [])
        section = store.get("job", "J-1").section
        self.assertTrue(section.endswith("### Log\n\n- 2026-11-09: posting may be closed."))
        self.assertLess(section.index("### Warm path"), section.index("### Log"))

    def test_planning_writes_nothing(self):
        self.store.changed[:] = []
        warmpath.plan_warm_paths({"J-1": self.full_research()}, self.store, self.profile)
        self.assertEqual(self.store.changed, [])


class NotFound(WarmCase):
    def test_nothing_found_still_says_so_plainly(self):
        warm = self.build({}, job_id="J-2", profile=dict(self.profile, jobs=dict(self.profile["jobs"])))
        self.store.upsert("person", "P-1", "Priya Raman", {"relationship": "Not connected"})
        warm = self.build({}, job_id="J-2")
        self.assertEqual(self.lines(warm), [
            "- **Contact first:** No one found yet. Apply directly.",
            "- **Hiring manager:** not found.",
            "- **Recruiter:** not found.",
            "- **Email format:** not found.",
            "- **1st-degree connections:** none in your network at this company.",
            "- **Warm paths:** none found.",
        ])
        self.assertIsNone(warm.warm)
        self.assertEqual([op["type"] for op in warm.operations], ["job"])
        self.assertEqual(warm.operations[0]["values"]["warm_path"], [])

    def test_named_person_without_a_format_gets_no_address(self):
        self.store.upsert("person", "P-1", "Priya Raman", {"relationship": "Not connected"})
        text = self.build({"hiring_manager": {"name": "Marcus Oyelaran", "title": "CTO"}}).text
        self.assertIn("[[workdecero/People/Marcus Oyelaran|Marcus Oyelaran]], CTO. Email: not found.", text)
        self.assertNotIn("@", text)

    def test_works_for_a_profile_with_no_job_settings_or_network(self):
        nurse, _ = wdprofile.load(os.path.join(EXAMPLES, "nurse-manager-texas.yaml"))
        with tempfile.TemporaryDirectory() as tmp:
            store = vault.VaultStore(tmp, today=TODAY)
            store.upsert("job", "J-1", "Nurse Manager, Lone Star Regional Health",
                         {"role": "Nurse Manager", "company": store.link("company", "Lone Star Regional Health")})
            research = {"hiring_manager": {"title": "Chief Nursing Officer"},
                        "recruiter": {"name": "Gabriela Soto", "title": "Talent Partner"},
                        "email_format": {"domain": "lonestar.example.org", "pattern": "flast", "evidence": "one-example"}}
            operations, results = warmpath.plan_warm_paths({"J-1": research}, store, nurse)
            store.apply(operations)
            text = store.get("job", "J-1").section
            self.assertIn("- **Hiring manager:** Likely Chief Nursing Officer (name not found).", text)
            self.assertIn("- **Contact first:** Gabriela Soto, the recruiter on this posting, directly by email "
                          "(email confidence 60%).", text)
            self.assertIn("Email: gsoto@lonestar.example.org (email confidence 60%: one published example of the "
                          "format).", text)
            self.assertEqual(results["J-1"].warm, "named-contact")
            self.assertEqual([row.title for row in store.rows("person")], ["Gabriela Soto"])


class QuietSearch(WarmCase):
    def test_coworkers_are_never_offered_as_a_path(self):
        text = self.build(self.full_research()).text          # this profile searches quietly
        self.assertNotIn("Tom Becker", text)
        open_profile = dict(self.profile, jobs=dict(self.profile["jobs"], search_visibility="open"))
        self.assertIn("Tom Becker", self.build(self.full_research(), profile=open_profile).text)

    def test_client_or_partner_of_the_current_employer_gets_a_caution_instead_of_outreach(self):
        research = self.full_research()
        research["related_to_current_employer"] = "client"
        warm = self.build(research)
        lines = self.lines(warm)
        self.assertEqual(lines[0], "- **Contact first:** No one for now. See the caution below.")
        self.assertEqual(lines[1], "- **Caution:** this company is a client of your current employer. Reaching out "
                                   "could reveal your search, so no outreach is suggested here.")
        self.assertIn("- **1st-degree connections:** none in your network at this company.", lines)
        self.assertIn("- **Warm paths:** none found.", lines)
        self.assertFalse(any(line.startswith("- **Next step") for line in lines))
        self.assertIsNone(warm.warm)
        self.assertEqual(warm.operations[-1]["values"]["warm_path"], [])
        open_profile = dict(self.profile, jobs=dict(self.profile["jobs"], search_visibility="open"))
        self.assertNotIn("Caution", self.build(research, profile=open_profile).text)

    def test_a_flagged_employer_gets_a_caution_and_no_outreach(self):
        profile = dict(self.profile)
        profile["exclusions"] = list(profile["exclusions"]) + [
            {"name": "Front Range Robotics, Inc.", "kind": "employer", "action": "flag",
             "reason": "Client of my current employer"}]
        warm = self.build(self.full_research(), profile=profile)
        lines = self.lines(warm)
        self.assertEqual(lines[0], "- **Contact first:** No one for now. See the caution below.")
        self.assertEqual(lines[1], "- **Caution:** check first. Client of my current employer. No outreach is "
                                   "suggested until you have.")
        self.assertIn("- **Warm paths:** none found.", lines)
        self.assertFalse(any(line.startswith("- **Next step") for line in lines))
        self.assertEqual(warm.operations[-1]["values"]["warm_path"], [])
        self.assertIsNone(warm.warm)
        self.assertNotIn("Caution", self.build(self.full_research()).text)     # not flagged: unchanged

    def test_confidential_names_are_scrubbed_from_everything_written(self):
        research = self.full_research()
        research["note"] = "Their CTO joined from Summit Ledger Software, and they compete with Granite Peak Software."
        research["insiders"][0]["why"] = "Ex Alpenglow Freight Systems"
        operations, _ = warmpath.plan_warm_paths({"J-1": research}, self.store, self.profile)
        self.store.apply(operations)
        text = self.store.get("job", "J-1").section
        self.assertIn("Their CTO joined from [confidential], and they compete with [confidential].", text)
        ines = [row for row in self.store.rows("person") if row.title == "Ines Carvalho-Reyes"][0]
        self.assertEqual(ines.values["why"], "Ex [confidential]")


CSV = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing. You will only see email addresses for connections who have allowed their connections to see or download their email address. Learn more at the help center"

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Priya,Raman,https://www.linkedin.com/in/example-priya,,Front Range Robotics,VP Engineering,09 Nov 2024
Luis,Ortega,https://www.linkedin.com/in/example-luis,luis@ortega.example.com,"Okafor, Stone & Partners","Partner, Design",03 Jan 2021
Tom,Becker,https://www.linkedin.com/in/example-tom,,"Granite Peak Software, Inc.",Engineer,15 Mar 2023
,,,,,,22 Feb 2020
Mei,Tanaka,https://www.linkedin.com/in/example-mei,,,,
Luis,Ortega,https://www.linkedin.com/in/example-luis,,Duplicate Row Co,Partner,03 Jan 2021
"""


class NetworkImport(WarmCase):
    def test_reads_the_export_past_the_note_lines(self):
        counts = {}
        rows = warmpath.read_connections(CSV, counts)
        self.assertEqual([row["name"] for row in rows], ["Priya Raman", "Luis Ortega", "Tom Becker", "Mei Tanaka",
                                                         "Luis Ortega"])
        self.assertEqual(rows[1]["company"], "Okafor, Stone & Partners")
        self.assertEqual(rows[0]["connected_on"], "2024-11-09")
        self.assertIsNone(rows[3]["connected_on"])
        self.assertEqual(counts, {"unnamed": 1})
        self.assertEqual(len(warmpath.read_connections(chr(0xFEFF) + CSV.split("\n\n", 1)[1])), 5)

    def test_a_file_that_is_not_the_export_is_refused(self):
        with self.assertRaises(ValueError):
            warmpath.read_connections("name,company\nPriya,Front Range\n")

    def test_import_creates_people_and_companies_and_keeps_what_is_known(self):
        plan = warmpath.plan_network_import(CSV, self.store, self.profile)
        self.assertEqual((plan.added, plan.updated, plan.skipped_confidential, plan.skipped_unnamed), (2, 1, 1, 1))
        self.store.apply(plan.operations)
        people = dict((row.title, row) for row in self.store.rows("person"))
        luis = people["Luis Ortega"]
        self.assertEqual(luis.values["relationship"], "1st")
        self.assertEqual(luis.values["met"], "LinkedIn connection since 2021-01-03")
        self.assertEqual(luis.values["company"],
                         "[[workdecero/Companies/Okafor, Stone & Partners|Okafor, Stone & Partners]]")
        self.assertNotIn("email", luis.values)                       # not without the user's say-so
        self.assertEqual(people["Mei Tanaka"].values["met"], "LinkedIn connection")
        self.assertEqual(people["Priya Raman"].values["met"], "Old team")          # set by the user earlier
        self.assertEqual(people["Priya Raman"].values["why"], "Worked together in 2019")
        self.assertIn("Okafor, Stone & Partners", [row.title for row in self.store.rows("company")])

    def test_emails_are_written_only_when_asked_for(self):
        plan = warmpath.plan_network_import(CSV, self.store, self.profile, include_emails=True)
        self.store.apply(plan.operations)
        luis = [row for row in self.store.rows("person") if row.title == "Luis Ortega"][0]
        self.assertEqual((luis.values["email"], luis.values["confidence"]), ("luis@ortega.example.com", 90))

    def test_coworkers_at_the_current_employer_are_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = vault.VaultStore(tmp, today=TODAY)
            plan = warmpath.plan_network_import(CSV, store, self.profile)
            store.apply(plan.operations)
            self.assertEqual(plan.skipped_confidential, 1)
            for folder, _, files in os.walk(tmp):
                for name in files:
                    self.assertNotIn("Becker", name)
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        self.assertNotIn("Granite Peak", handle.read())

    def test_importing_twice_adds_nothing(self):
        self.store.apply(warmpath.plan_network_import(CSV, self.store, self.profile).operations)
        store = vault.VaultStore(self.store.vault, today=TODAY)
        plan = warmpath.plan_network_import(CSV, store, self.profile)
        store.apply(plan.operations)
        self.assertEqual((plan.added, store.changed), (0, []))


if __name__ == "__main__":
    unittest.main()
