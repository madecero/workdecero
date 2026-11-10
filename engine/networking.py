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
import re

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
FOLLOW_UP_AFTER_EVENT_DAYS = 7     # someone met at an event gets a follow-up date a week out
GAP_FILLER = ("with", "that", "this", "from", "people", "other", "local", "groups", "group", "their", "who")
DEFAULT_PICKS = 2                  # events a month when the profile does not say
ALSO_COMING_UP = 5
GROUPS_WORTH_TRYING = 3
PICK_WINDOW_DAYS = 31
PLACE_KINDS = ("meetup", "user-group", "association", "chamber", "conference")
PLACE_WORDS = {"meetup": "Meetups", "user-group": "User groups", "association": "Associations",
               "chamber": "Chambers", "conference": "Conferences"}
GROUP_WEIGHTS = {"who": 40, "fit": 30, "active": 15, "place": 15}
PENALTY_COST = 5
PENALTY_TIME = 5

GROUP_KEYS = ("name", "url", "focus", "cadence", "next_meeting", "weekday", "next_time", "evening", "where",
              "distance_miles", "next_cost", "last_active", "virtual", "virtual_option", "cost_per_year", "members",
              "audience", "fit_assessment", "why", "takes_speakers", "speaking_angle", "conflicts", "in_area")
EVENT_KEYS = ("title", "url", "date", "weekday", "time", "evening", "virtual", "where", "distance_miles", "cost",
              "group", "special", "audience", "fit_assessment", "why", "conflicts", "in_area", "speaking_angle")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# A networking note describes interests. It never says the person is looking for work.
_SEARCH_WORDS = re.compile(r"job[ -]?(search|hunt|seek)|open to (new )?(opportunit|work|roles)|looking for (a )?(new )?"
                           r"(job|role|position)|between (jobs|roles)|laid off|unemployed|career change", re.IGNORECASE)


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


def discreet(text):
    """Text with any sentence about looking for work taken out. Networking notes never mention a search."""
    sentences = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return " ".join(sentence for sentence in sentences if sentence and not _SEARCH_WORDS.search(sentence))


def settle_date(value, weekday, today):
    """A date as YEAR-MONTH-DAY, with the year corrected from the weekday when a page left the year off.

    Listings often show "Tue, Nov 18" with no year. If the date given does not
    fall on the weekday given, the same day next year or last year is tried.
    Returns (date or None, True when the weekday could not be made to agree).
    """
    when = scoring._date(value)
    wanted = scoring._text(weekday)[:3]
    days = [name[:3] for name in WEEKDAYS]
    if when is None or wanted not in days:
        return when, False
    if days[when.weekday()] == wanted:
        return when, False
    for shift in (1, -1):
        try:
            other = when.replace(year=when.year + shift)
        except ValueError:
            continue
        if days[other.weekday()] == wanted and other >= today:
            return other, False
    return when, True


def when_text(day, time_text=None):
    """A date as people read it: "Tue Nov 18, 6:00 PM"."""
    when = day if isinstance(day, datetime.date) else scoring._date(day)
    if when is None:
        return "date not set"
    text = "%s %s %d" % (when.strftime("%a"), when.strftime("%b"), when.day)
    return "%s, %s" % (text, str(time_text).strip()) if time_text else text


_DAY_WORDS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
              "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
_DAYTIME = re.compile(r"daytime|day time|during the day|9 to 5|9-5|nine to five|work(ing)? hours|business hours|"
                      r"office hours|work ?day\b")


def time_rules(profile):
    """The person's "times I cannot do", read for the common ways people say it.

    Each phrase becomes a rule: which days, which part of the day, and whether
    it only applies to going somewhere in person ("unless it's virtual"). A
    phrase that names neither a day nor a part of the day is skipped here, and
    research's own judgement of it still applies.
    """
    rules = []
    for phrase in _net(profile).get("unavailable") or []:
        text = " %s " % re.sub(r"[^a-z0-9: ]+", " ", str(phrase).lower())
        days = set()
        if re.search(r"\bweek ?days?\b|\bweek ?nights?\b|\bduring the week\b|\bwork ?days?\b", text):
            days |= {0, 1, 2, 3, 4}
        if re.search(r"\bweekends?\b", text):
            days |= {5, 6}
        for word, number in _DAY_WORDS.items():
            if re.search(r"\b%ss?\b" % word, text):
                days.add(number)
        parts = set()
        if re.search(r"\bmornings?\b", text):
            parts.add("morning")
        if re.search(r"\bafternoons?\b|\blunch", text):
            parts.add("afternoon")
        if re.search(r"\bevenings?\b|\bnights?\b|\bweek ?nights?\b|after work", text):
            parts.add("evening")
        if _DAYTIME.search(text):
            parts |= {"morning", "afternoon"}
        before = re.search(r"\bbefore (\d{1,2})(?::\d{2})? ?(am|pm)?", text)
        after = re.search(r"\bafter (\d{1,2})(?::\d{2})? ?(am|pm)?", text)

        def hour(match):
            value = int(match.group(1))
            if match.group(2) == "pm" and value < 12:
                value += 12
            return value
        if not days and not parts and not before and not after:
            continue
        rules.append({"days": days or set(range(7)), "parts": parts, "before": hour(before) if before else None,
                      "after": hour(after) if after else None,
                      "in_person_only": bool(re.search(r"unless .*(virtual|online|remote)|\bin person\b|"
                                                       r"except .*(virtual|online)", text))})
    return rules


