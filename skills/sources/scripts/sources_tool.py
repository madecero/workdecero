#!/usr/bin/env python3
"""The helper behind /workdecero:sources.

Research proposes places to look. This script checks each one with a live
call, drops what does not hold up, removes duplicates and keeps the file
`sources.yaml` next to the profile.

Nothing found on the web ever travels on a command line. The assistant
writes what it found to ONE fixed file with its Write tool, and every
command here is a fixed line with no arguments:

    The hand-off file:  ~/.cache/workdecero/sources-entries.json

What goes in the hand-off file depends on the command that follows:
    add       {"jobs": [...], "contracts": [...], "networking": [...]}
              entry: {"name", "url", "kind", "notes", "last_active", "how", "added_by"}
    remove    {"remove": "Name of the source"}
    finish    {"searches": 9, "skipped": ["field job boards", "city job board"]}

Commands (none takes an argument):
    plan            what to research: the area, the field, the search budget, what is already
                    in the map, and a checklist in priority order (reads only)
    plan-refresh    the same for a refresh, with what is due for a re-check
    add             check the entries in the hand-off file and save the good ones
    reverify        re-check entries not verified for four weeks and drop dead ones
    remove          take the named source out and never add it back
    finish          record the run and print the summary for the person
    show            the map in plain words (reads only)

The hand-off file is deleted after each command that reads it.

Exit codes: 0 fine, 1 nothing usable or a problem to fix, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import merge  # noqa: E402
import scoring  # noqa: E402
import sources  # noqa: E402
import wdprofile  # noqa: E402

KIND_WORDS = {
    "employer": "employers", "job-board": "state and city job boards", "field-board": "job boards for your field",
    "marketplace": "marketplaces", "talent-network": "talent networks", "agency": "agencies",
    "expert-network": "expert networks", "bid-portal": "public-bid portals",
    "chamber": "chambers", "meetup": "meetups", "user-group": "user groups", "association": "associations",
    "conference": "conferences",
}
KIND_ONE = {
    "employer": "employer", "job-board": "state or city job board", "field-board": "job board for your field",
    "marketplace": "marketplace", "talent-network": "talent network", "agency": "agency",
    "expert-network": "expert network", "bid-portal": "public-bid portal", "chamber": "chamber", "meetup": "meetup",
    "user-group": "user group", "association": "association", "conference": "conference",
}
TRACK_WORDS = {"jobs": "Jobs", "contracts": "Contract work", "networking": "Networking"}
DRAFT_DIR_ENV = "WORKDECERO_DRAFT_DIR"       # for tests: where the hand-off file lives
HANDOFF_NAME = "sources-entries.json"
COMMANDS = ("plan", "plan-refresh", "add", "reverify", "remove", "finish", "show")


def handoff_path():
    folder = os.path.expanduser(os.environ.get(DRAFT_DIR_ENV) or os.path.join("~", ".cache", "workdecero"))
    return os.path.join(folder, HANDOFF_NAME)


def read_handoff():
    """What the assistant wrote for this command. The file is removed once read."""
    path = handoff_path()
    if not os.path.isfile(path):
        raise ValueError("Nothing to read. Write what you found to %s first, then run this command again."
                         % _tilde(path))
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    os.remove(path)
    folder = os.path.dirname(path)
    if os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("The hand-off file was not valid JSON (%s). Write it again as one JSON object." % exc)
    if not isinstance(data, dict):
        raise ValueError("The hand-off file must hold one JSON object.")
    return data


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _profile():
    try:
        profile, problems = wdprofile.load()
    except OSError:
        raise ValueError("There is no profile yet. Run /workdecero:setup first.")
    if profile is None or problems:
        raise ValueError("Your profile has problems to fix first. Run /workdecero:setup.")
    return profile


def _area(profile):
    location = profile.get("location") or {}
    parts = [location.get("home_base")]
    if location.get("metro"):
        parts.append("%s area" % location["metro"])
    return ", ".join(part for part in parts if part)


def _off_limits(profile, today):
    """Employer names the map must not contain: the current employer, confidential names, skipped employers."""
    names = list(scoring.confidential_names(profile))
    for item in scoring.active_exclusions(profile, today):
        if item.get("kind") == "employer" and item.get("action", "skip") == "skip":
            names.append(item.get("name", ""))
    return [merge.normalize_company(name) for name in names if name]


def plan(refresh=False, out=None, today=None):
    out = out or sys.stdout
    today = today or datetime.date.today()
    profile = _profile()
    source_map = sources.load()
    location = profile.get("location") or {}
    tracks = [track for track in sources.TRACKS if (profile.get("tracks") or {}).get(track)]
    budget = sources.budget(profile, refresh)
    mode = profile.get("mode") or "lite"

    print("RESEARCH PLAN (%s)" % ("refresh" if refresh else "first build"), file=out)
    print("  Area: %s. Radius: %s miles. Work modes: %s." % (
        _area(profile), location.get("radius_miles"), ", ".join(location.get("work_modes") or [])), file=out)
    if location.get("other_areas"):
        print("  Also in reach: %s." % ", ".join(location["other_areas"]), file=out)
    print("  Field: %s" % (profile.get("person") or {}).get("headline", ""), file=out)
    jobs, contracts, networking = profile.get("jobs") or {}, profile.get("contracts") or {}, profile.get("networking") or {}
    if "jobs" in tracks:
        print("  Job titles: %s." % ", ".join(jobs.get("titles") or []), file=out)
        if jobs.get("preferred_industries"):
            print("  Preferred industries: %s." % ", ".join(jobs["preferred_industries"]), file=out)
        if jobs.get("watch_employers"):
            print("  Employers they asked to watch (include these): %s." % ", ".join(jobs["watch_employers"]), file=out)
    if "contracts" in tracks:
        print("  Contract services: %s. Ways of working: %s." % (
            ", ".join(contracts.get("services") or []), ", ".join(contracts.get("engagement_types") or [])), file=out)
        bids = contracts.get("public_bids") or {}
        print("  Public bids: %s." % ("yes, for %s" % ", ".join(bids.get("regions") or ["their state"])
                                      if bids.get("enabled") else "no, skip bid portals"), file=out)
    if "networking" in tracks:
        print("  Wants to meet: %s." % ", ".join(networking.get("who_to_meet") or []), file=out)
        print("  Event formats: %s. Areas: %s." % (
            ", ".join(networking.get("formats") or []),
            ", ".join(networking.get("areas") or [location.get("home_base", "")])), file=out)
    print("  Sweeps on: %s. Mode: %s." % (", ".join(TRACK_WORDS[track] for track in tracks), mode), file=out)
    print("", file=out)
    print("SEARCH BUDGET: at most %d web searches this run. Reading a page you already found does not count." % budget,
          file=out)
    print("", file=out)

    counts = dict((track, len(source_map.get(track) or [])) for track in sources.TRACKS)
    print("ALREADY IN THE MAP: %s." % ", ".join("%d for %s" % (counts[t], TRACK_WORDS[t].lower()) for t in sources.TRACKS),
          file=out)
    if source_map.get("removed"):
        print("  Removed by the person, never add back: %s." % ", ".join(source_map["removed"]), file=out)
    if refresh:
        stale = sources.stale_entries(source_map, today)
        dormant = sources.needs_activity_check(source_map, today)
        print("  %d entr%s not checked for four weeks: run `reverify` first, it needs no searches." % (
            len(stale), "y" if len(stale) == 1 else "ies"), file=out)
        if dormant:
            print("  Groups to look at again (no recent activity on record): %s." % ", ".join(
                entry["name"] for entry in dormant), file=out)
    print("", file=out)

    print("CHECKLIST, most important first. Stop when the budget runs out and report what was skipped.", file=out)
    step = 0
    share = max(1, budget // max(1, len(tracks) * 2))
    if "jobs" in tracks:
        for text in (
            "Largest employers in this field within the area (about %d searches for lists). Aim for %d employers. "
            "For each one, open its careers page (reading a page is free) and pass that page to the helper, "
            "which finds and checks the job site behind it. Spend leftover searches on the biggest employers "
            "whose job site was not found: search the employer name with the word careers and a system name "
            "such as Workday." % (max(2, share), 12 if mode == "lite" else 30),
            "The state job board and any city or regional job board (1 search).",
            "Job boards and association career centers for this field (1 to %d searches)." % max(1, share),
        ):
            step += 1
            print("  %d. JOBS: %s" % (step, text), file=out)
    if "contracts" in tracks:
        for text in (
            "Marketplaces and talent networks that carry this kind of work (1 to %d searches)." % max(1, share),
            "Staffing or placement agencies for this field in the area, and expert networks if they sell advice "
            "(1 to %d searches)." % max(1, share),
        ):
            step += 1
            print("  %d. CONTRACT WORK: %s" % (step, text), file=out)
        if (contracts.get("public_bids") or {}).get("enabled"):
            step += 1
            print("  %d. CONTRACT WORK: the state and local public-bid portals (1 search)." % step, file=out)
    if "networking" in tracks:
        for text in (
            "Meetups and user groups for this field in the area, each with the date of its most recent event "
            "(about %d searches). Skip any with nothing in the last six months." % max(2, share),
            "Professional associations and their local chapters, and the local chamber (1 to %d searches)." % max(1, share),
        ):
            step += 1
            print("  %d. NETWORKING: %s" % (step, text), file=out)
    print("", file=out)
    print("PRIVACY: searches may name the area and the field. Never put the person's name, their current "
          "employer or a confidential name into a search.", file=out)
    return 0


def _print_result(result, out):
    for label, items in (("Added", result.added), ("Refreshed", result.updated)):
        if items:
            print("%s (%d): %s" % (label, len(items), "; ".join("%s" % name for _, name in items)), file=out)
    if result.dropped:
        print("Dropped (%d):" % len(result.dropped), file=out)
        for _, name, reason in result.dropped:
            print("  %s: %s" % (name, reason), file=out)
    if not (result.added or result.updated or result.dropped):
        print("Nothing to do.", file=out)


def add(text, out=None, fetcher=None, today=None):
    out = out or sys.stdout
    today = today or datetime.date.today()
    if isinstance(text, dict):
        proposals = text
    else:
        try:
            proposals = json.loads(text)
        except ValueError as exc:
            raise ValueError("The entries are not valid JSON (%s). Write one JSON object." % exc)
    if not isinstance(proposals, dict) or not any(track in proposals for track in sources.TRACKS):
        raise ValueError('Send an object with "jobs", "contracts" or "networking" lists.')
    profile = _profile()
    off_limits = _off_limits(profile, today)
    hidden = 0
    for track in sources.TRACKS:
        kept = []
        for entry in proposals.get(track) or []:
            if isinstance(entry, dict) and merge.normalize_company(entry.get("name")) in off_limits:
                hidden += 1          # never write the name, not even as a dropped line
            else:
                kept.append(entry)
        proposals[track] = kept
    source_map = sources.load()
    result = sources.add_entries(source_map, proposals, fetcher=fetcher, today=today)
    if result.added or result.updated:
        sources.save(source_map)
    _print_result(result, out)
    if hidden:
        print("%d left out because your profile excludes or keeps that employer confidential." % hidden, file=out)
    return 0 if (result.added or result.updated) else 1


def reverify(out=None, fetcher=None, today=None):
    out = out or sys.stdout
    _profile()
    source_map = sources.load()
    result = sources.reverify(source_map, fetcher=fetcher, today=today)
    if result.updated or result.dropped:
        sources.save(source_map)
    _print_result(result, out)
    return 0


def remove(name, out=None):
    out = out or sys.stdout
    source_map = sources.load()
    found = sources.remove(source_map, name)
    sources.save(source_map)
    print(("Removed %s. It will not be added back." if found else
           "%s was not in your map. It is now on the never-add list.") % name, file=out)
    return 0


def _summary_lines(source_map):
    lines = []
    for track in sources.TRACKS:
        entries = source_map.get(track) or []
        if not entries:
            continue
        by_kind = {}
        for entry in entries:
            by_kind.setdefault(entry.get("kind"), []).append(entry)
        bits = ["%d %s" % (len(items), KIND_WORDS.get(kind, kind)) for kind, items in sorted(by_kind.items())]
        lines.append("  %-14s %s" % (TRACK_WORDS[track], ", ".join(bits)))
        if track == "jobs":
            checked = [entry for entry in by_kind.get("employer", []) if entry.get("how") == "api"]
            if by_kind.get("employer"):
                lines.append("  %-14s %d of %d employers have a job list that was checked live" % (
                    "", len(checked), len(by_kind["employer"])))
    return lines


def finish(searches, skipped, out=None, today=None):
    out = out or sys.stdout
    today = today or datetime.date.today()
    profile = _profile()
    source_map = sources.load()
    first = not source_map.get("built_on")
    source_map["area"] = _area(profile)
    source_map["field"] = (profile.get("person") or {}).get("headline", "")
    source_map["mode"] = profile.get("mode") or "lite"
    source_map["built_on" if first else "refreshed_on"] = today.isoformat()
    source_map["searches_used"] = searches
    if isinstance(skipped, (list, tuple)):
        source_map["skipped"] = [str(part).strip() for part in skipped if str(part).strip()]
    else:
        source_map["skipped"] = [part.strip() for part in (skipped or "").split(";") if part.strip()]
    sources.save(source_map)
    total = sum(len(source_map.get(track) or []) for track in sources.TRACKS)
    print("Your local map is %s: %d places to look around %s." % (
        "built" if first else "refreshed", total, source_map["area"]), file=out)
    print("", file=out)
    for line in _summary_lines(source_map):
        print(line, file=out)
    print("", file=out)
    print("  Searches used: %d of %d." % (searches, sources.budget(profile, refresh=not first)), file=out)
    if source_map["skipped"]:
        print("  Skipped to stay in budget: %s." % "; ".join(source_map["skipped"]), file=out)
    print("  Saved to %s. You can edit it, or say \"remove <name> from my sources\"." % _tilde(sources.path()), file=out)
    print("  It is re-checked every few weeks as part of your sweeps.", file=out)
    return 0


def show(out=None):
    out = out or sys.stdout
    source_map = sources.load()
    total = sum(len(source_map.get(track) or []) for track in sources.TRACKS)
    if not total:
        print("Your local map is empty. Say \"build my local map\" to create it.", file=out)
        return 0
    print("Your local map: %d places to look%s." % (
        total, " around %s" % source_map["area"] if source_map.get("area") else ""), file=out)
    for track in sources.TRACKS:
        entries = sorted(source_map.get(track) or [], key=lambda e: (e.get("kind", ""), e.get("name", "").lower()))
        if not entries:
            continue
        print("\n%s" % TRACK_WORDS[track].upper(), file=out)
        for entry in entries:
            system = " (%s)" % entry["ats"]["system"] if isinstance(entry.get("ats"), dict) else ""
            print("  %s: %s%s. Checked %s by %s." % (KIND_ONE.get(entry.get("kind"), entry.get("kind")),
                                                  entry.get("name"), system, entry.get("verified_on"),
                                                  entry.get("how")), file=out)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if len(argv) != 1 or command not in COMMANDS:
        print(__doc__, file=sys.stderr)
        if len(argv) > 1:
            print("This helper takes no arguments. Put what you found in the hand-off file.", file=sys.stderr)
        return 2
    try:
        if command == "plan":
            return plan()
        if command == "plan-refresh":
            return plan(refresh=True)
        if command == "add":
            return add(read_handoff())
        if command == "reverify":
            return reverify()
        if command == "remove":
            name = str(read_handoff().get("remove") or "").strip()
            if not name:
                raise ValueError('Write {"remove": "Name of the source"} to the hand-off file first.')
            return remove(name)
        if command == "finish":
            data = read_handoff()
            searches = data.get("searches")
            if isinstance(searches, bool) or not isinstance(searches, int) or searches < 0:
                raise ValueError('Write {"searches": <number>, "skipped": [...]} to the hand-off file first.')
            return finish(searches, data.get("skipped") or [])
        if command == "show":
            return show()
    except (ValueError, sources.SourceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
