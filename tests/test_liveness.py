import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error

import _paths
import liveness
import model
import vault
from liveness import CLOSED, LIVE, UNKNOWN

FIXTURES = os.path.join(_paths.REPO_ROOT, "tests", "fixtures", "http")
TODAY = datetime.date(2026, 11, 9)


class FixtureFetcher(object):
    """Answers from recorded fixtures only. Any other request fails the test, so tests stay offline."""

    def __init__(self):
        self.answers = {}
        self.requested = []
        for name in sorted(os.listdir(FIXTURES)):
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
                for item in json.load(handle)["exchanges"]:
                    self.answers[item["url"]] = item

    def get(self, url, accept=None):
        self.requested.append(url)
        if url not in self.answers:
            raise AssertionError("request not covered by a fixture: %s" % url)
        item = self.answers[url]
        body = json.dumps(item["json"]) if "json" in item else item.get("body", "")
        return liveness.Response(item["status"], body, item.get("final_url", url), item.get("error"))


class SourceCase(unittest.TestCase):
    def setUp(self):
        self.fetcher = FixtureFetcher()

    def check(self, url):
        return liveness.check(url, self.fetcher, TODAY)

    def expect(self, url, state, fragment, suspect=False):
        result = self.check(url)
        self.assertEqual(result.state, state, "%s -> %s" % (url, result.evidence))
        self.assertIn(fragment, result.evidence)
        self.assertEqual(result.suspect, suspect, result.evidence)
        return result


