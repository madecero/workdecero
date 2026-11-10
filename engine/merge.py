"""Merge and dedupe: the same posting found twice becomes one tracker note.

A sweep finds the same job on the employer's site, on LinkedIn and on two
boards. This module decides which finds are the same thing, merges them into
one record, and matches that record against notes already in the vault.

What counts as the same thing:
    jobs       employer plus posting ID. If either side has no posting ID,
               employer plus normalised title. Failing that, the link.
    contracts  client plus job ID, else client plus title, else the link.
               An undisclosed client is never matched by title.
    events     title plus date, or link plus date.
    groups     name, or a link that points at the group itself.

Two finds from the same employer with different posting IDs are different
postings, even when the titles match (the same role in two cities).

When finds are merged, the employer's own page wins over a board copy, the
other places are kept as "also listed on", gaps are filled from the other
record, and the higher fit assessment is kept.

`plan_intake` runs the whole path for one sweep (dedupe, exclusions, scoring,
match against the vault) and returns operations for the vault store. Nothing
here writes to the vault.

Standard library only, Python 3.9+.
"""

import re
import urllib.parse

import model
import scoring

JOB, CONTRACT, EVENT, GROUP = "job", "contract", "event", "group"

_COMPANY_NOISE = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|the|group|holdings|plc|gmbh|lp|llp)\b\.?")
_TITLE_NOISE = re.compile(
    r"\b(remote|hybrid|onsite|on site|contract|contractor|freelance|consultant|c2c|w2|full time|part time|"
    r"us|usa|united states)\b")
_UNDISCLOSED = ("", "confidential", "undisclosed", "confidential client", "stealth", "stealth startup", "unknown",
                "n a", "client")

# Hosts where the employer itself publishes the posting.
_OWN_SITE_HOSTS = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "icims.com",
                   "smartrecruiters.com", "workable.com", "jobvite.com", "taleo.net", "successfactors.com",
                   "bamboohr.com", "paylocity.com", "recruitingbypaycor.com", "ultipro.com", "breezy.hr",
                   "oraclecloud.com", "eightfold.ai", "rippling.com")
# Hosts that copy postings from elsewhere.
_BOARD_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "builtin.com", "dice.com",
                "monster.com", "simplyhired.com", "careerbuilder.com", "himalayas.app", "dejobs.org",
                "wellfound.com", "talent.com", "jooble.org", "lensa.com", "google.com")


def _plain(text):
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_company(name):
    """'Front Range Robotics, Inc.' and 'front range robotics' compare equal."""
    return re.sub(r"\s+", " ", _COMPANY_NOISE.sub(" ", _plain(name))).strip()


def normalize_title(title):
    """Drop bracketed notes and words such as Remote or Contract that boards add to titles."""
    text = re.sub(r"[\(\[].*?[\)\]]", " ", title or "")
    text = _TITLE_NOISE.sub(" ", _plain(text))
    text = re.sub(r"\bsr\b", "senior", text)
    text = re.sub(r"\bvp\b", "vice president", text)
    return re.sub(r"\s+", " ", text).strip()


def link_key(url):
    """A link without its scheme, www, query string, fragment or trailing slash."""
    if not url or not url.strip().lower().startswith(("http://", "https://")):
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + parts.path.rstrip("/")


def _host(url):
    key = link_key(url)
    return key.split("/", 1)[0] if key else ""


