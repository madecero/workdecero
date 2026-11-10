#!/usr/bin/env python3
"""Move an existing spreadsheet tracker into the vault: CSV files plus a mapping.

A person who already keeps a tracker in a spreadsheet exports each table as
a CSV file and says, in a mapping, what each column means. Each row then
becomes one note, written through the same vault store the sweeps use.

What it promises:
  * Nothing is lost. A column with no matching property, and any value that
    could not be read as the kind of thing its property holds, is kept in the
    note's body under the person's own heading. The plugin never rewrites
    that part of a note.
  * Nothing is guessed. A status word that is not one of the plugin's and is
    not in the mapping stops the import until the person says what it means.
  * A second run adds nothing twice. A row already in the vault is left as
    it is, and only properties that are still empty are filled.
  * A row at a confidential employer or client is counted and not written.
  * check() writes nothing to the vault. run() only works on a plan that
    check() has just reported on.

The mapping (the "plan"):
    {"tables": [
       {"csv": "/full/path/Jobs.csv", "type": "job", "title": "Role",
        "columns": {"Employer": "company", "Low": "pay_min", "Status": "status", "Applied": "applied_on"},
        "statuses": {"sent": "Applied", "dead": "Closed"},
        "date_formats": ["%m/%d/%Y"],
        "keep_heading": "From my old tracker"}]}

Standard library only, Python 3.9+.
"""

import csv
import datetime
import hashlib
import io
import json
import os
import re

import merge
import model
import scoring

TYPES = ("job", "contract", "channel", "group", "event", "person", "company")
DEFAULT_HEADING = "From my old tracker"
MAX_CSV_BYTES = 20 * 1024 * 1024
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d")
# Which kind of note a link property points at. A link property not listed here cannot be imported into.
LINK_TARGETS = {"company": "company", "contact": "person", "warm_path": "person", "people_met": "person",
                "group": "group"}
YES, NO = ("yes", "y", "true", "x", "1", "done"), ("no", "n", "false", "0", "")
# Properties the plugin works out for itself. A mapping may not write them.
NOT_MAPPABLE = ("id", "type", "liveness", "last_checked", "text_hash", "fit_background", "near_miss", "near_reason",
                "outside_times", "linked_roles", "prep", "updated", "last_seen")


class ImportError_(ValueError):
    """A problem with the plan or a file that the person can fix."""


def mappable(type_name):
    """The properties a column may be mapped to, for one kind of note."""
    note_type = model.BY_NAME[type_name]
    return [field for field in note_type.fields
            if field.name not in NOT_MAPPABLE and (field.kind not in (model.LINK, model.LINKS) or field.name in LINK_TARGETS)]


PEOPLE_WORDS = ("referral", "referred", "referrer", "contact", "recruiter", "hiring manager", "manager", "introduced",
                "intro", "warm path", "who i know", "connection")


def people_columns(headers, type_name):
    """Columns whose names say they hold people, for kinds of note that can link to a person."""
    if type_name not in ("job", "contract"):
        return []
    return [header for header in headers if any(word in header.lower() for word in PEOPLE_WORDS)]