class Workday(SourceCase):
    BASE = "https://examplehealth.wd5.myworkdayjobs.com/"

    def test_open(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Nurse-Manager_R-10231", LIVE, "posted 2026-10-28")

    def test_open_with_locale_prefix_trailing_slash_and_query(self):
        self.expect(self.BASE + "en-US/Careers/job/Round-Rock-TX/Nurse-Manager_R-10231/?source=LinkedIn", LIVE, "open")

    def test_closed_when_applications_are_off(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Charge-Nurse_R-10232", CLOSED, "canApply is false")

    def test_closed_when_not_found(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Never-Existed_R-00000", CLOSED, "HTTP 404")

    def test_closed_when_unpublished(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Unpublished_R-10233", CLOSED, "S22")

    def test_closed_when_the_answer_has_no_posting(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Empty-Answer_R-10236", CLOSED, "no longer returned")

    def test_unknown_when_blocked_without_the_unpublished_code(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Blocked_R-10234", UNKNOWN, "HTTP 403")

    def test_unknown_on_server_error(self):
        self.expect(self.BASE + "Careers/job/Round-Rock-TX/Server-Trouble_R-10235", UNKNOWN, "HTTP 500")

    def test_unknown_for_a_search_page(self):
        self.expect(self.BASE + "Careers?q=nurse", UNKNOWN, "not a single posting")
        self.assertEqual(self.fetcher.requested, [])


class Greenhouse(SourceCase):
    def test_open(self):
        self.expect("https://job-boards.greenhouse.io/exampleco/jobs/4000001", LIVE, "listed")
        self.expect("https://boards.greenhouse.io/exampleco/jobs/4000001?gh_src=abc", LIVE, "listed")

    def test_open_on_the_eu_host(self):
        self.expect("https://job-boards.eu.greenhouse.io/exampleco/jobs/4000004", LIVE, "listed")

    def test_closed(self):
        self.expect("https://job-boards.greenhouse.io/exampleco/jobs/4000002", CLOSED, "HTTP 404")

    def test_unknown(self):
        self.expect("https://job-boards.greenhouse.io/exampleco/jobs/4000003", UNKNOWN, "HTTP 500")


class Lever(SourceCase):
    def test_open(self):
        self.expect("https://jobs.lever.co/exampleco/11111111-1111-4111-8111-111111111111", LIVE, "listed")
        self.expect("https://jobs.lever.co/exampleco/11111111-1111-4111-8111-111111111111/apply", LIVE, "listed")

    def test_closed(self):
        self.expect("https://jobs.lever.co/exampleco/22222222-2222-4222-8222-222222222222", CLOSED, "HTTP 404")

    def test_unknown(self):
        self.expect("https://jobs.lever.co/exampleco/33333333-3333-4333-8333-333333333333", UNKNOWN, "HTTP 503")


class Ashby(SourceCase):
    def test_open(self):
        self.expect("https://jobs.ashbyhq.com/exampleco/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", LIVE, "listed")

    def test_closed_when_the_board_no_longer_lists_it(self):
        self.expect("https://jobs.ashbyhq.com/exampleco/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", CLOSED, "no longer listed")

    def test_unknown_when_the_board_is_missing_or_unreadable(self):
        self.expect("https://jobs.ashbyhq.com/goneco/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", UNKNOWN, "HTTP 404")
        self.expect("https://jobs.ashbyhq.com/oddco/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", UNKNOWN, "could not be read")


class LinkedIn(SourceCase):
    def test_open(self):
        self.expect("https://www.linkedin.com/jobs/view/4000000001", LIVE, "open")
        self.expect("https://www.linkedin.com/jobs/view/vp-of-product-at-example-robotics-4000000001?trk=x", LIVE, "open")

    def test_closed(self):
        self.expect("https://www.linkedin.com/jobs/view/4000000002/", CLOSED, "no longer accepting")
        self.expect("https://www.linkedin.com/jobs/view/4000000003", CLOSED, "HTTP 404")

    def test_unknown_when_rate_limited(self):
        self.expect("https://www.linkedin.com/jobs/view/4000000004", UNKNOWN, "HTTP 429")

    def test_unknown_for_a_search_link(self):
        self.expect("https://www.linkedin.com/jobs/search/?keywords=product", UNKNOWN, "not a posting")
        self.assertEqual(self.fetcher.requested, [])


class Sam(SourceCase):
    def test_open_before_the_deadline(self):
        self.expect("https://sam.gov/opp/0123456789abcdef0123456789abcdef/view", LIVE, "due 2026-12-01")

    def test_closed_after_the_deadline(self):
        self.expect("https://sam.gov/opp/1123456789abcdef0123456789abcdef/view", CLOSED, "2026-10-15")

    def test_closed_when_removed(self):
        self.expect("https://sam.gov/workspace/contract/opp/3123456789abcdef0123456789abcdef/view", CLOSED, "HTTP 404")

    def test_unknown(self):
        self.expect("https://sam.gov/opp/2123456789abcdef0123456789abcdef/view", UNKNOWN, "no response deadline")
        self.expect("https://sam.gov/opp/4123456789abcdef0123456789abcdef/view", UNKNOWN, "HTTP 502")
        self.expect("https://sam.gov/search/?keywords=design", UNKNOWN, "no opportunity id")


class Phenom(SourceCase):
    """These pages print 'has been filled' on every page. Only the embedded data counts."""
    BASE = "https://careers.examplehealth.example.com/us/en/job/"

    def test_open_through_the_embedded_workday_link(self):
        result = self.expect(self.BASE + "R-10231/Nurse-Manager", LIVE, "points to Workday")
        self.assertEqual(result.source, "Phenom")

    def test_closed_through_the_embedded_workday_link(self):
        self.expect(self.BASE + "R-10233/Unpublished", CLOSED, "S22")

    def test_open_and_closed_through_the_status_field(self):
        self.expect(self.BASE + "R-20001/Open-Role", LIVE, "OPEN")
        self.expect(self.BASE + "R-20002/Closed-Role", CLOSED, "CLOSED")

    def test_template_wording_is_never_the_reason(self):
        for path in ("R-10231/Nurse-Manager", "R-20001/Open-Role"):
            self.assertNotIn("page says", self.check(self.BASE + path).evidence)


class TekSystems(SourceCase):
    BASE = "https://careers.teksystems.com/us/en/job/"

    def test_open_despite_closed_wording_in_the_template(self):
        self.expect(self.BASE + "JP-000001/Product-Owner", LIVE, "wording on the page is ignored")

    def test_closed_on_410(self):
        self.expect(self.BASE + "JP-000002/Scrum-Master", CLOSED, "HTTP 410")

    def test_unknown_otherwise(self):
        self.expect(self.BASE + "JP-000003/Blocked", UNKNOWN, "HTTP 403")


class Generic(SourceCase):
    BASE = "https://jobs.smallco.example.com/openings/"

    def test_open(self):
        self.expect(self.BASE + "office-manager", LIVE, "no sign of being closed")
        self.expect(self.BASE + "redirect-to-same-job", LIVE, "no sign")

    def test_closed_on_strong_signals(self):
        self.expect(self.BASE + "removed", CLOSED, "HTTP 404")
        self.expect(self.BASE + "gone", CLOSED, "HTTP 410")

    def test_one_weak_signal_never_closes(self):
        result = self.expect(self.BASE + "one-marker", UNKNOWN, "Not enough to close it", suspect=True)
        self.assertIn("position has been filled", result.evidence)
        self.expect(self.BASE + "redirect-only", UNKNOWN, "redirects to a list page", suspect=True)

    def test_two_weak_signals_close(self):
        result = self.expect(self.BASE + "marker-and-redirect", CLOSED, "Two signs")
        self.assertEqual(len(result.weak_signals), 2)

    def test_unknown_when_blocked_or_unreachable(self):
        self.expect(self.BASE + "blocked", UNKNOWN, "HTTP 403")
        self.expect(self.BASE + "no-answer", UNKNOWN, "no answer")

    def test_no_link_and_browser_only_sites_make_no_request(self):
        self.expect("", UNKNOWN, "No posting link")
        self.expect(None, UNKNOWN, "No posting link")
        self.expect("see the recruiter email", UNKNOWN, "No posting link")
        self.expect("https://www.upwork.com/jobs/~0123456789", UNKNOWN, "real browser")
        self.assertEqual(self.fetcher.requested, [])

    def test_lookalike_hosts_are_not_treated_as_the_real_source(self):
        # A host that merely contains "greenhouse.io" in its name must go down the generic path.
        self.fetcher.answers["https://notgreenhouse.io.example.com/x/jobs/1"] = {"status": 404, "url": ""}
        self.assertEqual(self.check("https://notgreenhouse.io.example.com/x/jobs/1").source, "Web page")


class FakeReply(object):
    def __init__(self, status=200, body=b"ok", url="https://a.example.com/x"):
        self.status, self._body, self._url = status, body, url

    def read(self, limit=None):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Fetcher(unittest.TestCase):
    def build(self, script):
        """`script` is a list of replies or exceptions returned in order."""
        self.sleeps, self.requests, self.now = [], [], [0.0]
        pending = list(script)

        def opener(request, timeout=None):
            self.requests.append((request, timeout))
            item = pending.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        def sleep(seconds):
            self.sleeps.append(round(seconds, 3))
            self.now[0] += seconds

        return liveness.HttpFetcher(opener=opener, sleep=sleep, clock=lambda: self.now[0])

    def http_error(self, code, retry_after=None):
        headers = {"Retry-After": retry_after} if retry_after else {}
        return urllib.error.HTTPError("https://a.example.com/x", code, "err", headers, io.BytesIO(b"body"))

    def test_spaces_requests_to_the_same_host_only(self):
        fetcher = self.build([FakeReply(), FakeReply(), FakeReply()])
        fetcher.get("https://a.example.com/1")
        fetcher.get("https://a.example.com/2")
        fetcher.get("https://b.example.com/1")
        self.assertEqual(self.sleeps, [1.0])

    def test_retries_on_429_and_honours_retry_after(self):
        fetcher = self.build([self.http_error(429, "7"), FakeReply(body=b"fine")])
        response = fetcher.get("https://a.example.com/x")
        self.assertEqual((response.status, response.body), (200, "fine"))
        self.assertIn(7, self.sleeps)

    def test_backs_off_then_gives_up(self):
        fetcher = self.build([self.http_error(503)] * 3)
        response = fetcher.get("https://a.example.com/x")
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual([s for s in self.sleeps if s >= 5], [5, 10])

    def test_other_errors_are_not_retried(self):
        fetcher = self.build([self.http_error(404)])
        response = fetcher.get("https://a.example.com/x")
        self.assertEqual((response.status, response.body), (404, "body"))
        self.assertEqual(len(self.requests), 1)

    def test_network_failure_gives_no_status(self):
        fetcher = self.build([TimeoutError("slow")])
        response = fetcher.get("https://a.example.com/x")
        self.assertIsNone(response.status)
        self.assertEqual(response.error, "TimeoutError")

    def test_request_is_plain_and_bounded(self):
        fetcher = self.build([FakeReply()])
        fetcher.get("https://a.example.com/x")
        request, timeout = self.requests[0]
        self.assertEqual(timeout, liveness.TIMEOUT_SECONDS)
        agent = request.get_header("User-agent")
        self.assertEqual(agent, liveness.USER_AGENT)
        self.assertNotIn("@", agent)
        self.assertNotIn("http", agent)
        self.assertEqual(sorted(request.headers), ["Accept", "User-agent"])   # no cookies, no tokens


class Planning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "vault"))
        self.store = vault.VaultStore(os.path.join(self.tmp.name, "vault"), today=TODAY)
        self.fetcher = FixtureFetcher()
        base = "https://jobs.smallco.example.com/openings/"
        jobs = [
            ("J-1", "Open role", "New", base + "office-manager"),
            ("J-2", "Removed role", "New", base + "removed"),
            ("J-3", "Removed after applying", "Applied", base + "gone"),
            ("J-4", "Maybe closed", "Interested", base + "one-marker"),
            ("J-5", "Already passed", "Passed", base + "removed"),
            ("J-6", "No link", "New", None),
        ]
        for note_id, title, status, url in jobs:
            values = {"status": status, "role": title, "first_seen": "2026-11-01"}
            if url:
                values["apply_url"] = url
            self.store.upsert("job", note_id, title, values, section="Why it fits: invented.")

    def plan(self, today=TODAY):
        return liveness.plan_updates(self.store.rows("job"), model.JOB, self.fetcher, today)

    def by_id(self, operations):
        return dict((operation["id"], operation) for operation in operations)

    def test_finished_rows_are_not_checked(self):
        operations = self.by_id(self.plan())
        self.assertNotIn("J-5", operations)
        self.assertEqual(sorted(operations), ["J-1", "J-2", "J-3", "J-4", "J-6"])

    def test_live_row(self):
        operation = self.by_id(self.plan())["J-1"]
        self.assertEqual(operation["values"], {"last_checked": "2026-11-09", "liveness": "live",
                                               "last_seen": "2026-11-09"})
        self.assertEqual((operation["force"], operation["append"]), ([], None))

    def test_closed_row_not_yet_applied_to_is_closed_with_evidence(self):
        operation = self.by_id(self.plan())["J-2"]
        self.assertEqual(operation["values"]["status"], "Closed")
        self.assertEqual(operation["force"], ["status", "reason"])
        self.assertEqual(operation["append"], "Closed 2026-11-09: Page is gone (HTTP 404).")

    def test_closed_row_the_user_applied_to_keeps_its_status(self):
        operation = self.by_id(self.plan())["J-3"]
        self.assertNotIn("status", operation["values"])
        self.assertEqual(operation["force"], [])
        self.assertTrue(operation["append"].startswith("POSTING CLOSED 2026-11-09: Page is gone (HTTP 410)."))

    def test_one_weak_signal_marks_suspect_and_does_not_close(self):
        operation = self.by_id(self.plan())["J-4"]
        self.assertEqual(operation["values"]["liveness"], "suspect")
        self.assertNotIn("status", operation["values"])
        self.assertIn("may be closed", operation["append"])

    def test_unknown_row_only_records_the_check(self):
        operation = self.by_id(self.plan())["J-6"]
        self.assertEqual(operation["values"], {"last_checked": "2026-11-09", "liveness": "unknown"})
        self.assertIsNone(operation["append"])

    def test_planning_writes_nothing(self):
        self.store.changed[:] = []
        self.plan()
        self.assertEqual(self.store.changed, [])

    def test_applied_operations_keep_user_text_and_respect_ownership(self):
        row = self.store.get("job", "J-3")
        path = os.path.join(self.store.vault, row.path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\nMy interview notes.\n")
        self.store.apply(self.plan())
        rows = dict((item.id, item) for item in self.store.rows("job"))
        self.assertEqual(rows["J-2"].values["status"], "Closed")
        self.assertEqual(rows["J-2"].values["reason"], "Posting closed")
        self.assertIn("Closed 2026-11-09: Page is gone (HTTP 404).", rows["J-2"].section)
        self.assertIn("Why it fits: invented.", rows["J-2"].section)
        self.assertEqual(rows["J-3"].values["status"], "Applied")
        self.assertIn("POSTING CLOSED 2026-11-09", rows["J-3"].section)
        self.assertTrue(rows["J-3"].body.endswith("\nMy interview notes.\n"))
        self.assertEqual(rows["J-1"].values["liveness"], "live")
        self.assertEqual(rows["J-4"].values["status"], "Interested")

    def test_same_weak_signal_on_a_later_day_closes(self):
        self.store.apply(self.plan())
        same_day = self.by_id(self.plan())["J-4"]
        self.assertEqual(same_day["state"], UNKNOWN)          # a second look on the same day is not new evidence
        self.assertIsNone(same_day["append"])                 # and the note is not told twice
        later = self.by_id(self.plan(today=datetime.date(2026, 11, 16)))["J-4"]
        self.assertEqual(later["state"], CLOSED)
        self.assertEqual(later["values"]["status"], "Closed")
        self.assertIn("two checks (2026-11-09 and 2026-11-16)", later["append"])

    def test_closed_note_is_not_reported_twice(self):
        self.store.apply(self.plan())
        row = self.store.get("job", "J-3")
        again = liveness.plan_update(row, liveness.Result(CLOSED, "Page is gone (HTTP 410)", "Web page"),
                                     model.JOB, datetime.date(2026, 11, 16))
        self.assertIsNone(again["append"])

    def test_hand_typed_statuses_are_respected(self):
        base = "https://jobs.smallco.example.com/openings/"
        self.store.upsert("job", "J-7", "Typed loosely", {"status": " applied", "apply_url": base + "removed"})
        self.store.upsert("job", "J-8", "Typo", {"status": "Intervewing", "apply_url": base + "removed"})
        self.store.upsert("job", "J-9", "Done, typed loosely", {"status": "passed ", "apply_url": base + "removed"})
        operations = self.by_id(self.plan())
        self.assertNotIn("J-9", operations)                       # recognised as finished, not checked
        for note_id in ("J-7", "J-8"):
            self.assertNotIn("status", operations[note_id]["values"], note_id)
            self.assertIn("POSTING CLOSED", operations[note_id]["append"], note_id)

    def test_contracts_use_their_own_link_and_statuses(self):
        self.store.upsert("contract", "C-1", "Gone gig", {"status": "Negotiating",
                          "link": "https://jobs.smallco.example.com/openings/removed"})
        self.store.upsert("contract", "C-2", "Lost gig", {"status": "Lost",
                          "link": "https://jobs.smallco.example.com/openings/removed"})
        operations = liveness.plan_updates(self.store.rows("contract"), model.CONTRACT, self.fetcher, TODAY)
        self.assertEqual([operation["id"] for operation in operations], ["C-1"])
        self.assertNotIn("status", operations[0]["values"])
        self.assertIn("POSTING CLOSED", operations[0]["append"])


class Fixtures(unittest.TestCase):
    def test_fixtures_hold_no_headers_cookies_or_tokens(self):
        for name in os.listdir(FIXTURES):
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertIn("Invented", data["note"])
            for item in data["exchanges"]:
                extra = set(item) - {"url", "status", "body", "json", "final_url", "error"}
                self.assertEqual(extra, set(), "%s stores more than the answer" % name)
                self.assertNotIn("?token", item["url"])

    def test_every_source_has_open_closed_and_unknown_cases(self):
        fetcher = FixtureFetcher()
        cases = {
            "Workday": "https://examplehealth.wd5.myworkdayjobs.com/Careers/job/Round-Rock-TX/",
        }
        self.assertTrue(cases)
        seen = {}
        probes = [
            "https://examplehealth.wd5.myworkdayjobs.com/Careers/job/Round-Rock-TX/Nurse-Manager_R-10231",
            "https://examplehealth.wd5.myworkdayjobs.com/Careers/job/Round-Rock-TX/Unpublished_R-10233",
            "https://examplehealth.wd5.myworkdayjobs.com/Careers/job/Round-Rock-TX/Blocked_R-10234",
            "https://job-boards.greenhouse.io/exampleco/jobs/4000001",
            "https://job-boards.greenhouse.io/exampleco/jobs/4000002",
            "https://job-boards.greenhouse.io/exampleco/jobs/4000003",
            "https://jobs.lever.co/exampleco/11111111-1111-4111-8111-111111111111",
            "https://jobs.lever.co/exampleco/22222222-2222-4222-8222-222222222222",
            "https://jobs.lever.co/exampleco/33333333-3333-4333-8333-333333333333",
            "https://jobs.ashbyhq.com/exampleco/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "https://jobs.ashbyhq.com/exampleco/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "https://jobs.ashbyhq.com/goneco/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "https://www.linkedin.com/jobs/view/4000000001",
            "https://www.linkedin.com/jobs/view/4000000002",
            "https://www.linkedin.com/jobs/view/4000000004",
            "https://sam.gov/opp/0123456789abcdef0123456789abcdef/view",
            "https://sam.gov/opp/1123456789abcdef0123456789abcdef/view",
            "https://sam.gov/opp/2123456789abcdef0123456789abcdef/view",
            "https://careers.teksystems.com/us/en/job/JP-000001/Product-Owner",
            "https://careers.teksystems.com/us/en/job/JP-000002/Scrum-Master",
            "https://careers.teksystems.com/us/en/job/JP-000003/Blocked",
            "https://jobs.smallco.example.com/openings/office-manager",
            "https://jobs.smallco.example.com/openings/removed",
            "https://jobs.smallco.example.com/openings/blocked",
        ]
        for url in probes:
            result = liveness.check(url, fetcher, TODAY)
            seen.setdefault(result.source, set()).add(result.state)
        for source in ("Workday", "Greenhouse", "Lever", "Ashby", "LinkedIn", "SAM.gov", "TEKsystems", "Web page"):
            self.assertEqual(seen[source], {LIVE, CLOSED, UNKNOWN}, source)


if __name__ == "__main__":
    unittest.main()
