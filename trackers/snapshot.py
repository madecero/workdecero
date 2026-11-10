"""An optional read-only spreadsheet of the trackers, rebuilt at the end of every sweep.

Some people like a grid to glance at. This writes one, and nothing more: the
notes in the vault stay the only place anything is kept. The file is written
fresh each time and never read back, so an edit made in it changes nothing
and is gone after the next sweep. The banner on every sheet says so.

    File     <vault>/<tracker folder>/workdecero snapshot.xlsx
    Sheets   Summary, then Jobs, Contracts, Channels, Groups, Events, People
    Columns  about ten a sheet: what the main view shows, plus the next step
    Rows     in the order of the default views, filled by stage
    Links    the first cell of each row opens its note in Obsidian

Only when storage.spreadsheet_snapshot is true in the profile. Off by default.

The workbook is plain Office Open XML written with the standard library: a
zip of a few small XML files. No spreadsheet library is needed.

Standard library only, Python 3.9+.
"""

import datetime
import os
import sys
import urllib.parse
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

import merge  # noqa: E402
import model  # noqa: E402
import scoring  # noqa: E402

FILE_NAME = "workdecero snapshot.xlsx"
SHEETS = (("job", "Jobs"), ("contract", "Contracts"), ("channel", "Channels"), ("group", "Groups"),
          ("event", "Events"), ("person", "People"))
BANNER = "Read-only snapshot from %s. Edit in Obsidian; this file is rebuilt every sweep."
# One soft fill per stage, in pipeline order. A status nobody recognises gets no fill.
STAGE_FILLS = ("FFE8F1FB", "FFE6F4EA", "FFFFF4D6", "FFFDE9D9", "FFEFE6F7", "FFE2F0EE", "FFEDEDED")
HEADER_FILL, BANNER_FILL = "FFD9D9D9", "FFFFF2CC"
# About ten columns a sheet: what the main view shows, plus the next step where there is one. Ids, check
# dates and sources are left out. The link in the first cell leads to everything else.
COLUMNS = {
    "job": ("status", "fit", "pay_min", "pay_max", "company", "work_mode", "approach", "near_reason", "closes",
            "next_step", "next_step_date"),
    "contract": ("status", "fit", "rate_min", "rate_max", "client", "via", "engagement", "closes", "next_step",
                 "next_step_date"),
    "channel": ("status", "why", "profile_url", "last_checked"),
    "group": ("status", "fit", "focus", "cadence", "next_meeting", "next_time", "next_where", "members",
              "cost_per_year"),
    "event": ("status", "date", "time", "where", "cost", "group", "going", "follow_up_by"),
    "person": ("company", "role", "relationship", "met", "follow_up_by", "last_contact"),
}
MAX_WIDTH, MIN_WIDTH = 60, 8
_EPOCH = (1980, 1, 1, 0, 0, 0)          # a fixed time inside the zip, so the same notes give the same bytes


def wanted(profile):
    return (profile.get("storage") or {}).get("spreadsheet_snapshot") is True


def path_for(store):
    return os.path.join(store.root, FILE_NAME)


# --------------------------------------------------------------------------
# What goes in the cells
# --------------------------------------------------------------------------

def _stage_number(note_type, status):
    """1, 2, 3... for the stage a status belongs to, or None for a note type without statuses or an unknown status."""
    name = note_type.canonical_status(status) if note_type.statuses else None
    if name is None:
        return None
    stage = note_type.stage_of(name) or ""
    return int(stage.split(" ", 1)[0]) if stage[:1].isdigit() else None


def _plain(value, names):
    """A cell value: a number stays a number, everything else becomes scrubbed text."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_plain(item, names)) for item in value if item not in (None, ""))
    text = str(value)
    if text.startswith("[["):
        text = merge.link_label(text)
    return scoring.scrub_confidential(text, names)


def _sorted_rows(type_name, rows):
    note_type = model.BY_NAME[type_name]

    def fit(row):
        value = row.values.get("fit")
        return -value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    if type_name in ("job", "contract", "group"):
        return sorted(rows, key=lambda row: (_stage_number(note_type, row.values.get("status")) or 0, fit(row),
                                             row.title.lower()))
    if type_name == "channel":
        return sorted(rows, key=lambda row: (_stage_number(note_type, row.values.get("status")) or 0, row.title.lower()))
    if type_name == "event":
        return sorted(rows, key=lambda row: (str(row.values.get("date") or "9999"), row.title.lower()))
    closeness = {"1st": 0, "2nd": 1}
    return sorted(rows, key=lambda row: (closeness.get(str(row.values.get("relationship") or "").lower(), 2),
                                         row.title.lower()))


def note_link(store, row):
    """An obsidian:// address that opens the note."""
    return "obsidian://open?path=" + urllib.parse.quote(os.path.join(store.vault, *row.path.split("/")), safe="")