def _hour(time_text):
    match = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m", str(time_text or "").lower())
    if not match:
        return None
    value = int(match.group(1)) % 12
    return value + 12 if match.group(3) == "p" else value


def outside_times(profile, day, time_text, evening, virtual):
    """True when a meeting falls in a time the person said they cannot do. The engine's own check."""
    when = day if isinstance(day, datetime.date) else scoring._date(day)
    if when is None:
        return False
    hour = _hour(time_text)
    if hour is not None:
        part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    elif evening is True:
        part = "evening"
    elif evening is False:
        part = "day"                      # daytime, with the hour unknown
    else:
        part = None
    for rule in time_rules(profile):
        if when.weekday() not in rule["days"] or (rule["in_person_only"] and virtual is True):
            continue
        if part is None and virtual is not True:
            # Nobody knows when it starts, and the person has ruled out part of this day. An in-person event
            # is not picked on a guess: it counts as a clash until the time is known.
            return True
        if rule["before"] is not None or rule["after"] is not None:
            if hour is None:
                continue
            if (rule["before"] is not None and hour < rule["before"]) or \
                    (rule["after"] is not None and hour >= rule["after"]):
                if not rule["parts"] or part in rule["parts"]:
                    return True
            continue
        if not rule["parts"]:
            return True                   # the whole day is out
        if part in rule["parts"] or (part == "day" and {"morning", "afternoon"} <= rule["parts"]):
            return True
    return False


def time_known(time_text, evening):
    return _hour(time_text) is not None or evening is True or evening is False


def schedule_tag(evening, virtual, outside, time_text=None):
    """The short word after a meeting: Virtual, Evening, Daytime, or that nobody has posted its time."""
    if virtual is True:
        tag = "Virtual"
    elif evening is True or (_hour(time_text) is not None and _hour(time_text) >= 17):
        tag = "Evening"
    elif time_known(time_text, evening):
        tag = "Daytime"
    else:
        # Never call an unknown time Daytime. It is not picked while a rule covers that day.
        return "Time not posted, check before going" if outside is True else "Time not posted"
    return "%s, outside your times" % tag if outside is True else tag


def cost_text(cost, cap):
    if cost in (None, ""):
        return "cost not listed"
    if not cost:
        return "free"
    text = "$%s" % format(int(cost), ",")
    if cap is not None and cost > cap:
        text += ", over your $%s limit" % format(int(cap), ",")
    return text


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
    return _event_status(row) in ("Going", "Registered", "Speaking") or row.values.get("going") is True


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
    why = discreet(item.get("why"))
    if why:
        lines.append("**Why go:** %s" % why)
    audience = item.get("audience")
    if audience:
        lines.append("**Who you would meet:** %s." % ", ".join(audience if isinstance(audience, list) else [audience]))
    if item.get("virtual_option") is True and item.get("virtual") is not True:
        lines.append("**Online option:** yes, you can also join online.")
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
    values = {"focus": item.get("focus"), "cadence": item.get("cadence"), "virtual": item.get("virtual"),
              "cost_per_year": item.get("cost_per_year"), "members": item.get("members"), "link": item.get("url"),
              "fit": rank.total}
    upcoming, unsure = settle_date(item.get("next_meeting"), item.get("weekday"), today)
    last = scoring._date(item.get("last_active"))
    if upcoming is not None and upcoming < today:
        last = max(last, upcoming) if last else upcoming
        upcoming = None
    if upcoming is not None and not unsure:
        values.update({"next_meeting": upcoming.isoformat(), "next_time": item.get("next_time"),
                       "next_where": item.get("where"), "evening": item.get("evening"),
                       "miles": item.get("distance_miles"), "next_cost": item.get("next_cost"),
                       "outside_times": item.get("conflicts") is True})
    if last is not None:
        values["last_meeting"] = last.isoformat()
    return dict((key, value) for key, value in values.items() if value not in (None, ""))


def _same_group(row, item):
    return merge.same("group", merge.row_as_item("group", row), {"name": item.get("name"), "url": item.get("url")})