def fields_report():
    """What a mapping may name: each kind of note, its properties and its statuses."""
    lines = ["WHAT A MAPPING MAY USE", ""]
    for type_name in TYPES:
        note_type = model.BY_NAME[type_name]
        lines.append("%s  (one row becomes one note in %s)" % (type_name, note_type.folder))
        lines.append("  properties: %s" % ", ".join("%s (%s)" % (field.name, field.kind) for field in mappable(type_name)))
        if note_type.statuses:
            lines.append("  statuses:   %s" % ", ".join(note_type.status_names()))
    lines += ["", "A column that names people (a referral, a contact, a recruiter, a hiring manager) should link to "
              "People: map one to contact and another to warm_path. Left unmapped, the names stay as plain text.",
              "\"yes_no\" says what a word in a tick-box column means when it is not plainly yes or no: "
              "{\"went\": true, \"skipped\": false}.",
              "\"title\" names the column that holds each row's name (the role, the gig, the group, the person).",
              "A column mapped to company, contact, warm_path, people_met or group makes a note for what it names "
              "and links to it.",
              "A column you leave out of the mapping is kept, word for word, in the note's body."]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def read_csv(path):
    """(headers, rows as dicts). Raises ImportError_ with a plain reason."""
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise ImportError_("Each table needs the full path of its CSV file.")
    path = os.path.expanduser(path)
    if not path.lower().endswith(".csv") or not os.path.isfile(path):
        raise ImportError_("%s is not a CSV file that exists." % os.path.basename(path))
    if os.path.getsize(path) > MAX_CSV_BYTES:
        raise ImportError_("%s is larger than 20 MB, which is more than a tracker should be." % os.path.basename(path))
    with open(path, "rb") as handle:
        raw = handle.read()
    text = None
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:
        raise ImportError_("%s could not be read as text." % os.path.basename(path))
    reader = csv.reader(io.StringIO(text, newline=""))
    table = [row for row in reader if any(cell.strip() for cell in row)]
    if not table:
        raise ImportError_("%s is empty." % os.path.basename(path))
    headers = [cell.strip() for cell in table[0]]
    if len(set(header for header in headers if header)) != len([header for header in headers if header]):
        raise ImportError_("%s has two columns with the same name. Rename one in the spreadsheet and export again."
                           % os.path.basename(path))
    rows = []
    for cells in table[1:]:
        cells = list(cells) + [""] * (len(headers) - len(cells))
        rows.append(dict((header, cells[index].strip()) for index, header in enumerate(headers) if header))
    return [header for header in headers if header], rows


def fingerprint(plan):
    """One value that changes if the plan or any of its CSV files changes."""
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8"))
    for table in plan.get("tables") or []:
        path = os.path.expanduser(str(table.get("csv") or ""))
        try:
            with open(path, "rb") as handle:
                digest.update(handle.read())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def check_plan(plan):
    """Problems with the plan itself, before any file is read."""
    if not isinstance(plan, dict) or not isinstance(plan.get("tables"), list) or not plan["tables"]:
        raise ImportError_('The plan needs a "tables" list with at least one table.')
    for number, table in enumerate(plan["tables"], start=1):
        if not isinstance(table, dict):
            raise ImportError_("Table %d is not an object." % number)
        if table.get("type") not in TYPES:
            raise ImportError_("Table %d: \"type\" must be one of %s." % (number, ", ".join(TYPES)))
        names = [field.name for field in mappable(table["type"])]
        columns = table.get("columns") if table.get("columns") is not None else {}
        if not isinstance(columns, dict):
            raise ImportError_("Table %d: \"columns\" must be an object of column name to property." % number)
        for column, target in columns.items():
            if target not in names:
                raise ImportError_("Table %d: column \"%s\" is mapped to \"%s\", which is not a property a %s note "
                                   "takes. Run the fields command to see them." % (number, column, target, table["type"]))
        targets = [target for target in columns.values()]
        if len(set(targets)) != len(targets):
            raise ImportError_("Table %d: two columns are mapped to the same property." % number)
        note_type = model.BY_NAME[table["type"]]
        for word, status in (table.get("statuses") or {}).items():
            if note_type.canonical_status(str(status)) is None:
                raise ImportError_("Table %d: status \"%s\" is mapped to \"%s\", which is not a status a %s note has. "
                                   "Use one of: %s." % (number, word, status, table["type"],
                                                        ", ".join(note_type.status_names())))
        if not isinstance(table.get("yes_no") or {}, dict) or any(
                not isinstance(value, bool) for value in (table.get("yes_no") or {}).values()):
            raise ImportError_("Table %d: \"yes_no\" must map each word to true or false, such as {\"went\": true}."
                               % number)
        for pattern in table.get("date_formats") or []:
            if not isinstance(pattern, str) or "%" not in pattern:
                raise ImportError_("Table %d: \"%s\" is not a date format such as %%m/%%d/%%Y." % (number, pattern))