def sheet_data(store, type_name, names):
    """(headers, rows) for one sheet. Each row is (cells, link for the first cell, stage number or None)."""
    note_type = model.BY_NAME[type_name]
    fields = [note_type.field(name) for name in COLUMNS[type_name] if note_type.has_field(name)]
    headers = [note_type.title_display] + [field.display for field in fields]
    data = []
    for row in _sorted_rows(type_name, store.rows(type_name)):
        cells = [scoring.scrub_confidential(row.title, names)] + [_plain(row.values.get(field.name), names)
                                                                  for field in fields]
        data.append((cells, note_link(store, row), _stage_number(note_type, row.values.get("status"))))
    return headers, data


def summary_data(store):
    """Counts by status for every tracker that has statuses, in pipeline order."""
    lines = []
    for type_name, label in SHEETS:
        note_type = model.BY_NAME[type_name]
        rows = store.rows(type_name)
        if not note_type.statuses:
            lines.append((label, "All", len(rows), None))
            continue
        counts = {}
        for row in rows:
            name = note_type.canonical_status(row.values.get("status")) or "Other"
            counts[name] = counts.get(name, 0) + 1
        for status in note_type.statuses:
            if counts.get(status.name):
                lines.append((label, status.name, counts[status.name], _stage_number(note_type, status.name)))
        if counts.get("Other"):
            lines.append((label, "Other", counts["Other"], None))
        if not rows:
            lines.append((label, "None yet", 0, None))
    return lines


# --------------------------------------------------------------------------
# The workbook, as XML
# --------------------------------------------------------------------------

def _column(index):
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _clean(text):
    """Text that is safe inside XML: no control characters."""
    return "".join(ch for ch in str(text) if ch in "\t\n" or ord(ch) >= 32)


def _cell(ref, value, style):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(value) if isinstance(value, float) else value)
    if value == "":
        return '<c r="%s" s="%d"/>' % (ref, style)
    return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style, escape(_clean(value)))


# Style numbers. 0 plain, 1 header, 2 banner, 3 link. Then two per stage: filled, and filled link.
def _fill_style(stage, link=False):
    if stage is None or not (1 <= stage <= len(STAGE_FILLS)):
        return 3 if link else 0
    return 4 + (stage - 1) * 2 + (1 if link else 0)


def styles_xml():
    fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
    for colour in (HEADER_FILL, BANNER_FILL) + STAGE_FILLS:
        fills.append('<fill><patternFill patternType="solid"><fgColor rgb="%s"/><bgColor indexed="64"/></patternFill>'
                     '</fill>' % colour)
    fonts = ('<font><sz val="11"/><name val="Calibri"/></font>'
             '<font><b/><sz val="11"/><name val="Calibri"/></font>'
             '<font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font>'
             '<font><i/><sz val="11"/><name val="Calibri"/></font>')
    xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
           '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>',
           '<xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>',
           '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>']
    for index in range(len(STAGE_FILLS)):
        xfs.append('<xf numFmtId="0" fontId="0" fillId="%d" borderId="0" xfId="0" applyFill="1"/>' % (4 + index))
        xfs.append('<xf numFmtId="0" fontId="2" fillId="%d" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
                   % (4 + index))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="4">%s</fonts><fills count="%d">%s</fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="%d">%s</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>') % (fonts, len(fills), "".join(fills), len(xfs), "".join(xfs))


