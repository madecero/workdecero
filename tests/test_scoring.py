import datetime
import os
import unittest

import _paths
import scoring
import wdprofile

TODAY = datetime.date(2026, 11, 9)
EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")


def load(name):
    profile, problems = wdprofile.load(os.path.join(EXAMPLES, name + ".yaml"))
    assert not problems, problems
    return profile


def postings():
    """One set of postings, scored for three different people. Everything here is invented."""
    return {
        "nurse_manager_local": dict(
            title="Nurse Manager, Medical Surgical", company="Lone Star Regional Health", industry="Hospitals",
            work_mode="onsite", location="Round Rock, TX", pay_min=105000, pay_max=125000, posted="2026-11-05",
            fit_assessment=30),
        "vp_product_denver": dict(
            title="VP of Product", company="Front Range Robotics", industry="B2B software", work_mode="hybrid",
            location="Denver, CO", pay_min=230000, pay_max=270000, posted="2026-11-06", fit_assessment=30),
        "head_of_product_remote": dict(
            title="Head of Product (Remote)", company="Mile High Analytics", industry="B2B software",
            work_mode="remote", pay_min=210000, pay_max=240000, posted="2026-10-20", fit_assessment=30),
        "director_nursing_no_pay": dict(
            title="Director of Nursing", company="Brushy Creek Medical Center", industry="Rehabilitation",
            work_mode="hybrid", location="Austin, TX", posted="2026-11-01", fit_assessment=30),
        "travel_nurse": dict(
            title="Travel Nurse Manager", company="Nomad Staffing", industry="Staffing agencies",
            work_mode="onsite", location="Round Rock, TX", pay_max=140000, posted="2026-11-07", fit_assessment=30),
        "at_current_employer_denver": dict(
            title="Senior Director of Product Management", company="Granite Peak Software, Inc.",
            industry="B2B software", work_mode="remote", pay_max=260000, posted="2026-11-07", fit_assessment=30),
        "consulting_firm": dict(
            title="Director of Product", company="Big Advisory Partners", industry="Consulting firms",
            work_mode="remote", pay_max=250000, posted="2026-11-07", fit_assessment=30),
    }


