import datetime
import os
import tempfile
import unittest

import _paths
import merge
import miniyaml
import vault
import wdprofile

TODAY = datetime.date(2026, 11, 9)
EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")


def job(**fields):
    base = dict(title="VP of Product", company="Front Range Robotics", work_mode="remote", pay_max=260000,
                posted="2026-11-06", fit_assessment=30)
    base.update(fields)
    return base


class Normalising(unittest.TestCase):
    def test_company_names(self):
        same = ["Front Range Robotics", "Front Range Robotics, Inc.", "front range robotics llc",
                "The Front Range Robotics Company"]
        self.assertEqual(len(set(merge.normalize_company(name) for name in same)), 1)
        self.assertNotEqual(merge.normalize_company("Front Range Robotics"), merge.normalize_company("Front Range"))
        self.assertEqual(merge.normalize_company("Smith & Jones"), merge.normalize_company("Smith and Jones"))

    def test_titles(self):
        same = ["VP of Product", "VP of Product (Remote)", "Vice President of Product - Remote",
                "vp of product [Contract]", "VP of Product, US"]
        self.assertEqual(len(set(merge.normalize_title(title) for title in same)), 1)
        self.assertEqual(merge.normalize_title("Sr. Product Manager"), merge.normalize_title("Senior Product Manager"))
        self.assertNotEqual(merge.normalize_title("VP of Product"), merge.normalize_title("VP of Product Design"))

    def test_links(self):
        self.assertEqual(merge.link_key("https://www.Example.com/jobs/123/?utm_source=x#apply"), "example.com/jobs/123")
        self.assertEqual(merge.link_key("http://example.com/jobs/123"), "example.com/jobs/123")
        self.assertEqual(merge.link_key("ask the recruiter"), "")
        self.assertEqual(merge.link_key(None), "")


class SameThing(unittest.TestCase):
    def test_jobs_by_posting_id(self):
        a = job(req_id="R-100", url="https://frontrange.wd5.myworkdayjobs.com/Careers/job/Denver/VP_R-100")
        b = job(req_id="r 100", title="Vice President, Product", url="https://www.linkedin.com/jobs/view/1")
        self.assertTrue(merge.same("job", a, b))

    def test_same_title_with_different_posting_ids_is_two_postings(self):
        a, b = job(req_id="R-100", location="Denver"), job(req_id="R-200", location="Seattle")
        self.assertFalse(merge.same("job", a, b))

    def test_jobs_by_title_when_an_id_is_missing(self):
        self.assertTrue(merge.same("job", job(req_id="R-100"), job(req_id=None, title="VP of Product (Remote)")))
        self.assertFalse(merge.same("job", job(), job(title="VP of Engineering")))
        self.assertFalse(merge.same("job", job(), job(company="Mile High Analytics")))

    def test_jobs_by_link_when_the_employer_differs_or_is_hidden(self):
        url = "https://jobs.example.com/openings/42?src=a"
        self.assertTrue(merge.same("job", job(company="Front Range Robotics", url=url),
                                   job(company="FRR Holdings", title="Other", url=url.replace("src=a", "src=b"))))
        self.assertFalse(merge.same("job", job(url="https://jobs.example.com/a"), job(company="X", url="https://jobs.example.com/b")))

    def test_undisclosed_clients_never_match_by_title(self):
        a = dict(title="Fractional Head of Product", client="Confidential", url="https://gigs.example.com/1")
        b = dict(title="Fractional Head of Product", client="Confidential", url="https://gigs.example.com/2")
        self.assertFalse(merge.same("contract", a, b))
        self.assertTrue(merge.same("contract", a, dict(b, url="https://gigs.example.com/1/")))

    def test_contracts_by_client_and_job_id_or_title(self):
        a = dict(title="Brand refresh", client="Tidewater Coffee", job_id="77")
        self.assertTrue(merge.same("contract", a, dict(title="Different words", client="Tidewater Coffee Co", job_id="77")))
        self.assertFalse(merge.same("contract", a, dict(title="Brand refresh", client="Tidewater Coffee", job_id="78")))
        self.assertTrue(merge.same("contract", a, dict(title="Brand Refresh (Contract)", client="Tidewater Coffee")))

    def test_events_need_the_same_date(self):
        a = dict(title="Product Leaders breakfast", date="2026-11-18", url="https://events.example.com/e/1")
        self.assertTrue(merge.same("event", a, dict(title="Product Leaders Breakfast", date="2026-11-18")))
        self.assertFalse(merge.same("event", a, dict(title="Product Leaders breakfast", date="2026-12-16")))
        self.assertTrue(merge.same("event", a, dict(title="PLB November", date="2026-11-18",
                                                    url="https://events.example.com/e/1?ref=x")))
        self.assertFalse(merge.same("event", dict(title="A"), dict(title="A")))

    def test_groups_by_name_or_a_link_to_the_group_itself(self):
        a = dict(name="Denver Product Leaders", url="https://www.meetup.example.com/denver-product-leaders/")
        self.assertTrue(merge.same("group", a, dict(name="denver product leaders")))
        self.assertTrue(merge.same("group", a, dict(name="DPL", url="https://meetup.example.com/denver-product-leaders")))
        home = dict(name="One", url="https://meetup.example.com/")
        self.assertFalse(merge.same("group", home, dict(name="Two", url="https://meetup.example.com")))


