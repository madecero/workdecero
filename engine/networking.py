"""The networking sweep, engine side.

Local networking depends on place more than any other sweep, so it runs off
the person's local map and the "who I want to meet" part of their profile.

    start    tidy by date (events that have passed), list what needs a
             second look, and tell research where to look and who the person
             wants to meet
    intake   take what research found, drop what is inactive or ruled out,
             rank by who the person wants to meet, and write the notes
    finish   write the digest note (follow-ups first) and report
    update   record what the person did: "I joined X", "registered for E-12",
             "went to E-4 and met Jane and Raj"
    prep     write a one-page brief for an event into its note

Two kinds of note, kept apart on purpose:

    Groups   anything that meets again and again. One note per group, with
             the date of its next meeting. A regular monthly meeting never
             gets an Events note of its own.
    Events   dated one-offs worth their own note: a conference, a session
             with a speaker squarely in the person's field, an open call for
             speakers.

A group is only kept if it is alive: a meeting in the last six months, or
one on the calendar. One that has gone quiet is marked Inactive with the
evidence, unless the person has joined it, in which case they are told.

What research is never told: the most the person will pay for an event or
for a year's membership, their exclusions, confidential names, current
employer, or who they know. It reports what a page says. Cost flags and
leave-outs are decided here.

Research only. Nothing here registers, RSVPs, joins, pays or sends anything.

Standard library only, Python 3.9+.
"""

import datetime
import json
import os

import merge
import model
import scoring

BUDGET = {
    "lite": {"searches": 10, "read": 12},
    "full": {"searches": 30, "read": 30},
}
STATE_NAME = "networking-state.json"
ACTIVE_WITHIN_DAYS = 183        # a group with nothing in six months and nothing scheduled is not alive
FOLLOW_UP_DAYS = 7
FOLLOW_UP_AFTER_EVENT_DAYS = 3
PLACE_KINDS = ("meetup", "user-group", "association", "chamber", "conference")
PLACE_WORDS = {"meetup": "Meetups", "user-group": "User groups", "association": "Associations",
               "chamber": "Chambers", "conference": "Conferences"}
GROUP_WEIGHTS = {"who": 40, "fit": 30, "active": 15, "place": 15}
PENALTY_COST = 5
PENALTY_TIME = 5

GROUP_KEYS = ("name", "url", "focus", "cadence", "next_meeting", "last_active", "virtual", "where", "cost_per_year",
              "audience", "fit_assessment", "why", "takes_speakers", "speaking_angle", "conflicts", "in_area")
EVENT_KEYS = ("title", "url", "date", "time", "evening", "virtual", "where", "cost", "group", "special",
              "audience", "fit_assessment", "why", "conflicts", "in_area", "speaking_angle")


def budget(profile):
    return BUDGET.get(profile.get("mode") or "lite", BUDGET["lite"])


def _safe(text, profile):
    """Text for the conversation, with every confidential name taken out."""
    return scoring.scrub_confidential(text, scoring.confidential_names(profile), "[withheld]")


def _net(profile):
    return profile.get("networking") or {}


def brief(profile):
    """What research may know about the person: their field and who they want to meet. Never a spending limit."""
    person, skills, net = profile.get("person") or {}, profile.get("skills") or {}, _net(profile)
    location = profile.get("location") or {}
    lines = [
        "Field: %s" % person.get("headline", ""),
        "Strongest skills: %s" % ", ".join(skills.get("core") or []),
    ]
    if net.get("goals"):
        lines.append("What they want from networking: %s" % "; ".join(net["goals"]))
    lines.append("WHO THEY WANT TO MEET, most important first (copy these words exactly into `audience`):")
    for index, who in enumerate(net.get("who_to_meet") or [], start=1):
        lines.append("  %d. %s" % (index, who))
    if not net.get("who_to_meet"):
        lines.append("  (not given: use people in their field)")
    if net.get("topics"):
        lines.append("Topics they care about or could speak on: %s" % "; ".join(net["topics"]))
    areas = net.get("areas") or [location.get("home_base", "")]
    lines.append("Areas for an ordinary event: %s" % ", ".join(area for area in areas if area))
    if net.get("stretch_areas"):
        lines.append("Would travel further for something bigger (a conference): %s" % ", ".join(net["stretch_areas"]))
    lines.append("Kinds of events they attend: %s" % ", ".join(net.get("formats") or ["in-person", "virtual"]))
    if net.get("skip"):
        lines.append("Leave out: %s" % "; ".join(net["skip"]))
    if net.get("unavailable"):
        lines.append("Times they cannot do, in their words (set `conflicts` to true when a meeting falls in one): %s"
                     % "; ".join(net["unavailable"]))
    if net.get("memberships"):
        lines.append("Already a member of (do not suggest again): %s" % ", ".join(net["memberships"]))
    speaking = net.get("speaking") or {}
    lines.append("Interested in speaking: %s" % ("yes" if speaking.get("interested") else "no"))
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# State kept between the steps of one sweep. Private to the helper.
# --------------------------------------------------------------------------

def state_path(folder):
    return os.path.join(folder, STATE_NAME)


