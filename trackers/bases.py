"""The Obsidian Bases views and the Start here note.

Each `.base` file is built here as plain data and written as YAML by the
vault store. Views use core Bases only: no Dataview, no community plugins.

Design rules, from the views spec:
  * Every file opens on its "what to do now" view, never on a dump of all rows.
  * Eight columns or fewer. The first column is always the note itself.
  * Columns show plain names (Status, Fit) through display names, while the
    properties underneath stay prefixed.
  * Numbers and dates are real numbers and dates, so sorting a column never
    falls back to alphabetical order.
  * Statuses group in pipeline order through a numbered stage label, built
    from the status list in the model.

The filter and formula syntax here was checked against Obsidian 1.15.
Standard library only, Python 3.9+.
"""

import model

START_HERE_NAME = "Start here.md"

_DAYS = "((number(today()) - number(%s)) / 86400000).round()"


def _key(name):
    return model.PREFIX + name


def _quoted_list(names):
    return "[" + ", ".join("'%s'" % name for name in names) + "]"


def stage_formula(note_type):
    """Nested if() that maps each status to its numbered stage label."""
    last = note_type.statuses[-1].stage
    expression = "'%s'" % last
    for status in reversed(note_type.statuses):
        if status.stage == last and status.kind == "done":
            continue
        expression = "if(%s == '%s', '%s', %s)" % (_key("status"), status.name, status.stage, expression)
    return expression


def _display_names(note_type, title, formulas):
    names = {"file.name": {"displayName": title if "role" not in formulas else "Note"}}
    for field in note_type.fields:
        names["note." + field.key] = {"displayName": field.display}
    for name, display in formulas.items():
        names["formula." + name] = {"displayName": display}
    return names


def _columns(note_type, *names):
    """Column list for a view. Plain names are model properties; others pass through."""
    out = []
    for name in names:
        if name == "name":
            out.append("file.name")
        elif name.startswith("formula."):
            out.append(name)
        else:
            out.append(note_type.field(name).key)
    return out


def _sort(note_type, *pairs):
    out = []
    for name, direction in pairs:
        prop = name if name.startswith(("formula.", "file.")) else note_type.field(name).key
        out.append({"property": prop, "direction": direction})
    return out


_KIND_WIDTH = {model.NUMBER: 70, model.DATE: 120, model.CHECKBOX: 80, model.URL: 130, model.LINK: 150,
               model.LINKS: 190, model.TEXT: 150}
_FORMULA_WIDTH = {"formula.role": 200, "formula.pay_top_k": 110, "formula.work": 120,
                  "formula.days_since_touch": 110, "formula.days_waiting": 110, "formula.day_time": 110,
                  "formula.kind": 110, "formula.place": 180}
_FIELD_WIDTH = {"status": 120, "next_step": 200, "why": 260, "reason": 180, "focus": 190, "client": 180,
                "where": 180, "track": 110}


def _widths(order, displays):
    """Starting column widths, so the first columns fit a narrow pane. The user can drag them."""
    sizes = {}
    for prop in order:
        if prop == "file.name":
            sizes[prop] = 220
        elif prop.startswith("formula."):
            sizes[prop] = _FORMULA_WIDTH.get(prop, 130)
        else:
            name = prop[len(model.PREFIX):]
            field = displays.get(name)
            base = _FIELD_WIDTH.get(name, _KIND_WIDTH.get(field.kind if field else model.TEXT, 150))
            label = len(field.display) * 8 + 44 if field else 0
            sizes["note." + prop] = max(base, min(label, 170))
    return sizes


def _view(name, filters, order, sort=None, group=None, limit=None):
    view = {"type": "table", "name": name}
    if filters:
        view["filters"] = filters
    if group:
        view["groupBy"] = {"property": group, "direction": "ASC"}
    view["order"] = order
    if sort:
        view["sort"] = sort
    if limit:
        view["limit"] = limit
    fields = {}
    for note_type in model.TYPES:
        for field in note_type.fields:
            fields.setdefault(field.name, field)
    view["columnSize"] = _widths(order, fields)
    return view


def _in_folder(folder, note_type):
    return "file.inFolder('%s/%s')" % (folder.replace("'", "\\'"), note_type.folder)


def _is(note_type):
    return "%s == '%s'" % (_key("type"), note_type.name)


