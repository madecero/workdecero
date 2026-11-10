"""The temporary folder the helpers share with the assistant, and the idea of an open run.

The assistant never puts a person's data on a command line. It writes one
small file into a private folder, and a helper reads and deletes it. This
module keeps that folder private and keeps track of whether a skill is in
the middle of a run.

Why runs matter: the plugin lets its own helper commands run without a
permission prompt. A command that changes saved data therefore only works
while its skill has a run open, and a run is opened by the read-only command
that starts the skill's flow. A stray call in some other conversation does
nothing.

    The folder:   ~/.cache/workdecero/      (only its owner can open it)
    A run:        <skill>-run.json in that folder, removed when the run ends

Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import stat

DRAFT_DIR_ENV = "WORKDECERO_DRAFT_DIR"       # for tests: where the folder lives
RUN_MINUTES = 120                            # a run left open longer than this has ended


def folder():
    return os.path.expanduser(os.environ.get(DRAFT_DIR_ENV) or os.path.join("~", ".cache", "workdecero"))


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def ensure_folder():
    """Create the folder if needed and close it to everyone else. A link in its place is refused."""
    path = folder()
    if os.path.islink(path):
        raise ValueError("%s is a link, not a folder. Remove it and try again." % tilde(path))
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise ValueError("%s is not a folder. Remove it and try again." % tilde(path))
    os.chmod(path, 0o700)
    return path


def _run_path(skill):
    return os.path.join(folder(), "%s-run.json" % skill)


def open_run(skill, now=None):
    """Start (or restart) a run for this skill."""
    ensure_folder()
    now = now or datetime.datetime.now()
    handle = os.open(_run_path(skill), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump({"started": now.isoformat(timespec="seconds")}, out)


def run_is_open(skill, now=None, minutes=RUN_MINUTES):
    now = now or datetime.datetime.now()
    path = _run_path(skill)
    try:
        if os.path.islink(path):
            return False
        with open(path, "r", encoding="utf-8") as handle:
            started = datetime.datetime.fromisoformat(json.load(handle)["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    age = (now - started).total_seconds() / 60.0
    return 0 <= age <= minutes


def require_run(skill, opener, now=None):
    """Raise unless this skill has a run open. `opener` names the command that opens one."""
    if not run_is_open(skill, now=now):
        raise ValueError("Nothing is in progress, so nothing was changed. Run `%s` first, then this command." % opener)


def close_run(skill):
    """End the run and remove the folder if nothing else is using it."""
    try:
        os.remove(_run_path(skill))
    except OSError:
        pass
    _remove_if_empty()


def _remove_if_empty():
    path = folder()
    if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
        os.rmdir(path)


def read(name):
    """The JSON object the assistant wrote to <folder>/<name>. The file is removed once read."""
    path = os.path.join(folder(), name)
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("Nothing to read. Write the hand-off file %s first, then run this command again."
                         % tilde(path))
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    os.remove(path)
    _remove_if_empty()
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("The hand-off file was not valid JSON (%s). Write it again as one JSON object." % exc)
    if not isinstance(data, dict):
        raise ValueError("The hand-off file must hold one JSON object.")
    return data