def read_date(text, formats):
    text = text.strip()
    if not text:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ].*)?$", text)
    candidates = [match.group(1)] if match else [text]
    for candidate in candidates:
        for pattern in list(formats) + list(DATE_FORMATS):
            try:
                return datetime.datetime.strptime(candidate, pattern).date().isoformat()
            except ValueError:
                continue
    return None


def read_number(text):
    cleaned = re.sub(r"[,$€£\s]|usd|/hr|/hour|per hour|a year|/yr", "", text.strip().lower())
    scale = 1
    if cleaned.endswith("k"):
        cleaned, scale = cleaned[:-1], 1000
    try:
        value = float(cleaned) * scale
    except ValueError:
        return None
    return int(value) if value == int(value) else value


def _names(text):
    """The names in a cell that may hold several, split on semicolons or line breaks."""
    return [part.strip() for part in re.split(r"[;\n]", text) if part.strip()]


# --------------------------------------------------------------------------
# One table
# --------------------------------------------------------------------------

class TableReport(object):
    def __init__(self, label, type_name):
        self.label = label
        self.type = type_name
        self.rows = 0
        self.new = 0
        self.already = 0
        self.confidential = 0
        self.no_title = 0
        self.repeats = 0
        self.statuses = {}          # unmapped status word -> count
        self.dates = {}             # column -> [values that could not be read]
        self.numbers = {}           # column -> count of values that could not be read
        self.yes_no = {}            # column -> {word that is neither yes nor no: count}
        self.kept_columns = []
        self.missing_columns = []
        self.headers = []
        self.people = 0
        self.companies = 0
        self.details = []           # row-level lines, for the private report only

    def skipped(self):
        return self.confidential + self.no_title + self.repeats


def _identity(type_name, title, values):
    """What makes two rows the same thing."""
    name = merge.normalize_title(title) if type_name in ("job", "contract") else scoring._text(title)
    if type_name == "job":
        return (name, merge.normalize_company(values.get("_company") or ""))
    if type_name == "contract":
        return (name, merge.normalize_company(values.get("client") or values.get("via") or ""))
    if type_name == "event":
        return (name, values.get("date") or "")
    return (name,)


def _existing_identity(type_name, row):
    if type_name == "job":
        item = merge.row_as_item("job", row)
        return (merge.normalize_title(item["title"]), merge.normalize_company(item.get("company") or ""))
    if type_name == "contract":
        item = merge.row_as_item("contract", row)
        return (merge.normalize_title(item["title"]),
                merge.normalize_company(row.values.get("client") or row.values.get("via") or ""))
    if type_name == "event":
        return (scoring._text(row.title), str(row.values.get("date") or ""))
    return (scoring._text(row.title),)


def _link_of(type_name, values):
    key = {"job": "apply_url", "contract": "link", "group": "link", "event": "link", "channel": "profile_url"}.get(type_name)
    return merge.link_key(values.get(key)) if key and values.get(key) else ""


