"""Warm paths: who to reach at an employer, how sure the email is, and who can introduce you.

For each job note this module writes a "Warm path" part inside the note's
workdecero block, links the people involved to People notes, and tells the
scoring step how warm the connection is. It works from two inputs:

  * Research output for the posting, in the shape described under
    "Research input" below. A research step fills it from public pages.
  * The user's network, which lives in People notes in the vault. It can be
    started from LinkedIn's Connections.csv export.

Rules it keeps:
  * Never guess a name. A role with no name is written as
    "Likely <title> (name not found)", and no address is built for it.
  * An email address is built only for a named person, from a format that
    published evidence supports, and it always shows a percent and the basis:
        90  the exact address is published
        75  the format is confirmed by two or more current addresses, and the
            name has only one reading
        60  one example of the format, or a name with more than one reading
        40  only aggregator sites or old examples show the format
        20  a guess from convention
  * No mail-server probing and no email-finder sign-ups. Evidence is what a
    person could read on a public page.
  * A recruiter found by searching for a role type, not on the posting, is
    marked low confidence and "not confirmed on this posting".
  * Quiet search: coworkers at the current employer are never suggested, and
    a posting at a client or partner of the current employer gets a caution
    in place of outreach suggestions.
  * Confidential names are scrubbed from everything written.

Research input, per job (every key is optional):
    {
      "hiring_manager": {"name", "title", "source_url", "email_published"},
      "skip_level":     {"name", "title", "source_url", "email_published"},
      "recruiter":      {"name", "title", "source_url", "email_published",
                         "scope": "posting" or "function"},
      "email_format":   {"domain", "pattern", "evidence", "source_url"},
           pattern:  one of FORMATS, such as "first.last"
           evidence: "two-current", "one-example", "aggregator" or "guess"
      "insiders":   [{"name", "title", "source_url", "why"}],
      "warm_paths": [{"via": a person in the network, "to": who they know, "how"}],
      "shared_background": "text", for example a former employer in common
      "related_to_current_employer": "client" or "partner",
      "note": "text",
      "next_step": "text"
    }
A person with a title and no name means the name was not found.

Nothing here writes to the vault. Functions return operations for
VaultStore.apply.

Standard library only, Python 3.9+.
"""

import csv
import datetime
import io
import re
import unicodedata

import merge
import model
import scoring

FORMATS = {
    "first.last": "{first}.{last}",
    "firstlast": "{first}{last}",
    "first_last": "{first}_{last}",
    "first-last": "{first}-{last}",
    "flast": "{f}{last}",
    "f.last": "{f}.{last}",
    "firstl": "{first}{l}",
    "first.l": "{first}.{l}",
    "first": "{first}",
    "last": "{last}",
    "lastf": "{last}{f}",
    "last.first": "{last}.{first}",
}

EVIDENCE = {
    "published": (90, "exact address published"),
    "two-current": (75, "format confirmed by two or more current addresses"),
    "one-example": (60, "one published example of the format"),
    "aggregator": (40, "format seen only on aggregator sites or in old examples"),
    "guess": (20, "a guess from the usual convention, with no published example"),
}

_NAME_EXTRAS = re.compile(r",?\s+\b(jr|sr|ii|iii|iv|phd|md|mba|cpa|rn|esq|pmp)\b\.?", re.IGNORECASE)
_HONORIFICS = re.compile(r"^(dr|mr|mrs|ms|mx|prof)\.?\s+", re.IGNORECASE)