class Merging(unittest.TestCase):
    def test_employers_own_page_wins_and_other_places_are_kept(self):
        board = job(url="https://www.linkedin.com/jobs/view/4000000001", source="LinkedIn", fit_assessment=28,
                    pay_max=None, why="Level and focus match.")
        own = job(url="https://frontrange.wd5.myworkdayjobs.com/Careers/job/Denver/VP_R-100", source="Company site",
                  req_id="R-100", fit_assessment=33, link_verified=True)
        other = job(url="https://www.indeed.com/viewjob?jk=abc", source="Indeed")
        merged = merge.dedupe("job", [board, own, other])
        self.assertEqual(len(merged), 1)
        item = merged[0]
        self.assertEqual(item["url"], own["url"])
        self.assertEqual(item["req_id"], "R-100")
        self.assertEqual(item["fit_assessment"], 33)                 # the higher assessment
        self.assertEqual(item["why"], "Level and focus match.")      # a gap filled from the board copy
        self.assertTrue(item["link_verified"])
        self.assertEqual([entry["source"] for entry in item["also_listed"]], ["LinkedIn", "Indeed"])

    def test_source_rank(self):
        self.assertEqual(merge.source_rank({"url": "https://job-boards.greenhouse.io/x/jobs/1"}), 0)
        self.assertEqual(merge.source_rank({"url": "https://careers.example.com/1", "source": "Company site"}), 0)
        self.assertEqual(merge.source_rank({"url": "https://www.linkedin.com/jobs/view/1"}), 2)
        self.assertEqual(merge.source_rank({"url": "https://jobs.example.com/1"}), 1)
        self.assertEqual(merge.source_rank({"url": "https://notlinkedin.com.example.org/1"}), 1)

    def test_batch_order_and_distinct_items_are_kept(self):
        items = [job(title="A"), job(title="B"), job(title="A", url="https://x.example.com/1"), job(title="C")]
        self.assertEqual([item["title"] for item in merge.dedupe("job", items)], ["A", "B", "C"])

    def test_inputs_are_not_changed(self):
        a, b = job(url="https://www.linkedin.com/jobs/view/1"), job(url="https://jobs.example.com/1")
        before = (dict(a), dict(b))
        merge.dedupe("job", [a, b])
        self.assertEqual((a, b), before)


class IntakeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "vault"))
        self.store = vault.VaultStore(os.path.join(self.tmp.name, "vault"), today=TODAY)
        self.profile, problems = wdprofile.load(os.path.join(EXAMPLES, "product-leader-denver.yaml"))
        self.assertEqual(problems, [])

    def intake(self, items, kind="job", profile=None):
        return merge.plan_intake(kind, items, self.store, profile or self.profile, TODAY)

    def finds(self):
        return [
            job(req_id="R-100", url="https://www.linkedin.com/jobs/view/4000000001", source="LinkedIn",
                industry="B2B software", why="Level and focus match."),
            job(req_id="R-100", url="https://frontrange.wd5.myworkdayjobs.com/Careers/job/Denver/VP_R-100",
                source="Company site", industry="B2B software", warm="1st"),
            job(title="Head of Product", company="Mile High Analytics", url="https://jobs.example.com/mha/1"),
            job(title="Director of Product", company="Granite Peak Software", url="https://jobs.example.com/gps/9"),
            job(title="Product Marketing Lead", company="Alta Systems", url="https://jobs.example.com/alta/2"),
            job(title="VP of Product", company="Big Advisory Partners", industry="Consulting firms",
                url="https://jobs.example.com/bap/3", pay_max=None, pay_min=None),
        ]


