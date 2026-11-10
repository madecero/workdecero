#!/usr/bin/env python3
"""The helper behind /workdecero:job-sweep.

The engine does every step that touches the person's private settings or
their vault. The assistant researches on the web and hands plain facts back
through ONE fixed file, written with its Write tool:

    The hand-off file:  ~/.cache/workdecero/sweep-handoff.json

What goes in the hand-off file depends on the command that follows:
    intake   {"assessed": [{"id": "c1", "fit_assessment": 31, "why": "...", ...}],
              "finds": [{"title": "...", "company": "...", "url": "...", "fit_assessment": 28, ...}],
              "searches": 7, "not_covered": ["Site that needs a login"]}
    warm     {"warm": {"J-12": {"hiring_manager": {...}, "recruiter": {...}, "email_format": {...}, ...}}}

Commands (none takes an argument):
    start     re-check open postings, read employer job lists, and print what to research
    restart   discard a sweep left unfinished and start again
    intake    score the judgements and finds in the hand-off file and write the notes
    warm      write warm paths from the research in the hand-off file
    finish    verify, write the digest note, and print the summary for the person
    cancel    stop the sweep. Notes already written stay
    list      open roles with their ids and statuses (reads only)
    open      start a change the person asked for outside a sweep
    update    record the status changes in the hand-off file ({"updates": [{"id": "J-12", "status": "Applied"}]})
    close     end what `open` started

`start` (or `restart`) opens a sweep. `intake`, `warm` and `finish` only work
while one is open and has been active in the last six hours.

Exit codes: 0 fine, 1 a problem to fix, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "trackers"))

import handoff  # noqa: E402
import integrations  # noqa: E402
import liveness  # noqa: E402
import sources  # noqa: E402
import sweep  # noqa: E402
import snapshot  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

DRAFT_DIR_ENV = handoff.DRAFT_DIR_ENV        # for tests: where the hand-off and state files live
HANDOFF_NAME = "sweep-handoff.json"
COMMANDS = ("start", "restart", "intake", "warm", "finish", "cancel", "list", "open", "update", "close")
RUN = "job"
ACTIVE_MINUTES = 30
STALE_HOURS = 6                              # a sweep with no activity for this long has ended


def work_dir():
    return handoff.folder()


def handoff_path():
    return os.path.join(work_dir(), HANDOFF_NAME)


def read_handoff():
    """What the assistant wrote for this command. The file is removed once read."""
    return handoff.read(HANDOFF_NAME)


def _context():
    try:
        profile, problems = wdprofile.load()
    except OSError:
        raise ValueError("There is no profile yet. Run /workdecero:setup first.")
    if profile is None or problems:
        raise ValueError("Your profile has problems to fix first. Run /workdecero:setup.")
    if not (profile.get("tracks") or {}).get("jobs"):
        raise ValueError("The job sweep is switched off in this profile. Run /workdecero:setup to turn it on.")
    try:
        store = vault.VaultStore.from_profile(profile, wdprofile.config_home())
    except vault.VaultError as exc:
        raise ValueError(str(exc))
    store.ensure_layout()
    return profile, store


def _state():
    path = sweep.state_path(work_dir())
    state = None if os.path.islink(path) else sweep.load_state(work_dir())
    if state is None:
        raise ValueError("No sweep is in progress, so nothing was changed. Run the start command first.")
    idle = (time.time() - os.path.getmtime(path)) / 3600.0
    if idle > STALE_HOURS:
        raise ValueError("The sweep in progress has been idle for %d hours, so nothing was changed. "
                         "Run the restart command to begin a fresh one." % int(idle))
    return state


def start(out=None, fetcher=None, today=None, force=False):
    out = out or sys.stdout
    profile, store = _context()
    path = sweep.state_path(work_dir())
    if os.path.isfile(path) and not force:
        idle = (time.time() - os.path.getmtime(path)) / 60.0
        if idle < ACTIVE_MINUTES:
            print("A SWEEP IS ALREADY IN PROGRESS (last activity %d minute%s ago)." % (int(idle), "" if int(idle) == 1 else "s"),
                  file=out)
            print("  If you started it earlier in this conversation, carry on with the next step.", file=out)
            print("  Otherwise tell the person. Only if they want to discard it, run the restart command.", file=out)
            return 1
    sweep.clear_state(work_dir())
    handoff.ensure_folder()                  # private, and never a link, before anything is written into it
    source_map = sources.load()
    state = sweep.start(profile, store, source_map, fetcher or liveness.HttpFetcher(), today)
    sweep.save_state(work_dir(), state)
    print(sweep.start_report(state, profile), file=out)
    print("\n".join(integrations.start_lines(profile, "jobs", wdprofile.config_home(), today)), file=out)
    if not any(source_map.get(track) for track in sources.TRACKS):
        print("\nNOTE: there is no local map yet, so only web searches were available. Tell the person they can run "
              "/workdecero:sources (or say \"build my local map\") to make later sweeps much more thorough.", file=out)
    return 0


def _screen(state, data, profile, today):
    """Refuse what came from a connection that is off, count LinkedIn use, and keep the lines for the digest."""
    data, lines = integrations.screen(data, profile, wdprofile.config_home(), today)
    for line in lines:
        if line not in state.setdefault("integration_lines", []):
            state["integration_lines"].append(line)
    return data


def intake(out=None, fetcher=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    state = _state()
    data = _screen(state, read_handoff(), profile, today)
    source_map = sources.load()
    before = json.dumps(source_map, sort_keys=True)
    state = sweep.intake(state, data, profile, store, source_map, fetcher or liveness.HttpFetcher(), today)
    if json.dumps(source_map, sort_keys=True) != before:
        sources.save(source_map)
    report = sweep.intake_report(state, profile)          # may note that it has nudged, so save after
    sweep.save_state(work_dir(), state)
    print(report, file=out)
    return 0


def warm(out=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    state = _state()
    data = _screen(state, read_handoff(), profile, today)
    added = sweep.add_connections(state, data, profile, store)       # people found on LinkedIn, when that is on
    before = len(state["warm_done"])
    state = sweep.warm(state, data, profile, store, today)
    sweep.save_state(work_dir(), state)
    print("WARM PATHS WRITTEN: %d.%s" % (len(state["warm_done"]) - before,
                                         " People added from LinkedIn: %d." % added if added else ""), file=out)
    return 0


def finish(out=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    state = _state()
    refusal = sweep.refusal(state, profile)
    if refusal:
        sweep.save_state(work_dir(), state)      # it refuses once: the next finish goes ahead
        print(refusal, file=out)
        return 1
    store.refresh_rollups()
    problems = sweep.verify(state, store)
    title, featured, more = sweep.write_digest(state, profile, store, problems, today)
    print(sweep.finish_report(state, profile, store, title, featured, more, problems), file=out)
    import datetime as _datetime
    near = len([entry for entry in state["new"] if entry.get("near")])
    for text in integrations.tracker_lines(profile, "jobs", today or _datetime.date.today(), [
            ("new fits", len(state["new"]) - near), ("near misses", near), ("closed", len(state["closed"])),
            ("left out", state["left_out"])], len(state["not_covered"])):
        print(text, file=out)
    line = snapshot.refresh(store, profile, today)          # only when the profile asks for it
    if line:
        print(line, file=out)
    sweep.clear_state(work_dir())
    if os.path.isfile(handoff_path()):
        os.remove(handoff_path())
    if os.path.isdir(work_dir()) and not os.listdir(work_dir()):
        os.rmdir(work_dir())
    return 0


def cancel(out=None):
    out = out or sys.stdout
    had = sweep.load_state(work_dir()) is not None
    sweep.clear_state(work_dir())
    if os.path.isfile(handoff_path()):
        os.remove(handoff_path())
    if os.path.isdir(work_dir()) and not os.listdir(work_dir()):
        os.rmdir(work_dir())
    print("Sweep stopped. %s" % ("Notes already written stay in the tracker." if had else "No sweep was in progress."),
          file=out)
    return 0


def _sweep_open():
    """True when a sweep is open and recent."""
    path = sweep.state_path(work_dir())
    return sweep.load_state(work_dir()) is not None and (time.time() - os.path.getmtime(path)) / 3600.0 <= STALE_HOURS


def listing(out=None):
    out = out or sys.stdout
    profile, store = _context()
    print(sweep.listing(store, profile), file=out)
    return 0


def open_change(out=None):
    out = out or sys.stdout
    _context()
    handoff.open_run(RUN)
    print("Ready. Write the hand-off file, run `update`, then run `close`.", file=out)
    return 0


def update(out=None, today=None):
    out = out or sys.stdout
    if not _sweep_open():
        handoff.require_run(RUN, "open")
    profile, store = _context()
    data = read_handoff()
    done, problems = sweep.apply_updates(data.get("updates"), store, profile, today)
    for line in done:
        print("DONE  " + line, file=out)
    for line in problems:
        print("NOT DONE  " + line, file=out)
    if not done and not problems:
        print('Nothing to do. Write {"updates": [{"id": "J-12", "status": "Applied"}]} to the hand-off file.', file=out)
    return 0 if done and not problems else 1


def close_change(out=None):
    out = out or sys.stdout
    handoff.close_run(RUN)
    if sweep.load_state(work_dir()) is None:
        if os.path.isfile(handoff_path()):
            os.remove(handoff_path())
        if os.path.isdir(work_dir()) and not os.path.islink(work_dir()) and not os.listdir(work_dir()):
            os.rmdir(work_dir())
    print("Closed.", file=out)
    return 0


def _dispatch(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if len(argv) != 1 or command not in COMMANDS:
        print(__doc__, file=sys.stderr)
        if len(argv) > 1:
            print("This helper takes no arguments. Put your results in the hand-off file.", file=sys.stderr)
        return 2
    try:
        if command == "start":
            return start()
        if command == "restart":
            return start(force=True)
        if command == "intake":
            return intake()
        if command == "warm":
            return warm()
        if command == "finish":
            return finish()
        if command == "cancel":
            return cancel()
        if command == "list":
            return listing()
        if command == "open":
            return open_change()
        if command == "update":
            return update()
        if command == "close":
            return close_change()
    except (ValueError, sources.SourceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


ENDS = ("finish", "cancel", "close")        # after one of these succeeds, nothing more is written


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    code = _dispatch(argv)
    if len(args) == 1 and args[0] in COMMANDS:
        handoff.keep_folder(args[0], code, ENDS)       # so the next hand-off file can be written without a prompt
    return code


if __name__ == "__main__":
    sys.exit(main())
