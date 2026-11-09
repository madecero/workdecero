"""Harvest: read open postings straight from employers' job sites.

When the source map knows the system behind an employer's job site, a sweep
does not need a web search to see what that employer has open. This module
asks the system's public job list and keeps the postings whose titles match
what the person is looking for.

It returns "finds" in the shape the merge and scoring steps take:
    {"title", "company", "url", "req_id", "location", "work_mode", "posted",
     "source": "Company site", "link_verified": True}

A find from here is real by construction: it came from the employer's own
list a moment ago. What it lacks is a judgement of fit, which the research
step adds after reading the posting.

Standard library only, Python 3.9+.
"""

import datetime
import json
import re
import urllib.parse

import merge
import scoring
import sources

_STOP_WORDS = ("of", "the", "and", "for", "in", "at", "to", "a", "an", "or", "with")
WORKDAY_PAGE = 20


def _tokens(text):
    return [word for word in merge.normalize_title(text).split() if word not in _STOP_WORDS]


def title_matches(title, profile):
    """True when a posting title is the kind of role the profile asks for.

    A title matches when it contains every significant word of one of the
    person's target titles ("VP of Product" matches "VP of Product, Platform"),
    or when it is at one of their levels and names one of their functions.
    Titles with a word the profile leaves out never match.
    """
    jobs = profile.get("jobs") or {}
    plain = " %s " % merge.normalize_title(title)
    for keyword in jobs.get("exclude_title_keywords") or []:
        if " %s " % merge.normalize_title(keyword) in plain:
            return False
    have = set(_tokens(title))
    for wanted in (jobs.get("titles") or []) + (jobs.get("search_keywords") or []):
        need = set(_tokens(wanted))
        if need and need <= have:
            return True
    levels = jobs.get("levels") or []
    if levels and scoring.level_of({"title": title}) in levels:
        for function in jobs.get("functions") or []:
            need = set(_tokens(function))
            if need and need <= have:
                return True
    return False


def search_terms(profile, limit):
    """Short search phrases for job sites that search by text, most important first."""
    jobs = profile.get("jobs") or {}
    terms = []
    for text in (jobs.get("titles") or []) + (jobs.get("functions") or []):
        words = _tokens(text)
        # The distinctive part of a title is its function ("product"), not its level ("vp").
        core = [word for word in words if word not in ("vice", "president", "senior", "director", "head", "chief",
                                                       "manager", "lead", "principal", "officer", "management")]
        phrase = " ".join(core or words)
        if phrase and phrase not in terms:
            terms.append(phrase)
    return terms[:limit]


def _posted_from_words(text, today):
    """Workday says 'Posted 3 Days Ago'. Turn that into a date, or None when it is vague."""
    low = (text or "").lower()
    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - datetime.timedelta(days=1)).isoformat()
    match = re.search(r"(\d+)(\+?)\s+day", low)
    if match and not match.group(2):
        return (today - datetime.timedelta(days=int(match.group(1)))).isoformat()
    return None


def _iso_day(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.datetime.utcfromtimestamp(value / 1000.0).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value or "") if isinstance(value, str) else None
    return match.group(1) if match else None


def _mode(location_text):
    low = (location_text or "").lower()
    if "remote" in low:
        return "remote"
    if "hybrid" in low:
        return "hybrid"
    return None


def _find(company, title, url, location, posted, req_id=None):
    find = {"title": title.strip(), "company": company, "url": url, "source": "Company site", "link_verified": True}
    if location:
        find["location"] = location.strip()
        mode = _mode(location)
        if mode:
            find["work_mode"] = mode
    if posted:
        find["posted"] = posted
    if req_id:
        find["req_id"] = str(req_id)
    return find


def _json(response):
    if response.status != 200:
        return None
    try:
        return json.loads(response.body)
    except ValueError:
        return None


def _workday(entry, profile, fetcher, today, terms):
    ats = entry["ats"]
    base = "https://%s/%s" % (ats["host"], ats["site"])
    found, seen = [], set()
    for term in terms:
        data = _json(fetcher.post_json(sources.api_url(ats), {"limit": WORKDAY_PAGE, "offset": 0, "searchText": term}))
        for posting in (data or {}).get("jobPostings") or []:
            path = posting.get("externalPath") or ""
            title = posting.get("title") or ""
            if not path or path in seen or not title_matches(title, profile):
                continue
            seen.add(path)
            fields = posting.get("bulletFields") or []
            found.append(_find(entry["name"], title, base + path, posting.get("locationsText"),
                               _posted_from_words(posting.get("postedOn"), today), fields[0] if fields else None))
    return found