def plan_table(table, store, profile, today, taken, linked):
    """Work out what one table would write. Returns (TableReport, operations). Writes nothing.

    `taken` holds the ids already promised to earlier rows, by note type.
    `linked` holds the People, Company and Group notes earlier rows will make, by (type, name).
    """
    type_name = table["type"]
    note_type = model.BY_NAME[type_name]
    headers, rows = read_csv(table["csv"])
    report = TableReport(os.path.basename(os.path.expanduser(table["csv"])), type_name)
    report.headers = headers
    columns = dict(table.get("columns") or {})
    title_column = table.get("title")
    if not title_column or title_column not in headers:
        raise ImportError_("%s: \"title\" must name the column that holds each row's name. Its columns are: %s."
                           % (report.label, ", ".join(headers)))
    report.missing_columns = [column for column in columns if column not in headers]
    if report.missing_columns:
        raise ImportError_("%s has no column named %s. Its columns are: %s." % (
            report.label, ", ".join('"%s"' % column for column in report.missing_columns), ", ".join(headers)))
    report.kept_columns = [header for header in headers if header != title_column and header not in columns]
    statuses = dict((str(word).strip().lower(), note_type.canonical_status(str(status)))
                    for word, status in (table.get("statuses") or {}).items())
    formats = list(table.get("date_formats") or [])
    yes_no = dict((str(word).strip().lower(), value) for word, value in (table.get("yes_no") or {}).items())
    heading = str(table.get("keep_heading") or DEFAULT_HEADING).strip().lstrip("#").strip() or DEFAULT_HEADING
    names = scoring.confidential_names(profile)
    existing = store.rows(type_name)
    known = dict((_existing_identity(type_name, row), row) for row in existing)
    known_links = dict((_link_of(type_name, row.values), row) for row in existing if _link_of(type_name, row.values))
    seen = {}
    operations = []

    def link_to(target, name, about=None):
        """A link to the note for this person, company or group, planning the note if it does not exist.

        `about` is what the row says about a person: their part (the column's name) and their company.
        """
        clean = scoring.scrub_confidential(name, names).strip()
        key = (target, scoring._text(clean))
        if key not in linked:
            match = [row for row in store.rows(target) if scoring._text(row.title) == key[1] or (
                target == "company" and merge.normalize_company(row.title) == merge.normalize_company(clean))]
            if match:
                linked[key] = match[0].title
            else:
                note_id = merge.next_id(model.BY_NAME[target], store.rows(target), taken.setdefault(target, []))
                taken[target].append(note_id)
                linked[key] = clean
                operations.append({"kind": "make", "type": target, "id": note_id, "title": clean,
                                   "values": dict(about or {})})
                if target == "person":
                    report.people += 1
                elif target == "company":
                    report.companies += 1
        return store.link(target, linked[key])

    for number, row in enumerate(rows, start=2):                      # row 1 is the header, as a spreadsheet shows it
        report.rows += 1
        title = row.get(title_column, "").strip()
        if not title:
            report.no_title += 1
            report.details.append("row %d: no %s, not imported" % (number, title_column))
            continue
        owner_words = " ".join([title] + [row.get(column, "") for column, target in columns.items()
                                          if target in ("company", "client", "via")])
        if any(scoring._contains_phrase(scoring._text(owner_words), scoring._text(secret)) for secret in names):
            report.confidential += 1                                  # counted, never written and never named
            continue
        values, kept = {}, []
        # A person named in a job or gig row works at that row's employer, in the part the column names.
        employer = next((row.get(column, "").strip() for column, target in columns.items() if target == "company"), "")
        employer = _names(employer)[0] if employer and type_name == "job" else ""
        for header in headers:
            cell = row.get(header, "")
            if header == title_column or not cell:
                continue
            target = columns.get(header)
            if target is None:
                kept.append((header, cell))
                continue
            field = note_type.field(target)
            if target == "status":
                status = note_type.canonical_status(cell) or statuses.get(cell.strip().lower())
                if status is None:
                    report.statuses[cell.strip()] = report.statuses.get(cell.strip(), 0) + 1
                    kept.append((header, cell))
                else:
                    values["status"] = status
                    if status.lower() != cell.strip().lower():
                        kept.append((header, cell))                   # their own word is kept beside the plugin's
            elif field.kind == model.DATE:
                day = read_date(cell, formats)
                if day is None:
                    report.dates.setdefault(header, []).append(cell)
                    kept.append((header, cell))
                else:
                    values[target] = day
            elif field.kind == model.NUMBER:
                value = read_number(cell)
                if value is None:
                    report.numbers[header] = report.numbers.get(header, 0) + 1
                    kept.append((header, cell))
                else:
                    values[target] = value
            elif field.kind == model.CHECKBOX:
                word = cell.strip().lower()
                if word in yes_no:
                    values[target] = yes_no[word]
                    kept.append((header, cell))                       # their own word is kept beside the tick
                elif word in YES or word in NO:
                    values[target] = word in YES
                else:
                    # Not plainly yes or no. It is never read as "no": the person says what it means.
                    words = report.yes_no.setdefault(header, {})
                    words[cell.strip()] = words.get(cell.strip(), 0) + 1
                    kept.append((header, cell))
            elif field.kind == model.URL:
                if cell.lower().startswith(("http://", "https://")):
                    values[target] = cell
                else:
                    kept.append((header, cell))
            elif field.kind in (model.LINK, model.LINKS):
                people = _names(cell)
                if target == "company":
                    values["_company"] = people[0]
                about = None
                if LINK_TARGETS[target] == "person" and target in ("contact", "warm_path"):
                    about = {"role": header}
                    if employer:
                        about["company"] = link_to("company", employer)
                links = [link_to(LINK_TARGETS[target], name, about) for name in people]
                values[target] = links if field.kind == model.LINKS else links[0]
                if field.kind == model.LINK and len(people) > 1:
                    kept.append((header, cell))                       # one link fits. The full cell is kept too
            else:
                values[target] = scoring.scrub_confidential(cell, names)
        identity = _identity(type_name, title, values)
        if identity in seen:
            report.repeats += 1
            report.details.append("row %d: the same as row %d, not imported twice" % (number, seen[identity]))
            continue
        seen[identity] = number
        company = values.pop("_company", None)
        match = known.get(identity) or known_links.get(_link_of(type_name, values))
        body = "\n".join("- **%s:** %s" % (header, scoring.scrub_confidential(cell, names).replace("\n", " / "))
                         for header, cell in kept)
        if match is not None:
            report.already += 1
            operations.append({"kind": "fill", "type": type_name, "id": match.id, "title": match.title,
                               "values": values})
            continue
        if type_name == "job":
            values.setdefault("role", scoring.scrub_confidential(title, names))
            note_title = merge._note_title("job", {"title": title, "company": company})
        elif type_name == "contract":
            note_title = merge._note_title("contract", {"title": title, "client": values.get("client"),
                                                        "via": values.get("via")})
        else:
            note_title = title
        note_id = merge.next_id(note_type, existing, taken.setdefault(type_name, []))
        taken[type_name].append(note_id)
        if type_name in ("person", "company", "group"):
            linked[(type_name, scoring._text(note_title))] = note_title   # a later row that names it links here
        report.new += 1
        operations.append({"kind": "new", "type": type_name, "id": note_id,
                           "title": scoring.scrub_confidential(note_title, names), "values": values,
                           "heading": heading, "body": body,
                           "section": "Imported on %s from row %d of %s." % (today.isoformat(), number, report.label)})
    return report, operations