def same_slot(first, second):
    """Two listings for the same date, start time and place are one gathering, whoever lists it."""
    def slot(entry):
        return tuple(scoring._text(str(entry.get(key) or "")) for key in ("date", "time", "where"))
    return all(slot(first)) and slot(first) == slot(second)


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
        settled, unsure = settle_date(item.get("next_meeting"), item.get("weekday"), today)
        if settled is not None and not unsure:
            item["next_meeting"] = settled.isoformat()
        elif "next_meeting" in item:
            item.pop("next_meeting")            # a date that does not fall on the weekday shown is not trusted
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
                                    "who": rank.parts.get("who", 0), "speaking": bool(angle)})

    # Events. A group's ordinary meeting only moves that group's next-meeting date.
    group_rows = store.rows("group")
    event_rows = store.rows("event")
    taken = []
    for entry in handoff.get("events") or []:
        if not (isinstance(entry, dict) and entry.get("title") and entry.get("url")):
            continue
        finds += 1
        item = _clean(entry, EVENT_KEYS)
        settled, unsure = settle_date(item.get("date"), item.get("weekday"), today)
        if unsure:
            state["left_out"] += 1              # the date and the weekday on the page do not agree
            continue
        if settled is not None:
            item["date"] = settled.isoformat()
        host = [row for row in group_rows if item.get("group") and merge.same(
            "group", merge.row_as_item("group", row), {"name": item["group"], "url": None})]
        if not (item.get("special") or "").strip():
            when = scoring._date(item.get("date"))
            if host and when is not None and when >= today and not model.GROUP.is_done(host[0].values.get("status")):
                current = scoring._date(host[0].values.get("next_meeting"))
                if current is None or current < today or when <= current:   # the same day fills in its details
                    values = {"next_meeting": when.isoformat(), "next_time": item.get("time"),
                              "next_where": item.get("where"), "evening": item.get("evening"),
                              "miles": item.get("distance_miles"), "next_cost": item.get("cost"),
                              "outside_times": item.get("conflicts") is True}
                    store.apply([{"type": "group", "id": host[0].id, "force": [],
                                  "values": dict((k, v) for k, v in values.items() if v not in (None, ""))}])
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
        slot = {"date": item.get("date"), "time": item.get("time"), "where": item.get("where")}
        twin = [row for row in event_rows if same_slot(slot, row.values)]
        twin_id = twin[0].id if twin else next((other["id"] for other in state["new_events"] if same_slot(slot, other)), None)
        if twin_id:
            # Co-hosted: two groups list one gathering. It stays one note, with the other listing named on it.
            also = scoring.scrub_confidential("%s (%s)" % (item["title"], item.get("group") or item.get("url")), names)
            store.apply([{"type": "event", "id": twin_id, "values": {}, "force": [],
                          "append": "%s: also listed as %s." % (day, also)}])
            state["folded"] += 1
            continue
        note_id = merge.next_id(model.EVENT, event_rows, taken)
        taken.append(note_id)
        values = {"date": item.get("date"), "time": item.get("time"), "evening": item.get("evening"),
                  "virtual": item.get("virtual"), "where": item.get("where"), "cost": item.get("cost"),
                  "miles": item.get("distance_miles"), "outside_times": item.get("conflicts") is True,
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
                                    "url": item.get("url"), "speaking": bool(angle),
                                    "time": item.get("time"), "where": item.get("where")})

    state["new_groups"].sort(key=lambda entry: (-entry.get("who", 0), -entry["fit"]))
    state["new_events"].sort(key=lambda entry: (entry["date"] or "", -entry["fit"]))
    state["finds_read"] = state.get("finds_read", 0) + finds
    if isinstance(handoff.get("searches"), int) and not isinstance(handoff.get("searches"), bool):
        state["searches"] = max(0, handoff["searches"])
    for name in handoff.get("not_covered") or []:
        if isinstance(name, str):
            scoring.add_not_covered(state["not_covered"], name)
    for line in handoff.get("not_found") or []:
        line = scoring.scrub_confidential(discreet(line), names).strip()
        if line and line not in state.setdefault("not_found", []):
            state["not_found"].append(line)
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

def _why(row):
    match = re.search(r"\*\*Why go:\*\* (.+)", row.section or "")
    return match.group(1).strip() if match else ""


def coming_up(store, today, profile=None):
    """Everything on the calendar in the next month: special events, and each open group's next meeting once."""
    last = today + datetime.timedelta(days=PICK_WINDOW_DAYS)
    found, taken = [], set()
    for row in store.rows("event"):
        when = scoring._date(row.values.get("date"))
        if when is None or not (today <= when <= last) or model.EVENT.is_done(row.values.get("status")):
            continue
        host = merge.link_label(row.values.get("group")) or ""
        taken.add((scoring._text(host), when))
        found.append({"kind": "event", "id": row.id, "title": row.title, "date": when,
                      "time": row.values.get("time"), "evening": row.values.get("evening"),
                      "virtual": row.values.get("virtual"), "where": row.values.get("where"),
                      "miles": row.values.get("miles"), "cost": row.values.get("cost"), "host": host,
                      "fit": row.values.get("fit") or 0, "outside": row.values.get("outside_times") is True,
                      "why": _why(row), "going": _committed(row)})
    for row in store.rows("group"):
        when = scoring._date(row.values.get("next_meeting"))
        if when is None or not (today <= when <= last) or model.GROUP.is_done(row.values.get("status")):
            continue
        if (scoring._text(row.title), when) in taken:
            continue                              # the series shows once: its special event stands for it
        found.append({"kind": "group", "id": row.id, "title": row.title, "date": when,
                      "time": row.values.get("next_time"), "evening": row.values.get("evening"),
                      "virtual": row.values.get("virtual"), "where": row.values.get("next_where"),
                      "miles": row.values.get("miles"), "cost": row.values.get("next_cost"), "host": row.title,
                      "fit": row.values.get("fit") or 0, "outside": row.values.get("outside_times") is True,
                      "why": _why(row), "going": False})
    if profile is not None:
        # Research flags a clash when it reads the page. The engine checks the person's own words as well,
        # so a pick never rests on research alone.
        for entry in found:
            if not entry["outside"] and outside_times(profile, entry["date"], entry["time"], entry["evening"],
                                                      entry["virtual"]):
                entry["outside"] = True
            entry["far"] = beyond_reach(profile, entry["miles"], entry["where"], entry["virtual"])
    # One gathering listed by two groups shows once: the better fit, with the other named beside it.
    kept = []
    for entry in sorted(found, key=lambda entry: (entry["kind"] != "event", -entry["fit"])):
        twin = next((other for other in kept if same_slot(entry, other)), None)
        if twin is not None:
            if entry["host"] and scoring._text(entry["host"]) != scoring._text(twin["host"]):
                twin.setdefault("with", []).append(entry["host"])
            twin["going"] = twin["going"] or entry["going"]
            continue
        kept.append(entry)
    return kept


