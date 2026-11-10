#!/usr/bin/env python3
"""Liveness: is a job or contract posting still open?

One checker for every sweep. Each source has its own rule, learned the hard
way, and every answer comes with the evidence it rests on.

    live      the source says the posting is open
    closed    the source says it is gone, on strong evidence
    unknown   could not decide: blocked, a search link, or only a weak hint

The rule that matters most: never close a posting on one weak signal. Career
page templates often contain "no longer available" text on every page, live or
not. So:
  * A strong signal closes at once: the hiring system's own API says the
    posting is gone or cannot be applied to, or the page returns 404 or 410.
  * A weak signal (closed wording on a page, a redirect to the job list)
    closes only together with a second weak signal, or when the same posting
    was already suspect at an earlier check on another day. Otherwise the
    result is unknown and marked suspect, for a person to look at.

Network manners: one request per host per second, a retry with a growing wait
on 429 and 503, a timeout, and a plain User-Agent with no personal details.

This module never writes to the vault. `plan_updates` turns results into a
list of operations, and the vault store applies them.

Command line:
    liveness.py check URL [URL ...]     check links and print the result

Standard library only, Python 3.9+.
"""

import datetime
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LIVE, CLOSED, UNKNOWN = "live", "closed", "unknown"
SUSPECT = "suspect"   # stored on a note: unknown, with one weak sign of being closed

USER_AGENT = "Mozilla/5.0 (compatible; workdecero posting check)"
TIMEOUT_SECONDS = 20
MIN_SECONDS_PER_HOST = 1.0
MAX_RETRIES = 2
MAX_BODY_BYTES = 3000000

# Wording that suggests a posting is closed. Weak evidence: some sites print
# these phrases on every page.
CLOSED_MARKERS = (
    "no longer accepting applications",
    "not accepting applications",
    "no longer accepting proposals",
    "job is no longer available",
    "position is no longer available",
    "no longer available",
    "position has been filled",
    "role has been filled",
    "this position is closed",
    "this job has been closed",
    "this opportunity is closed",
    "job is closed",
    "job has expired",
    "posting has expired",
    "job posting is no longer",
    "no longer active",
    "job not found",
    "the job you are looking for",
    "page not found",
)

# Sites that only answer a real browser. The sweep checks these another way.
BROWSER_ONLY_HOSTS = ("upwork.com",)


class Response(object):
    def __init__(self, status, body="", final_url=None, error=None):
        self.status = status          # HTTP status, or None when the request failed
        self.body = body or ""
        self.final_url = final_url
        self.error = error


class Result(object):
    def __init__(self, state, evidence, source, weak_signals=()):
        self.state = state
        self.evidence = evidence
        self.source = source
        self.weak_signals = list(weak_signals)

    @property
    def suspect(self):
        return self.state == UNKNOWN and bool(self.weak_signals)

    def as_dict(self):
        return {"state": self.state, "evidence": self.evidence, "source": self.source, "suspect": self.suspect}

    def __repr__(self):
        return "Result(%s, %r)" % (self.state, self.evidence)