def plan_import(plan, store, profile, today=None):
    """Everything the plan would do. Returns ([TableReport], operations). Writes nothing."""
    today = today or datetime.date.today()
    check_plan(plan)
    reports, operations, taken, linked = [], [], {}, {}
    # People, companies and groups first, so rows that name them link to the fuller note.
    order = sorted(range(len(plan["tables"])),
                   key=lambda index: 0 if plan["tables"][index]["type"] in ("company", "person", "group") else 1)
    for index in order:
        report, steps = plan_table(plan["tables"][index], store, profile, today, taken, linked)
        reports.append((index, report))
        operations += steps
    # A note that a row will write in full must not also be made as a bare name.
    full = set((step["type"], scoring._text(step["title"])) for step in operations if step["kind"] == "new")
    operations = [step for step in operations
                  if not (step["kind"] == "make" and (step["type"], scoring._text(step["title"])) in full)]
    return [report for _, report in sorted(reports, key=lambda pair: pair[0])], operations


def unmapped_statuses(reports):
    """How many words still need a meaning from the person: status words, and yes/no words. Zero means ready."""
    return sum(len(report.statuses) + sum(len(words) for words in report.yes_no.values()) for report in reports)


def report_text(reports, profile, wrote=False):
    """The report for the conversation: counts, column names, status words and unread dates. No row is named."""
    names = scoring.confidential_names(profile)
    lines = ["IMPORT DONE" if wrote else "IMPORT CHECK. Nothing has been written.", ""]
    for report in reports:
        lines.append("%s -> %s notes" % (report.label, report.type))
        lines.append("  Rows: %d. %s: %d. Already in the vault: %d. Not imported: %d." % (
            report.rows, "Written" if wrote else "Would be written", report.new, report.already, report.skipped()))
        if report.confidential:
            lines.append("    %d at a confidential name in your profile (counted only, never written)." % report.confidential)
        if report.no_title:
            lines.append("    %d with nothing in the title column." % report.no_title)
        if report.repeats:
            lines.append("    %d that repeat an earlier row of the same file." % report.repeats)
        if report.people or report.companies:
            lines.append("  Also %s: %d People notes and %d Company notes for the names in these rows." % (
                "made" if wrote else "would be made", report.people, report.companies))
        if report.kept_columns:
            lines.append("  Columns kept word for word in each note's body: %s." % ", ".join(report.kept_columns))
        for column, values in sorted(report.dates.items()):
            lines.append("  Dates in \"%s\" that could not be read (%d), kept as text in the note: %s%s. Add a "
                         "date format to the mapping to read them." % (
                             column, len(values), ", ".join('"%s"' % value for value in sorted(set(values))[:5]),
                             " and more" if len(set(values)) > 5 else ""))
        for column, count in sorted(report.numbers.items()):
            lines.append("  Numbers in \"%s\" that could not be read: %d. Kept as text in the note." % (column, count))
        for column, words in sorted(report.yes_no.items()):
            lines.append("  YES OR NO? \"%s\" is a tick box, and these words are neither (the import cannot run until "
                         "each is mapped under \"yes_no\" as true or false): %s." % (column, ", ".join(
                             '"%s" (%d)' % (word, count) for word, count in sorted(words.items()))))
        if report.statuses:
            lines.append("  STATUS WORDS WITH NO MEANING YET (the import cannot run until each is mapped): %s." % ", ".join(
                '"%s" (%d)' % (word, count) for word, count in sorted(report.statuses.items())))
            lines.append("    A %s note can be: %s." % (report.type, ", ".join(model.BY_NAME[report.type].status_names())))
        lines.append("")
    total = sum(report.rows for report in reports)
    accounted = sum(report.new + report.already + report.skipped() for report in reports)
    lines.append("Every row is accounted for: %d rows, %d written or already there or listed above as not imported."
                 % (total, accounted))
    return scoring.scrub_confidential("\n".join(lines), names, "[withheld]")


def detail_text(reports):
    """Row by row, for the private report file only. Never printed into the conversation."""
    lines = []
    for report in reports:
        lines.append("%s" % report.label)
        lines += ["  " + line for line in report.details] or [
            "  nothing to list: every row was imported, was already in the vault, or was counted as confidential"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def apply(operations, store):
    """Write what plan_import() planned. Returns the number of notes created."""
    created = 0
    for step in operations:
        if step["kind"] == "make":
            if store.get(step["type"], step["id"]) is None:
                store.upsert(step["type"], step["id"], step["title"], step.get("values") or {})
                created += 1
        elif step["kind"] == "fill":
            # Only properties the note does not have yet are filled. Nothing the person set is changed.
            if step["values"]:
                store.upsert(step["type"], step["id"], step["title"], step["values"])
        else:
            store.upsert(step["type"], step["id"], step["title"], step["values"], section=step["section"])
            if step["body"]:
                store.add_own_text(step["type"], step["id"], step["heading"], step["body"])
            created += 1
    return created