class ThreeProfiles(unittest.TestCase):
    """The same postings must score differently, and sensibly, for different people."""

    @classmethod
    def setUpClass(cls):
        cls.nurse = load("nurse-manager-texas")
        cls.leader = load("product-leader-denver")
        cls.designer = load("freelance-designer-portland")

    def job(self, name, profile):
        return scoring.score_job(postings()[name], profile, TODAY)

    def test_each_person_ranks_their_own_kind_of_role_first(self):
        def ranking(profile):
            scored = [(name, self.job(name, profile)) for name in postings()]
            kept = [(result.total, name) for name, result in scored if not result.dropped]
            return [name for _, name in sorted(kept, reverse=True)]

        self.assertEqual(ranking(self.nurse)[0], "nurse_manager_local")
        self.assertEqual(ranking(self.leader)[0], "vp_product_denver")
        self.assertNotIn("nurse_manager_local", ranking(self.leader))
        self.assertNotIn("travel_nurse", ranking(self.nurse))

    def test_local_onsite_role_suits_the_nurse_and_is_ruled_out_for_the_product_leader(self):
        nurse = self.job("nurse_manager_local", self.nurse)
        self.assertEqual(nurse.parts, {"fit": 30, "level": 15, "industry": 5, "location": 12, "warm": 0,
                                       "freshness": 10})
        self.assertEqual(nurse.total, 72)
        leader = self.job("nurse_manager_local", self.leader)
        self.assertEqual(leader.dropped, "pay tops out at 125000, under your floor of 210000")

    def test_denver_hybrid_role_is_local_for_one_and_out_of_area_for_the_other(self):
        leader = self.job("vp_product_denver", self.leader)
        self.assertEqual((leader.parts["location"], leader.parts["level"], leader.parts["industry"]), (14, 15, 5))
        self.assertEqual(leader.total, 74)
        nurse = self.job("vp_product_denver", self.nurse)
        # A job that needs them in a place they cannot reach is never listed as a fit.
        self.assertEqual(nurse.dropped, "it is hybrid work in a place outside your area")

    def test_remote_role_is_full_marks_for_one_and_a_caveat_for_the_other(self):
        leader = self.job("head_of_product_remote", self.leader)
        self.assertEqual(leader.parts["location"], 15)
        nurse = self.job("head_of_product_remote", self.nurse)
        self.assertEqual(nurse.parts["location"], 5)
        self.assertIn("Remote role, and your profile lists only onsite and hybrid", nurse.caveats)

    def test_unlisted_pay_is_kept_and_marked_with_no_penalty(self):
        result = self.job("director_nursing_no_pay", self.nurse)
        self.assertIsNone(result.dropped)
        self.assertIn("Pay not listed", result.caveats)
        self.assertEqual(result.penalties, [])
        self.assertEqual(result.parts["location"], 14)   # Austin is the nurse's metro, and hybrid is accepted
        self.assertEqual(result.parts["industry"], 5)

    def test_title_keyword_and_skipped_industry_drop_the_posting(self):
        self.assertIn("'travel'", self.job("travel_nurse", self.nurse).dropped)
        quiet = dict(postings()["travel_nurse"], title="Nurse Manager")
        self.assertIn("excluded by your profile (industry", scoring.score_job(quiet, self.nurse, TODAY).dropped)

    def test_current_employer_is_always_dropped_and_never_named(self):
        result = self.job("at_current_employer_denver", self.leader)
        self.assertEqual(result.dropped, "it is at an employer or client your profile keeps confidential")
        self.assertNotIn("Granite", result.dropped)
        self.assertIsNone(self.job("at_current_employer_denver", self.nurse).dropped)

    def test_flagged_exclusion_keeps_the_posting_with_a_caveat_and_a_penalty(self):
        result = self.job("consulting_firm", self.leader)
        self.assertIsNone(result.dropped)
        self.assertIn((scoring.PENALTY_FLAGGED, "flagged in your profile"), result.penalties)
        self.assertIn("Check first: Check for conflicts with advisory clients first", result.caveats)
        self.assertEqual(self.job("consulting_firm", self.nurse).penalties, [])

    def test_exclusion_with_an_end_date_stops_applying_after_it(self):
        posting = dict(title="VP of Product", company="Summit Ledger Software", work_mode="remote",
                       pay_max=260000, posted="2026-11-07", fit_assessment=30)
        confidential = scoring.score_job(posting, self.leader, TODAY)
        self.assertIn("confidential", confidential.dropped)
        profile = dict(self.leader, confidential={"names": []})
        self.assertIn("Non-solicit", scoring.score_job(posting, profile, TODAY).dropped)
        later = dict(posting, posted="2027-03-30")
        self.assertIsNone(scoring.score_job(later, profile, datetime.date(2027, 4, 1)).dropped)

    def test_contracts_score_for_the_designer_and_the_leader_differently(self):
        gig = dict(title="Brand identity for a cold brew startup", client="Tidewater Coffee", engagement="1099",
                   rate_min=90, rate_max=110, remote=True, hours_per_week=20, length_weeks=6,
                   posted="2026-11-07", fit_assessment=32)
        designer = scoring.score_contract(gig, self.designer, TODAY)
        self.assertIsNone(designer.dropped)
        self.assertEqual(designer.parts, {"engagement": 10, "rate": 15, "fit": 32, "location": 10, "warm": 0,
                                          "freshness": 10})
        self.assertEqual(designer.total, 77)
        leader = scoring.score_contract(gig, self.leader, TODAY)
        self.assertIn("which your profile does not take", leader.dropped)
        fractional = dict(gig, engagement="Fractional, C2C", rate_min=180, rate_max=200)
        self.assertEqual(scoring.score_contract(fractional, self.leader, TODAY).parts["rate"], 10)
        self.assertIn("does not take", scoring.score_contract(fractional, self.designer, TODAY).dropped)


