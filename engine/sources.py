"""The local source map: where the sweeps look, for one person in one place.

A sweep is only as good as its list of places to look. This module keeps
that list, `sources.yaml`, next to the profile. Research (web search and
reading pages) proposes entries. This module checks each one before it is
kept, removes duplicates, and writes the file.

What is in the map, by sweep:
    jobs        employers in the person's field and area with the job-site
                system each one uses, state and city job boards, boards for
                the field
    contracts   marketplaces, talent networks, agencies, expert networks and
                public-bid portals that fit the field and the state
    networking  chambers, meetups, user groups, associations and conferences,
                each with the date it was last seen active

Every entry has: name, url, kind, how to query it (api, rss, page or
browser), the date it was verified, and notes.

Nothing is kept on trust:
  * An employer's job-site system is checked with a live call to that
    system's public job list (Workday, Greenhouse, Lever, Ashby,
    SmartRecruiters, Workable). A career site on another system is checked
    by loading the page.
  * Any other entry is checked by loading its page. Gone means dropped.
    Blocked means kept and marked for the browser.
  * A meetup or user group needs a last-active date within six months.

The person's own edits win. An entry they added is never dropped by a
refresh, and a name they removed is never added back.

Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import re
import urllib.parse

import liveness
import merge
import miniyaml
import wdprofile

FILE_NAME = "sources.yaml"
TRACKS = ("jobs", "contracts", "networking")
KINDS = {
    "jobs": ("employer", "job-board", "field-board"),
    "contracts": ("marketplace", "talent-network", "agency", "expert-network", "bid-portal"),
    "networking": ("chamber", "meetup", "user-group", "association", "conference"),
}
HOWS = ("api", "rss", "page", "browser")
NEEDS_ACTIVITY = ("meetup", "user-group")
ACTIVE_WITHIN_DAYS = 183
STALE_AFTER_WEEKS = 4

# How many web searches one run may use. A sweep that refreshes the map shares this budget.
SEARCH_BUDGET = {"lite": {"build": 12, "refresh": 4}, "full": {"build": 40, "refresh": 12}}

_WORKDAY_URL = re.compile(r"^https://(([a-z0-9-]+)\.(wd[0-9]+)\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)")
_GREENHOUSE_URL = re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")
_LEVER_URL = re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)")
_ASHBY_URL = re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")
_SMART_URL = re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")
_WORKABLE_URL = re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")
_PAGE_SYSTEMS = (("icims.com", "icims"), ("taleo.net", "taleo"), ("successfactors.com", "successfactors"),
                 ("oraclecloud.com", "oracle"), ("jobvite.com", "jobvite"), ("paylocity.com", "paylocity"),
                 ("ultipro.com", "ukg"), ("bamboohr.com", "bamboohr"), ("eightfold.ai", "eightfold"),
                 ("adp.com", "adp"), ("dayforcehcm.com", "dayforce"))


class SourceError(ValueError):
    """An entry that cannot be used, with a reason a person can read."""


def path():
    return os.path.join(wdprofile.config_home(), FILE_NAME)


def empty_map():
    return {"schema_version": 1, "jobs": [], "contracts": [], "networking": [], "removed": []}


def load(file_path=None):
    """The saved map, or an empty one. Raises SourceError when the file cannot be read."""
    file_path = file_path or path()
    if not os.path.isfile(file_path):
        return empty_map()
    with open(file_path, "r", encoding="utf-8") as handle:
        try:
            data = miniyaml.loads(handle.read())
        except miniyaml.YamlError as exc:
            raise SourceError("%s cannot be read (line %s: %s). Fix that line or delete the file to rebuild it."
                              % (FILE_NAME, exc.line, exc.problem))
    result = empty_map()
    if isinstance(data, dict):
        result.update(data)
    for track in TRACKS + ("removed",):
        if not isinstance(result.get(track), list):
            result[track] = []
    return result


def save(source_map, file_path=None):
    file_path = file_path or path()
    ordered = {}
    for key in ("schema_version", "area", "field", "mode", "built_on", "refreshed_on", "searches_used", "skipped"):
        if source_map.get(key) not in (None, "", []):
            ordered[key] = source_map[key]
    for track in TRACKS:
        ordered[track] = sorted(source_map.get(track) or [], key=lambda e: (e.get("kind", ""), e.get("name", "").lower()))
    ordered["removed"] = sorted(set(source_map.get("removed") or []), key=str.lower)
    header = ("# Where your sweeps look. Built by /workdecero:sources and refreshed every few weeks.\n"
              "# You can edit it: add an entry with added_by: user and it is never dropped,\n"
              "# or list a name under removed and it is never added back.\n\n")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    temp = file_path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(header + miniyaml.dumps(ordered))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, file_path)
    return file_path


# --------------------------------------------------------------------------
# Job-site systems
# --------------------------------------------------------------------------

def detect_ats(url):
    """Work out the job-site system from a careers link. Returns a dict, or None for an ordinary page."""
    url = (url or "").strip()
    match = _WORKDAY_URL.match(url)
    if match:
        host, tenant, _, site = match.groups()
        if site.lower() not in ("wday", "job"):
            return {"system": "workday", "host": host, "tenant": tenant, "site": site}
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host.endswith("greenhouse.io"):
        match = _GREENHOUSE_URL.search(url)
        if match and match.group(1) not in ("embed", "v1"):
            return {"system": "greenhouse", "board": match.group(1), "eu": "eu.greenhouse" in url}
    match = _LEVER_URL.search(url)
    if match:
        return {"system": "lever", "board": match.group(2), "eu": bool(match.group(1))}
    match = _ASHBY_URL.search(url)
    if match:
        return {"system": "ashby", "board": urllib.parse.unquote(match.group(1))}
    match = _SMART_URL.search(url)
    if match:
        return {"system": "smartrecruiters", "board": match.group(1)}
    match = _WORKABLE_URL.search(url)
    if match:
        return {"system": "workable", "board": match.group(1)}
    for domain, system in _PAGE_SYSTEMS:
        if host == domain or host.endswith("." + domain):
            return {"system": system}
    return None


def api_url(ats):
    """The public job-list address for a system this module can call, or None."""
    system = ats.get("system")
    if system == "workday" and ats.get("host") and ats.get("tenant") and ats.get("site"):
        return "https://%s/wday/cxs/%s/%s/jobs" % (ats["host"], ats["tenant"], ats["site"])
    board = ats.get("board")
    if not board:
        return None
    if system == "greenhouse":
        return "https://boards-api.%sgreenhouse.io/v1/boards/%s/jobs" % ("eu." if ats.get("eu") else "", board)
    if system == "lever":
        return "https://api.%slever.co/v0/postings/%s?mode=json&limit=1" % ("eu." if ats.get("eu") else "", board)
    if system == "ashby":
        return "https://api.ashbyhq.com/posting-api/job-board/%s" % urllib.parse.quote(board)
    if system == "smartrecruiters":
        return "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=1" % board
    if system == "workable":
        return "https://apply.workable.com/api/v1/widget/accounts/%s" % board
    return None


def verify_ats(ats, fetcher):
    """Live check of an employer's job list. Returns (state, note): 'ok', 'gone' or 'unknown'."""
    address = api_url(ats)
    if address is None:
        return "unknown", "no public job list for this system"
    if ats["system"] == "workday":
        response = fetcher.post_json(address, {"limit": 1, "offset": 0, "searchText": ""})
    else:
        response = fetcher.get(address)
    if response.status == 200:
        try:
            data = json.loads(response.body)
        except ValueError:
            return "unknown", "the job list answered, but not in the expected form"
        count = _count(ats["system"], data)
        if count is None:
            return "unknown", "the job list answered, but not in the expected form"
        return "ok", "%s job list answered with %s open posting%s" % (
            ats["system"].capitalize(), count, "" if count == 1 else "s")
    if response.status in (404, 410) or (ats["system"] == "workday" and response.status == 422):
        return "gone", "%s has no job list at that address (HTTP %s)" % (ats["system"].capitalize(), response.status)
    if response.status is None:
        return "unknown", "no answer (%s)" % (response.error or "network error")
    return "unknown", "HTTP %s" % response.status