def beyond_reach(profile, miles, where, virtual):
    """The person's travel limit in miles, when an in-person meeting is farther than that. Otherwise None.

    A town they named as one they go to (networking.areas, or other_areas) is in reach however far it is.
    """
    if virtual is True or isinstance(miles, bool) or not isinstance(miles, (int, float)):
        return None
    location = profile.get("location") or {}
    radius = location.get("radius_miles")
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or miles <= radius:
        return None
    named = list(_net(profile).get("areas") or []) + list(location.get("other_areas") or [])
    place = scoring._text(where)
    for area in named:
        town = scoring._text(str(area).split(",")[0])
        if town and town != scoring._text(str(location.get("home_base") or "").split(",")[0]) and \
                scoring._contains_phrase(place, town):
            return None
    return int(radius)


def choose(found, cap):
    """Split what is coming up into (picks, the rest). The cap is hard, and a pick always fits the schedule."""
    order = sorted(found, key=lambda entry: (-entry["fit"], entry["date"]))
    going = [entry for entry in order if entry["going"]]
    # A pick fits the schedule and is within the distance they said they travel.
    eligible = [entry for entry in order if not entry["going"] and not entry["outside"] and not entry.get("far")]
    picks = list(going[:max(0, cap)])
    for entry in eligible:
        if len(picks) >= cap:
            break
        if any(entry["date"] == other["date"] for other in picks):
            continue                              # nobody goes to two things in one evening: the better one is the pick
        picks.append(entry)
    rest = [entry for entry in order if entry not in picks]
    return sorted(picks, key=lambda entry: entry["date"]), rest[:ALSO_COMING_UP]


def _coming_line(entry, store, cap):
    link = store.link(entry["kind"], entry["title"])
    if entry["virtual"] is True:
        place = "Virtual"
    else:
        place = "In person"
        if isinstance(entry["miles"], (int, float)) and not isinstance(entry["miles"], bool):
            place += ", %d miles" % round(entry["miles"])
            if entry.get("far"):
                place += " (outside your %d)" % entry["far"]
        if entry["where"]:
            place += ", %s" % str(entry["where"]).strip()
    what = "%s (its regular meeting)" % link if entry["kind"] == "group" else link
    if entry["kind"] == "event" and entry["host"]:
        what += ", run by %s" % entry["host"]
    if entry.get("with"):
        what += ", with %s" % " and ".join(entry["with"])
    cost = cost_text(entry["cost"], cap)
    line = "- **%s**: %s. %s. %s." % (when_text(entry["date"], entry["time"]), what, place, cost[:1].upper() + cost[1:])
    if entry["why"]:
        line += " %s" % entry["why"].rstrip(".") + "."
    line += " *%s.*" % schedule_tag(entry["evening"], entry["virtual"], entry["outside"], entry["time"])
    if entry["going"]:
        line += " You said you are going."
    return line


def _group_line(row, store, cap):
    values = row.values
    bits = []
    if values.get("members"):
        bits.append("%s members" % format(int(values["members"]), ","))
    if values.get("cadence"):
        bits.append(str(values["cadence"]).strip().rstrip("."))
    if values.get("last_meeting"):
        bits.append("last met %s" % when_text(values["last_meeting"]))
    bits.append("next %s" % when_text(values["next_meeting"], values.get("next_time")) if values.get("next_meeting")
                else "no next meeting posted yet")
    if values.get("virtual") is True:
        bits.append("online only")
    elif "**Online option:**" in (row.section or ""):
        bits.append("online option")
    yearly = values.get("cost_per_year")
    if yearly not in (None, ""):
        text = cost_text(yearly, cap)
        bits.append(text if text == "free" else text.replace(", over", " a year, over") if ", over" in text else text + " a year")
    line = "- %s: %s." % (store.link("group", row.title), ", ".join(bits))
    why = _why(row)
    return line + (" %s." % why.rstrip(".") if why else "")