def save_state(folder, state):
    handle = os.open(state_path(folder), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(state, out, ensure_ascii=False)
    os.chmod(state_path(folder), 0o600)


def load_state(folder):
    path = state_path(folder)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def clear_state(folder):
    path = state_path(folder)
    if os.path.isfile(path) or os.path.islink(path):
        os.remove(path)
    if os.path.isdir(folder) and not os.path.islink(folder) and not os.listdir(folder):
        os.rmdir(folder)


# --------------------------------------------------------------------------
# Housekeeping by date
# --------------------------------------------------------------------------

def _event_status(row):
    return model.EVENT.canonical_status(row.values.get("status")) or "New"


def _committed(row):
    """The person registered, agreed to speak, or ticked Going? themselves."""
    return _event_status(row) in ("Registered", "Speaking") or row.values.get("going") is True


def housekeeping(store, today):
    """Tidy events whose date has passed. Returns (titles marked Did not go, titles to ask "did you go?").

    An event the person never committed to is marked Did not go. One they
    registered for, were speaking at or ticked Going? on is never changed by
    a script: only they know whether they went, so the digest asks.
    """
    marked, ask = [], []
    for row in store.rows("event"):
        when = scoring._date(row.values.get("date"))
        if when is None or when >= today:
            continue
        status = _event_status(row)
        if status in ("Went", "Did not go", "Passed") or row.values.get("attended") is True:
            continue
        if _committed(row):
            ask.append(row.title)
            continue
        store.apply([{"type": "event", "id": row.id, "values": {"status": "Did not go"}, "force": ["status"],
                      "append": "%s: the date passed with no sign you planned to go, so it was marked Did not go."
                                % today.isoformat()}])
        marked.append(row.title)
    return marked, ask


def groups_to_recheck(store, today):
    """Open groups whose stored next meeting has passed or was never known: [(id, title, link)]."""
    due = []
    for row in store.rows("group"):
        if model.GROUP.is_done(row.values.get("status")):
            continue
        when = scoring._date(row.values.get("next_meeting"))
        if when is None or when < today:
            due.append((row.id, row.title, row.values.get("link") or ""))
    return due


def follow_ups(store, today):
    """What the person owes someone: overdue first, then due this week. Returns (overdue lines, due lines)."""
    overdue, due = [], []
    for row in store.rows("person"):
        when = scoring._date(row.values.get("follow_up_by"))
        if when is None:
            continue
        link = store.link("person", row.title)
        where = row.values.get("met")
        about = " (met at %s)" % where if where else ""
        if when < today:
            overdue.append((when, "%s%s: follow-up was due %s." % (link, about, when.isoformat())))
        elif (when - today).days <= FOLLOW_UP_DAYS:
            due.append((when, "%s%s: follow up by %s." % (link, about, when.isoformat())))
    for row in store.rows("event"):
        when = scoring._date(row.values.get("follow_up_by"))
        if when is None or row.values.get("attended") is not True:
            continue
        link = store.link("event", row.title)
        if when < today:
            overdue.append((when, "%s: follow up with the people you met. It was due %s." % (link, when.isoformat())))
        elif (when - today).days <= FOLLOW_UP_DAYS:
            due.append((when, "%s: follow up with the people you met by %s." % (link, when.isoformat())))
    return ([text for _, text in sorted(overdue, key=lambda pair: pair[0])],
            [text for _, text in sorted(due, key=lambda pair: pair[0])])


# --------------------------------------------------------------------------
# Step 1: start
# --------------------------------------------------------------------------

def places(source_map):
    found = []
    for kind in PLACE_KINDS:
        for entry in source_map.get("networking") or []:
            if entry.get("kind") == kind and entry.get("how") != "browser":
                found.append((kind, entry["name"], entry.get("url") or ""))
    return found


def start(profile, store, source_map, today=None):
    today = today or datetime.date.today()
    marked, ask = housekeeping(store, today)
    recheck = groups_to_recheck(store, today)
    known = [row.title for row in store.rows("group")]
    browser_only = [entry["name"] for entry in source_map.get("networking") or [] if entry.get("how") == "browser"]
    return {
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "day": today.isoformat(),
        "mode": profile.get("mode") or "lite",
        "did_not_go": marked, "did_you_go": ask,
        "recheck": [list(entry) for entry in recheck],
        "known_groups": known,
        "places": [list(place) for place in places(source_map)],
        "not_covered": browser_only,
        "new_groups": [], "new_events": [], "refreshed": [], "inactive": [], "told": [],
        "left_out": 0, "folded": 0, "searches": 0, "finds_read": 0,
    }


def start_report(state, profile):
    """What research is told after start. Checked to carry nothing private."""
    limits = budget(profile)
    lines = ["NETWORKING SWEEP STARTED (%s mode)" % state["mode"], ""]
    lines.append("ALREADY DONE BY THE ENGINE")
    lines.append("  Past events tidied: %d marked Did not go. %d the person will be asked about."
                 % (len(state["did_not_go"]), len(state["did_you_go"])))
    lines += ["", "ABOUT THE PERSON"]
    lines += ["  " + line for line in brief(profile).splitlines()]
    lines += ["", "YOUR BUDGET THIS RUN: use at most %d web searches, and read up to %d pages." % (
        limits["searches"], limits["read"])]
    lines += ["", "GROUPS TO LOOK AT AGAIN (%d): find each one's next meeting and its most recent one" % len(state["recheck"])]
    for note_id, title, link in state["recheck"]:
        lines.append("  %s | %s | %s" % (note_id, title, link or "no link on file"))
    if not state["recheck"]:
        lines.append("  none")
    lines += ["", "PLACES TO LOOK, from their local map"]
    shown = None
    for kind, name, url in state["places"]:
        if kind != shown:
            lines.append("  %s" % PLACE_WORDS[kind])
            shown = kind
        lines.append("    %s: %s" % (name, url))
    if not state["places"]:
        lines.append("  The local map has no groups yet. Search for meetups, associations and the chamber in their area.")
    known = [title for title in state["known_groups"] if title not in [entry[1] for entry in state["recheck"]]]
    if known:
        lines += ["", "ALREADY IN THE TRACKER (no need to send again unless something changed): %s" % ", ".join(known)]
    if state["not_covered"]:
        lines += ["", "NOT COVERED WITHOUT A BROWSER (name these in your final message): %s"
                  % ", ".join(state["not_covered"])]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

class Rank(object):
    def __init__(self):
        self.parts = {}
        self.penalties = []
        self.caveats = []
        self.dropped = None
        self.cost_flag = None

    @property
    def total(self):
        return max(0, min(100, sum(self.parts.values()) - sum(points for points, _ in self.penalties)))

    def breakdown(self):
        labels = {"who": "who you want to meet", "fit": "fit", "active": "activity", "place": "place and format"}
        bits = ["%s %d/%d" % (labels[key], self.parts.get(key, 0), GROUP_WEIGHTS[key]) for key in ("who", "fit", "active", "place")]
        bits += ["minus %d (%s)" % (points, why) for points, why in self.penalties]
        return ", ".join(bits)


def _who_points(item, profile, rank):
    wanted = [scoring._text(who) for who in _net(profile).get("who_to_meet") or []]
    given = item.get("audience")
    audience = [scoring._text(who) for who in (given if isinstance(given, list) else [given] if given else [])]
    places_ = [wanted.index(who) for who in audience if who in wanted]
    if not wanted:
        return GROUP_WEIGHTS["who"] // 2
    if not places_:
        rank.caveats.append("Not clearly a place to meet the people you named")
        return 5
    return max(16, GROUP_WEIGHTS["who"] - 8 * min(places_))


def _place_points(item, profile, rank):
    formats = _net(profile).get("formats") or ["in-person", "virtual"]
    virtual = item.get("virtual") is True
    if virtual and "virtual" not in formats:
        rank.dropped = "it is virtual, and your profile takes in-person events only"
        return 0
    if not virtual and "in-person" not in formats:
        rank.dropped = "it is in person, and your profile takes virtual events only"
        return 0
    if virtual:
        return 12
    area = item.get("in_area")
    if area in (True, "area", None):
        return GROUP_WEIGHTS["place"]
    if area == "stretch":
        rank.caveats.append("Outside your usual area. Worth it only for something bigger")
        return 6
    rank.dropped = "it is outside the areas in your profile"
    return 0


def _skipped(item, profile):
    text = scoring._text(" ".join(str(item.get(key) or "") for key in ("name", "title", "focus", "why")))
    for phrase in _net(profile).get("skip") or []:
        if scoring._contains_phrase(text, scoring._text(phrase)):
            return "it is a kind of group or event your profile leaves out"
    return None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def rank_group(item, profile, today):
    rank = Rank()
    names = scoring.confidential_names(profile)
    if any(scoring._contains_phrase(scoring._text(item.get("name")), scoring._text(name)) for name in names):
        rank.dropped = "its name is one your profile keeps confidential"
        return rank
    exclusion = scoring.match_exclusion({"title": item.get("name"), "industry": item.get("focus")}, item.get("name"),
                                        scoring.active_exclusions(profile, today))
    if exclusion is not None and exclusion.get("action", "skip") == "skip":
        rank.dropped = "excluded by your profile"
        return rank
    rank.dropped = _skipped(item, profile)
    if rank.dropped:
        return rank
    if scoring._text(item.get("name")) in [scoring._text(name) for name in _net(profile).get("memberships") or []]:
        rank.dropped = "you are already a member"
        return rank
    upcoming, last = scoring._date(item.get("next_meeting")), scoring._date(item.get("last_active"))
    if upcoming is not None and upcoming < today:
        last = max(last, upcoming) if last else upcoming
        upcoming = None
    if upcoming is None and (last is None or (today - last).days > ACTIVE_WITHIN_DAYS):
        rank.dropped = "no meeting in the last six months and none scheduled"
        return rank
    if upcoming is not None and (upcoming - today).days <= 30:
        rank.parts["active"] = GROUP_WEIGHTS["active"]
    elif upcoming is not None:
        rank.parts["active"] = 10
    else:
        rank.parts["active"] = 5
        rank.caveats.append("No next meeting is on its calendar yet")
    rank.parts["place"] = _place_points(item, profile, rank)
    if rank.dropped:
        return rank
    rank.parts["who"] = _who_points(item, profile, rank)
    fit = _number(item.get("fit_assessment"))
    rank.parts["fit"] = int(max(0, min(GROUP_WEIGHTS["fit"], fit))) if fit is not None else GROUP_WEIGHTS["fit"] // 2
    cost, cap = _number(item.get("cost_per_year")), _number(_net(profile).get("max_cost_per_year"))
    if cost is not None and cap is not None and cost > cap:
        rank.penalties.append((PENALTY_COST, "costs more than you set"))
        rank.cost_flag = "Costs $%s a year, above the $%s a year you set" % (format(int(cost), ","), format(int(cap), ","))
        rank.caveats.append(rank.cost_flag)
    if item.get("conflicts") is True:
        rank.penalties.append((PENALTY_TIME, "meets at a time you cannot do"))
        rank.caveats.append("It usually meets at a time you said you cannot do")
    return rank


def rank_event(item, profile, today):
    rank = Rank()
    names = scoring.confidential_names(profile)
    if any(scoring._contains_phrase(scoring._text(item.get(key)), scoring._text(name))
           for name in names for key in ("title", "group") if item.get(key)):
        rank.dropped = "its title names one your profile keeps confidential"
        return rank
    exclusion = scoring.match_exclusion({"title": item.get("title")}, item.get("group"),
                                        scoring.active_exclusions(profile, today))
    if exclusion is not None and exclusion.get("action", "skip") == "skip":
        rank.dropped = "excluded by your profile"
        return rank
    rank.dropped = _skipped(item, profile)
    if rank.dropped:
        return rank
    when = scoring._date(item.get("date"))
    if when is None:
        rank.dropped = "no date"
        return rank
    if when < today:
        rank.dropped = "it has already happened"
        return rank
    days = (when - today).days
    rank.parts["active"] = GROUP_WEIGHTS["active"] if days <= 30 else 10 if days <= 90 else 6
    rank.parts["place"] = _place_points(item, profile, rank)
    if rank.dropped:
        return rank
    rank.parts["who"] = _who_points(item, profile, rank)
    fit = _number(item.get("fit_assessment"))
    rank.parts["fit"] = int(max(0, min(GROUP_WEIGHTS["fit"], fit))) if fit is not None else GROUP_WEIGHTS["fit"] // 2
    cost, cap = _number(item.get("cost")), _number(_net(profile).get("max_cost_per_event"))
    if cost is not None and cap is not None and cost > cap:
        rank.penalties.append((PENALTY_COST, "costs more than you set"))
        rank.cost_flag = "Costs $%s, above the $%s an event you set" % (format(int(cost), ","), format(int(cap), ","))
        rank.caveats.append(rank.cost_flag)
    if item.get("conflicts") is True:
        rank.penalties.append((PENALTY_TIME, "at a time you cannot do"))
        rank.caveats.append("It is at a time you said you cannot do")
    return rank


# --------------------------------------------------------------------------
# Step 2: intake
# --------------------------------------------------------------------------

def _clean(item, keys):
    return dict((key, item[key]) for key in keys if key in item and item[key] not in (None, ""))


def _section(rank, item, names):
    lines = ["**Fit %d**: %s." % (rank.total, rank.breakdown())]
    if item.get("why"):
        lines.append("**Why go:** %s" % str(item["why"]).strip())
    audience = item.get("audience")
    if audience:
        lines.append("**Who you would meet:** %s." % ", ".join(audience if isinstance(audience, list) else [audience]))
    if rank.caveats:
        lines.append("**Caveats:** " + " ".join(caveat.rstrip(".") + "." for caveat in rank.caveats))
    return scoring.scrub_confidential("\n\n".join(lines), names)


def speaking_angle(item, profile):
    """A draft talk idea for a group that takes speakers, or None. Nothing is ever sent."""
    speaking = _net(profile).get("speaking") or {}
    if not speaking.get("interested") or item.get("takes_speakers") is not True:
        return None
    idea = (item.get("speaking_angle") or "").strip()
    if not idea:
        ideas = list(speaking.get("talk_ideas") or []) + list(_net(profile).get("topics") or [])
        if not ideas:
            return None
        idea = "A short talk: \"%s\"" % ideas[0]
    return ("%s.\n\nThis is a draft idea only. Nothing was proposed or sent. If you like it, contact the organisers "
            "yourself." % idea.rstrip("."))


def _group_values(item, rank, today):
    values = {"focus": item.get("focus"), "cadence": item.get("cadence"), "next_meeting": item.get("next_meeting"),
              "virtual": item.get("virtual"), "cost_per_year": item.get("cost_per_year"), "link": item.get("url"),
              "fit": rank.total}
    when = scoring._date(values.get("next_meeting"))
    if when is not None and when < today:
        values.pop("next_meeting")
    return dict((key, value) for key, value in values.items() if value not in (None, ""))


def _same_group(row, item):
    return merge.same("group", merge.row_as_item("group", row), {"name": item.get("name"), "url": item.get("url")})


def intake(state, handoff, profile, store, today=None):
    """Rank what research found, write the notes, and record what happened."""
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    day = today.isoformat()
    group_rows = store.rows("group")

    # Groups research looked at again: refresh the next meeting, or mark a quiet one Inactive.
    by_id = dict((row.id, row) for row in group_rows)
    for entry in handoff.get("rechecked") or []:
        row = by_id.get(str(entry.get("id") or "")) if isinstance(entry, dict) else None
        if row is None or row.id in state["refreshed"] or row.title in state["inactive"] or row.title in state["told"]:
            continue
        upcoming, last = scoring._date(entry.get("next_meeting")), scoring._date(entry.get("last_active"))
        if upcoming is not None and upcoming >= today:
            store.apply([{"type": "group", "id": row.id, "values": {"next_meeting": upcoming.isoformat()}, "force": []}])
            state["refreshed"].append(row.id)
        elif last is not None and (today - last).days <= ACTIVE_WITHIN_DAYS:
            state["refreshed"].append(row.id)          # alive, with nothing on the calendar yet
        else:
            evidence = ("last meeting found was %s" % last.isoformat()) if last else "no meeting found in the last six months"
            status = model.GROUP.canonical_status(row.values.get("status"))
            if status in ("Joined", "Trying"):
                # The person is in it. The status is theirs: tell them and leave it.
                store.apply([{"type": "group", "id": row.id, "values": {}, "force": [],
                              "append": "%s: this group looks quiet (%s, nothing scheduled). Your status was left as "
                                        "it is." % (day, evidence)}])
                state["told"].append(row.title)
            else:
                store.apply([{"type": "group", "id": row.id, "values": {"status": "Inactive"}, "force": ["status"],
                              "append": "%s: marked Inactive (%s, nothing scheduled)." % (day, evidence)}])
                state["inactive"].append(row.title)

    # New groups.
    taken = []
    finds = 0
    for entry in handoff.get("groups") or []:
        if not (isinstance(entry, dict) and entry.get("name") and entry.get("url")):
            continue
        finds += 1
        item = _clean(entry, GROUP_KEYS)
        rank = rank_group(item, profile, today)
        if rank.dropped:
            state["left_out"] += 1
            continue
        existing = [row for row in group_rows if _same_group(row, item)]
        values = _group_values(item, rank, today)
        if existing:
            row = existing[0]
            if model.GROUP.is_done(row.values.get("status")):
                state["left_out"] += 1          # passed on, or inactive: seeing it again does not reopen it
                continue
            store.apply([{"type": "group", "id": row.id, "values": values, "force": [],
                          "section": _section(rank, item, names)}])
            if row.id not in state["refreshed"]:
                state["refreshed"].append(row.id)
            continue
        if any(merge.same("group", {"name": item["name"], "url": item.get("url")},
                          {"name": other["title"], "url": other.get("url")}) for other in state["new_groups"]):
            continue
        note_id = merge.next_id(model.GROUP, group_rows, taken)
        taken.append(note_id)
        values["first_seen"] = day
        title = scoring.scrub_confidential(item["name"], names)
        store.upsert("group", note_id, title, values, section=_section(rank, item, names))
        angle = speaking_angle(item, profile)
        if angle:
            store.apply([{"type": "group", "id": note_id, "values": {}, "force": [],
                          "part": (model.PART_SPEAKING, scoring.scrub_confidential(angle, names))}])
        state["new_groups"].append({"id": note_id, "title": title, "fit": rank.total, "url": item.get("url"),
                                    "next_meeting": values.get("next_meeting"), "cost_flag": rank.cost_flag,
                                    "speaking": bool(angle)})

    # Events. A group's ordinary meeting only moves that group's next-meeting date.
    group_rows = store.rows("group")
    event_rows = store.rows("event")
    taken = []
    for entry in handoff.get("events") or []:
        if not (isinstance(entry, dict) and entry.get("title") and entry.get("url")):
            continue
        finds += 1
        item = _clean(entry, EVENT_KEYS)
        host = [row for row in group_rows if item.get("group") and merge.same(
            "group", merge.row_as_item("group", row), {"name": item["group"], "url": None})]
        if not (item.get("special") or "").strip():
            when = scoring._date(item.get("date"))
            if host and when is not None and when >= today and not model.GROUP.is_done(host[0].values.get("status")):
                current = scoring._date(host[0].values.get("next_meeting"))
                if current is None or current < today or when < current:
                    store.apply([{"type": "group", "id": host[0].id, "values": {"next_meeting": when.isoformat()},
                                  "force": []}])
            state["folded"] += 1
            continue
        rank = rank_event(item, profile, today)
        if rank.dropped:
            state["left_out"] += 1
            continue
        find = {"title": item["title"], "date": item.get("date"), "url": item.get("url")}
        if any(merge.same("event", merge.row_as_item("event", row), find) for row in event_rows) or \
                any(merge.same("event", find, {"title": other["title"], "date": other["date"], "url": other["url"]})
                    for other in state["new_events"]):
            continue
        note_id = merge.next_id(model.EVENT, event_rows, taken)
        taken.append(note_id)
        values = {"date": item.get("date"), "time": item.get("time"), "evening": item.get("evening"),
                  "virtual": item.get("virtual"), "where": item.get("where"), "cost": item.get("cost"),
                  "link": item.get("url"), "fit": rank.total, "first_seen": day}
        if host:
            values["group"] = store.link("group", host[0].title)
        values = dict((key, value) for key, value in values.items() if value not in (None, ""))
        title = scoring.scrub_confidential(item["title"], names)
        section = _section(rank, item, names) + "\n\n**Why it has its own note:** %s." % scoring.scrub_confidential(
            str(item["special"]).strip().rstrip("."), names)
        store.upsert("event", note_id, title, values, section=section)
        angle = (item.get("speaking_angle") or "").strip()
        if angle and (_net(profile).get("speaking") or {}).get("interested"):
            store.apply([{"type": "event", "id": note_id, "values": {}, "force": [],
                          "part": (model.PART_SPEAKING, scoring.scrub_confidential(
                              angle.rstrip(".") + ".\n\nThis is a draft idea only. Nothing was proposed or sent.", names))}])
        state["new_events"].append({"id": note_id, "title": title, "fit": rank.total, "date": item.get("date"),
                                    "url": item.get("url"), "cost_flag": rank.cost_flag, "speaking": bool(angle)})

    state["new_groups"].sort(key=lambda entry: -entry["fit"])
    state["new_events"].sort(key=lambda entry: (entry["date"] or "", -entry["fit"]))
    state["finds_read"] = state.get("finds_read", 0) + finds
    if isinstance(handoff.get("searches"), int) and not isinstance(handoff.get("searches"), bool):
        state["searches"] = max(0, handoff["searches"])
    for name in handoff.get("not_covered") or []:
        if isinstance(name, str) and name.strip() and name.strip() not in state["not_covered"]:
            state["not_covered"].append(name.strip())
    return state


def intake_report(state, profile):
    limits = budget(profile)
    lines = ["INTAKE DONE",
             "  New groups: %d. New events: %d. Groups refreshed: %d. Marked inactive: %d. Left out by the profile: %d."
             % (len(state["new_groups"]), len(state["new_events"]), len(state["refreshed"]), len(state["inactive"]),
                state["left_out"])]
    if state["folded"]:
        lines.append("  Ordinary meetings folded into their group's next-meeting date: %d." % state["folded"])
    unchecked = [entry for entry in state["recheck"] if entry[0] not in state["refreshed"]
                 and entry[1] not in state["inactive"] and entry[1] not in state["told"]]
    left = limits["searches"] - state.get("searches", 0)
    if (not state.get("finds_read") or unchecked) and left >= limits["searches"] // 2 and not state.get("nudged"):
        state["nudged"] = True                  # said once, so a sweep can never loop on it
        lines += ["", "BEFORE YOU FINISH: %d of %d searches are unused." % (left, limits["searches"])]
        if unchecked:
            lines.append("  Groups not looked at again yet: %s." % ", ".join(entry[1] for entry in unchecked))
        if not state.get("finds_read"):
            lines.append("  No group or event was read. Go back to PLACES TO LOOK and read the most promising ones.")
        lines.append("  Then run intake again with only what is new. If nothing more can be found, carry on and say so "
                     "in not_covered.")
    else:
        lines += ["", "Run the finish command next. Do not ask why something was left out."]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Step 3: finish
# --------------------------------------------------------------------------

def write_digest(state, profile, store, today=None):
    """Write the digest note. Returns (title, featured events, featured groups, overdue count, due count)."""
    today = today or datetime.date.today()
    net = _net(profile)
    limit = ((profile.get("digest") or {}).get("max_new_items")) or 10
    per_month = net.get("max_events_per_month") if isinstance(net.get("max_events_per_month"), int) else limit
    month_ahead = (today + datetime.timedelta(days=31)).isoformat()
    soon = [entry for entry in state["new_events"] if (entry["date"] or "") <= month_ahead]
    later = [entry for entry in state["new_events"] if (entry["date"] or "") > month_ahead]
    events = sorted(soon, key=lambda entry: -entry["fit"])[:max(1, per_month)]
    events = sorted(events, key=lambda entry: entry["date"] or "") + later[:max(0, limit - len(events))]
    groups = state["new_groups"][:limit]
    overdue, due = follow_ups(store, today)
    lines = []

    lines.append("## Follow-ups")
    if overdue:
        lines.append("Overdue:")
        lines += ["- " + text for text in overdue]
    if due:
        lines.append("Due this week:")
        lines += ["- " + text for text in due]
    if not overdue and not due:
        lines.append("Nothing is due.")

    if state["did_you_go"]:
        lines += ["", "## Did you go?",
                  "These have passed and you had planned to go. Tell me, for example \"I went to E-4 and met Jane\", "
                  "or \"I did not go to E-4\". They are left as they are until you do."]
        lines += ["- %s" % store.link("event", title) for title in state["did_you_go"]]

    lines += ["", "## Coming up"]
    for entry in events:
        flag = " **%s.**" % entry["cost_flag"] if entry.get("cost_flag") else ""
        lines.append("- %s on %s. **Fit %d.**%s" % (store.link("event", entry["title"]), entry["date"], entry["fit"], flag))
    if not events:
        lines.append("No special events this time. Your groups' regular meetings are in the Groups view.")
    hidden = len(state["new_events"]) - len(events)
    if hidden > 0:
        lines.append("")
        lines.append("%d more %s in your tracker. You asked for at most %s a month here." % (
            hidden, "is" if hidden == 1 else "are", per_month))

    lines += ["", "## Groups to try"]
    for entry in groups:
        when = " Next meeting %s." % entry["next_meeting"] if entry.get("next_meeting") else " No next meeting posted yet."
        flag = " **%s.**" % entry["cost_flag"] if entry.get("cost_flag") else ""
        lines.append("- %s **Fit %d.**%s%s" % (store.link("group", entry["title"]), entry["fit"], when, flag))
    if not groups:
        lines.append("No new groups this time.")

    talks = [entry for entry in state["new_groups"] + state["new_events"] if entry.get("speaking")]
    if talks:
        lines += ["", "## Speaking angles",
                  "Draft ideas only, in each note. Nothing was proposed or sent."]
        lines += ["- %s" % store.link("group" if entry["id"].startswith(model.GROUP.id_prefix) else "event", entry["title"])
                  for entry in talks]

    if state["did_not_go"] or state["inactive"] or state["told"]:
        lines += ["", "## Housekeeping"]
        lines += ["- %s: the date passed, marked Did not go." % store.link("event", title) for title in state["did_not_go"]]
        lines += ["- %s: marked Inactive. No meeting in six months and none scheduled." % store.link("group", title)
                  for title in state["inactive"]]
        lines += ["- %s: looks quiet. You are in it, so its status was left alone." % store.link("group", title)
                  for title in state["told"]]

    lines += ["", "## What this sweep covered"]
    lines.append("- Places in your local map searched: %d." % len(state["places"]))
    lines.append("- Groups looked at again: %d of %d." % (
        len([entry for entry in state["recheck"] if entry[0] in state["refreshed"] or entry[1] in state["inactive"]
             or entry[1] in state["told"]]), len(state["recheck"])))
    lines.append("- Web searches used: %d of %d (%s mode)." % (state["searches"], budget(profile)["searches"],
                                                              "Lite" if state["mode"] == "lite" else "Full"))
    if state["left_out"]:
        lines.append("- Left out by your profile (inactive, format, area, leave-outs): %d." % state["left_out"])
    if state["folded"]:
        lines.append("- Regular meetings kept on their group instead of as events: %d." % state["folded"])
    lines += ["", "## Not covered"]
    lines += ["- %s" % name for name in state["not_covered"]] or ["Every source in your local map was covered."]
    lines += ["", "Calendar is off, so nothing was added to it.",
              "Nothing was registered for, joined, paid for or sent."]

    title = "%s Networking sweep" % today.isoformat()
    existing = [row for row in store.rows("digest") if row.title == title]
    note_id = existing[0].id if existing else merge.next_id(model.DIGEST, store.rows("digest"))
    touched = [store.link("group", entry["title"]) for entry in state["new_groups"]] + \
              [store.link("event", entry["title"]) for entry in state["new_events"]]
    store.upsert("digest", note_id, title,
                 {"date": today.isoformat(), "track": "Networking", "new": len(touched), "closed": len(state["inactive"]),
                  "touched": touched},
                 section=scoring.scrub_confidential("\n".join(lines), scoring.confidential_names(profile)))
    return title, events, groups, len(overdue), len(due)


def finish_report(state, profile, title, events, groups, overdue, due):
    """The short version for the conversation. No names of people the person knows, and no costs."""
    lines = ["NETWORKING SWEEP DONE", ""]
    lines.append("Follow-ups: %d overdue, %d due this week. They lead the digest note." % (overdue, due))
    if state["did_you_go"]:
        lines.append("Events to ask about (did they go?): %s." % ", ".join(state["did_you_go"]))
    lines.append("New groups: %d. New events: %d. Marked inactive: %d." % (
        len(state["new_groups"]), len(state["new_events"]), len(state["inactive"])))
    if events:
        lines += ["", "COMING UP"]
        for entry in events:
            lines.append("  %s  %s  Fit %d%s" % (entry["date"], entry["title"], entry["fit"],
                                                 "  (costs more than they set)" if entry.get("cost_flag") else ""))
    if groups:
        lines += ["", "GROUPS TO TRY"]
        for entry in groups:
            lines.append("  Fit %d  %s%s%s" % (entry["fit"], entry["title"],
                                               ", next meeting %s" % entry["next_meeting"] if entry.get("next_meeting") else "",
                                               "  (costs more than they set)" if entry.get("cost_flag") else ""))
    if state["not_covered"]:
        lines += ["", "Not covered: %s." % ", ".join(state["not_covered"])]
    lines += ["", "The full digest is the note \"%s\" in the Digests folder of the tracker, with follow-ups first." % title,
              "Nothing was registered for, joined, paid for or sent."]
    return _safe("\n".join(lines), profile)


# --------------------------------------------------------------------------
# Updates the person asks for, at any time
# --------------------------------------------------------------------------

def listing(store, profile, today=None):
    """Groups and events by id, so a request such as "I joined the product one" can be matched. No people."""
    today = today or datetime.date.today()
    lines = ["GROUPS"]
    groups = sorted(store.rows("group"), key=lambda row: row.id)
    for row in groups:
        lines.append("  %s | %s | %s" % (row.id, row.title, row.values.get("status") or "no status"))
    if not groups:
        lines.append("  none")
    lines.append("EVENTS (upcoming, and passed ones still open)")
    events = [row for row in store.rows("event") if not model.EVENT.is_done(row.values.get("status"))]
    for row in sorted(events, key=lambda row: str(row.values.get("date") or "")):
        lines.append("  %s | %s | %s | %s" % (row.id, row.title, row.values.get("date") or "no date", _event_status(row)))
    if not events:
        lines.append("  none")
    lines.append("GROUP STATUSES: %s" % ", ".join(model.GROUP.status_names()))
    lines.append("EVENT STATUSES: %s" % ", ".join(model.EVENT.status_names()))
    return _safe("\n".join(lines), profile)


def _find(store, kind, note_id):
    note_type = model.BY_NAME[kind]
    wanted = note_id.strip().upper()
    rest = wanted[len(note_type.id_prefix):].lstrip("-") if wanted.startswith(note_type.id_prefix) else wanted
    canonical = "%s-%d" % (note_type.id_prefix, int(rest)) if rest.isdigit() else wanted
    return store.get(kind, canonical)


def apply_updates(updates, store, profile, today=None):
    """Record what the person did. Returns (lines describing what was done, problems)."""
    today = today or datetime.date.today()
    day = today.isoformat()
    names = scoring.confidential_names(profile)
    done, problems = [], []
    for entry in updates if isinstance(updates, list) else []:
        if not isinstance(entry, dict):
            continue
        note_id = str(entry.get("id") or "").strip().upper()
        kind = "group" if note_id.startswith(model.GROUP.id_prefix) else "event" if note_id.startswith(
            model.EVENT.id_prefix) else None
        row = _find(store, kind, note_id) if kind else None
        if row is None:
            problems.append("There is no group or event with the id %s." % (entry.get("id") or "(none)"))
            continue
        note_type = model.BY_NAME[kind]
        status = note_type.canonical_status(str(entry["status"])) if entry.get("status") is not None else None
        if entry.get("status") is not None and status is None:
            problems.append("%s is not a status for %s. Use one of: %s." % (
                entry["status"], row.title, ", ".join(note_type.status_names())))
            continue
        values, force, extra = {}, [], []
        if status:
            values["status"] = status
            force.append("status")
        if kind == "group":
            if entry.get("attended_on") or status == "Joined" and entry.get("attended"):
                values["last_attended"] = str(entry.get("attended_on") or day)
                force.append("last_attended")
        else:
            if status in ("Registered", "Speaking"):
                values["going"] = True
                force.append("going")
                extra.append("Calendar is off, so add it to your calendar yourself")
            if status == "Went":
                values.update({"going": True, "attended": True})
                force += ["going", "attended"]
            if status in ("Did not go", "Passed"):
                values["going"] = False
                force.append("going")
            met = [str(name).strip() for name in entry.get("met") or [] if str(name).strip()] \
                if isinstance(entry.get("met"), list) else []
            met = [name for name in met if not any(scoring._contains_phrase(scoring._text(name), scoring._text(secret))
                                                   for secret in names)]
            if met:
                people = store.rows("person")
                taken = []
                links = list(row.values.get("people_met") or []) if isinstance(row.values.get("people_met"), list) else []
                for name in met:
                    existing = [person for person in people if scoring._text(person.title) == scoring._text(name)]
                    if not existing:
                        person_id = merge.next_id(model.PERSON, people, taken)
                        taken.append(person_id)
                        store.upsert("person", person_id, name, {"met": row.title})
                    link = store.link("person", existing[0].title if existing else name)
                    if link not in links:
                        links.append(link)
                follow = (today + datetime.timedelta(days=FOLLOW_UP_AFTER_EVENT_DAYS)).isoformat()
                for name in met:
                    person = [p for p in store.rows("person") if scoring._text(p.title) == scoring._text(name)]
                    if person and not person[0].values.get("follow_up_by"):
                        store.apply([{"type": "person", "id": person[0].id, "values": {"follow_up_by": follow},
                                      "force": ["follow_up_by"]}])
                values.update({"people_met": links, "follow_up_by": follow})
                force += ["people_met", "follow_up_by"]
                extra.append("%d %s added to People, with a follow-up due %s" % (
                    len(met), "person" if len(met) == 1 else "people", follow))
                host = merge.link_label(row.values.get("group"))
                if host and status == "Went":
                    for group in store.rows("group"):
                        if scoring._text(group.title) == scoring._text(host):
                            store.apply([{"type": "group", "id": group.id, "values": {"last_attended": day},
                                          "force": ["last_attended"]}])
        if not force:
            problems.append("Nothing to change was given for %s." % row.title)
            continue
        note = scoring.scrub_confidential(str(entry.get("note") or "").strip(), names)
        what = "status set to %s" % status if status else "updated"
        store.apply([{"type": kind, "id": row.id, "values": values, "force": force,
                      "append": "%s: %s%s" % (day, what, ". " + note.rstrip(".") + "." if note else ".")}])
        done.append("%s: %s.%s" % (row.title, "now %s" % status if status else "updated",
                                   " " + ". ".join(extra) + "." if extra else ""))
    return done, problems


# --------------------------------------------------------------------------
# Event prep
# --------------------------------------------------------------------------

def write_prep(note_id, store, profile, today=None):
    """Write a one-page brief into an event's note. Returns the line to say, never the people's names.

    The brief names people from the person's own notes, so it goes into the
    note and not into the conversation.
    """
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    row = _find(store, "event", str(note_id or ""))
    if row is None:
        raise ValueError("There is no event with the id %s. Run the list command to see the ids." % (note_id or "(none)"))
    net = _net(profile)
    host = merge.link_label(row.values.get("group"))
    likely = []
    for person in store.rows("person"):
        met = scoring._text(person.values.get("met"))
        company = scoring._text(merge.link_label(person.values.get("company")))
        if host and (scoring._text(host) in met or company == scoring._text(host)):
            likely.append(person)
    for other in store.rows("event"):
        if host and other.id != row.id and scoring._text(merge.link_label(other.values.get("group"))) == scoring._text(host):
            for link in other.values.get("people_met") or []:
                label = merge.link_label(link)
                for person in store.rows("person"):
                    if scoring._text(person.title) == scoring._text(label) and person not in likely:
                        likely.append(person)
    when = row.values.get("date") or "date not set"
    where = "online" if row.values.get("virtual") is True else (row.values.get("where") or "place not listed")
    lines = ["**%s, %s%s, %s.**" % (row.title, when, " at %s" % row.values["time"] if row.values.get("time") else "", where)]
    if row.values.get("cost") not in (None, ""):
        lines.append("Cost: $%s." % row.values["cost"])
    lines.append("")
    lines.append("**Who may be there that you know**")
    if likely:
        for person in likely[:8]:
            role = ", %s" % person.values["role"] if person.values.get("role") else ""
            lines.append("- %s%s" % (store.link("person", person.title), role))
    else:
        lines.append("- No one in your People notes is tied to this group yet. Go to meet new people.")
    who = list(net.get("who_to_meet") or [])
    topics = list(net.get("topics") or []) + list((net.get("speaking") or {}).get("talk_ideas") or [])
    goals = list(net.get("goals") or [])
    points = []
    if who:
        points.append("Look for %s. Ask what they are working on this quarter before saying what you do." % who[0].lower())
    if topics:
        points.append("Have one story ready about %s: the problem, what you did, the result." % topics[0].lower())
    if goals:
        points.append("Keep your aim in mind: %s." % goals[0].rstrip(".").lower())
    while len(points) < 3:
        points.append(["Ask each person who else here you should meet.",
                       "Offer one useful thing (an introduction, an article, a tool) before asking for anything.",
                       "Say in one sentence what you are looking for, so people can help."][len(points)])
    lines += ["", "**Three talking points**"] + ["%d. %s" % (index, point) for index, point in enumerate(points[:3], start=1)]
    lines += ["", "**Follow-up plan**",
              "- Aim to leave with two names.",
              "- The same evening, tell me who you met (\"I went to %s and met ...\"). They are added to People with a "
              "follow-up date %d days out." % (row.id, FOLLOW_UP_AFTER_EVENT_DAYS),
              "- Send each a short note within two days that mentions what you talked about.",
              "", "Written %s. Nothing was registered or sent." % today.isoformat()]
    store.apply([{"type": "event", "id": row.id, "values": {}, "force": [],
                  "part": (model.PART_PREP, scoring.scrub_confidential("\n".join(lines), names))}])
    return _safe("Prep brief written into the note \"%s\": %d %s you know may be there, three talking points and a "
                 "follow-up plan. Open the note to read it." % (
                     row.title, len(likely), "person" if len(likely) == 1 else "people"), profile)