def sheet_xml(banner, headers, rows):
    """One worksheet. Returns (xml, [links in row order]). Row 1 is the banner, row 2 the headers."""
    widths = [len(str(header)) for header in headers]
    for cells, _, _ in rows:
        for index, value in enumerate(cells):
            widths[index] = max(widths[index], len(str(value)))
    widths = [max(MIN_WIDTH, min(MAX_WIDTH, width + 2)) for width in widths]
    if sum(widths) < len(banner) + 2:
        widths[-1] += len(banner) + 2 - sum(widths)      # room for the banner on a narrow sheet
    last = _column(len(headers) - 1)
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><cols>']
    for index, width in enumerate(widths, start=1):
        out.append('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (index, index, width))
    out.append('</cols><sheetData>')
    out.append('<row r="1">%s</row>' % "".join(
        _cell("%s1" % _column(index), banner if index == 0 else "", 2) for index in range(len(headers))))
    out.append('<row r="2">%s</row>' % "".join(
        _cell("%s2" % _column(index), header, 1) for index, header in enumerate(headers)))
    links = []
    for number, (cells, link, stage) in enumerate(rows, start=3):
        parts = []
        for index, value in enumerate(cells):
            parts.append(_cell("%s%d" % (_column(index), number), value,
                               _fill_style(stage, link=bool(link) and index == 0)))
        out.append('<row r="%d">%s</row>' % (number, "".join(parts)))
        if link:
            links.append((number, link))
    out.append('</sheetData>')
    if len(headers) > 1:
        out.append('<mergeCells count="1"><mergeCell ref="A1:%s1"/></mergeCells>' % last)
    if links:
        out.append('<hyperlinks>%s</hyperlinks>' % "".join(
            '<hyperlink ref="A%d" r:id="rId%d"/>' % (number, index) for index, (number, _) in enumerate(links, start=1)))
    out.append('</worksheet>')
    return "".join(out), [link for _, link in links]


def _sheet_rels(links):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
            % "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      'relationships/hyperlink" Target="%s" TargetMode="External"/>' % (index, escape(link, {'"': "&quot;"}))
                      for index, link in enumerate(links, start=1)))


def build(store, profile, today=None):
    """Every file of the workbook, as {name inside the zip: text}."""
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    banner = BANNER % today.isoformat()
    sheets = []
    summary_rows = [([label, status, count], None, stage) for label, status, count, stage in summary_data(store)]
    sheets.append(("Summary", sheet_xml(banner, ["Tracker", "Status", "Notes"], summary_rows)))
    for type_name, label in SHEETS:
        headers, rows = sheet_data(store, type_name, names)
        sheets.append((label, sheet_xml(banner, headers, rows)))

    files = {}
    overrides, workbook_sheets, workbook_rels = [], [], []
    for index, (label, (xml, links)) in enumerate(sheets, start=1):
        files["xl/worksheets/sheet%d.xml" % index] = xml
        if links:
            files["xl/worksheets/_rels/sheet%d.xml.rels" % index] = _sheet_rels(links)
        overrides.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-'
                         'officedocument.spreadsheetml.worksheet+xml"/>' % index)
        workbook_sheets.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(label), index, index))
        workbook_rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             'relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (index, index))
    style_id = len(sheets) + 1
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.styles+xml"/>%s</Types>' % "".join(overrides))
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    files["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>%s</sheets></workbook>' % "".join(workbook_sheets))
    files["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
        '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>' % ("".join(workbook_rels), style_id))
    files["xl/styles.xml"] = styles_xml()
    return files


def write(store, profile, today=None):
    """Rebuild the snapshot in one step, readable by its owner alone. Returns its path."""
    target = path_for(store)
    temp = target + ".tmp"
    files = build(store, profile, today)
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as raw:
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ["[Content_Types].xml", "_rels/.rels"] + sorted(
                    key for key in files if key not in ("[Content_Types].xml", "_rels/.rels")):
                info = zipfile.ZipInfo(name, _EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, files[name].encode("utf-8"))
        raw.flush()
        os.fsync(raw.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, target)
    return target


def refresh(store, profile, today=None):
    """What every sweep calls at the end. Does nothing unless the profile asks for the snapshot.

    Returns a line to add to the sweep's report, or None. A failure to write
    the snapshot never fails the sweep: the notes are what matter.
    """
    if not wanted(profile):
        return None
    try:
        write(store, profile, today)
    except (OSError, ValueError) as exc:
        return "The spreadsheet snapshot could not be written (%s). Your notes are unaffected." % exc.__class__.__name__
    return "Spreadsheet snapshot rebuilt: %s, in the tracker folder. It is read-only: edit in Obsidian." % FILE_NAME