def not_found_lines(state, profile, store):
    """Said plainly: which of the people they want to meet no group or event was found for."""
    wanted = list(_net(profile).get("who_to_meet") or [])
    text = scoring._text(" ".join((row.section or "") for row in store.rows("group") + store.rows("event")
                                  if not model.BY_NAME[row.values.get("type") or "group"].is_done(row.values.get("status"))))
    missing = [who for who in wanted if scoring._text(who) not in text]
    detailed, seen = [], set()
    for line in state.get("not_found") or []:
        said = scoring._text(line)
        if said and said not in seen:
            seen.add(said)
            detailed.append(line)

    def covered(who):
        # Research names the same gap in its own words, with detail (the nearest thing it did find). When it
        # has, its line is the one kept, and the plain one is not said as well.
        words = [word for word in scoring._text(who).split() if len(word) > 3 and word not in GAP_FILLER]
        if not words:
            return False
        for line in detailed:
            said = scoring._text(line)
            hits = len([word for word in words if word in said or word.rstrip("s") in said])
            if hits * 2 >= len(words):
                return True
        return False
    return ["No group or event found for: %s." % who for who in missing if not covered(who)] + detailed


def write_digest(state, profile, store, today=None):
    """Write the digest note: who is owed a note, then the one or two things worth going to this month.

    Returns (title, picks, also coming up, overdue count, due count).
    """
    today = today or datetime.date.today()
    net = _net(profile)
    cap = net.get("max_events_per_month") if isinstance(net.get("max_events_per_month"), int) else DEFAULT_PICKS
    event_cap, year_cap = _number(net.get("max_cost_per_event")), _number(net.get("max_cost_per_year"))
    picks, rest = choose(coming_up(store, today, profile), cap)
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
        lines.append("Nothing owed yet.")

    if state["did_you_go"]:
        lines += ["", "## Did you go?",
                  "These have passed and you had planned to go. Tell me, for example \"I went to E-4 and met Jane\", "
                  "or \"I did not go to E-4\". They are left as they are until you do."]
        lines += ["- %s" % store.link("event", title) for title in state["did_you_go"]]

    lines += ["", "## This month's pick" + ("" if cap == 1 else "s")]
    lines += [_coming_line(entry, store, event_cap) for entry in picks]
    if not picks:
        lines.append("Nothing in the next month fits your times and the people you want to meet.")
    elif len(picks) == cap:
        lines.append("")
        lines.append("You asked for at most %d a month, so that is the list." % cap)

    if rest:
        lines += ["", "## Also coming up", "In case you want to swap one in."]
        lines += [_coming_line(entry, store, event_cap) for entry in rest]

    new_rows = dict((row.id, row) for row in store.rows("group"))
    worth = [new_rows[entry["id"]] for entry in state["new_groups"] if entry["id"] in new_rows][:GROUPS_WORTH_TRYING]
    lines += ["", "## Groups worth trying"]
    lines += [_group_line(row, store, year_cap) for row in worth]
    if not worth:
        lines.append("No new groups this time.")
    extra = len(state["new_groups"]) - len(worth)
    if extra > 0:
        lines.append("")
        lines.append("%d more %s in the Groups view." % (extra, "is" if extra == 1 else "are"))

    if state["inactive"] or state["told"]:
        lines += ["", "## Groups to drop"]
        lines += ["- %s: no meeting in six months and none scheduled. Moved to Inactive. The note is kept."
                  % store.link("group", title) for title in state["inactive"]]
        lines += ["- %s: looks quiet. You are in it, so its status was left alone." % store.link("group", title)
                  for title in state["told"]]

    missing = not_found_lines(state, profile, store)
    if missing:
        lines += ["", "## Not found"]
        lines += ["- %s" % line for line in missing]

    talks = [entry for entry in state["new_groups"] + state["new_events"] if entry.get("speaking")]
    if talks:
        first = talks[0]
        kind = "group" if first["id"].startswith(model.GROUP.id_prefix) else "event"
        lines += ["", "## Speaking angle",
                  "%s takes speakers. A draft talk idea is in its note. Nothing was proposed or sent."
                  % store.link(kind, first["title"])]

    if state["did_not_go"]:
        lines += ["", "## Tidied"]
        lines += ["- %s: the date passed, marked Did not go." % store.link("event", title) for title in state["did_not_go"]]

    if state.get("integration_lines"):
        lines += ["", "## Connections"]
        lines += ["- %s" % text for text in state["integration_lines"]]
    lines += ["", "## What was searched"]
    lines.append("%d %s in your local map, %d of %d web searches (%s mode), %d of %d known groups looked at again." % (
        len(state["places"]), "place" if len(state["places"]) == 1 else "places", state["searches"],
        budget(profile)["searches"], "Lite" if state["mode"] == "lite" else "Full",
        len([entry for entry in state["recheck"] if entry[0] in state["refreshed"] or entry[1] in state["inactive"]
             or entry[1] in state["told"]]), len(state["recheck"])))
    if state["not_covered"]:
        lines.append("Not covered: %s." % "; ".join(state["not_covered"]))
    lines += ["", "Nothing was registered for, RSVP'd, joined or messaged. Nothing was added to your calendar.",
              "Your notes and this digest stay on your computer."]

    # A second sweep on the same day gets its own note. The first one is never overwritten.
    title = "%s Networking sweep" % today.isoformat()
    taken = [row.title for row in store.rows("digest")]
    number = 2
    while title in taken:
        title = "%s Networking sweep %d" % (today.isoformat(), number)
        number += 1
    note_id = merge.next_id(model.DIGEST, store.rows("digest"))
    touched = [store.link("group", entry["title"]) for entry in state["new_groups"]] + \
              [store.link("event", entry["title"]) for entry in state["new_events"]]
    store.upsert("digest", note_id, title,
                 {"date": today.isoformat(), "track": "Networking", "new": len(touched), "closed": len(state["inactive"]),
                  "touched": touched},
                 section=scoring.scrub_confidential(discreet_lines(lines), scoring.confidential_names(profile)))
    return title, picks, rest, len(overdue), len(due)


