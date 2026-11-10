#!/usr/bin/env python3
"""The helper behind /workdecero:contract-sweep.

The engine does every step that touches the person's private settings or
their vault. The assistant researches on the web and hands plain facts back
through ONE fixed file, written with its Write tool:

    The hand-off file:  ~/.cache/workdecero/contract-handoff.json

What goes in the hand-off file depends on the command that follows:
    intake   {"finds": [{"title": "...", "client": "...", "via": "...", "url": "...", "engagement": "...",
              "rate_min": 90, "rate_max": 120, "fit_assessment": 28, "why": "..."}],
              "searches": 7, "not_covered": ["A marketplace that needed a login"]}
    update   {"updates": [{"id": "C-12", "status": "Applied", "note": "Sent a proposal"}]}

Commands (none takes an argument):
    start     re-check open gigs, bring the Channels list up to date, and print where to look
    restart   discard a sweep left unfinished and start again
    intake    filter and score the finds in the hand-off file and write the notes
    finish    verify, write the digest note, and print the summary for the person
    cancel    stop the sweep. Notes already written stay
    list      open gigs and channels with their ids and statuses (reads only)
    open      start a change the person asked for outside a sweep
    update    record the status changes in the hand-off file
    close     end what `open` started

`start` and `restart` open a sweep. `intake` and `finish` only work while one
is open and has been active in the last six hours. `update` only works
during a sweep or after `open`.

Research only: nothing here bids, applies, signs up or sends anything.

Exit codes: 0 fine, 1 a problem to fix, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "trackers"))

import contracts  # noqa: E402
import handoff  # noqa: E402
import integrations  # noqa: E402
import liveness  # noqa: E402
import sources  # noqa: E402
import snapshot  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

DRAFT_DIR_ENV = handoff.DRAFT_DIR_ENV        # for tests: where the hand-off and state files live
HANDOFF_NAME = "contract-handoff.json"
RUN = "contract"
COMMANDS = ("start", "restart", "intake", "finish", "cancel", "list", "open", "update", "close")
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
    if not (profile.get("tracks") or {}).get("contracts"):
        raise ValueError("The contract sweep is switched off in this profile. Run /workdecero:setup to turn it on.")
    try:
        store = vault.VaultStore.from_profile(profile, wdprofile.config_home())
    except vault.VaultError as exc:
        raise ValueError(str(exc))
    store.ensure_layout()
    return profile, store


def _sweep_open():
    """The state of a sweep that is open and recent, or None."""
    path = contracts.state_path(work_dir())
    state = contracts.load_state(work_dir())
    if state is None:
        return None
    if (time.time() - os.path.getmtime(path)) / 3600.0 > STALE_HOURS:
        return None
    return state


def _state():
    path = contracts.state_path(work_dir())
    state = contracts.load_state(work_dir())
    if state is None:
        raise ValueError("No sweep is in progress, so nothing was changed. Run the start command first.")
    idle = (time.time() - os.path.getmtime(path)) / 3600.0
    if idle > STALE_HOURS:
        raise ValueError("The sweep in progress has been idle for %d hours, so nothing was changed. "
                         "Run the restart command to begin a fresh one." % int(idle))
    return state


def _tidy():
    if os.path.isfile(handoff_path()):
        os.remove(handoff_path())
    folder = work_dir()
    if os.path.isdir(folder) and not os.path.islink(folder) and not os.listdir(folder):
        os.rmdir(folder)


def start(out=None, fetcher=None, today=None, force=False):
    out = out or sys.stdout
    profile, store = _context()
    path = contracts.state_path(work_dir())
    if os.path.isfile(path) and not force:
        idle = (time.time() - os.path.getmtime(path)) / 60.0
        if idle < ACTIVE_MINUTES:
            print("A SWEEP IS ALREADY IN PROGRESS (last activity %d minute%s ago)." % (int(idle), "" if int(idle) == 1 else "s"),
                  file=out)
            print("  If you started it earlier in this conversation, carry on with the next step.", file=out)
            print("  Otherwise tell the person. Only if they want to discard it, run the restart command.", file=out)
            return 1
    contracts.clear_state(work_dir())
    handoff.ensure_folder()                  # private, and never a link, before anything is written into it
    source_map = sources.load()
    state = contracts.start(profile, store, source_map, fetcher or liveness.HttpFetcher(), today)
    contracts.save_state(work_dir(), state)
    print(contracts.start_report(state, profile), file=out)
    print("\n".join(integrations.start_lines(profile, "contracts", wdprofile.config_home(), today)), file=out)
    if not source_map.get("contracts"):
        print("\nNOTE: the local map has no contract sources yet. Tell the person they can run /workdecero:sources "
              "(or say \"build my local map\") to make later sweeps much more thorough.", file=out)
    return 0


def intake(out=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    state = _state()
    data, notes = integrations.screen(read_handoff(), profile, wdprofile.config_home(), today)
    state.setdefault("integration_lines", []).extend(line for line in notes if line not in state["integration_lines"])
    state = contracts.intake(state, data, profile, store, today)
    report = contracts.intake_report(state, profile)       # may note that it has nudged, so save after
    contracts.save_state(work_dir(), state)
    print(report, file=out)
    return 0


def finish(out=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    state = _state()
    problems = contracts.verify(state, store)
    title, featured, more, pending = contracts.write_digest(state, profile, store, problems, today)
    import datetime
    due = contracts.deadlines(store, today or datetime.date.today())
    print(contracts.finish_report(state, profile, title, featured, more, pending, due, problems), file=out)
    for text in integrations.tracker_lines(profile, "contracts", today or datetime.date.today(), [
            ("new gigs", len(state["new"])), ("closed", len(state["closed"])), ("left out", state["left_out"]),
            ("due within 7 days", len(due)), ("channels to set up", len(pending))], len(state["not_covered"])):
        print(text, file=out)
    line = snapshot.refresh(store, profile, today)          # only when the profile asks for it
    if line:
        print(line, file=out)
    contracts.clear_state(work_dir())
    _tidy()
    return 0


def cancel(out=None):
    out = out or sys.stdout
    had = contracts.load_state(work_dir()) is not None
    contracts.clear_state(work_dir())
    handoff.close_run(RUN)
    _tidy()
    print("Sweep stopped. %s" % ("Notes already written stay in the tracker." if had else "No sweep was in progress."),
          file=out)
    return 0


def listing(out=None):
    out = out or sys.stdout
    profile, store = _context()
    print(contracts.listing(store, profile), file=out)
    return 0


def open_change(out=None):
    out = out or sys.stdout
    _context()
    handoff.open_run(RUN)
    print("Ready. Write the hand-off file, run `update`, then run `close`.", file=out)
    return 0


def update(out=None, today=None):
    out = out or sys.stdout
    if _sweep_open() is None:
        handoff.require_run(RUN, "open")
    profile, store = _context()
    data = read_handoff()
    done, problems = contracts.apply_updates(data.get("updates"), store, profile, today)
    for line in done:
        print("DONE  " + line, file=out)
    for line in problems:
        print("NOT DONE  " + line, file=out)
    if not done and not problems:
        print('Nothing to do. Write {"updates": [{"id": "C-12", "status": "Applied"}]} to the hand-off file.', file=out)
    return 0 if done and not problems else 1


def close_change(out=None):
    out = out or sys.stdout
    handoff.close_run(RUN)
    if contracts.load_state(work_dir()) is None:
        _tidy()
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