def _fold(text):
    """Plain lower-case letters: 'Zoë' becomes 'zoe'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if ch.isalpha() and ord(ch) < 128).lower()


def name_parts(name):
    """Return (first, last, wrinkle). `wrinkle` is True when the name has more than one reading."""
    cleaned = _HONORIFICS.sub("", _NAME_EXTRAS.sub("", (name or "").strip()))
    cleaned = re.sub(r"\(.*?\)|\".*?\"", " ", cleaned)
    tokens = [token for token in re.split(r"\s+", cleaned.strip()) if token]
    if len(tokens) < 2:
        return (_fold(tokens[0]) if tokens else ""), "", True
    first, last = tokens[0], tokens[-1]
    wrinkle = (
        len(tokens) > 2                                   # a middle name or a two-word surname
        or "-" in last or "'" in last or "-" in first     # a hyphen or apostrophe can be kept or dropped
        or len(first.strip(".")) == 1                     # an initial in place of a first name
        or any(ord(ch) > 127 for ch in first + last)      # accents are written several ways
    )
    return _fold(first), _fold(last), wrinkle


def build_address(name, email_format):
    """An address for a named person from an evidenced format, or None."""
    if not email_format or not name:
        return None
    pattern = FORMATS.get((email_format.get("pattern") or "").strip().lower())
    domain = (email_format.get("domain") or "").strip().lower()
    first, last, _ = name_parts(name)
    if not pattern or not domain or not first or not last:
        return None
    local = pattern.format(first=first, last=last, f=first[0], l=last[0])
    return "%s@%s" % (local, domain)


def email_for(person, email_format):
    """Return (address, percent, basis) for a person, or (None, None, None).

    A published address always wins. Otherwise the percent comes from how well
    the format is evidenced, lowered one step when the name has more than one
    reading.
    """
    if not person or not person.get("name"):
        return None, None, None
    published = (person.get("email_published") or "").strip()
    if published:
        percent, basis = EVIDENCE["published"]
        return published, percent, basis
    evidence = (email_format or {}).get("evidence")
    if evidence not in ("two-current", "one-example", "aggregator", "guess"):
        return None, None, None
    address = build_address(person["name"], email_format)
    if not address:
        return None, None, None
    percent, basis = EVIDENCE[evidence]
    _, _, wrinkle = name_parts(person["name"])
    if wrinkle:
        if evidence == "two-current":
            percent, basis = 60, basis + ", but the name can be written more than one way"
        elif evidence == "one-example":
            percent, basis = 40, basis + ", and the name can be written more than one way"
        else:
            basis += ", and the name can be written more than one way"
    return address, percent, basis


# --------------------------------------------------------------------------
# Building the warm path for one job
# --------------------------------------------------------------------------

def _person_key(name):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", _fold_keep_spaces(name))).strip()


def _fold_keep_spaces(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if ord(ch) < 128).lower()


def vault_name(title):
    """The file name the vault store gives a note with this title."""
    import vault
    return vault.safe_file_name(title)


def _same_company(a, b):
    left, right = merge.normalize_company(a), merge.normalize_company(b)
    return bool(left) and left == right


class WarmPath(object):
    def __init__(self):
        self.text = ""           # the "Warm path" part of the note
        self.operations = []     # People notes to create or update, then the job note itself
        self.warm = None         # the tier scoring.WARM_POINTS understands, or None
        self.people = []         # names written as People notes


def _is_quiet(profile):
    return ((profile.get("jobs") or {}).get("search_visibility") == "quiet"
            and bool((profile.get("person") or {}).get("current_employer")))


def _source(person):
    url = person.get("source_url")
    return " ([source](%s))" % url if url else ""


def _named(person):
    return bool(person and (person.get("name") or "").strip())


def build(job_row, research, people_rows, store, profile, taken_ids=()):
    """Work out the warm path for one job note. Returns a WarmPath."""
    research = research or {}
    result = WarmPath()
    names = scoring.confidential_names(profile)
    employer = (profile.get("person") or {}).get("current_employer")
    quiet = _is_quiet(profile)
    company = merge.link_label(job_row.values.get("company"))
    email_format = research.get("email_format")
    job_link = "[[%s|%s]]" % (job_row.path[:-3], job_row.title)

    def off_limits(person_company):
        return quiet and _same_company(person_company, employer)

    known = {}
    for row in people_rows:
        known[_person_key(row.title)] = row
    planned = {}

    def person_link(name):
        return store.link("person", name)

    def plan_person(person, role_label, relationship=None):
        """Queue a People note for a named person and return (link, address, percent, basis)."""
        name = person["name"].strip()
        address, percent, basis = email_for(person, email_format)
        key = _person_key(name)
        existing = known.get(key)
        values = {"company": store.link("company", company) if company else None,
                  "role": person.get("title"), "source_url": person.get("source_url"),
                  "why": person.get("why") or "%s for %s" % (role_label, job_row.values.get("role") or job_row.title)}
        if address:
            values["email"], values["confidence"] = address, percent
        roles = list((existing.values.get("linked_roles") if existing else None) or [])
        if key in planned:
            roles = list(planned[key]["values"].get("linked_roles") or roles)
        if job_link not in roles:
            roles.append(job_link)
        values["linked_roles"] = roles
        values = dict((k, v) for k, v in values.items() if v not in (None, ""))
        if existing is not None:
            # Keep what the network already says about this person: how you know them and where you met.
            values.pop("why", None) if existing.values.get("why") else None
            operation = {"op": "update", "type": "person", "id": existing.id, "values": values, "force": []}
        elif key in planned:
            planned[key]["values"].update(values)
            operation = None
        else:
            values["relationship"] = relationship or model.NOT_CONNECTED
            note_id = merge.next_id(model.PERSON, people_rows,
                                    [op["id"] for op in planned.values()] + list(taken_ids))
            operation = {"op": "create", "type": "person", "id": note_id, "title": name, "values": values}
            planned[key] = operation
        if operation is not None and operation["op"] == "update":
            result.operations.append(operation)
        if name not in result.people:
            result.people.append(name)
        return person_link(name), address, percent, basis

    def contact_line(label, person, role_label):
        if not person:
            return None
        if not _named(person):
            title = (person.get("title") or "").strip()
            return "- **%s:** Likely %s (name not found)." % (label, title) if title else None
        link, address, percent, basis = plan_person(person, role_label)
        line = "- **%s:** %s%s" % (label, "Likely " if person.get("inferred") is True else "", link)
        if person.get("title"):
            line += ", %s" % person["title"].strip()
        line += _source(person) + "."
        if person.get("inferred") is True:
            line += " Not named on the posting. Worked out from the company's public pages."
        if address:
            line += " Email: %s (email confidence %d%%: %s)." % (address, percent, basis)
        else:
            line += " Email: not found."
        return line

    lines = []
    related = (research.get("related_to_current_employer") or "").strip().lower()
    caution = quiet and related in ("client", "partner")
    if caution:
        lines.append("- **Caution:** this company is a %s of your current employer. Reaching out could reveal "
                     "your search, so no outreach is suggested here." % related)

    manager = research.get("hiring_manager")
    lines.append(contact_line("Hiring manager", manager, "Hiring manager") or "- **Hiring manager:** not found.")
    skip = contact_line("Skip-level", research.get("skip_level"), "Skip-level manager")
    if skip:
        lines.append(skip)

    recruiter = research.get("recruiter")
    recruiter_line = contact_line("Recruiter", recruiter, "Recruiter")
    if recruiter_line and _named(recruiter):
        # Two separate judgements: is the address right, and is this the recruiter for this posting.
        if (recruiter.get("scope") or "posting") == "posting":
            recruiter_line += " Role match: named on this posting."
        else:
            recruiter_line += (" Role match: low confidence. Recruits for this kind of role, not confirmed on "
                               "this posting.")
    lines.append(recruiter_line or "- **Recruiter:** not found.")

    if email_format and email_format.get("domain") and FORMATS.get((email_format.get("pattern") or "").lower()):
        evidence = email_format.get("evidence")
        basis = EVIDENCE[evidence][1] if evidence in EVIDENCE else "basis not recorded"
        lines.append("- **Email format:** %s@%s (%s)%s." % (email_format["pattern"].lower(), email_format["domain"].lower(),
                                                              basis, _source(email_format)))
    else:
        lines.append("- **Email format:** not found.")

    # Who the user already knows there.
    first_degree, second_degree = [], []
    for row in people_rows:
        if not _same_company(merge.link_label(row.values.get("company")), company):
            continue
        degree = (row.values.get("relationship") or "").strip().lower()
        if degree == "1st":
            first_degree.append(row)
        elif degree == "2nd":
            second_degree.append(row)
    hiring_team = [_person_key(p["name"]) for p in (research.get("hiring_manager"), research.get("skip_level"), recruiter)
                   if _named(p)]
    if first_degree and not caution:
        lines.append("- **1st-degree connections:** " + ", ".join(
            "%s%s" % (person_link(row.title), ", %s" % row.values["role"] if row.values.get("role") else "")
            for row in first_degree) + ".")
    else:
        lines.append("- **1st-degree connections:** none in your network at this company.")

    insiders = [p for p in research.get("insiders") or [] if _named(p)]
    if insiders:
        shown = []
        for person in insiders:
            link, _, _, _ = plan_person(person, "Works at the company")
            shown.append("%s%s%s" % (link, ", %s" % person["title"] if person.get("title") else "", _source(person)))
        lines.append("- **Insiders:** " + "; ".join(shown) + ".")

    # Every way in through someone the user knows. This one list feeds the Warm paths bullet, the
    # Warm path property shown in the tables, the Contact first line and the score, so they agree.
    warm = []          # (People row, degree, what to say about them, who they lead to)
    if not caution:
        for row in first_degree:
            warm.append((row, "1st", "works there", None))
        for path in research.get("warm_paths") or []:
            via = (path.get("via") or "").strip()
            via_row = known.get(_person_key(via))
            if not via or via_row is None:
                continue                                   # only people actually in the network can introduce
            if off_limits(merge.link_label(via_row.values.get("company"))):
                continue                                   # never route a quiet search through a coworker
            if any(via_row is entry[0] for entry in warm):
                continue
            target = (path.get("to") or "").strip()
            degree = (via_row.values.get("relationship") or "").strip() or "1st"
            about = "knows %s" % (target or "someone there")
            if path.get("how"):
                about += " (%s)" % path["how"].strip().rstrip(".")
            warm.append((via_row, degree if degree in ("1st", "2nd") else "1st", about, target))
        for row in second_degree:
            if not any(row is entry[0] for entry in warm):
                warm.append((row, "2nd", "works there, one introduction away", None))

    def labelled(row, degree):
        """A link that shows the degree, as in 'Priya Raman (1st)'."""
        return "[[%s/%s/%s|%s (%s)]]" % (store.folder, model.PERSON.folder, vault_name(row.title), row.title, degree)

    if warm:
        lines.append("- **Warm paths:** " + "; ".join(
            "%s %s" % (labelled(row, degree), about) for row, degree, about, _ in warm) + ".")
    elif research.get("shared_background") and not caution:
        lines.append("- **Warm paths:** no introduction found. In common: %s."
                     % research["shared_background"].strip().rstrip("."))
    else:
        lines.append("- **Warm paths:** none found.")

    if research.get("note"):
        lines.append("- **Note:** %s" % research["note"].strip())
    if research.get("next_step") and not caution:
        lines.append("- **Next step:** %s" % research["next_step"].strip())

    # How warm is this, for scoring.
    if caution:
        result.warm = None
    elif any(_person_key(row.title) in hiring_team for row in first_degree):
        result.warm = "1st-hiring-team"
    elif first_degree:
        result.warm = "1st"
    elif any(target and _person_key(target) in hiring_team for _, _, _, target in warm):
        result.warm = "2nd-hiring-team"
    elif warm:
        result.warm = "2nd"
    elif research.get("shared_background"):
        result.warm = "shared-background"
    elif hiring_team:
        result.warm = "named-contact"

    # Who to contact first, in one sentence. It leads the block and is also a property.
    manager_email = email_for(manager, email_format) if _named(manager) else (None, None, None)
    recruiter_email = email_for(recruiter, email_format) if _named(recruiter) else (None, None, None)
    # Never sound surer than the research was: a manager worked out from a leadership page is "likely".
    manager_role = ("likely the hiring manager (not named on the posting)"
                    if isinstance(manager, dict) and manager.get("inferred") is True else "the hiring manager")
    if caution:
        first = "No one for now. See the caution below"
    elif result.warm == "1st-hiring-team":
        row = [r for r in first_degree if _person_key(r.title) in hiring_team][0]
        first = "%s, directly. A 1st-degree connection who is on the hiring team" % row.title
    elif warm:
        row, degree, about, target = warm[0]
        if target:
            first = "%s (%s), for a warm introduction to %s" % (row.title, degree, target)
        elif degree == "1st":
            first = "%s (1st), for a warm introduction. A connection of yours who works there" % row.title
        else:
            first = "%s (2nd), through a shared connection. Works there" % row.title
    elif _named(manager) and manager_email[0]:
        first = ("%s, %s, directly by email (email confidence %d%%). No one in your network "
                 "works there" % (manager["name"].strip(), manager_role, manager_email[1]))
    elif _named(recruiter) and recruiter_email[0] and (recruiter.get("scope") or "posting") == "posting":
        first = ("%s, the recruiter on this posting, directly by email (email confidence %d%%)"
                 % (recruiter["name"].strip(), recruiter_email[1]))
    elif _named(manager):
        first = "%s, %s, directly. No email was found, so use LinkedIn or the application" \
            % (manager["name"].strip(), manager_role)
    else:
        title = (manager or {}).get("title") if isinstance(manager, dict) else None
        first = "No one found yet. Apply directly" + ("; the hiring manager is likely %s" % re.sub(r"(?i)[\s,]*\(?\b(likely|probably)\)?\s*$", "", title.strip()) if title else "")
    lines.insert(0, "- **Contact first:** %s." % first)

    result.text = scoring.scrub_confidential("\n".join(lines), names)
    for operation in planned.values():
        result.operations.append(operation)
    for operation in result.operations:
        operation["values"] = dict(
            (key, value if key in ("company", "linked_roles", "source_url", "email") else
             scoring.scrub_confidential(value, names)) for key, value in operation["values"].items())

    result.operations.append({"op": "update", "type": "job", "id": job_row.id, "force": [],
                              "values": {"warm_path": [labelled(row, degree) for row, degree, _, _ in warm],
                                         "contact_first": scoring.scrub_confidential(first + ".", names)},
                              "part": (model.PART_WARM_PATH, result.text)})
    return result


def plan_warm_paths(research_by_job_id, store, profile):
    """Warm paths for several jobs at once. Returns (operations, {job id: WarmPath})."""
    jobs = dict((row.id, row) for row in store.rows("job"))
    people_rows = store.rows("person")
    operations, results, taken = [], {}, []
    merged_creates = {}
    for job_id, research in research_by_job_id.items():
        row = jobs.get(job_id)
        if row is None:
            continue
        warm = build(row, research, people_rows, store, profile, taken_ids=taken)
        results[job_id] = warm
        for operation in warm.operations:
            if operation["op"] == "create":
                key = _person_key(operation["title"])
                if key in merged_creates:
                    # The same person turned up on two postings: one note, both roles linked.
                    kept = merged_creates[key]
                    for role in operation["values"].get("linked_roles") or []:
                        if role not in kept["values"].setdefault("linked_roles", []):
                            kept["values"]["linked_roles"].append(role)
                    continue
                merged_creates[key] = operation
                taken.append(operation["id"])
            operations.append(operation)
    # People first, so the links in the job notes resolve as soon as they are written.
    operations.sort(key=lambda operation: 0 if operation["type"] == "person" else 1)
    return operations, results


# --------------------------------------------------------------------------
# Starting the network from LinkedIn's export
# --------------------------------------------------------------------------

_MONTHS = dict((name, number) for number, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1))


def _connected_on(text):
    """'09 Nov 2026' becomes '2026-11-09'. Anything else comes back as None."""
    match = re.match(r"^\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\s*$", text or "")
    if match and match.group(2).lower() in _MONTHS:
        try:
            return datetime.date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1))).isoformat()
        except ValueError:
            return None
    return text.strip() if re.match(r"^\d{4}-\d{2}-\d{2}$", (text or "").strip()) else None


def read_connections(text, counts=None):
    """Rows from LinkedIn's Connections.csv as dicts. Copes with the note lines LinkedIn puts above the header.

    Pass a dict as `counts` to learn how many rows had no name (LinkedIn blanks some members).
    """
    text = text.lstrip(chr(0xFEFF))
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        cells = [cell.strip().strip('"').lower() for cell in line.split(",")]
        if "first name" in cells and "last name" in cells:
            start = index
            break
    if start is None:
        raise ValueError("This does not look like LinkedIn's Connections.csv: no row with First Name and Last Name.")
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    rows = []
    for raw in reader:
        row = dict(((key or "").strip().lower(), (value or "").strip()) for key, value in raw.items())
        name = ("%s %s" % (row.get("first name", ""), row.get("last name", ""))).strip()
        if not name:
            if counts is not None:
                counts["unnamed"] = counts.get("unnamed", 0) + 1
            continue                                       # LinkedIn leaves the name blank for some members
        rows.append({"name": name, "company": row.get("company", ""), "position": row.get("position", ""),
                     "url": row.get("url", ""), "email": row.get("email address", ""),
                     "connected_on": _connected_on(row.get("connected on", ""))})
    return rows


class NetworkImport(object):
    def __init__(self):
        self.operations = []
        self.added = 0
        self.updated = 0
        self.skipped_confidential = 0
        self.skipped_unnamed = 0


def plan_network_import(csv_text, store, profile, include_emails=False):
    """Turn LinkedIn's Connections.csv into People notes. Returns a NetworkImport.

    Email addresses in the file are left out unless `include_emails` is True,
    which setup sets only when the user says so. Connections at a confidential
    employer, the current one included, are not written at all.
    """
    result = NetworkImport()
    names = scoring.confidential_names(profile)
    people_rows = store.rows("person")
    company_rows = store.rows("company")
    known = dict((_person_key(row.title), row) for row in people_rows)
    known_companies = set(merge.normalize_company(row.title) for row in company_rows)
    taken_people, taken_companies, seen = [], [], set()
    counts = {}
    connections = read_connections(csv_text, counts)
    result.skipped_unnamed = counts.get("unnamed", 0)

    for person in connections:
        key = _person_key(person["name"])
        if not key or key in seen:
            continue
        seen.add(key)
        if any(_same_company(person["company"], name) for name in names):
            result.skipped_confidential += 1
            continue
        values = {"relationship": "1st", "role": person["position"], "source_url": person["url"]}
        if person["company"]:
            values["company"] = store.link("company", person["company"])
            company_key = merge.normalize_company(person["company"])
            if company_key and company_key not in known_companies:
                known_companies.add(company_key)
                company_id = merge.next_id(model.COMPANY, company_rows, taken_companies)
                taken_companies.append(company_id)
                result.operations.append({"op": "create", "type": "company", "id": company_id,
                                          "title": person["company"], "values": {}})
        if include_emails and person["email"]:
            values["email"], values["confidence"] = person["email"], EVIDENCE["published"][0]
        values = dict((k, scoring.scrub_confidential(v, names) if k in ("role",) else v)
                      for k, v in values.items() if v not in (None, ""))
        existing = known.get(key)
        if existing is not None:
            result.operations.append({"op": "update", "type": "person", "id": existing.id, "values": values, "force": []})
            result.updated += 1
        else:
            values["met"] = "LinkedIn connection%s" % (" since %s" % person["connected_on"] if person["connected_on"] else "")
            note_id = merge.next_id(model.PERSON, people_rows, taken_people)
            taken_people.append(note_id)
            result.operations.append({"op": "create", "type": "person", "id": note_id, "title": person["name"],
                                      "values": values})
            result.added += 1
    result.operations.sort(key=lambda operation: 0 if operation["type"] == "company" else 1)
    return result