class Intake(IntakeCase):
    def test_planning_writes_nothing(self):
        self.intake(self.finds())
        self.assertEqual(self.store.changed, [])
        self.assertEqual(self.store.rows("job"), [])

    def test_new_finds_are_deduped_scored_and_ordered(self):
        intake = self.intake(self.finds())
        new = [(operation["title"], result.total) for operation, result in intake.new]
        self.assertEqual([title for title, _ in new], [
            "VP of Product, Front Range Robotics",
            "Head of Product, Mile High Analytics",
            "VP of Product, Big Advisory Partners",
        ])
        self.assertEqual(new[0][1], 87)
        self.assertEqual(sorted((score for _, score in new), reverse=True), [score for _, score in new])
        dropped = dict(intake.dropped)
        self.assertIn("marketing", dropped["Product Marketing Lead, Alta Systems"])
        self.assertEqual(dropped["Director of Product, [confidential]"],
                         "it is at an employer or client your profile keeps confidential")

    def test_applied_operations_create_notes_and_company_notes(self):
        intake = self.intake(self.finds())
        self.store.apply(intake.operations)
        jobs = dict((row.title, row) for row in self.store.rows("job"))
        self.assertEqual(sorted(row.id for row in jobs.values()), ["J-1", "J-2", "J-3"])
        top = jobs["VP of Product, Front Range Robotics"]
        self.assertEqual(top.values["status"], "New")
        self.assertEqual(top.values["fit"], 87)
        self.assertEqual(top.values["role"], "VP of Product")
        self.assertEqual(top.values["req_id"], "R-100")
        self.assertEqual(top.values["company"], "[[workdecero/Companies/Front Range Robotics|Front Range Robotics]]")
        self.assertIn("myworkdayjobs.com", top.values["apply_url"])       # the employer's own page
        self.assertEqual(top.values["first_seen"], "2026-11-09")
        self.assertIn("**Fit 87**: background 30/40, level 15/15, industry 5/5, location 15/15, warm path 12/15, "
                      "freshness 10/10.", top.section)
        self.assertIn("**Why it fits:** Level and focus match.", top.section)
        self.assertIn("**Also listed on:** [LinkedIn](https://www.linkedin.com/jobs/view/4000000001)", top.section)
        flagged = jobs["VP of Product, Big Advisory Partners"]
        self.assertIn("Pay not listed.", flagged.section)
        self.assertIn("Check first: Check for conflicts with advisory clients first.", flagged.section)
        companies = sorted(row.title for row in self.store.rows("company"))
        self.assertEqual(companies, ["Big Advisory Partners", "Front Range Robotics", "Mile High Analytics"])

    def test_confidential_names_never_reach_the_vault(self):
        finds = self.finds() + [job(title="Head of Platform", company="Tall Pine Labs",
                                    url="https://jobs.example.com/tpl/1",
                                    why="Their CPO came from Summit Ledger Software, like you.",
                                    location="Near the Alpenglow Freight Systems campus")]
        self.store.apply(self.intake(finds).operations)
        for folder, _, files in os.walk(self.store.root):
            for name in files:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    text = handle.read()
                for secret in ("Granite Peak", "Summit Ledger", "Alpenglow"):
                    self.assertNotIn(secret, text, name)
                    self.assertNotIn(secret, name)
        note = [row for row in self.store.rows("job") if row.values["role"] == "Head of Platform"][0]
        self.assertIn("Their CPO came from [confidential], like you.", note.section)

    def test_second_sweep_refreshes_and_does_not_duplicate(self):
        self.store.apply(self.intake(self.finds()).operations)
        row = [r for r in self.store.rows("job") if r.values["role"] == "Head of Product"][0]
        path = os.path.join(self.store.vault, row.path)
        with open(path, encoding="utf-8") as handle:
            properties, body = miniyaml.split_front_matter(handle.read())
        properties["wd_status"] = "Applied"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(miniyaml.join_front_matter(properties, body + "\nMy notes.\n"))

        again = self.finds()
        again[2] = dict(again[2], pay_max=275000, url="https://www.linkedin.com/jobs/view/4000000009")
        later = datetime.date(2026, 11, 16)
        intake = merge.plan_intake("job", again, self.store, self.profile, later)
        self.assertEqual(intake.new, [])
        self.assertEqual(len(intake.seen_again), 3)
        self.assertEqual([op["op"] for op in intake.operations], ["update"] * 3)
        self.store.apply(intake.operations)
        rows = self.store.rows("job")
        self.assertEqual(len(rows), 3)
        refreshed = [r for r in rows if r.values["role"] == "Head of Product"][0]
        self.assertEqual(refreshed.values["status"], "Applied")          # the user's status stands
        self.assertEqual(refreshed.values["pay_max"], 275000)            # sweep-owned values refresh
        self.assertEqual(refreshed.values["last_seen"], "2026-11-16")
        self.assertEqual(refreshed.values["first_seen"], "2026-11-09")
        self.assertTrue(refreshed.body.endswith("\nMy notes.\n"))
        self.assertEqual(len(self.store.rows("company")), 3)

    def test_a_posting_the_user_passed_on_is_not_reopened(self):
        self.store.apply(self.intake(self.finds()).operations)
        row = [r for r in self.store.rows("job") if r.values["role"] == "Head of Product"][0]
        self.store.upsert("job", row.id, row.title, {"status": "passed "}, force=["status"])
        intake = self.intake(self.finds())
        self.assertIn(("Head of Product, Mile High Analytics", "already in your tracker as passed "), intake.dropped)
        self.assertEqual(len(intake.seen_again), 2)

    def test_digest_features_only_the_best_few_but_everything_is_written(self):
        finds = [job(title="Role %02d" % n, company="Company %02d" % n, url="https://jobs.example.com/%d" % n,
                     fit_assessment=n) for n in range(1, 13)]
        intake = self.intake(finds)                       # this profile sets digest.max_new_items to 8
        self.assertEqual(len(intake.new), 12)
        self.assertEqual(len(intake.featured), 8)
        self.assertEqual(intake.more, 4)
        self.assertEqual(intake.featured[0][0]["title"], "Role 12, Company 12")
        self.assertEqual(len([op for op in intake.operations if op["type"] == "job"]), 12)

    def test_ids_continue_from_what_is_in_the_vault(self):
        self.store.upsert("job", "J-41", "Older role", {"role": "Older role"})
        self.store.upsert("company", "O-7", "Mile High Analytics", {})
        intake = self.intake(self.finds())
        self.assertEqual(sorted(op["id"] for op in intake.operations if op["type"] == "job"), ["J-42", "J-43", "J-44"])
        self.assertEqual(sorted(op["id"] for op in intake.operations if op["type"] == "company"), ["O-8", "O-9"])

    def test_contract_intake(self):
        profile, _ = wdprofile.load(os.path.join(EXAMPLES, "freelance-designer-portland.yaml"))
        gigs = [
            dict(title="Brand identity for a cold brew startup", client="Tidewater Coffee", engagement="1099",
                 rate_min=90, rate_max=110, remote=True, hours_per_week=20, posted="2026-11-07", fit_assessment=32,
                 url="https://gigs.example.com/1", via="direct"),
            dict(title="Brand Identity for a Cold Brew Startup (Contract)", client="Tidewater Coffee Co",
                 url="https://www.linkedin.com/jobs/view/5", engagement="1099", rate_max=110, posted="2026-11-07"),
            dict(title="Packaging refresh", client="Vapor Cloud", industry="Tobacco and vaping", engagement="1099",
                 rate_max=120, url="https://gigs.example.com/2"),
            dict(title="Unpaid internship design", client="Tiny Studio", engagement="1099", rate_max=80,
                 url="https://gigs.example.com/3"),
            dict(title="Logo sprint", client="Cheap Co", engagement="1099", rate_max=40,
                 url="https://gigs.example.com/4"),
        ]
        intake = merge.plan_intake("contract", gigs, self.store, profile, TODAY)
        self.assertEqual([op["title"] for op, _ in intake.new], ["Brand identity for a cold brew startup"])
        reasons = dict(intake.dropped)
        self.assertIn("industry", reasons["Packaging refresh"])
        self.assertIn("keyword", reasons["Unpaid internship design"])
        self.assertIn("under your floor of 70", reasons["Logo sprint"])
        self.store.apply(intake.operations)
        row = self.store.rows("contract")[0]
        self.assertEqual((row.id, row.values["client"], row.values["fit"], row.values["remote"]),
                         ("C-1", "Tidewater Coffee", 77, True))
        self.assertIn("**Win odds 77**", row.section)


if __name__ == "__main__":
    unittest.main()