class HttpFetcher(object):
    """Polite HTTP GET: per-host spacing, retry on 429 and 503, timeout."""

    def __init__(self, user_agent=USER_AGENT, min_seconds_per_host=MIN_SECONDS_PER_HOST, timeout=TIMEOUT_SECONDS,
                 sleep=time.sleep, clock=time.monotonic, opener=None):
        self.user_agent = user_agent
        self.min_seconds_per_host = min_seconds_per_host
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._open = opener or urllib.request.urlopen
        self._last = {}

    def _wait_for_host(self, host):
        last = self._last.get(host)
        if last is not None:
            remaining = self.min_seconds_per_host - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()

    def get(self, url, accept="application/json, text/html;q=0.9"):
        host = urllib.parse.urlsplit(url).netloc.lower()
        response = None
        for attempt in range(MAX_RETRIES + 1):
            self._wait_for_host(host)
            response, retry_after = self._once(url, accept)
            if response.status not in (429, 503) or attempt == MAX_RETRIES:
                break
            self._sleep(min(retry_after if retry_after is not None else 5 * (2 ** attempt), 60))
        return response

    def post_json(self, url, payload):
        """POST a small JSON body, for job-site systems whose public job list is a POST. Same manners as get."""
        host = urllib.parse.urlsplit(url).netloc.lower()
        response = None
        for attempt in range(MAX_RETRIES + 1):
            self._wait_for_host(host)
            response, retry_after = self._once(url, "application/json", json.dumps(payload).encode("utf-8"))
            if response.status not in (429, 503) or attempt == MAX_RETRIES:
                break
            self._sleep(min(retry_after if retry_after is not None else 5 * (2 ** attempt), 60))
        return response

    def _once(self, url, accept, body=None):
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as reply:
                body = reply.read(MAX_BODY_BYTES).decode("utf-8", "replace")
                return Response(reply.status, body, reply.geturl()), None
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(MAX_BODY_BYTES).decode("utf-8", "replace") if exc.fp else ""
            except Exception:
                body = ""
            retry_after = None
            value = exc.headers.get("Retry-After") if exc.headers else None
            if value and value.strip().isdigit():
                retry_after = int(value.strip())
            exc.close()
            return Response(exc.code, body, url), retry_after
        except Exception as exc:   # DNS failure, timeout, TLS error, bad URL
            return Response(None, "", url, error=type(exc).__name__), None


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

_WORKDAY_RE = re.compile(r"^https://([^/]+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+?)/?(?:\?.*)?$")
_GREENHOUSE_RE = re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)")
_LEVER_RE = re.compile(r"jobs\.(eu\.)?lever\.co/([^/]+)/([0-9a-f-]{36})")
_ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})")
_LINKEDIN_RE = re.compile(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d+)")
_SAM_RE = re.compile(r"sam\.gov/(?:opp|workspace/contract/opp)/([0-9a-f]{32})")
_PHENOM_APPLY_RE = re.compile(r'"applyUrl"\s*:\s*"(https:[^"]+myworkdayjobs\.com[^"]+)"')
_PHENOM_STATUS_RE = re.compile(r'"jobStatus"\s*:\s*"(\w+)"')
_LISTING_PATH_RE = re.compile(r"/(?:jobs|careers|search|job-search|openings|positions)/?$")


def _unreachable(response, source):
    if response.status is None:
        return Result(UNKNOWN, "%s: no answer (%s)" % (source, response.error or "network error"), source)
    return Result(UNKNOWN, "%s: HTTP %s" % (source, response.status), source)


def check_workday(url, fetcher):
    source = "Workday"
    match = _WORKDAY_RE.match(url)
    if not match:
        return Result(UNKNOWN, "Workday link is not a single posting", source)
    host, site, path = match.groups()
    tenant = host.split(".")[0]
    response = fetcher.get("https://%s/wday/cxs/%s/%s/job/%s" % (host, tenant, site, path))
    if response.status == 200:
        try:
            info = json.loads(response.body).get("jobPostingInfo")
        except ValueError:
            info = None
        if not isinstance(info, dict):
            return Result(CLOSED, "Workday: the posting is no longer returned", source)
        if info.get("canApply") is False:
            return Result(CLOSED, "Workday: applications are closed (canApply is false)", source)
        return Result(LIVE, "Workday: open (posted %s)" % info.get("startDate", "date not given"), source)
    if response.status in (404, 410):
        return Result(CLOSED, "Workday: posting not found (HTTP %s)" % response.status, source)
    # A posting that never existed answers 404. A real one that was taken down answers 403 with
    # error code S22 and drops out of the site's search.
    if response.status == 403 and '"S22"' in response.body:
        return Result(CLOSED, "Workday: posting was unpublished (HTTP 403, code S22)", source)
    return _unreachable(response, source)


