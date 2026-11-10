"""Optional connections: a browser pass, signed-in LinkedIn, a calendar and a task tracker.

Everything here is off unless the person turned it on in their profile, and
nothing here calls an outside service. The engine cannot drive a browser or
a calendar. What it does is decide, from the profile, what the assistant may
do in this run, say so in exact words, refuse results that came from a
connection that is off, keep count against the daily caps, and write the
text of anything that is to be posted or added, so that confidential names
and private numbers never have to pass through the assistant's hands.

    browser      Claude in Chrome, for sites a script cannot read. Its own
                 tab, human pacing, never a CAPTCHA, never a form or a button
                 that applies, saves or follows.
    linkedin     Signed-in, read-only passes. Only with both switches on:
                 linkedin_signed_in and linkedin_risk_acknowledged. Daily
                 caps. In a quiet search no profile is ever opened, because
                 a profile view can be shown to its owner.
    calendar     Outlook or Google, for an event the person says they are
                 going to. Conflicts are checked first. No attendees.
    tracker      A comment with counts on an issue the person named.

The plugin gives no standing permission for browser, calendar or tracker
tools. Claude Code asks for each as usual, or the person allows them in
their own settings.

Set WORKDECERO_DRY_RUN=1 to have every instruction say "do not call the
tool": the text that would be sent is shown instead. Tests and trial runs
use it, so nothing is ever added to a real calendar or posted to a real
issue by accident.

Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import re

import scoring

DRY_RUN_ENV = "WORKDECERO_DRY_RUN"
USAGE_NAME = "usage.json"
# Conservative on purpose. LinkedIn restricts accounts that browse like a script.
LINKEDIN_CAPS = {"lite": {"pages": 20, "searches": 5}, "full": {"pages": 40, "searches": 10}}
BROWSER_PAGES = {"lite": 15, "full": 40}
PAUSE_SECONDS = (4, 12)                 # between page loads, varied, like a person reading
LINKEDIN_PAUSE_SECONDS = (8, 25)
EVENT_HOURS = 2                         # how long a calendar entry lasts when the listing gives no end time
TRACK_WORDS = {"jobs": "Job sweep", "contracts": "Contract sweep", "networking": "Networking sweep"}


def dry_run():
    return os.environ.get(DRY_RUN_ENV, "").strip().lower() in ("1", "true", "yes")


def plan(profile):
    """What the profile turns on. LinkedIn needs every one of its switches."""
    settings = profile.get("integrations") or {}
    browser = settings.get("browser") or {}
    tracker = settings.get("task_tracker") or {}
    on = browser.get("enabled") is True
    quiet = ((profile.get("jobs") or {}).get("search_visibility") == "quiet")
    return {
        "browser": on,
        "linkedin": on and browser.get("linkedin_signed_in") is True and browser.get("linkedin_risk_acknowledged") is True,
        "quiet": quiet,
        "calendar": settings.get("calendar") if settings.get("calendar") in ("outlook", "google") else None,
        "tracker": tracker.get("kind") if tracker.get("kind") == "linear" else None,
        "issues": dict((track, (tracker.get("%s_issue" % track) or "").strip()) for track in TRACK_WORDS),
        "mode": profile.get("mode") if profile.get("mode") in ("lite", "full") else "lite",
    }


# --------------------------------------------------------------------------
# Daily caps
# --------------------------------------------------------------------------

def _usage_path(home):
    return os.path.join(home, USAGE_NAME)


def _load_usage(home):
    try:
        with open(_usage_path(home), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def linkedin_left(profile, home, today=None):
    """How many LinkedIn pages and searches are left today: (pages, searches)."""
    today = today or datetime.date.today()
    caps = LINKEDIN_CAPS[plan(profile)["mode"]]
    used = (_load_usage(home).get("linkedin") or {}).get(today.isoformat()) or {}
    return (max(0, caps["pages"] - int(used.get("pages") or 0)),
            max(0, caps["searches"] - int(used.get("searches") or 0)))


def record_linkedin(home, pages, searches, today=None):
    """Add what one step used to today's count. Only the last week is kept."""
    today = today or datetime.date.today()
    usage = _load_usage(home)
    days = usage.get("linkedin") if isinstance(usage.get("linkedin"), dict) else {}
    day = dict(days.get(today.isoformat()) or {})
    day["pages"] = int(day.get("pages") or 0) + max(0, int(pages or 0))
    day["searches"] = int(day.get("searches") or 0) + max(0, int(searches or 0))
    days[today.isoformat()] = day
    oldest = (today - datetime.timedelta(days=7)).isoformat()
    usage["linkedin"] = dict((key, value) for key, value in days.items() if key >= oldest)
    os.makedirs(home, mode=0o700, exist_ok=True)
    temp = _usage_path(home) + ".tmp"
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(usage, out)
    os.replace(temp, _usage_path(home))
    return day


# --------------------------------------------------------------------------
# What the assistant is told at the start of a run
# --------------------------------------------------------------------------