class JobRules(unittest.TestCase):
    def setUp(self):
        self.profile = load("product-leader-denver")
        self.base = dict(title="VP of Product", company="Front Range Robotics", industry="B2B software",
                         work_mode="remote", pay_min=230000, pay_max=270000, posted="2026-11-06", fit_assessment=30)

    def score(self, **changes):
        item = dict(self.base)
        item.update(changes)
        return scoring.score_job(item, self.profile, TODAY)

    def test_weights_add_up_to_one_hundred(self):
        self.assertEqual(sum(scoring.JOB_WEIGHTS.values()), 100)
        self.assertEqual(sum(scoring.CONTRACT_WEIGHTS.values()), 100)

    def test_total_is_capped_between_zero_and_one_hundred(self):
        best = self.score(fit_assessment=99, warm="1st-hiring-team", posted="2026-11-09")
        self.assertEqual(best.total, 100)
        worst = self.score(fit_assessment=-5, work_mode="onsite", location="Miami, FL", link_verified=False,
                           title="Product Associate II", industry="Retail", posted="2026-10-25")
        self.assertGreaterEqual(worst.total, 0)
        self.assertLess(worst.total, 15)

    def test_warm_path_scale(self):
        points = [self.score(warm=kind).parts["warm"] for kind in
                  ("1st-hiring-team", "1st", "2nd-hiring-team", "2nd", "shared-background", "named-contact", None)]
        self.assertEqual(points, [15, 12, 10, 8, 5, 2, 0])

    def test_freshness_bands_and_age_limit(self):
        def at(days):
            posted = (TODAY - datetime.timedelta(days=days)).isoformat()
            return self.score(posted=posted)

        self.assertEqual([at(days).parts["freshness"] for days in (0, 7, 8, 14, 15, 21)], [10, 10, 8, 8, 5, 5])
        self.assertEqual(self.score(posted=None).parts["freshness"], 3)
        self.assertIn("older than the 21 days", at(22).dropped)      # this profile allows 21 days

    def test_level_is_read_from_the_title_when_not_given(self):
        cases = {"Chief Product Officer": "executive", "SVP, Product": "vp", "Head of Product": "director",
                 "Senior Director, Product": "director", "Group Product Manager": "manager",
                 "Principal Product Manager": "manager", "Staff Designer": "lead", "Senior Designer": "senior",
                 "Product Designer": None, "Associate Product Manager": "manager"}
        for title, level in cases.items():
            self.assertEqual(scoring.level_of({"title": title}), level, title)
        self.assertEqual(scoring.level_of({"title": "Anything", "level": "VP"}), "vp")
        self.assertEqual(self.score(title="Group Product Manager").parts["level"], 8)    # one step below director
        self.assertEqual(self.score(title="Senior Product Designer").parts["level"], 0)
        self.assertEqual(self.score(title="Product person").parts["level"], 8)           # level unknown

    def test_starting_pay_under_the_floor_costs_points_but_does_not_drop(self):
        result = self.score(pay_min=190000, pay_max=None)
        self.assertIsNone(result.dropped)
        self.assertIn((6, "starting pay under your floor"), result.penalties)
        self.assertIsNone(self.score(pay_min=190000, pay_max=215000).dropped)
        self.assertEqual(self.score(pay_min=190000, pay_max=215000).penalties, [])

    def test_unverified_link_and_deal_breaker(self):
        self.assertIn((3, "link not verified"), self.score(link_verified=False).penalties)
        self.assertEqual(self.score(link_verified=True).penalties, [])
        self.assertIn("deal breaker", self.score(deal_breaker="Fully onsite five days a week").dropped)

    def test_relocation_target_is_scored_as_a_stretch(self):
        result = self.score(work_mode="onsite", location="Seattle, WA")
        self.assertEqual(result.parts["location"], 8)
        self.assertIn("Would mean relocating", result.caveats)
        self.assertEqual(result.penalties, [])

    def test_distance_beats_place_names_when_given(self):
        self.assertEqual(self.score(work_mode="hybrid", location="Somewhere", distance_miles=10).parts["location"], 14)
        self.assertEqual(self.score(work_mode="hybrid", location="Denver, CO", distance_miles=80).parts["location"], 0)

    def test_missing_assessment_gets_half_marks_and_a_caveat(self):
        result = self.score(fit_assessment=None)
        self.assertEqual(result.parts["fit"], 20)
        self.assertIn("Fit to your background was not assessed", result.caveats)

    def test_breakdown_reads_plainly(self):
        text = self.score(link_verified=False).breakdown()
        self.assertEqual(text, "background 30/40, level 15/15, industry 5/5, location 15/15, warm path 0/15, "
                               "freshness 10/10, minus 3 (link not verified)")


class Confidential(unittest.TestCase):
    def test_names_are_scrubbed_from_text_and_lists_whatever_the_case(self):
        names = ["Summit Ledger Software", "Alpenglow"]
        self.assertEqual(scoring.scrub_confidential("Ex SUMMIT LEDGER SOFTWARE lead, knows alpenglow well", names),
                         "Ex [confidential] lead, knows [confidential] well")
        self.assertEqual(scoring.scrub_confidential(["Alpenglow Freight", 5, None], names),
                         ["[confidential] Freight", 5, None])

    def test_company_name_variants_match_an_exclusion(self):
        profile = load("product-leader-denver")
        for name in ("Granite Peak Software", "granite peak software inc", "The Granite Peak Software Company"):
            item = dict(title="VP of Product", company=name, work_mode="remote", pay_max=260000)
            self.assertIsNotNone(scoring.score_job(item, profile, TODAY).dropped, name)
        other = dict(title="VP of Product", company="Granite Peak Brewing", work_mode="remote", pay_max=260000)
        self.assertIsNone(scoring.score_job(other, profile, TODAY).dropped)


if __name__ == "__main__":
    unittest.main()