def check_greenhouse(url, fetcher):
    source = "Greenhouse"
    match = _GREENHOUSE_RE.search(url)
    if not match:
        return None
    board, job_id = match.groups()
    api = "boards-api.eu.greenhouse.io" if "eu.greenhouse" in url else "boards-api.greenhouse.io"
    response = fetcher.get("https://%s/v1/boards/%s/jobs/%s" % (api, board, job_id))
    if response.status == 200:
        return Result(LIVE, "Greenhouse: listed on the job board", source)
    if response.status == 404:
        return Result(CLOSED, "Greenhouse: posting removed (HTTP 404)", source)
    return _unreachable(response, source)


def check_lever(url, fetcher):
    source = "Lever"
    match = _LEVER_RE.search(url)
    if not match:
        return None
    eu, company, job_id = match.groups()
    response = fetcher.get("https://api.%slever.co/v0/postings/%s/%s" % (eu or "", company, job_id))
    if response.status == 200:
        return Result(LIVE, "Lever: listed on the job board", source)
    if response.status == 404:
        return Result(CLOSED, "Lever: posting removed (HTTP 404)", source)
    return _unreachable(response, source)


def check_ashby(url, fetcher):
    source = "Ashby"
    match = _ASHBY_RE.search(url)
    if not match:
        return None
    organization, job_id = match.groups()
    response = fetcher.get("https://api.ashbyhq.com/posting-api/job-board/%s" % organization)
    if response.status == 200:
        try:
            jobs = json.loads(response.body).get("jobs")
        except ValueError:
            jobs = None
        if not isinstance(jobs, list):
            return Result(UNKNOWN, "Ashby: the job board answer could not be read", source)
        if any(isinstance(job, dict) and job.get("id") == job_id for job in jobs):
            return Result(LIVE, "Ashby: listed on the job board", source)
        return Result(CLOSED, "Ashby: no longer listed on the job board", source)
    return _unreachable(response, source)


def check_linkedin(url, fetcher):
    source = "LinkedIn"
    match = _LINKEDIN_RE.search(url)
    if not match:
        return Result(UNKNOWN, "LinkedIn link is a search or a company page, not a posting", source)
    response = fetcher.get("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/%s" % match.group(1),
                           accept="text/html")
    if response.status == 200:
        low = response.body.lower()
        if "no longer accepting applications" in low or "closed-job" in low:
            return Result(CLOSED, "LinkedIn: no longer accepting applications", source)
        return Result(LIVE, "LinkedIn: posting is open", source)
    if response.status in (404, 410):
        return Result(CLOSED, "LinkedIn: posting removed (HTTP %s)" % response.status, source)
    return _unreachable(response, source)


def check_sam(url, fetcher, today=None):
    source = "SAM.gov"
    match = _SAM_RE.search(url)
    if not match:
        return Result(UNKNOWN, "SAM.gov link has no opportunity id", source)
    response = fetcher.get("https://sam.gov/api/prod/opps/v2/opportunities/%s" % match.group(1))
    if response.status == 200:
        try:
            data = json.loads(response.body).get("data2") or {}
            deadline = ((data.get("solicitation") or {}).get("deadlines") or {}).get("response") or ""
        except (ValueError, AttributeError):
            return Result(UNKNOWN, "SAM.gov: the answer could not be read", source)
        day = _parse_date(deadline)
        if day is None:
            return Result(UNKNOWN, "SAM.gov: found, but no response deadline is given", source)
        today = today or datetime.date.today()
        if day < today:
            return Result(CLOSED, "SAM.gov: the response deadline passed on %s" % day.isoformat(), source)
        return Result(LIVE, "SAM.gov: open, responses due %s" % day.isoformat(), source)
    if response.status in (404, 410):
        return Result(CLOSED, "SAM.gov: opportunity removed or archived (HTTP %s)" % response.status, source)
    return _unreachable(response, source)