def jobs_base(folder):
    job = model.JOB
    status, done = _key("status"), _quoted_list(job.status_names("done"))
    not_active = _quoted_list(job.status_names("new") + job.status_names("done"))
    formulas = {
        # The note is named "Role, Company" so file names stay unique. Tables show the role alone,
        # still as a link to the note, because the company has its own column.
        "role": "if(!%(r)s.isEmpty(), file.asLink(%(r)s), file.asLink())" % {"r": _key("role")},
        "stage": stage_formula(job),
        "pay_top_k": "if(!%(k)s.isEmpty(), (%(k)s / 1000).round())" % {"k": _key("pay_max")},
        "pay_range": (
            "if(!%(lo)s.isEmpty() && !%(hi)s.isEmpty(), '$' + (%(lo)s / 1000).toFixed(0) + 'K to $' + "
            "(%(hi)s / 1000).toFixed(0) + 'K', if(!%(hi)s.isEmpty(), 'Up to $' + (%(hi)s / 1000).toFixed(0) + 'K', "
            "if(!%(lo)s.isEmpty(), 'From $' + (%(lo)s / 1000).toFixed(0) + 'K', 'Not listed')))"
            % {"lo": _key("pay_min"), "hi": _key("pay_max")}
        ),
        "work": "if(%(m)s == 'Hybrid' && !%(d)s.isEmpty(), 'Hybrid ' + %(d)s.toFixed(0) + ' mi', %(m)s)"
                % {"m": _key("work_mode"), "d": _key("distance_miles")},
        "days_since_touch": _DAYS % (
            "if(!%(t)s.isEmpty(), %(t)s, if(!%(a)s.isEmpty(), %(a)s, %(f)s))"
            % {"t": _key("last_touch"), "a": _key("applied_on"), "f": _key("first_seen")}),
        "days_waiting": "if(!%(a)s.isEmpty(), %(d)s)" % {"a": _key("applied_on"), "d": _DAYS % _key("applied_on")},
    }
    displays = {"role": "Role", "stage": "Stage", "pay_top_k": "Pay to ($K)", "pay_range": "Pay range", "work": "Work mode",
                "days_since_touch": "Days since last touch", "days_waiting": "Days waiting"}
    views = [
        _view("New this week",
              {"and": ["%s == 'New'" % status, "%s >= today() - '7d'" % _key("first_seen")]},
              _columns(job, "formula.role", "company", "fit", "formula.pay_top_k", "formula.work", "posted", "apply_url",
                       "warm_path"),
              _sort(job, ("fit", "DESC"))),
        _view("Pipeline",
              {"and": ["!%s.contains(%s)" % (not_active, status)]},
              _columns(job, "formula.role", "company", "status", "next_step", "next_step_date",
                       "formula.days_since_touch", "contact"),
              _sort(job, ("next_step_date", "ASC")), group="formula.stage"),
        _view("Follow up",
              {"or": [
                  {"and": ["%s == 'Applied'" % status, "%s <= today() - '10d'" % _key("applied_on"),
                           {"or": ["%s.isEmpty()" % _key("last_touch"),
                                   "%s <= %s" % (_key("last_touch"), _key("applied_on"))]}]},
                  {"and": ["!%s.contains(%s)" % (not_active, status),
                           "%s <= today() + '3d'" % _key("next_step_date")]},
              ]},
              _columns(job, "formula.role", "company", "status", "applied_on", "formula.days_waiting", "next_step_date",
                       "contact"),
              _sort(job, ("next_step_date", "ASC"))),
        _view("Decide or drop",
              {"or": [
                  {"and": ["%s == 'New'" % status, "%s < today() - '14d'" % _key("first_seen")]},
                  {"and": ["!%s.contains(%s)" % (done, status), "%s >= today()" % _key("closes"),
                           "%s <= today() + '7d'" % _key("closes")]},
              ]},
              _columns(job, "formula.role", "company", "fit", "formula.pay_top_k", "closes", "apply_url"),
              _sort(job, ("closes", "ASC"))),
        _view("Archive",
              {"and": ["%s.contains(%s)" % (done, status)]},
              _columns(job, "formula.role", "company", "status", "reason", "updated"),
              _sort(job, ("updated", "DESC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, job), _is(job)]},
        "formulas": formulas,
        "properties": _display_names(job, job.title_display, displays),
        "views": views,
    }


def contracts_base(folder):
    contract, channel = model.CONTRACT, model.CHANNEL
    status = _key("status")
    not_active = _quoted_list(contract.status_names("new") + contract.status_names("done"))
    formulas = {
        "stage": stage_formula(contract),
        "channel_stage": stage_formula(channel),
    }
    displays = {"stage": "Stage", "channel_stage": "Stage"}
    names = _display_names(contract, "Name", displays)
    for field in channel.fields:
        names.setdefault("note." + field.key, {"displayName": field.display})
    views = [
        _view("New gigs",
              {"and": [_is(contract), "%s == 'New'" % status, "%s >= today() - '7d'" % _key("first_seen")]},
              _columns(contract, "name", "client", "rate_max", "hours_per_week", "length_weeks", "remote", "link"),
              _sort(contract, ("fit", "DESC"))),
        _view("Pursuing",
              {"and": [_is(contract), "!%s.contains(%s)" % (not_active, status)]},
              _columns(contract, "name", "client", "status", "rate_max", "next_step", "next_step_date"),
              _sort(contract, ("next_step_date", "ASC")), group="formula.stage"),
        _view("Channels",
              {"and": [_is(channel)]},
              _columns(channel, "name", "status", "why", "profile_url", "last_checked"),
              _sort(channel, ("formula.channel_stage", "ASC"), ("value", "DESC"))),
    ]
    return {
        "filters": {"or": [
            {"and": [_in_folder(folder, contract), _is(contract)]},
            {"and": [_in_folder(folder, channel), _is(channel)]},
        ]},
        "formulas": formulas,
        "properties": names,
        "views": views,
    }


def events_base(folder):
    event = model.EVENT
    date = _key("date")
    formulas = {
        "day_time": "if(!%(t)s.isEmpty(), %(d)s.format('ddd') + ' ' + %(t)s, %(d)s.format('ddd'))"
                    % {"d": date, "t": _key("time")},
        "kind": "if(%s == true, 'Virtual', if(%s == true, 'Evening', 'Daytime'))" % (_key("virtual"), _key("evening")),
        "place": "if(%s == true, 'Virtual', %s)" % (_key("virtual"), _key("where")),
    }
    displays = {"day_time": "Day and time", "kind": "Evening or virtual", "place": "Where"}
    views = [
        _view("Next 30 days",
              {"and": ["%s >= today()" % date, "%s <= today() + '30d'" % date]},
              _columns(event, "name", "date", "formula.day_time", "formula.kind", "formula.place", "cost", "group",
                       "going"),
              _sort(event, ("date", "ASC"))),
        _view("Going",
              {"and": ["%s == true" % _key("going"), "%s >= today()" % date]},
              _columns(event, "name", "date", "formula.day_time", "formula.place", "prep"),
              _sort(event, ("date", "ASC"))),
        _view("Met, follow up",
              {"and": ["%s == true" % _key("attended"), "%s >= today() - '14d'" % date, "%s <= today()" % date,
                       "!%s.isEmpty()" % _key("people_met"), "%s.isEmpty()" % _key("follow_up_by")]},
              _columns(event, "name", "date", "people_met", "follow_up_by"),
              _sort(event, ("date", "DESC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, event), _is(event)]},
        "formulas": formulas,
        "properties": _display_names(event, event.title_display, displays),
        "views": views,
    }


def groups_base(folder):
    group = model.GROUP
    status = _key("status")
    out = _quoted_list(["Joined"] + group.status_names("done"))
    views = [
        _view("Groups to try",
              {"and": ["!%s.contains(%s)" % (out, status)]},
              _columns(group, "name", "focus", "cadence", "next_meeting", "virtual", "cost_per_year", "link"),
              _sort(group, ("fit", "DESC"))),
        _view("Joined",
              {"and": ["%s == 'Joined'" % status]},
              _columns(group, "name", "next_meeting", "last_attended"),
              _sort(group, ("next_meeting", "ASC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, group), _is(group)]},
        "properties": _display_names(group, group.title_display, {}),
        "views": views,
    }


def people_base(folder):
    person = model.PERSON
    rel = _key("relationship")
    formulas = {"closeness": "if(%s == '1st', 1, if(%s == '2nd', 2, 3))" % (rel, rel)}
    views = [
        _view("Warm paths",
              {"and": ["!%s.isEmpty()" % _key("linked_roles")]},
              _columns(person, "name", "company", "relationship", "linked_roles", "source_url", "confidence",
                       "last_contact"),
              _sort(person, ("formula.closeness", "ASC"), ("confidence", "DESC"))),
        _view("Follow-ups due",
              {"and": ["%s <= today() + '7d'" % _key("follow_up_by")]},
              _columns(person, "name", "company", "why", "follow_up_by", "last_contact"),
              _sort(person, ("follow_up_by", "ASC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, person), _is(person)]},
        "formulas": formulas,
        "properties": _display_names(person, person.title_display, {"closeness": "Closeness"}),
        "views": views,
    }


def companies_base(folder):
    company = model.COMPANY
    views = [
        _view("Companies with openings",
              {"and": ["%s >= 1" % _key("open_roles")]},
              _columns(company, "name", "open_roles", "work_policy", "hiring_system", "people_known"),
              _sort(company, ("open_roles", "DESC"))),
        _view("All companies", None,
              _columns(company, "name", "open_roles", "work_policy", "hiring_system", "people_known"),
              _sort(company, ("file.name", "ASC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, company), _is(company)]},
        "properties": _display_names(company, company.title_display, {}),
        "views": views,
    }


def digests_base(folder):
    digest = model.DIGEST
    views = [
        _view("Digests", None, _columns(digest, "name", "date", "track", "new", "closed"),
              _sort(digest, ("date", "DESC"))),
    ]
    return {
        "filters": {"and": [_in_folder(folder, digest), _is(digest)]},
        "properties": _display_names(digest, digest.title_display, {}),
        "views": views,
    }


def build_all(folder):
    """Every view file, as {file name: content}."""
    return {
        "Jobs.base": jobs_base(folder),
        "Contracts.base": contracts_base(folder),
        "Events.base": events_base(folder),
        "Groups.base": groups_base(folder),
        "People.base": people_base(folder),
        "Companies.base": companies_base(folder),
        "Digests.base": digests_base(folder),
    }


def start_here(folder):
    """The landing note: three questions, each answered by an embedded view."""
    def embed(base, view):
        return "![[%s/%s#%s]]" % (folder, base, view)

    return "\n".join([
        "This folder is your tracker. Sweeps add notes here, and the tables below update by themselves. "
        "Before your first sweep the tables are empty, and that is expected.",
        "",
        "## What is new?",
        "",
        "Jobs found in the last 7 days that you have not looked at yet, best fit first. "
        "Empty means nothing new this week.",
        "",
        embed("Jobs.base", "New this week"),
        "",
        "Contract work found in the last 7 days.",
        "",
        embed("Contracts.base", "New gigs"),
        "",
        "## What do I owe someone?",
        "",
        "Applications that have gone 10 days without a reply, and next steps due within 3 days. "
        "Empty means you are caught up.",
        "",
        embed("Jobs.base", "Follow up"),
        "",
        "People to get back to this week.",
        "",
        embed("People.base", "Follow-ups due"),
        "",
        "## What is coming up?",
        "",
        "Events in the next 30 days. Tick Going? on the ones you plan to attend.",
        "",
        embed("Events.base", "Next 30 days"),
        "",
        "## How to use the tables",
        "",
        "- **Change Status right in a table.** Click the cell and type, for example `Applied` or `Passed`. "
        "The row moves to the right group at once, and the next sweep respects your change.",
        "- Job statuses, in order: " + ", ".join(model.JOB.status_names()) + ".",
        "- Contract statuses, in order: " + ", ".join(model.CONTRACT.status_names()) + ".",
        "- Fill in Next step, Next step date and Applied on as you go. Sweeps never overwrite them.",
        "- Write anything you like in a note. Sweeps only rewrite the properties that start with `wd_` "
        "and the block between the two `workdecero` markers.",
        "",
        "## Everything else",
        "",
        "- [[%s/Jobs.base|Jobs]]: New this week, Pipeline, Follow up, Decide or drop, Archive" % folder,
        "- [[%s/Contracts.base|Contracts]]: New gigs, Pursuing, Channels" % folder,
        "- [[%s/Events.base|Events]]: Next 30 days, Going, Met and follow up" % folder,
        "- [[%s/Groups.base|Groups]]: Groups to try, Joined" % folder,
        "- [[%s/People.base|People]]: Warm paths, Follow-ups due" % folder,
        "- [[%s/Companies.base|Companies]]: Companies with openings" % folder,
        "- [[%s/Digests.base|Digests]]: what each sweep found" % folder,
        "",
    ])