def _count(system, data):
    if system == "workday":
        return data.get("total") if isinstance(data, dict) and isinstance(data.get("jobPostings"), list) else None
    if system == "greenhouse":
        return len(data["jobs"]) if isinstance(data, dict) and isinstance(data.get("jobs"), list) else None
    if system == "lever":
        return "at least %d" % len(data) if isinstance(data, list) and data else (0 if isinstance(data, list) else None)
    if system == "ashby":
        return len(data["jobs"]) if isinstance(data, dict) and isinstance(data.get("jobs"), list) else None
    if system == "smartrecruiters":
        return data.get("totalFound") if isinstance(data, dict) and "totalFound" in data else None
    if system == "workable":
        return len(data["jobs"]) if isinstance(data, dict) and isinstance(data.get("jobs"), list) else None
    return None


_SYSTEM_LINKS = re.compile(
    r"https:(?:\\?/){2}(?:[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com|(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io|"
    r"jobs\.(?:eu\.)?lever\.co|jobs\.ashbyhq\.com|(?:jobs|careers)\.smartrecruiters\.com|apply\.workable\.com)"
    r"(?:\\?/[A-Za-z0-9_.%-]+)+", re.IGNORECASE)


def find_system_link(html):
    """Links on a careers page that point at a job-site system this module can call, most frequent first."""
    counts = {}
    for match in _SYSTEM_LINKS.finditer(html or ""):
        link = match.group(0).replace("\\/", "/")
        ats = detect_ats(link)
        if ats and api_url(ats):
            key = json.dumps(ats, sort_keys=True)
            counts.setdefault(key, [0, link, ats])[0] += 1
    return [(link, ats) for _, link, ats in sorted(counts.values(), key=lambda item: -item[0])]