def check_status_code_only(url, fetcher, source):
    """For sites whose page template always carries closed wording: trust the status code alone."""
    response = fetcher.get(url, accept="text/html")
    if response.status == 410:
        return Result(CLOSED, "%s: posting removed (HTTP 410)" % source, source)
    if response.status == 200:
        return Result(LIVE, "%s: page is up (wording on the page is ignored for this site)" % source, source)
    return _unreachable(response, source)


def check_generic(url, fetcher):
    source = "Web page"
    response = fetcher.get(url, accept="text/html")
    if response.status in (404, 410):
        return Result(CLOSED, "Page is gone (HTTP %s)" % response.status, source)
    if response.status != 200:
        return _unreachable(response, source)

    # Phenom career sites print "has been filled" on every page. Trust what the page embeds instead:
    # the Workday apply link if there is one, otherwise the posting's own status field.
    match = _PHENOM_APPLY_RE.search(response.body)
    if match:
        target = re.sub(r"/apply/?$", "", match.group(1).replace("\\/", "/"))
        inner = check_workday(target, fetcher)
        return Result(inner.state, "Career page points to Workday. " + inner.evidence, "Phenom")
    match = _PHENOM_STATUS_RE.search(response.body)
    if match:
        status = match.group(1)
        if status.upper() == "OPEN":
            return Result(LIVE, "Career page marks the posting OPEN", "Phenom")
        return Result(CLOSED, "Career page marks the posting %s" % status.upper(), "Phenom")

    weak = []
    low = response.body.lower()
    marker = next((text for text in CLOSED_MARKERS if text in low), None)
    if marker:
        weak.append("the page says '%s'" % marker)
    final = response.final_url or url
    if final.rstrip("/") != url.rstrip("/"):
        path = urllib.parse.urlsplit(final).path
        if _LISTING_PATH_RE.search(path) or path in ("", "/"):
            weak.append("the link now redirects to a list page")
    if len(weak) >= 2:
        return Result(CLOSED, "Two signs it is closed: %s, and %s" % (weak[0], weak[1]), source, weak)
    if weak:
        return Result(UNKNOWN, "One weak sign it may be closed (%s). Not enough to close it: check by hand"
                      % weak[0], source, weak)
    return Result(LIVE, "Page is up with no sign of being closed", source)


def _plain(markup, limit):
    """Posting text as plain words: tags and entities removed, spaces collapsed, cut at a word."""
    if not isinstance(markup, str):
        return ""
    text = html.unescape(markup)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6]|/div)\s*/?>", ". ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\s*\.){2,}", ".", text).replace(" .", ".").replace(":.", ":")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " ..."
    return text


def posting_text(url, fetcher, limit=1800):
    """The words of a posting, read from the job site's own public data. Empty when it cannot be read that way.

    Many job sites build their pages in the browser, so loading the page as
    text shows nothing. The same public data the liveness checks use carries
    the description, so a sweep can judge a posting without a browser.
    """
    url = url or ""
    try:
        match = _WORKDAY_RE.match(url)
        if match:
            host, site, path = match.groups()
            response = fetcher.get("https://%s/wday/cxs/%s/%s/job/%s" % (host, host.split(".")[0], site, path))
            info = json.loads(response.body).get("jobPostingInfo") if response.status == 200 else None
            return _plain((info or {}).get("jobDescription"), limit)
        match = _GREENHOUSE_RE.search(url)
        if match:
            board, job_id = match.groups()
            api = "boards-api.eu.greenhouse.io" if "eu.greenhouse" in url else "boards-api.greenhouse.io"
            response = fetcher.get("https://%s/v1/boards/%s/jobs/%s" % (api, board, job_id))
            return _plain(json.loads(response.body).get("content"), limit) if response.status == 200 else ""
        match = _LEVER_RE.search(url)
        if match:
            eu, company, job_id = match.groups()
            response = fetcher.get("https://api.%slever.co/v0/postings/%s/%s" % (eu or "", company, job_id))
            if response.status != 200:
                return ""
            data = json.loads(response.body)
            parts = [data.get("descriptionPlain") or ""]
            for block in data.get("lists") or []:
                if isinstance(block, dict):
                    parts.append("%s: %s" % (block.get("text") or "", block.get("content") or ""))
            return _plain(" ".join(part for part in parts if part), limit)
        match = _ASHBY_RE.search(url)
        if match:
            organization, job_id = match.groups()
            response = fetcher.get("https://api.ashbyhq.com/posting-api/job-board/%s" % organization)
            jobs = json.loads(response.body).get("jobs") if response.status == 200 else None
            for job in jobs or []:
                if isinstance(job, dict) and job.get("id") == job_id:
                    return _plain(job.get("descriptionPlain") or job.get("descriptionHtml"), limit)
    except (ValueError, AttributeError, TypeError):
        return ""
    return ""