def discreet_lines(lines):
    """The digest text, with any line that speaks of a job search removed."""
    return "\n".join(line for line in lines if not _SEARCH_WORDS.search(line))


def finish_report(state, profile, title, picks, rest, overdue, due):
    """The short version for the conversation. No names of people the person knows, and no spending limits."""
    lines = ["NETWORKING SWEEP DONE", ""]
    lines.append("Follow-ups: %d overdue, %d due this week. They lead the digest note." % (overdue, due))
    if state["did_you_go"]:
        lines.append("Events to ask about (did they go?): %s." % ", ".join(state["did_you_go"]))
    lines.append("New groups: %d. New special events: %d. Moved to inactive: %d." % (
        len(state["new_groups"]), len(state["new_events"]), len(state["inactive"])))
    lines += ["", "THIS MONTH'S PICK%s (%d)" % ("" if len(picks) == 1 else "S", len(picks))]
    for entry in picks:
        lines.append("  %s  %s  %s" % (when_text(entry["date"], entry["time"]), entry["title"],
                                       schedule_tag(entry["evening"], entry["virtual"], entry["outside"], entry["time"])))
    if not picks:
        lines.append("  none that fit their times this month")
    if rest:
        lines.append("ALSO COMING UP: %d more in the digest." % len(rest))
    if state["not_covered"]:
        lines += ["", "Not covered: %s." % ", ".join(state["not_covered"])]
    lines += ["", "The full digest is the note \"%s\" in the Digests folder of the tracker, with follow-ups first." % title,
              "Nothing was registered for, RSVP'd, joined or messaged."]
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