_HREF = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
_CAREER_WORDS = re.compile(r"career|jobs?\b|join[ -]?(?:us|our)|work[ -]?(?:with|for|at)|openings|opportunit|employment|hiring",
                           re.IGNORECASE)
MAX_PAGES_PER_EMPLOYER = 3


def career_links(html, base_url):
    """Links on a page that look like they lead to careers or job listings, best guesses first."""
    found = []
    for href, label in _HREF.findall(html or ""):
        text = re.sub(r"<[^>]+>", " ", label)
        if not (_CAREER_WORDS.search(href) or _CAREER_WORDS.search(text)):
            continue
        link = urllib.parse.urljoin(base_url, href.strip())
        if not link.lower().startswith(("http://", "https://")) or link.rstrip("/") == base_url.rstrip("/"):
            continue
        if detect_ats(link):
            continue          # a link to a job-site system is checked as one, not loaded as a page
        score = (2 if re.search(r"career|jobs", href, re.IGNORECASE) else 0) + \
                (1 if re.search(r"search|openings|all jobs|view jobs|current", text + href, re.IGNORECASE) else 0)
        if link not in [item[1] for item in found]:
            found.append((-score, link))
    return [link for _, link in sorted(found, key=lambda item: item[0])]


def discover_job_site(url, html, fetcher, final_url=None):
    """Follow an employer's own pages to the system that holds its postings.

    Starts from a page already loaded (the homepage or the careers page), reads
    any job-site link off it, and otherwise follows up to two careers-looking
    links. Returns (link, ats, note) for the first job list that answers, or None.
    """
    seen = set()
    queue = [(url, html, final_url)]
    pages = 1
    while queue:
        page_url, page_html, landed = queue.pop(0)
        candidates = []
        direct = detect_ats(landed or "")
        if direct and api_url(direct):
            candidates.append((landed, direct))          # the careers link redirected straight to the job site
        candidates += find_system_link(page_html)
        for link, ats in candidates[:3]:
            key = json.dumps(ats, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            state, note = verify_ats(ats, fetcher)
            if state == "ok":
                return link, ats, note
        if page_url != url:
            continue                                      # only follow links from the first page
        for link in career_links(page_html, landed or page_url):
            if pages >= MAX_PAGES_PER_EMPLOYER:
                break
            pages += 1
            response = fetcher.get(link, accept="text/html")
            if response.status == 200:
                queue.append((link, response.body, response.final_url))
    return None


def verify_page(url, fetcher, want_body=False):
    """Check that a page is there. Returns (state, note): 'ok', 'gone' or 'blocked'.

    With `want_body` the page text is returned as a third value, for reading links off it.
    """
    response = fetcher.get(url, accept="text/html")
    if want_body:
        state, note = verify_page_result(response)
        return state, note, (response.body if response.status == 200 else ""), response.final_url
    return verify_page_result(response)


def verify_page_result(response):
    if response.status == 200:
        return "ok", "page loads"
    if response.status in (404, 410):
        return "gone", "page is gone (HTTP %s)" % response.status
    if response.status is None:
        if response.error in ("URLError", "gaierror", "InvalidURL"):
            return "gone", "the address does not answer"
        return "blocked", "no answer (%s)" % response.error
    return "blocked", "the site refused a script (HTTP %s)" % response.status


# --------------------------------------------------------------------------
# Checking and merging entries
# --------------------------------------------------------------------------

def _date(value):
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value or "") if isinstance(value, str) else None
    if not match:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def same_entry(a, b):
    if a.get("kind") == "employer" and b.get("kind") == "employer":
        if merge.normalize_company(a.get("name")) == merge.normalize_company(b.get("name")):
            return True
    elif merge.normalize_company(a.get("name")) == merge.normalize_company(b.get("name")) and a.get("name"):
        return True
    key_a, key_b = merge.link_key(a.get("url")), merge.link_key(b.get("url"))
    return bool(key_a) and key_a == key_b