def start_lines(profile, track, home, today=None):
    """The INTEGRATIONS section of a start report. Says what is on, the rules, and what is off."""
    use = plan(profile)
    lines = ["", "INTEGRATIONS THIS RUN. These tools are not pre-approved: Claude Code asks the person as usual."]
    if use["browser"]:
        lines += [
            "  BROWSER PASS: ON, for the places listed as needing a browser and nothing else. At most %d pages."
            % BROWSER_PAGES[use["mode"]],
            "    Open ONE new tab of your own, work only in it, and close it when you finish.",
            "    Wait %d to %d seconds between page loads, varying it, as a person reading would." % PAUSE_SECONDS,
            "    Read only. Never type into a form or a search box that needs a login, and never click apply, save, "
            "follow, connect or message.",
            "    If a site shows a CAPTCHA or asks you to prove you are human, stop on that site at once. Do not try "
            "to solve it. Put the site's name in not_covered.",
            "    Mark anything you read this way with \"read_with\": \"browser\".",
        ]
    else:
        lines.append("  BROWSER PASS: OFF. Do not use any browser tool. Places that need one go in not_covered.")
    if use["linkedin"]:
        pages, searches = linkedin_left(profile, home, today)
        lines += [
            "  SIGNED-IN LINKEDIN: ON, read-only. Left today: %d pages, %d searches. Stop when either runs out."
            % (pages, searches),
            "    Wait %d to %d seconds between pages. One tab. No rapid paging." % LINKEDIN_PAUSE_SECONDS,
            "    Never connect, message, follow, like, react, save, apply or endorse. Never open a message thread.",
        ]
        if track == "jobs":
            lines += [
                "    Allowed passes, for the roles listed under WARM PATHS only:",
                "      1. Their 1st-degree connections at that employer (a people search filtered to the company "
                "and to 1st connections).",
                "      2. The hiring team shown on the posting itself.",
                "      3. One targeted people search for the likely hiring manager's title at that employer.",
            ]
        else:
            lines.append("    Allowed: one people search per company or group, to see who they already know there.")
        if use["quiet"]:
            lines.append("    QUIET SEARCH: never open anyone's profile, because a profile view can be shown to its "
                         "owner. Read names and titles from search result pages and posting pages only.")
        lines += [
            "    Report people you found as \"connections\": [{\"name\", \"title\", \"company\", \"degree\": "
            "\"1st\" or \"2nd\"}], and add \"linkedin_pages\" and \"linkedin_searches\" with the true counts.",
        ]
    else:
        lines.append("  SIGNED-IN LINKEDIN: OFF. Do not open LinkedIn while signed in. Public search results only.")
    lines.append("  If a tool an integration needs is not in this session, do not look for another way. Add a line to "
                 "\"integrations_skipped\", such as \"browser: Claude in Chrome is not connected\".")
    if dry_run():
        lines.append("  DRY RUN: do not call any browser, calendar or tracker tool. Say what you would have done.")
    return lines


# --------------------------------------------------------------------------
# What the engine accepts back
# --------------------------------------------------------------------------

def screen(handoff, profile, home, today=None):
    """Take out of a hand-off anything that came from a connection that is off, and count LinkedIn use.

    Returns (hand-off, lines for the digest). The rules are enforced here, on
    what is written, as well as asked for in the skill.
    """
    use = plan(profile)
    cleaned = dict(handoff)
    lines = []
    for key in ("finds", "groups", "events"):
        items = handoff.get(key)
        if not isinstance(items, list):
            continue
        kept = [item for item in items if not (isinstance(item, dict) and item.get("read_with") == "browser"
                                               and not use["browser"])]
        if len(kept) != len(items):
            lines.append("%d item%s read with a browser %s left out: the browser pass is off in your profile."
                         % (len(items) - len(kept), "" if len(items) - len(kept) == 1 else "s",
                            "was" if len(items) - len(kept) == 1 else "were"))
        used = len([item for item in kept if isinstance(item, dict) and item.get("read_with") == "browser"])
        if used:
            lines.append("Browser pass: %d item%s read in your browser." % (used, "" if used == 1 else "s"))
        cleaned[key] = kept

    def strip(container):
        """Remove LinkedIn connections from one piece of warm-path research when LinkedIn is off."""
        if isinstance(container, dict) and container.get("connections") and not use["linkedin"]:
            container = dict(container)
            container.pop("connections")
            return container, True
        return container, False

    dropped = False
    if isinstance(handoff.get("warm"), dict):
        warm = {}
        for note_id, facts in handoff["warm"].items():
            warm[note_id], gone = strip(facts)
            dropped = dropped or gone
        cleaned["warm"] = warm
    cleaned, gone = strip(cleaned)
    if dropped or gone:
        lines.append("People reported from signed-in LinkedIn were left out: that connection is off in your profile.")
    pages, searches = handoff.get("linkedin_pages"), handoff.get("linkedin_searches")
    counted = [value if isinstance(value, int) and not isinstance(value, bool) else 0 for value in (pages, searches)]
    if use["linkedin"] and any(counted):
        day = record_linkedin(home, counted[0], counted[1], today)
        caps = LINKEDIN_CAPS[use["mode"]]
        lines.append("Signed-in LinkedIn: %d page%s and %d search%s this step. Today so far: %d of %d pages, %d of %d "
                     "searches." % (counted[0], "" if counted[0] == 1 else "s", counted[1],
                                    "" if counted[1] == 1 else "es", day["pages"], caps["pages"], day["searches"],
                                    caps["searches"]))
        if day["pages"] > caps["pages"] or day["searches"] > caps["searches"]:
            lines.append("The day's LinkedIn limit was passed. No more signed-in LinkedIn today.")
    for entry in handoff.get("integrations_skipped") or []:
        if isinstance(entry, str) and entry.strip():
            lines.append("Skipped: %s." % scoring.scrub_confidential(entry.strip().rstrip("."),
                                                                     scoring.confidential_names(profile)))
    return cleaned, lines