def _on(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def source_rank(item):
    """0 for the employer's own page, 2 for a board copy, 1 when it is not clear."""
    host = _host(item.get("url"))
    if _on(host, _OWN_SITE_HOSTS):
        return 0
    if _on(host, _BOARD_HOSTS):
        return 2
    source = _plain(item.get("source"))
    if source in ("company site", "employer site", "careers page", "direct"):
        return 0
    return 1


def _is_deep_link(url):
    key = link_key(url)
    return bool(key) and "/" in key and len(key.split("/", 1)[1]) > 1


def _owner(kind, item):
    return normalize_company(item.get("company") if kind == JOB else item.get("client"))


def _posting_id(kind, item):
    return _plain(item.get("req_id") if kind == JOB else item.get("job_id"))


def same(kind, a, b):
    """True when two finds are the same job, contract, event or group."""
    link_a, link_b = link_key(a.get("url")), link_key(b.get("url"))
    if kind in (JOB, CONTRACT):
        owner_a, owner_b = _owner(kind, a), _owner(kind, b)
        known = owner_a not in _UNDISCLOSED and owner_b not in _UNDISCLOSED
        if known and owner_a == owner_b:
            id_a, id_b = _posting_id(kind, a), _posting_id(kind, b)
            if id_a and id_b:
                return id_a == id_b
            title_a, title_b = normalize_title(a.get("title")), normalize_title(b.get("title"))
            if title_a and title_a == title_b:
                return True
        return bool(link_a) and link_a == link_b
    if kind == EVENT:
        if not a.get("date") or a.get("date") != b.get("date"):
            return False
        title_a, title_b = normalize_title(a.get("title")), normalize_title(b.get("title"))
        return (bool(title_a) and title_a == title_b) or (bool(link_a) and link_a == link_b)
    if kind == GROUP:
        name_a, name_b = _plain(a.get("name")), _plain(b.get("name"))
        if name_a and name_a == name_b:
            return True
        return _is_deep_link(a.get("url")) and link_a == link_b
    raise ValueError("unknown kind %r" % kind)


def merge_pair(a, b):
    """One record from two finds of the same thing. The employer's own page leads."""
    first, second = (a, b) if source_rank(a) <= source_rank(b) else (b, a)
    merged = dict(first)
    for key, value in second.items():
        if merged.get(key) in (None, "", []):
            merged[key] = value
    fits = [x.get("fit_assessment") for x in (a, b) if isinstance(x.get("fit_assessment"), (int, float))]
    if fits:
        merged["fit_assessment"] = max(fits)
    if a.get("link_verified") or b.get("link_verified"):
        merged["link_verified"] = True
    also = list(first.get("also_listed") or [])
    for other in [second] + list(second.get("also_listed") or []):
        url = other.get("url")
        if url and link_key(url) != link_key(merged.get("url")) and \
                link_key(url) not in [link_key(entry.get("url")) for entry in also]:
            also.append({"source": other.get("source") or _host(url), "url": url})
    if also:
        merged["also_listed"] = also
    return merged


def dedupe(kind, items):
    """Merge finds of the same thing within one batch. Order of first appearance is kept."""
    merged = []
    for item in items:
        for index, existing in enumerate(merged):
            if same(kind, existing, item):
                merged[index] = merge_pair(existing, item)
                break
        else:
            merged.append(dict(item))
    return merged


# --------------------------------------------------------------------------
# Matching against the vault
# --------------------------------------------------------------------------

_LINK_LABEL_RE = re.compile(r"^\[\[(?:[^\]|]*/)?([^\]|/]+?)(?:\|([^\]]*))?\]\]$")


def link_label(value):
    """The readable name inside [[path/Name|Name]]."""
    match = _LINK_LABEL_RE.match((value or "").strip()) if isinstance(value, str) else None
    if not match:
        return value if isinstance(value, str) else ""
    return (match.group(2) or match.group(1)).strip()


def row_as_item(kind, row):
    """A tracker note in the same shape as a find, so the two can be compared."""
    values = row.values
    if kind == JOB:
        return {"title": values.get("role") or row.title, "company": link_label(values.get("company")),
                "req_id": values.get("req_id"), "url": values.get("apply_url")}
    if kind == CONTRACT:
        return {"title": row.title, "client": values.get("client"), "job_id": None, "url": values.get("link")}
    if kind == EVENT:
        return {"title": row.title, "date": values.get("date"), "url": values.get("link")}
    return {"name": row.title, "url": values.get("link")}


def find_existing(kind, item, rows):
    for row in rows:
        if same(kind, row_as_item(kind, row), item):
            return row
    return None


def next_id(note_type, rows, taken=()):
    """The next free ID for a note type, such as J-13."""
    highest = 0
    for value in [row.id for row in rows] + list(taken):
        match = re.match(r"^%s-(\d+)$" % re.escape(note_type.id_prefix), value or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return "%s-%d" % (note_type.id_prefix, highest + 1)


# --------------------------------------------------------------------------
# One sweep's intake
# --------------------------------------------------------------------------

def _note_title(kind, item):
    if kind == JOB:
        return "%s, %s" % (item.get("title") or "Untitled role", item.get("company") or "Unknown employer")
    return item.get("title") or "Untitled"


def _section(kind, item, result, names):
    lines = ["**%s %d**: %s." % ("Fit" if kind == JOB else "Win odds", result.total, result.breakdown())]
    if getattr(result, "near", None):
        reason = result.near
        lines.append("**Near miss:** %s. It is kept here in case you see it differently, and left out of the "
                     "digest's top list." % (reason[:1].upper() + reason[1:]))
    if item.get("why"):
        lines.append("**Why it fits:** %s" % item["why"].strip())
    if result.caveats:
        lines.append("**Caveats:** " + " ".join(caveat.rstrip(".") + "." for caveat in result.caveats))
    if item.get("also_listed"):
        lines.append("**Also listed on:** " + ", ".join(
            "[%s](%s)" % (entry.get("source") or "link", entry["url"]) for entry in item["also_listed"]))
    return scoring.scrub_confidential("\n\n".join(lines), names)


def _values(kind, item, result, store, today, names):
    day = today.isoformat()
    if kind == JOB:
        values = {
            "role": item.get("title"), "company": store.link("company", item["company"]) if item.get("company") else None,
            "fit": result.total, "pay_min": item.get("pay_min"), "pay_max": item.get("pay_max"),
            "work_mode": (item.get("work_mode") or "").capitalize() or None,
            "distance_miles": item.get("distance_miles"), "location": item.get("location"),
            "posted": item.get("posted"), "closes": item.get("closes"), "apply_url": item.get("url"),
            "source": item.get("source"), "req_id": item.get("req_id"),
            "near_miss": bool(result.near), "near_reason": (result.near or None),
            "fit_background": item.get("fit_assessment"), "text_hash": item.get("text_hash"),
        }
    else:
        values = {
            "client": item.get("client"), "via": item.get("via"), "fit": result.total,
            "rate_min": item.get("rate_min"), "rate_max": item.get("rate_max"),
            "hours_per_week": item.get("hours_per_week"), "length_weeks": item.get("length_weeks"),
            "remote": item.get("remote"), "engagement": item.get("engagement"), "link": item.get("url"),
            "posted": item.get("posted"), "closes": item.get("closes"),
        }
    values.update({"last_seen": day, "updated": day})
    values = dict((key, value) for key, value in values.items() if value not in (None, ""))
    return dict((key, scoring.scrub_confidential(value, names) if key not in ("company", "apply_url", "link") else value)
                for key, value in values.items())


class Intake(object):
    """What one sweep's finds turned into."""

    def __init__(self):
        self.operations = []   # for VaultStore.apply
        self.new = []          # (operation, ScoreResult), best first
        self.seen_again = []   # operations that refresh notes already in the vault
        self.dropped = []      # (title, reason) for finds that were left out
        self.featured = []     # the top of `new`, sized by digest.max_new_items
        self.more = 0          # how many other new items are in the tracker but not in the digest


def plan_intake(kind, items, store, profile, today):
    """Dedupe, drop exclusions, score, match against the vault and plan the writes.

    `kind` is "job" or "contract". Returns an Intake. Every find that passes is
    written to the tracker. The digest features only the best few, sized by
    digest.max_new_items, so a short read stays short.
    """
    if kind not in (JOB, CONTRACT):
        raise ValueError("plan_intake handles jobs and contracts")
    note_type = model.BY_NAME[kind]
    names = scoring.confidential_names(profile)
    rows = store.rows(kind)
    company_rows = store.rows("company") if kind == JOB else []
    intake = Intake()
    taken_ids, planned_companies, staged = [], {}, []

    for item in dedupe(kind, items):
        result = scoring.score(kind, item, profile, today)
        label = scoring.scrub_confidential(_note_title(kind, item), names)
        if result.dropped:
            intake.dropped.append((label, result.dropped))
            continue
        existing = find_existing(kind, item, rows)
        values = _values(kind, item, result, store, today, names)
        if existing is not None:
            if note_type.is_done(existing.values.get("status")):
                # The user passed on it, or it closed. Seeing it again does not reopen it.
                intake.dropped.append((label, "already in your tracker as %s" % existing.values.get("status")))
                continue
            operation = {"op": "update", "type": kind, "id": existing.id, "values": values, "force": [],
                         "section": _section(kind, item, result, names)}
            intake.seen_again.append(operation)
            staged.append(operation)
            continue
        if kind == JOB and item.get("company"):
            key = normalize_company(item["company"])
            known = any(normalize_company(row.title) == key for row in company_rows)
            if not known and key not in planned_companies:
                company_id = next_id(model.COMPANY, company_rows, planned_companies.values())
                planned_companies[key] = company_id
                staged.append({"op": "create", "type": "company", "id": company_id,
                               "title": item["company"], "values": {}})
        note_id = next_id(note_type, rows, taken_ids)
        taken_ids.append(note_id)
        values["first_seen"] = today.isoformat()
        operation = {"op": "create", "type": kind, "id": note_id, "title": _note_title(kind, item),
                     "values": values, "section": _section(kind, item, result, names)}
        intake.new.append((operation, result))
        staged.append(operation)

    intake.new.sort(key=lambda pair: -pair[1].total)
    limit = ((profile.get("digest") or {}).get("max_new_items")) or 10
    intake.featured = intake.new[:limit]
    intake.more = max(0, len(intake.new) - limit)
    intake.operations = staged
    return intake