def check_entry(track, entry, fetcher, today, require_activity=True):
    """Validate and verify one proposed entry.

    Returns the entry ready to save. Raises SourceError with the reason when
    it must be dropped. `require_activity=False` skips the six-month rule, for
    re-checking an entry whose stored date is simply old.
    """
    if track not in TRACKS:
        raise SourceError("unknown sweep %r" % track)
    if not isinstance(entry, dict):
        raise SourceError("an entry must be a set of name, url and kind")
    name = (entry.get("name") or "").strip()
    url = (entry.get("url") or "").strip()
    kind = (entry.get("kind") or "").strip().lower()
    if not name:
        raise SourceError("no name")
    if not url.lower().startswith(("http://", "https://")):
        raise SourceError("no web address")
    if kind not in KINDS[track]:
        raise SourceError("kind must be one of %s for %s" % (", ".join(KINDS[track]), track))

    clean = {"name": name, "url": url, "kind": kind}
    notes = [(entry.get("notes") or "").strip()] if (entry.get("notes") or "").strip() else []

    if require_activity and (kind in NEEDS_ACTIVITY or entry.get("last_active")):
        active = _date(entry.get("last_active"))
        if active is None:
            if kind in NEEDS_ACTIVITY:
                raise SourceError("no date of last activity was found, so it may be dormant")
        else:
            if (today - active).days > ACTIVE_WITHIN_DAYS:
                raise SourceError("no activity since %s" % active.isoformat())
            clean["last_active"] = active.isoformat()

    ats = None
    if kind == "employer":
        ats = dict(entry["ats"]) if isinstance(entry.get("ats"), dict) else detect_ats(url)
    if ats and api_url(ats):
        state, note = verify_ats(ats, fetcher)
        if state == "gone":
            raise SourceError(note)
        clean["ats"] = dict((key, value) for key, value in ats.items() if value not in (None, "", False))
        if state == "ok":
            clean["how"] = "api"
            notes.append(note)
        else:
            clean["how"] = "browser"
            notes.append("Job list could not be checked by script: %s" % note)
    else:
        state, note, html, landed = verify_page(url, fetcher, want_body=True)
        if state == "gone":
            raise SourceError(note)
        if kind == "employer" and state == "ok":
            # An employer's own pages usually lead to the system that holds its postings. If one does,
            # and that system's job list answers, the sweeps can read the whole list directly.
            discovered = discover_job_site(url, html, fetcher, landed)
            if discovered:
                link, found, found_note = discovered
                clean["careers_page"] = url
                clean["url"] = link
                clean["ats"] = dict((k, v) for k, v in found.items() if v not in (None, "", False))
                clean["how"] = "api"
                notes.append("Job site found from the employer's own pages. %s" % found_note)
                clean["verified_on"] = today.isoformat()
                if entry.get("added_by") == "user":
                    clean["added_by"] = "user"
                clean["notes"] = ". ".join(n.rstrip(".") for n in notes) + "."
                return clean
        if ats:
            clean["ats"] = ats
        wanted = (entry.get("how") or "").strip().lower()
        if state == "blocked":
            clean["how"] = "browser"
            notes.append("Could not be checked by script: %s" % note)
        else:
            clean["how"] = wanted if wanted in HOWS else "page"
    clean["verified_on"] = today.isoformat()
    if entry.get("added_by") == "user":
        clean["added_by"] = "user"
    if notes:
        clean["notes"] = ". ".join(note.rstrip(".") for note in notes) + "."
    return clean


class AddResult(object):
    def __init__(self):
        self.added = []      # (track, name)
        self.updated = []    # (track, name)
        self.dropped = []    # (track, name, reason)


def add_entries(source_map, proposals, fetcher=None, today=None):
    """Check proposed entries and merge the good ones into the map. `proposals` is {track: [entries]}."""
    fetcher = fetcher or liveness.HttpFetcher()
    today = today or datetime.date.today()
    result = AddResult()
    removed = [merge.normalize_company(name) for name in source_map.get("removed") or []]
    for track in TRACKS:
        for entry in proposals.get(track) or []:
            label = (entry.get("name") if isinstance(entry, dict) else None) or "(unnamed)"
            if merge.normalize_company(label) in removed:
                result.dropped.append((track, label, "you removed it from your map"))
                continue
            try:
                clean = check_entry(track, entry, fetcher, today)
            except SourceError as exc:
                result.dropped.append((track, label, str(exc)))
                continue
            existing = [item for item in source_map[track] if same_entry(item, clean)]
            if existing:
                kept = existing[0]
                if kept.get("added_by") == "user":
                    clean["added_by"] = "user"
                index = source_map[track].index(kept)
                source_map[track][index] = clean
                result.updated.append((track, clean["name"]))
            else:
                source_map[track].append(clean)
                result.added.append((track, clean["name"]))
    return result


