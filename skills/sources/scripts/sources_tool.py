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
    open            start a single change made by hand (an add or a remove) without a plan
    add             check the entries in the hand-off file and save the good ones
    reverify        re-check entries not verified for four weeks and drop dead ones
    remove          take the named source out and never add it back
    finish          record the run, print the summary for the person and end the run
    close           end a run started with `open`
    show            the map in plain words (reads only)

The hand-off file is deleted after each command that reads it.

`plan`, `plan-refresh` and `open` start a run. The commands that change the map
(add, reverify, remove, finish) only work while a run is open, and a run ends
with `finish` or `close`, or by itself after two hours.

Exit codes: 0 fine, 1 nothing usable or a problem to fix, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import handoff  # noqa: E402
import harvest  # noqa: E402
import liveness  # noqa: E402
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
DRAFT_DIR_ENV = handoff.DRAFT_DIR_ENV        # for tests: where the hand-off file lives
RUN = "sources"
HANDOFF_NAME = "sources-entries.json"
COMMANDS = ("plan", "plan-refresh", "open", "add", "reverify", "remove", "finish", "close", "show")
CHANGES = ("add", "reverify", "remove", "finish")     # only work while a run is open


def handoff_path():
    return os.path.join(handoff.folder(), HANDOFF_NAME)


def read_handoff():
    """What the assistant wrote for this command. The file is removed once read."""
    return handoff.read(HANDOFF_NAME)


_tilde = handoff.tilde


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


def _safe(text, profile):
    """Text for the conversation with every confidential name taken out.

    This skill reads web pages, and a page can carry instructions meant to make
    an assistant leak what it knows. So the conversation is never given the
    current employer, confidential names, pay or contact details in the first
    place. Filtering by those names happens inside this helper.
    """
    return scoring.scrub_confidential(text, scoring.confidential_names(profile), "[withheld]")