def _greenhouse(entry, profile, fetcher, today, terms):
    data = _json(fetcher.get(sources.api_url(entry["ats"])))
    return [_find(entry["name"], job.get("title") or "", job.get("absolute_url"), (job.get("location") or {}).get("name"),
                  _iso_day(job.get("first_published") or job.get("updated_at")), job.get("id"))
            for job in (data or {}).get("jobs") or []
            if job.get("absolute_url") and title_matches(job.get("title") or "", profile)]


def _lever(entry, profile, fetcher, today, terms):
    ats = entry["ats"]
    data = _json(fetcher.get("https://api.%slever.co/v0/postings/%s?mode=json" % ("eu." if ats.get("eu") else "",
                                                                                  ats["board"])))
    return [_find(entry["name"], job.get("text") or "", job.get("hostedUrl"),
                  (job.get("categories") or {}).get("location"), _iso_day(job.get("createdAt")), job.get("id"))
            for job in (data if isinstance(data, list) else [])
            if job.get("hostedUrl") and title_matches(job.get("text") or "", profile)]


def _ashby(entry, profile, fetcher, today, terms):
    data = _json(fetcher.get(sources.api_url(entry["ats"])))
    found = []
    for job in (data or {}).get("jobs") or []:
        if job.get("isListed") is False or not job.get("jobUrl") or not title_matches(job.get("title") or "", profile):
            continue
        location = job.get("location") or ""
        if job.get("isRemote") and "remote" not in location.lower():
            location = ("%s (Remote)" % location).strip()
        found.append(_find(entry["name"], job["title"], job["jobUrl"], location, _iso_day(job.get("publishedAt")),
                           job.get("id")))
    return found


def _smartrecruiters(entry, profile, fetcher, today, terms):
    board = entry["ats"]["board"]
    found, seen = [], set()
    for term in terms:
        data = _json(fetcher.get("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=50&q=%s"
                                 % (board, urllib.parse.quote(term))))
        for job in (data or {}).get("content") or []:
            if not job.get("id") or job["id"] in seen or not title_matches(job.get("name") or "", profile):
                continue
            seen.add(job["id"])
            place = job.get("location") or {}
            location = ", ".join(part for part in (place.get("city"), place.get("region")) if part)
            if place.get("remote"):
                location = ("%s (Remote)" % location).strip()
            found.append(_find(entry["name"], job["name"], "https://jobs.smartrecruiters.com/%s/%s" % (board, job["id"]),
                               location, _iso_day(job.get("releasedDate")), job.get("refNumber")))
    return found


def _workable(entry, profile, fetcher, today, terms):
    data = _json(fetcher.get(sources.api_url(entry["ats"])))
    found = []
    for job in (data or {}).get("jobs") or []:
        url = job.get("url") or job.get("shortlink")
        if not url or not title_matches(job.get("title") or "", profile):
            continue
        location = ", ".join(part for part in (job.get("city"), job.get("state")) if part)
        if job.get("telecommuting"):
            location = ("%s (Remote)" % location).strip()
        found.append(_find(entry["name"], job["title"], url, location, _iso_day(job.get("published_on")),
                           job.get("shortcode")))
    return found


_READERS = {"workday": _workday, "greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
            "smartrecruiters": _smartrecruiters, "workable": _workable}


class Harvest(object):
    def __init__(self):
        self.finds = []
        self.read = []        # employer names whose job list was read
        self.failed = []      # employer names whose job list did not answer
        self.unreadable = []  # employer names with no public job list, for research to read as pages


def harvest(source_map, profile, fetcher, today=None, max_employers=None, max_terms=3):
    """Read every employer job list the map can call and keep the postings that match the profile."""
    today = today or datetime.date.today()
    terms = search_terms(profile, max_terms)
    result = Harvest()
    employers = [entry for entry in source_map.get("jobs") or [] if entry.get("kind") == "employer"]
    callable_ones = [entry for entry in employers
                     if entry.get("how") == "api" and isinstance(entry.get("ats"), dict)
                     and entry["ats"].get("system") in _READERS]
    result.unreadable = [entry["name"] for entry in employers if entry not in callable_ones]
    for entry in callable_ones[:max_employers] if max_employers else callable_ones:
        try:
            found = _READERS[entry["ats"]["system"]](entry, profile, fetcher, today, terms)
        except (KeyError, TypeError, AttributeError):
            found = None
        if found is None:
            result.failed.append(entry["name"])
            continue
        result.read.append(entry["name"])
        result.finds.extend(found)
    return result