def stale_entries(source_map, today=None, weeks=STALE_AFTER_WEEKS):
    """Entries whose last check is older than `weeks`. Returns [(track, entry)]."""
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(weeks=weeks)
    stale = []
    for track in TRACKS:
        for entry in source_map.get(track) or []:
            checked = _date(entry.get("verified_on"))
            if checked is None or checked < cutoff:
                stale.append((track, entry))
    return stale


def reverify(source_map, fetcher=None, today=None, weeks=STALE_AFTER_WEEKS):
    """Re-check stale entries. Dead ones are dropped unless the person added them. Returns an AddResult."""
    fetcher = fetcher or liveness.HttpFetcher()
    today = today or datetime.date.today()
    result = AddResult()
    for track, entry in stale_entries(source_map, today, weeks):
        try:
            # The stored activity date is old by now. Whether a group is still alive is for research
            # to answer (see needs_activity_check), so an old date alone never drops it here.
            clean = check_entry(track, entry, fetcher, today, require_activity=False)
        except SourceError as exc:
            if entry.get("added_by") == "user":
                entry["notes"] = "Check this one: %s." % str(exc).rstrip(".")
                result.updated.append((track, entry["name"]))
            else:
                source_map[track].remove(entry)
                result.dropped.append((track, entry["name"], str(exc)))
            continue
        if entry.get("last_active"):
            clean["last_active"] = entry["last_active"]
        source_map[track][source_map[track].index(entry)] = clean
        result.updated.append((track, clean["name"]))
    return result


def needs_activity_check(source_map, today=None):
    """Meetups and user groups whose last-active date is now older than six months. Research should look again."""
    today = today or datetime.date.today()
    old = []
    for entry in source_map.get("networking") or []:
        if entry.get("kind") in NEEDS_ACTIVITY:
            active = _date(entry.get("last_active"))
            if active is None or (today - active).days > ACTIVE_WITHIN_DAYS:
                old.append(entry)
    return old


def remove(source_map, name):
    """Take a source out of the map and remember the name so research does not add it back."""
    wanted = merge.normalize_company(name)
    found = False
    for track in TRACKS:
        for entry in list(source_map[track]):
            if merge.normalize_company(entry.get("name")) == wanted:
                source_map[track].remove(entry)
                found = True
    if name.strip() and wanted not in [merge.normalize_company(item) for item in source_map["removed"]]:
        source_map["removed"].append(name.strip())
    return found


def learn_employer(source_map, name, posting_url, fetcher=None, today=None):
    """Add an employer to the map from a posting link a sweep came across.

    A posting on an employer's own job site shows which system they use. That
    is the cheapest way the map grows: no search is spent. Returns True when
    the employer was added or refreshed.
    """
    ats = detect_ats(posting_url)
    if not name or not ats or not api_url(ats):
        return False
    result = add_entries(source_map, {"jobs": [{"name": name, "kind": "employer", "ats": ats,
                                                "url": _board_url(ats, posting_url),
                                                "notes": "Learned from a posting a sweep found"}]},
                         fetcher=fetcher, today=today)
    return bool(result.added or result.updated)


def _board_url(ats, posting_url):
    if ats["system"] == "workday":
        return "https://%s/%s" % (ats["host"], ats["site"])
    hosts = {"greenhouse": "https://job-boards.%sgreenhouse.io/%%s" % ("eu." if ats.get("eu") else ""),
             "lever": "https://jobs.%slever.co/%%s" % ("eu." if ats.get("eu") else ""),
             "ashby": "https://jobs.ashbyhq.com/%s", "smartrecruiters": "https://jobs.smartrecruiters.com/%s",
             "workable": "https://apply.workable.com/%s"}
    return hosts[ats["system"]] % ats["board"] if ats["system"] in hosts else posting_url


def budget(profile, refresh=False):
    mode = profile.get("mode") or "lite"
    return SEARCH_BUDGET.get(mode, SEARCH_BUDGET["lite"])["refresh" if refresh else "build"]