def meeting_event(store, group_row, when, today):
    """The Events note for one meeting of a group, made from the group's note if it does not exist yet."""
    for row in store.rows("event"):
        if scoring._text(merge.link_label(row.values.get("group"))) == scoring._text(group_row.title) and \
                scoring._date(row.values.get("date")) == when:
            return row
    values = {"date": when.isoformat(), "group": store.link("group", group_row.title),
              "link": group_row.values.get("link"), "fit": group_row.values.get("fit"), "first_seen": today.isoformat()}
    if scoring._date(group_row.values.get("next_meeting")) == when:
        values.update({"time": group_row.values.get("next_time"), "where": group_row.values.get("next_where"),
                       "evening": group_row.values.get("evening"), "virtual": group_row.values.get("virtual"),
                       "miles": group_row.values.get("miles"), "cost": group_row.values.get("next_cost"),
                       "outside_times": group_row.values.get("outside_times")})
    values = dict((key, value) for key, value in values.items() if value not in (None, ""))
    note_id = merge.next_id(model.EVENT, store.rows("event"))
    title = "%s, %s" % (group_row.title, when_text(when))
    why = _why(group_row)
    store.upsert("event", note_id, title, values,
                 section="A regular meeting of %s.%s" % (group_row.title, "\n\n**Why go:** %s" % why if why else ""))
    return store.get("event", note_id)


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
        met = [str(name).strip() for name in entry.get("met") or [] if str(name).strip()] \
            if isinstance(entry.get("met"), list) else []
        met = [name for name in met if not any(scoring._contains_phrase(scoring._text(name), scoring._text(secret))
                                               for secret in names)]
        going = entry.get("going") is True
        extra, calendar = [], False

        # "I am going to (a group)" or "I met someone at (a group)" is about one meeting of it.
        if kind == "group" and (going or met):
            upcoming = scoring._date(row.values.get("next_meeting"))
            said = scoring._date(entry.get("date"))
            if going and said is not None:
                # They named a day. Only that day will do: the group's next meeting on file is not a stand-in for it.
                on_file = [other for other in store.rows("event")
                           if scoring._text(merge.link_label(other.values.get("group"))) == scoring._text(row.title)
                           and scoring._date(other.values.get("date")) == said]
                if said != upcoming and not on_file:
                    known = sorted(set([upcoming] if upcoming and upcoming >= today else []) | set(
                        scoring._date(other.values.get("date")) for other in store.rows("event")
                        if scoring._text(merge.link_label(other.values.get("group"))) == scoring._text(row.title)
                        and scoring._date(other.values.get("date")) and scoring._date(other.values.get("date")) >= today))
                    problems.append("%s has nothing on file for %s. %s Nothing was changed. Ask the person which they "
                                    "mean, or run the sweep to look for that date." % (
                                        row.title, when_text(said),
                                        "On file: %s." % ", ".join(when_text(day) for day in known) if known
                                        else "No coming meeting is on file."))
                    continue
            elif going and (upcoming is None or upcoming < today):
                problems.append("%s has no next meeting on file, so there is nothing to mark as going. Run the sweep "
                                "to find its next date." % row.title)
                continue
            when = (said or upcoming) if going else (said or today)
            if entry.get("status") is not None and model.GROUP.canonical_status(str(entry["status"])):
                store.apply([{"type": "group", "id": row.id, "force": ["status"],
                              "values": {"status": model.GROUP.canonical_status(str(entry["status"]))}}])
            group_row, row, kind = row, meeting_event(store, row, when, today), "event"
            entry = dict(entry, status="Went" if met else None)
            extra.append("Its note is %s" % row.id)

        note_type = model.BY_NAME[kind]
        status = note_type.canonical_status(str(entry["status"])) if entry.get("status") is not None else None
        if entry.get("status") is not None and status is None:
            problems.append("%s is not a status for %s. Use one of: %s." % (
                entry["status"], row.title, ", ".join(note_type.status_names())))
            continue
        values, force = {}, []
        if status:
            values["status"] = status
            force.append("status")
        if kind == "group":
            if status == "Joined" and entry.get("attended_on"):
                values["last_attended"] = str(entry["attended_on"])
                force.append("last_attended")
        else:
            if going and not status:
                # Going? is theirs to tick. Saying "I am going" is the same as ticking it.
                values["going"] = True
                force.append("going")
                if _event_status(row) in ("New", "Want to go"):
                    values["status"] = "Going"
                    force.append("status")
                calendar = True
            if entry.get("going") is False and not status:
                values["going"] = False
                force.append("going")
                if _event_status(row) == "Going":
                    values["status"] = "Want to go"    # still interested, no longer committed
                    force.append("status")
            if status in ("New", "Want to go"):
                values["going"] = False                # interested is not going: the box and the status always agree
                force.append("going")
            if status == "Going":
                values["going"] = True
                force.append("going")
                calendar = True
            if status in ("Registered", "Speaking"):
                values["going"] = True
                force.append("going")
                calendar = True
            if status == "Went":
                values.update({"going": True, "attended": True})
                force += ["going", "attended"]
            if status in ("Did not go", "Passed"):
                values.update({"going": False, "attended": False})
                force += ["going", "attended"]
            if met:
                people = store.rows("person")
                taken = []
                links = list(row.values.get("people_met") or []) if isinstance(row.values.get("people_met"), list) else []
                follow = (today + datetime.timedelta(days=FOLLOW_UP_AFTER_EVENT_DAYS)).isoformat()
                for name in met:
                    existing = [person for person in people if scoring._text(person.title) == scoring._text(name)]
                    if existing:
                        person_id = existing[0].id
                    else:
                        person_id = merge.next_id(model.PERSON, people, taken)
                        taken.append(person_id)
                        store.upsert("person", person_id, name, {"met": row.title})
                    person = store.get("person", person_id)
                    if person is not None and not person.values.get("follow_up_by"):
                        store.apply([{"type": "person", "id": person_id, "values": {"follow_up_by": follow},
                                      "force": ["follow_up_by"]}])
                    link = store.link("person", person.title if person is not None else name)
                    if link not in links:
                        links.append(link)
                values.update({"people_met": links, "follow_up_by": follow})
                force += ["people_met", "follow_up_by"]
                extra.append("%d %s added to People, with a follow-up due %s" % (
                    len(met), "person" if len(met) == 1 else "people", follow))
                host = merge.link_label(row.values.get("group"))
                if host:
                    for group in store.rows("group"):
                        if scoring._text(group.title) == scoring._text(host):
                            held = scoring._date(row.values.get("date"))
                            store.apply([{"type": "group", "id": group.id, "force": ["last_attended"],
                                          "values": {"last_attended": (held if held and held <= today else today).isoformat()}}])
        if not force:
            problems.append("Nothing to change was given for %s." % row.title)
            continue
        note = scoring.scrub_confidential(discreet(entry.get("note")), names)
        what = "status set to %s" % values["status"] if "status" in values else "updated"
        store.apply([{"type": kind, "id": row.id, "values": values, "force": force,
                      "append": "%s: %s%s" % (day, what, ". " + note.rstrip(".") + "." if note else ".")}])
        done.append("%s: %s.%s" % (row.title, "now %s" % values["status"] if "status" in values else "updated",
                                   " " + ". ".join(extra) + "." if extra else ""))
        if calendar:
            # The engine writes the calendar entry's text, so nothing private is left to the assistant to compose.
            import integrations
            done += integrations.calendar_lines(profile, row.title, row.values.get("date"), row.values.get("time"),
                                                "Online" if row.values.get("virtual") is True else row.values.get("where"),
                                                row.values.get("link"), _why(row))
    return done, problems


# --------------------------------------------------------------------------
# Event prep
# --------------------------------------------------------------------------