def plan(refresh=False, out=None, today=None):
    out = out or sys.stdout
    today = today or datetime.date.today()
    profile = _profile()
    hidden = _off_limits(profile, today)
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
    print("  Field: %s" % _safe((profile.get("person") or {}).get("headline", ""), profile), file=out)
    jobs, contracts, networking = profile.get("jobs") or {}, profile.get("contracts") or {}, profile.get("networking") or {}
    if "jobs" in tracks:
        print("  Job titles: %s." % _safe(", ".join(jobs.get("titles") or []), profile), file=out)
        if jobs.get("preferred_industries"):
            print("  Preferred industries: %s." % _safe(", ".join(jobs["preferred_industries"]), profile), file=out)
        watch = [name for name in jobs.get("watch_employers") or [] if merge.normalize_company(name) not in hidden]
        if watch:
            print("  Employers they asked to watch (include these): %s." % _safe(", ".join(watch), profile), file=out)
    if "contracts" in tracks:
        print("  Contract services: %s. Ways of working: %s." % (
            _safe(", ".join(contracts.get("services") or []), profile),
            ", ".join(contracts.get("engagement_types") or [])), file=out)
        bids = contracts.get("public_bids") or {}
        print("  Public bids: %s." % ("yes, for %s" % ", ".join(bids.get("regions") or ["their state"])
                                      if bids.get("enabled") else "no, skip bid portals"), file=out)
    if "networking" in tracks:
        print("  Wants to meet: %s." % _safe(", ".join(networking.get("who_to_meet") or []), profile), file=out)
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
            "The state's public workforce job board, the one its labor department runs. Every state has one. "
            "It goes in every map (1 search). Then any city or regional job board.",
            "Job boards and association career centers for this field (1 to %d searches)." % max(1, share),
        ):
            step += 1
            print("  %d. JOBS: %s" % (step, text), file=out)
    if "contracts" in tracks:
        for text in (
            "Marketplaces and talent networks that carry this kind of work (1 to %d searches). For each one, pass "
            "the page a freelancer uses to find work or to join, never the page a client uses to hire." % max(1, share),
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
            "Meetups and user groups for this field in the area (about %d searches). Search Meetup ONCE FOR EACH "
            "of these field words, with the area: %s. From each search take the groups with the most members "
            "that have an event coming up: the largest active groups must not be missed. Put the member count "
            "in notes. Each group needs the date of its most recent event. Skip any with nothing in the last "
            "six months." % (max(2, share), ", ".join(field_words(profile, 2)) or "the field's main topic"),
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


def has_matching_role(entry, profile, fetcher, today):
    """None when a new employer has an open role that matches the person's titles, else why it is left out."""
    if entry.get("how") != "api" or not isinstance(entry.get("ats"), dict):
        return "its job list could not be read, so no matching open role could be confirmed. Add it by name if you want it"
    found = harvest.harvest({"jobs": [entry]}, profile, fetcher, today)
    if found.finds:
        return None
    if found.failed:
        return "its job list did not answer, so no matching open role could be confirmed"
    return "it has no open role that matches your titles right now"


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
    keep = None
    if source_map.get("built_on"):
        # A refresh. The map grows only by employers that have something for this person right now.
        def keep(entry):
            return has_matching_role(entry, profile, fetcher or liveness.HttpFetcher(), today)
    result = sources.add_entries(source_map, proposals, fetcher=fetcher, today=today, keep_new_employer=keep)
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


EMPLOYER_TARGET = {"lite": 12, "full": 30}
_FIELD_NOISE = set("""a an and at for in of on the to with by vp svp evp chief officer head director senior sr junior
    lead principal staff manager management managing associate assistant executive fractional freelance contract
    consultant consulting advisor advisory specialist coordinator leader leaders leading team teams years year
    recently focused focus based experience experienced b2b b2c software services service small business businesses
    company companies features strategy strategic new work working people""".split())


def field_words(profile, limit=3):
    """The words that name this person's field, most used first. Group searches are run once for each."""
    person, skills = profile.get("person") or {}, profile.get("skills") or {}
    texts = [person.get("headline") or ""] + list((profile.get("jobs") or {}).get("titles") or []) + \
        list((profile.get("contracts") or {}).get("titles") or []) + list(skills.get("core") or [])
    counts, order = {}, []
    for text in texts:
        seen = set()
        for word in "".join(ch if ch.isalnum() else " " for ch in scoring.scrub_confidential(
                text, scoring.confidential_names(profile), " ")).split():
            key = word.lower()
            if key in _FIELD_NOISE or key.isdigit() or (len(key) < 3 and word != word.upper()) or key in seen:
                continue
            seen.add(key)
            if key not in counts:
                order.append(word if word == word.upper() else key)
            counts[key] = counts.get(key, 0) + 1
    ranked = sorted(order, key=lambda word: (-counts[word.lower()], order.index(word)))
    return ranked[:limit]


def coverage_gaps(source_map, profile):
    """What a first build still lacks, in plain words. An empty list means the plan was covered."""
    tracks = profile.get("tracks") or {}
    mode = profile.get("mode") or "lite"
    gaps = []

    def count(track, *kinds):
        return len([entry for entry in source_map.get(track) or [] if entry.get("kind") in kinds])

    if tracks.get("jobs"):
        employers, target = count("jobs", "employer"), EMPLOYER_TARGET.get(mode, 12)
        if employers < target:
            gaps.append("%d of %d employers" % (employers, target))
        if not count("jobs", "job-board"):
            gaps.append("no state workforce job board yet")
        if not count("jobs", "field-board"):
            gaps.append("no job board for the field yet")
    if tracks.get("contracts"):
        taken = (profile.get("contracts") or {}).get("engagement_types") or []
        if not count("contracts", "marketplace", "talent-network"):
            gaps.append("no marketplaces or talent networks yet")
        if any(kind in taken for kind in ("independent-1099", "corp-to-corp", "w2-contract")) and \
                not count("contracts", "agency"):
            gaps.append("no agencies yet")
        if "expert-call" in taken and not count("contracts", "expert-network"):
            gaps.append("no expert networks yet")
        if ((profile.get("contracts") or {}).get("public_bids") or {}).get("enabled") and \
                not count("contracts", "bid-portal"):
            gaps.append("no public-bid portal yet")
    if tracks.get("networking"):
        groups, wanted = count("networking", "meetup", "user-group"), max(2, len(field_words(profile, 2)))
        if groups < wanted:
            gaps.append("%d of %d meetups or user groups (one Meetup search for each of: %s)" % (
                groups, wanted, ", ".join(field_words(profile, 2))))
        if not count("networking", "association", "chamber"):
            gaps.append("no association or chamber yet")
    return gaps


def finish(searches, skipped, out=None, today=None):
    """Record the run. Returns 0 when recorded, 1 when research is asked for one more pass first."""
    out = out or sys.stdout
    today = today or datetime.date.today()
    profile = _profile()
    source_map = sources.load()
    first = not source_map.get("built_on")
    gaps = coverage_gaps(source_map, profile) if first else []
    left = sources.budget(profile, refresh=not first) - searches
    if gaps and left > 0 and handoff.note(RUN, "coverage"):
        # Said once per run. The second finish takes the map as it is and lists what is missing.
        print("COVERAGE CHECK: %d search%s left, and the map is short of the plan." % (left, "" if left == 1 else "es"),
              file=out)
        for gap in gaps:
            print("  %s" % gap, file=out)
        print("Do one more pass on exactly these gaps with the searches you have left, add what you find, and "
              "run finish again. If nothing more can be found, run finish again and it will record what is "
              "missing. Nothing was recorded yet.", file=out)
        return 1
    if gaps:
        listed = [str(part).strip() for part in skipped] if isinstance(skipped, (list, tuple)) else \
            [part.strip() for part in (skipped or "").split(";")]
        skipped = [part for part in listed if part] + ["Not covered: %s" % gap for gap in gaps]
    source_map["area"] = _area(profile)
    source_map["field"] = _safe((profile.get("person") or {}).get("headline", ""), profile)
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
    notes = [part for part in source_map["skipped"] if not part.startswith("Not covered: ")]
    missing = [part[len("Not covered: "):] for part in source_map["skipped"] if part.startswith("Not covered: ")]
    if notes:
        print("  Skipped to stay in budget: %s." % "; ".join(notes), file=out)
    if missing:
        print("  Not covered: %s." % "; ".join(missing), file=out)
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
        if command in CHANGES:
            handoff.require_run(RUN, "plan` (or `open` for a single change")
        if command == "plan" or command == "plan-refresh":
            code = plan(refresh=command == "plan-refresh")
            if code == 0:
                handoff.open_run(RUN)
            return code
        if command == "open":
            _profile()
            handoff.open_run(RUN)
            print("Ready. Write the hand-off file, run `add` or `remove`, then run `close`.")
            return 0
        if command == "close":
            handoff.close_run(RUN)
            print("Closed.")
            return 0
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
            code = finish(searches, data.get("skipped") or [])
            if code == 0:
                handoff.close_run(RUN)      # a coverage reminder leaves the run open for one more pass
            return code
        if command == "show":
            return show()
    except (ValueError, sources.SourceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