def connections(handoff_part):
    """People research found on LinkedIn, in a clean shape: [{name, title, company, degree}]."""
    found = []
    for person in (handoff_part or {}).get("connections") or []:
        if not isinstance(person, dict) or not str(person.get("name") or "").strip():
            continue
        degree = str(person.get("degree") or "").strip().lower()
        if degree not in ("1st", "2nd"):
            continue
        found.append({"name": str(person["name"]).strip(), "title": str(person.get("title") or "").strip(),
                      "company": str(person.get("company") or "").strip(), "degree": degree})
    return found


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def _start(day, time_text):
    """A local start time as YEAR-MONTH-DAYTHOUR:MINUTE, or the day alone when no time is known."""
    when = scoring._date(day)
    if when is None:
        return None, None
    match = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m", str(time_text or "").lower())
    if not match:
        return when.isoformat(), None
    hour = int(match.group(1)) % 12 + (12 if match.group(3) == "p" else 0)
    begin = datetime.datetime(when.year, when.month, when.day, hour, int(match.group(2) or 0))
    end = begin + datetime.timedelta(hours=EVENT_HOURS)
    return begin.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")


def calendar_lines(profile, title, day, time_text, where, link, why):
    """What to say or do about the calendar when the person is going to an event."""
    use = plan(profile)
    if not use["calendar"]:
        return ["Calendar is off, so add it to your calendar yourself."]
    names = scoring.confidential_names(profile)
    start, end = _start(day, time_text)
    if start is None:
        return ["The event has no date on file, so it was not offered to your calendar."]
    body = " ".join(part for part in (link or "", scoring.scrub_confidential(why or "", names)) if part).strip()
    lines = ["CALENDAR (%s). Do this now, in this order:" % use["calendar"],
             "  1. Look at their calendar for %s to %s. If anything is already there, tell the person what clashes "
             "and ask before adding." % (start, end or "the whole day"),
             "  2. Add ONE event, with no attendees and no invitations:",
             "       title:    %s" % scoring.scrub_confidential(title, names),
             "       start:    %s%s" % (start, "  (all day)" if end is None else ""),
             "       end:      %s" % (end or start),
             "       timezone: %s" % ((profile.get("location") or {}).get("timezone") or "their local time"),
             "       location: %s" % (scoring.scrub_confidential(where or "", names) or "(none)"),
             "       body:     %s" % (body or "(none)"),
             "  3. If no %s calendar tool is in this session, do not try another way. Tell the person to add it "
             "themselves." % use["calendar"]]
    if dry_run():
        lines.insert(1, "  DRY RUN: do not call the calendar tool. Show the person the event above and say it was not "
                        "added.")
    return lines


# --------------------------------------------------------------------------
# Task tracker
# --------------------------------------------------------------------------

def tracker_lines(profile, track, day, counts, not_covered=0):
    """The comment to post after a sweep: counts only, never a name. Empty when no tracker is set for this sweep.

    `counts` is a list of (label, number) in the order to show them.
    """
    use = plan(profile)
    issue = use["issues"].get(track)
    if not use["tracker"] or not issue:
        return []
    bits = ["%d %s" % (number, label) for label, number in counts]
    comment = "%s, %s: %s." % (TRACK_WORDS[track], day.isoformat(), ", ".join(bits))
    if not_covered:
        comment += " %d source%s not covered." % (not_covered, "" if not_covered == 1 else "s")
    comment += " The digest note in the tracker has the detail."
    lines = ["TASK TRACKER (%s). Post exactly this as ONE comment on issue %s, and change nothing else there:"
             % (use["tracker"], issue),
             "    %s" % comment,
             "  If no %s tool is in this session, do not try another way. Say it was not posted." % use["tracker"]]
    if dry_run():
        lines.insert(1, "  DRY RUN: do not call the tracker tool. Show the person the comment and say it was not posted.")
    return lines