def write_prep(note_id, store, profile, today=None, there=None):
    """Write a short brief (ten lines at most) into an event's note. Returns the line to say.

    `there` holds names research read on the group's public page (organisers,
    speakers). People from the person's own notes are added here, which is why
    the brief goes into the note and the conversation is told only that it exists.
    """
    today = today or datetime.date.today()
    names = scoring.confidential_names(profile)
    wanted = str(note_id or "").strip().upper()
    if wanted.startswith(model.GROUP.id_prefix):
        group_row = _find(store, "group", wanted)
        upcoming = scoring._date(group_row.values.get("next_meeting")) if group_row is not None else None
        if group_row is None or upcoming is None or upcoming < today:
            raise ValueError("That group has no next meeting on file to prepare for. Run the sweep to find its next date.")
        row = meeting_event(store, group_row, upcoming, today)
    else:
        row = _find(store, "event", wanted)
    if row is None:
        raise ValueError("There is no event with the id %s. Run the list command to see the ids." % (note_id or "(none)"))
    net = _net(profile)
    host = merge.link_label(row.values.get("group"))
    people = store.rows("person")
    known = []
    for person in people:
        met = scoring._text(person.values.get("met"))
        company = scoring._text(merge.link_label(person.values.get("company")))
        if host and (scoring._text(host) in met or company == scoring._text(host)):
            known.append(person)
    for other in store.rows("event"):
        if host and other.id != row.id and scoring._text(merge.link_label(other.values.get("group"))) == scoring._text(host):
            for link in other.values.get("people_met") or []:
                for person in people:
                    if scoring._text(person.title) == scoring._text(merge.link_label(link)) and person not in known:
                        known.append(person)
    public = [scoring.scrub_confidential(str(name).strip(), names) for name in (there if isinstance(there, list) else [])
              if str(name).strip()][:3]
    place = "online" if row.values.get("virtual") is True else (row.values.get("where") or "place not listed")
    who = list(net.get("who_to_meet") or [])
    topics = list(net.get("topics") or []) + list((net.get("speaking") or {}).get("talk_ideas") or [])
    lines = ["**%s, %s.** %s." % (when_text(row.values.get("date"), row.values.get("time")), place,
                                  cost_text(row.values.get("cost"), None).capitalize())]
    lines.append("**People you know who may be there:** %s." % (
        ", ".join(store.link("person", person.title) for person in known[:3]) if known else "no one yet"))
    if public:
        lines.append("**From the group's public page:** %s." % "; ".join(public))
    def mid(text):
        """Their words, for the middle of a sentence: the first letter lowered unless it starts a name or acronym."""
        text = str(text).strip().rstrip(".")
        return text[:1].lower() + text[1:] if len(text) > 1 and text[1].islower() and " " in text else text
    about = scoring._text("%s %s" % (row.title, row.section or ""))
    own = list(net.get("topics") or [])
    # What they care about: the topics they named, or failing that their strongest skills.
    interests = own or list((profile.get("skills") or {}).get("core") or [])[:2]
    fitting = [topic for topic in interests if scoring._text(topic) and scoring._text(topic) in about]
    ideas = own + list((net.get("speaking") or {}).get("talk_ideas") or []) + interests
    story = (" Have one short story ready about %s: the problem, what you did, the result." % mid(ideas[0])
             if ideas else "")
    subject = row.title if not host or "**Why it has its own note:**" in (row.section or "") else None
    if fitting:
        lines.append("**Opener 1:** \"What is your team doing about %s?\" It is in this event's description and it "
                     "is one of your interests." % mid(fitting[0]))
    elif interests:
        lines.append("**Opener 1:** \"I spend most of my time on %s. Does that come up in your work?\"" % mid(interests[0]))
    elif subject:
        lines.append("**Opener 1:** \"What brought you to %s?\" Then ask what they hope to take back to work." % subject)
    elif host:
        lines.append("**Opener 1:** \"How long have you been coming to %s, and what keeps you coming back?\"" % host)
    else:
        lines.append("**Opener 1:** \"What are you working on this quarter?\"")
    if who:
        lines.append("**Opener 2:** \"I'm hoping to meet %s. Who here should I talk to?\"%s" % (mid(who[0]), story))
    else:
        lines.append("**Opener 2:** \"Who else here should I meet?\"%s" % story)
    if public and (net.get("speaking") or {}).get("interested"):
        lines.append("**If you meet an organiser:** ask what topics they are short of speakers on.")
    lines.append("**Bring:** one sentence on what you do, and a way to swap details.")
    lines.append("**After:** tell me who you met (\"I met ... at %s\"). Each gets a note and a follow-up date %d days "
                 "out." % (row.id, FOLLOW_UP_AFTER_EVENT_DAYS))
    lines.append("Written %s. Nothing was registered or sent." % today.isoformat())
    store.apply([{"type": "event", "id": row.id, "values": {}, "force": [],
                  "part": (model.PART_PREP, scoring.scrub_confidential(discreet_lines(lines), names))}])
    return _safe("Prep brief written into the note \"%s\" (%s): %d %s you know may be there, two openers and what to "
                 "bring. Open the note to read it." % (
                     row.title, row.id, len(known), "person" if len(known) == 1 else "people"), profile)
