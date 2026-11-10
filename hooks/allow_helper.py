#!/usr/bin/env python3
"""Let this plugin's own helper commands run without a permission prompt. Nothing else.

Why this exists: a skill can list the commands it may run without asking, but
that list only holds for the turn in which the skill starts. Setup is a
conversation, so from the second turn on every helper call would stop and
ask. This hook gives the same narrow permission for the whole conversation.

What it approves, exactly:
  * Bash: one of this plugin's helper scripts, called as
        python3 "<plugin folder>/skills/<skill>/scripts/<helper>.py" <subcommand>
    where the subcommand is on the list below and nothing follows it. No
    arguments, no pipes, no second command.
  * Write: one fixed file per skill, the small hand-off file in
        ~/.cache/workdecero/
    that the helper reads and deletes. Only when that folder is a real
    directory owned by this user and closed to everyone else, and the file
    is new or a plain file. A link at the folder or at the file is refused,
    so the permission can never be bent into writing somewhere else.

What it refuses, exactly one thing:
  * A browser tool (Claude in Chrome, or any connected tool whose name says
    it drives a browser), while a workdecero sweep is running for a person
    who has not turned the browser pass on. A sweep must not
    open someone's browser unless they asked for that. When no sweep is
    running, nothing is refused.

Everything else is left alone: this hook prints nothing, and the normal
permission prompt decides. It never approves a command it does not
recognise in full.

The helpers take no arguments, so an approved call cannot be pointed at
another file. They read and write only the profile, the local map, the
tracker folder in the vault and the temporary folder above.

Standard library only, Python 3.9+. Kept free of imports from the engine so
it starts fast: it runs before every Bash and Write call.
"""

import json
import os
import stat
import sys
import time

# skill folder -> (helper script, subcommands allowed, hand-off file the assistant may write)
HELPERS = {
    "setup": ("setup_tool.py",
              ("status", "takeover", "pick-up", "fields", "save", "read-resume", "check-vault", "preview", "preview-details",
               "preview-file", "write", "cancel", "import-connections"),
              "setup-answers.json"),
    "sources": ("sources_tool.py",
                ("plan", "plan-refresh", "open", "add", "reverify", "remove", "finish", "close", "show"),
                "sources-entries.json"),
    "job-sweep": ("sweep_tool.py",
                  ("start", "restart", "intake", "warm", "finish", "cancel"),
                  "sweep-handoff.json"),
    "contract-sweep": ("contract_tool.py",
                       ("start", "restart", "intake", "finish", "cancel", "list", "open", "update", "close"),
                       "contract-handoff.json"),
}
HANDOFF_DIR = os.path.join("~", ".cache", "workdecero")


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def allowed_commands():
    """Every command line this hook approves, spelled out in full. Approval is by exact match only."""
    root = plugin_root()
    real_root = os.path.realpath(root)
    commands = set()
    for skill, (script, subcommands, _) in HELPERS.items():
        path = os.path.join(root, "skills", skill, "scripts", script)
        # The script must be a real file inside this plugin. A link that leads somewhere else is not ours.
        real = os.path.realpath(path)
        if not os.path.isfile(real) or not real.startswith(real_root + os.sep):
            continue
        for spelling in set([path, real]):
            for subcommand in subcommands:
                commands.add('python3 "%s" %s' % (spelling, subcommand))
    return commands


def handoff_dir():
    return os.path.expanduser(HANDOFF_DIR)


def folder_is_private(folder):
    """A real directory (not a link), owned by this user, that only this user can open."""
    try:
        info = os.lstat(folder)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):           # lstat: a link to a directory is not a directory here
        return False
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    return stat.S_IMODE(info.st_mode) == 0o700


def write_allowed(path):
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    folder = handoff_dir()
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        return False
    names = [name for _, _, name in HELPERS.values()]
    # Exactly <folder>/<one of our file names>, compared as written: no "..", no extra parts.
    if os.path.normpath(expanded) != expanded or os.path.dirname(expanded) != folder:
        return False
    if os.path.basename(expanded) not in names:
        return False
    if not folder_is_private(folder):
        return False
    try:
        info = os.lstat(expanded)
    except OSError:
        return True                                # does not exist yet: a new file in a private folder
    return stat.S_ISREG(info.st_mode)              # exists: only a plain file, never a link or a folder


# Files a sweep keeps in the hand-off folder while it runs. Each records whether the person has turned
# the browser pass on.
RUN_FILES = ("sweep-state.json", "contract-state.json", "networking-state.json", "sources-run.json")
# Tools that drive a browser. Claude in Chrome by name, and any other connected tool whose name says it
# drives one. A name is all the hook has to go on, so the words are chosen to be unmistakable.
# A sweep counts as running while its file was written in the last half hour. Every helper step rewrites it,
# and finishing or cancelling deletes it, so an abandoned sweep stops mattering after 30 minutes.
RUN_ACTIVE_SECONDS = 30 * 60
BROWSER_PREFIX = "mcp__claude-in-chrome__"
BROWSER_WORDS = ("browser", "playwright", "puppeteer", "selenium", "chrome", "chromium", "webdriver")


def is_browser_tool(tool):
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return False
    name = tool.lower()
    return name.startswith(BROWSER_PREFIX) or any(word in name for word in BROWSER_WORDS)


def browser_is_off_for_a_running_sweep(now=None):
    """True while a workdecero sweep is in progress for a person who has not turned the browser pass on.

    A sweep must never open the person's browser unless they asked for that.
    The skills say so, and this makes it hold: while a sweep is running with
    the browser pass off, browser tools are refused outright. Nothing is
    refused when no sweep is running, so the person's own use of their
    browser in other work is left alone.
    """
    now = now if now is not None else time.time()
    folder = handoff_dir()
    if not folder_is_private(folder):
        return False
    for name in RUN_FILES:
        path = os.path.join(folder, name)
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or now - info.st_mtime > RUN_ACTIVE_SECONDS:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("browser_pass") is False:
            return True
    return False


def refuse(event):
    """A reason, when this tool call must be refused. Only ever a browser tool during a sweep with the pass off."""
    tool = event.get("tool_name")
    if is_browser_tool(tool) and browser_is_off_for_a_running_sweep():
        return ("workdecero: browser use is off in your profile. A sweep is running, so the browser is not opened. "
                "Do not try another way to open one. Carry on with public web pages, and name what could not be read "
                "under not covered. Browser use can be turned on with /workdecero:setup.")
    return None


def decide(event):
    """True when this exact tool call is one of ours."""
    tool = event.get("tool_name")
    data = event.get("tool_input") or {}
    if not isinstance(data, dict):
        return False
    if tool == "Bash":
        command = data.get("command")
        return isinstance(command, str) and command in allowed_commands()
    if tool == "Write":
        return write_allowed(data.get("file_path"))
    return False


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = refuse(event) if isinstance(event, dict) else None
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        return 0
    if isinstance(event, dict) and decide(event):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "workdecero helper command",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