_LD_JSON = re.compile(r"<script[^>]*type=[\"']application/ld(?:\+|&#x2B;|&#43;)json[\"'][^>]*>(.*?)</script>",
                      re.IGNORECASE | re.DOTALL)


def _job_postings(node):
    """Every schema.org JobPosting inside a piece of structured data."""
    if isinstance(node, list):
        return [found for item in node for found in _job_postings(item)]
    if not isinstance(node, dict):
        return []
    kinds = node.get("@type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    found = [node] if "JobPosting" in kinds else []
    return found + _job_postings(node.get("@graph"))


def _money(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def page_posting(url, fetcher, limit=1800):
    """What a posting page says about the job, from the structured data job sites publish for search engines.

    Returns a dict with any of: title, company, location, work_mode, posted,
    closes, pay_min, pay_max, text. An empty dict when the page did not load
    or carries no structured job data. Nothing is guessed.
    """
    response = fetcher.get(url, accept="text/html")
    if response.status != 200 or not response.body:
        return {}
    for block in _LD_JSON.findall(response.body):
        try:
            data = json.loads(html.unescape(block) if "&quot;" in block else block)
        except ValueError:
            continue
        for job in _job_postings(data):
            found = {}
            if isinstance(job.get("title"), str):
                found["title"] = html.unescape(job["title"]).strip()
            organization = job.get("hiringOrganization")
            if isinstance(organization, dict) and isinstance(organization.get("name"), str):
                found["company"] = html.unescape(organization["name"]).strip()
            places = job.get("jobLocation")
            places = places if isinstance(places, list) else [places]
            names = []
            for place in places:
                address = place.get("address") if isinstance(place, dict) else None
                if isinstance(address, dict):
                    name = ", ".join(str(address[key]).strip() for key in ("addressLocality", "addressRegion")
                                     if address.get(key))
                    if name and name not in names:
                        names.append(name)
            if names:
                found["location"] = "; ".join(names[:4])
            if "TELECOMMUTE" in str(job.get("jobLocationType") or "").upper():
                found["work_mode"] = "remote"
            for key, source in (("posted", "datePosted"), ("closes", "validThrough")):
                match = re.match(r"\d{4}-\d{2}-\d{2}", str(job.get(source) or ""))
                if match:
                    found[key] = match.group(0)
            salary = job.get("baseSalary")
            value = salary.get("value") if isinstance(salary, dict) else None
            if isinstance(value, dict) and str(value.get("unitText") or "").upper() in ("YEAR", "ANNUAL", "YEARLY"):
                low, high = _money(value.get("minValue")), _money(value.get("maxValue"))
                if low and low >= 10000:
                    found["pay_min"] = low
                if high and high >= 10000:
                    found["pay_max"] = high
            text = _plain(job.get("description"), limit)
            if text:
                found["text"] = text
            if found.get("title"):
                return found
    return {}


def _parse_date(text):
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text or "")
    if not match:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def check(url, fetcher=None, today=None):
    """Check one posting link. Returns a Result."""
    url = (url or "").strip().split()[0] if (url or "").strip() else ""
    if not url.lower().startswith(("http://", "https://")):
        return Result(UNKNOWN, "No posting link", "None")
    fetcher = fetcher or HttpFetcher()
    host = urllib.parse.urlsplit(url).netloc.lower()

    def on(domain):
        return host == domain or host.endswith("." + domain)

    if on("myworkdayjobs.com"):
        return check_workday(url, fetcher)
    if on("greenhouse.io"):
        return check_greenhouse(url, fetcher) or check_generic(url, fetcher)
    if on("lever.co"):
        return check_lever(url, fetcher) or check_generic(url, fetcher)
    if on("ashbyhq.com"):
        return check_ashby(url, fetcher) or check_generic(url, fetcher)
    if on("linkedin.com"):
        return check_linkedin(url, fetcher)
    if on("sam.gov"):
        return check_sam(url, fetcher, today)
    if on("teksystems.com") or on("allegisgroup.com"):
        return check_status_code_only(url, fetcher, "TEKsystems")
    if any(on(domain) for domain in BROWSER_ONLY_HOSTS):
        return Result(UNKNOWN, "This site only answers a real browser. Check it in the browser", "Browser only")
    return check_generic(url, fetcher)


# --------------------------------------------------------------------------
# Turning results into tracker updates
# --------------------------------------------------------------------------

LINK_FIELD = {"job": "apply_url", "contract": "link"}


def rows_to_check(rows, note_type):
    """Rows whose status is not a finished one. A status typed with a typo still gets checked."""
    return [row for row in rows if not note_type.is_done(row.values.get("status"))]


def plan_update(row, result, note_type, today):
    """One operation for one checked row. The vault store applies it; nothing is written here.

    An operation is a dict: type, id, values, force, append.
    """
    day = today.isoformat()
    values = {"last_checked": day}
    force, line = [], None
    state = result.state

    # A weak sign seen at two checks on different days is enough to close.
    if result.suspect:
        earlier = row.values.get("last_checked")
        if row.values.get("liveness") == SUSPECT and earlier and earlier != day:
            state = CLOSED
            result = Result(CLOSED, "Weak sign of being closed at two checks (%s and %s): %s"
                            % (earlier, day, result.weak_signals[0]), result.source, result.weak_signals)

    if state == LIVE:
        values.update({"liveness": LIVE, "last_seen": day})
    elif state == UNKNOWN:
        values["liveness"] = SUSPECT if result.suspect else UNKNOWN
        if result.suspect and row.values.get("liveness") != SUSPECT:
            line = "%s: posting may be closed. %s." % (day, result.evidence.rstrip("."))
    else:
        values["liveness"] = CLOSED
        if row.values.get("liveness") != CLOSED:
            status = row.values.get("status")
            recognised = note_type.canonical_status(status)
            if note_type.is_engaged(status) or (status and recognised is None):
                # The user is already in the process, or typed a status we do not recognise.
                # Either way the status is theirs: keep it and tell them.
                line = "POSTING CLOSED %s: %s. Your status was left as it is." % (day, result.evidence.rstrip("."))
            else:
                values.update({"status": "Closed", "reason": "Posting closed", "updated": day})
                force = ["status", "reason"]
                line = "Closed %s: %s." % (day, result.evidence.rstrip("."))
    return {"type": note_type.name, "id": row.id, "values": values, "force": force, "append": line,
            "state": state, "evidence": result.evidence}


def plan_updates(rows, note_type, fetcher=None, today=None, on_result=None):
    """Check every open row and return the list of operations."""
    today = today or datetime.date.today()
    fetcher = fetcher or HttpFetcher()
    operations = []
    for row in rows_to_check(rows, note_type):
        result = check(row.values.get(LINK_FIELD[note_type.name]), fetcher, today)
        operation = plan_update(row, result, note_type, today)
        operations.append(operation)
        if on_result:
            on_result(row, operation)
    return operations


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] != "check":
        print(__doc__, file=sys.stderr)
        return 2
    fetcher = HttpFetcher()
    for url in argv[1:]:
        result = check(url, fetcher)
        print("%-8s %s\n         %s" % (result.state.upper(), url, result.evidence))
    return 0


if __name__ == "__main__":
    sys.exit(main())
