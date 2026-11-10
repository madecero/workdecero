#!/usr/bin/env python3
"""The helper behind /workdecero:import.

Moves a tracker the person already keeps in a spreadsheet into their vault.
They export each table as a CSV file. The assistant writes a mapping (which
column means what) into ONE fixed file with its Write tool:

    The hand-off file:  ~/.cache/workdecero/import-plan.json

    {"tables": [{"csv": "/full/path/Jobs.csv", "type": "job", "title": "Role",
                 "columns": {"Employer": "company", "Status": "status", "Applied": "applied_on"},
                 "statuses": {"sent": "Applied"}, "date_formats": ["%m/%d/%Y"]}]}

Commands (none takes an argument):
    fields   what a mapping may use: the kinds of note, their properties and statuses
    check    read the plan and the CSV files and report what would happen. Writes nothing to the vault
    run      do what the last check reported, if the files have not changed since
    cancel   stop. Nothing more is written

`run` only works after `check`, on the same plan and the same files, and
only when every status word has a meaning. The report printed here holds
counts, column names, status words and dates that could not be read. Rows
are never named in it. The row-by-row detail goes to a private file beside
the profile, for the person to read.

Exit codes: 0 the command ran (read its answer: it may say NOT READY), 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "trackers"))

import handoff  # noqa: E402
import importer  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

DRAFT_DIR_ENV = handoff.DRAFT_DIR_ENV        # for tests: where the hand-off and state files live
HANDOFF_NAME = "import-plan.json"
CHECKED_NAME = "import-checked.json"
REPORT_NAME = "import-report.txt"
RUN = "import"
COMMANDS = ("fields", "check", "run", "cancel")


def work_dir():
    return handoff.folder()


def handoff_path():
    return os.path.join(work_dir(), HANDOFF_NAME)


def checked_path():
    return os.path.join(work_dir(), CHECKED_NAME)


def report_path():
    return os.path.join(wdprofile.config_home(), REPORT_NAME)


def _context():
    try:
        profile, problems = wdprofile.load()
    except OSError:
        raise ValueError("There is no profile yet. Run /workdecero:setup first.")
    if profile is None or problems:
        raise ValueError("Your profile has problems to fix first. Run /workdecero:setup.")
    try:
        store = vault.VaultStore.from_profile(profile, wdprofile.config_home())
    except vault.VaultError as exc:
        raise ValueError(str(exc))
    return profile, store


def _tidy():
    for path in (handoff_path(), checked_path()):
        if os.path.isfile(path) and not os.path.islink(path):
            os.remove(path)
    handoff.close_run(RUN)


def _save_report(reports, wrote):
    """The row-by-row detail, for the person only."""
    text = "workdecero import, %s, %s\n\n%s" % (
        "written" if wrote else "check only", datetime.datetime.now().isoformat(timespec="seconds"),
        importer.detail_text(reports, wrote))
    wdprofile.write_private(report_path(), text)


def fields(out=None):
    print(importer.fields_report(), file=out or sys.stdout)
    return 0


def check(out=None, today=None):
    out = out or sys.stdout
    profile, store = _context()
    plan = handoff.read(HANDOFF_NAME)
    if not plan:
        raise ValueError("There is no plan to check. Write the mapping to the hand-off file first.")
    importer.check_plan(plan)
    if any(not table.get("title") for table in plan["tables"]):
        # A first look: only the column names and the number of rows. Nothing else from the files is shown.
        print("COLUMNS. Choose a \"title\" column and map the rest, then check again.", file=out)
        for table in plan["tables"]:
            headers, rows = importer.read_csv(table["csv"])
            print("  %s (%s, %d rows): %s" % (os.path.basename(table["csv"]), table["type"], len(rows),
                                             ", ".join(headers)), file=out)
            people = importer.people_columns(headers, table["type"])
            if people:
                print("    These look like people. Map them to contact or warm_path so each becomes a linked People "
                      "note: %s." % ", ".join(people), file=out)
        return 0
    reports, _ = importer.plan_import(plan, store, profile, today)
    handoff.ensure_folder()
    handle = os.open(checked_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as saved:
        json.dump({"plan": plan, "fingerprint": importer.fingerprint(plan)}, saved)
    handoff.open_run(RUN)
    _save_report(reports, wrote=False)
    print(importer.report_text(reports, profile), file=out)
    print("\nThe row-by-row detail is in %s, for the person to read. It is not shown here." % handoff.tilde(report_path()),
          file=out)
    if importer.unmapped_statuses(reports):
        print("\nNOT READY. Nothing can be written yet. Ask the person what each word above means, one question "
              "per word, offering the choices listed and marking none of them as likely. Status words go under "
              "\"statuses\", yes or no words under \"yes_no\". Then run check again.", file=out)
        return 0                               # a report, not a failure. The run command is the hard stop
    print("\nREADY. Show the person the counts above and ask for a clear yes. Only then run the run command.", file=out)
    return 0


def run(out=None, today=None):
    out = out or sys.stdout
    handoff.require_run(RUN, "check")
    profile, store = _context()
    try:
        with open(checked_path(), "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        raise ValueError("Nothing has been checked, so nothing was written. Run `check` first.")
    plan = saved.get("plan")
    if importer.fingerprint(plan) != saved.get("fingerprint"):
        raise ValueError("A CSV file changed after the check, so nothing was written. Run `check` again.")
    reports, operations = importer.plan_import(plan, store, profile, today)
    if importer.unmapped_statuses(reports):
        raise ValueError("Some words still have no meaning, so nothing was written. Run `check` to see them.")
    store.ensure_layout()
    importer.apply(operations, store)
    store.refresh_rollups()
    _save_report(reports, wrote=True)
    _tidy()
    print(importer.report_text(reports, profile, wrote=True), file=out)
    print("\nThe notes are in the %s folder of the vault. Their old columns are kept in each note under its own "
          "heading. Running the same import again adds nothing twice." % store.folder, file=out)
    return 0


def cancel(out=None):
    _tidy()
    print("Import stopped. Nothing more will be written.", file=out or sys.stdout)
    return 0


def _dispatch(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if len(argv) != 1 or command not in COMMANDS:
        print(__doc__, file=sys.stderr)
        if len(argv) > 1:
            print("This helper takes no arguments. Put the mapping in the hand-off file.", file=sys.stderr)
        return 2
    try:
        return {"fields": fields, "check": check, "run": run, "cancel": cancel}[command]()
    except (ValueError, vault.VaultError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


ENDS = ("run", "cancel")        # after one of these succeeds, nothing more is written


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    code = _dispatch(argv)
    if len(args) == 1 and args[0] in COMMANDS:
        handoff.keep_folder(args[0], code, ENDS)       # so the next hand-off file can be written without a prompt
    return code


if __name__ == "__main__":
    sys.exit(main())
