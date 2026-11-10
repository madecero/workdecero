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
    it drives a browser), in a conversation that has started a workdecero
    skill, for a person who has not turned browser use on. workdecero must
    not open someone's browser unless they asked for that. Other
    conversations are never affected.

It also notes, in a private folder, which conversations have started a
workdecero skill. That note is an empty file named by the session id.

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
import re
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


# --------------------------------------------------------------------------
# The one refusal: a browser tool, in a workdecero session, with browser use off
# --------------------------------------------------------------------------

# Tools that drive a browser. Claude in Chrome by name, and any other connected tool whose name says it
# drives one. A name is all the hook has to go on, so the words are chosen to be unmistakable.
BROWSER_PREFIX = "mcp__claude-in-chrome__"
BROWSER_WORDS = ("browser", "playwright", "puppeteer", "selenium", "chrome", "chromium", "webdriver")
SKILL_PREFIX = "workdecero:"
# One empty file per conversation that has started a workdecero skill, named by its session id.
SESSIONS_DIR = os.path.join("~", ".cache", "workdecero-sessions")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{8,80}\Z")
MARK_KEPT_SECONDS = 24 * 60 * 60
# Where the settings live, and the one small file in it this hook reads: {"browser_enabled": true or false}.
HOME_ENV = "WORKDECERO_HOME"
DEFAULT_HOME = os.path.join("~", ".config", "workdecero")
FLAGS_NAME = "flags.json"


def is_browser_tool(tool):
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return False
    name = tool.lower()
    return name.startswith(BROWSER_PREFIX) or any(word in name for word in BROWSER_WORDS)


def sessions_dir():
    return os.path.expanduser(SESSIONS_DIR)


def _mark_path(session_id):
    if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
        return None                                # anything that is not a plain id never becomes a file name
    return os.path.join(sessions_dir(), session_id)


def mark_session(session_id, now=None):
    """Remember that this conversation has started a workdecero skill. Never raises."""
    path = _mark_path(session_id)
    if path is None:
        return False
    folder = sessions_dir()
    now = now if now is not None else time.time()
    try:
        if not os.path.lexists(folder):
            os.makedirs(os.path.dirname(folder), exist_ok=True)
            os.mkdir(folder, 0o700)
            os.chmod(folder, 0o700)
        if not folder_is_private(folder):
            return False                           # not ours, or a link: write nothing into it
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(handle)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return False
        os.utime(path, (now, now))
        for name in os.listdir(folder)[:500]:      # keep the folder tidy: a mark lasts a day
            other = os.path.join(folder, name)
            old = os.lstat(other)
            if stat.S_ISREG(old.st_mode) and now - old.st_mtime > MARK_KEPT_SECONDS:
                os.remove(other)
    except OSError:
        return False
    return True


def session_marked(session_id, now=None):
    """True when this conversation started a workdecero skill in the last day."""
    path = _mark_path(session_id)
    if path is None or not folder_is_private(sessions_dir()):
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
        return False
    now = now if now is not None else time.time()
    return now - info.st_mtime <= MARK_KEPT_SECONDS


def flags_path():
    return os.path.join(os.path.expanduser(os.environ.get(HOME_ENV) or DEFAULT_HOME), FLAGS_NAME)


def browser_enabled():
    """True only when the person's own settings folder plainly says browser use is on.

    A missing file, a link, a file or folder someone else owns or can write to,
    or anything that is not exactly true: all of those mean off.
    """
    path = flags_path()
    me = os.getuid() if hasattr(os, "getuid") else None
    try:
        folder = os.lstat(os.path.dirname(path))
        info = os.lstat(path)
        if not stat.S_ISDIR(folder.st_mode) or not stat.S_ISREG(info.st_mode):
            return False
        if me is not None and (folder.st_uid != me or info.st_uid != me):
            return False
        if stat.S_IMODE(folder.st_mode) & 0o022 or stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > 4096:
            return False
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("browser_enabled") is True


def starts_a_skill(event):
    """True when this event is a workdecero skill starting: by slash command, or by the Skill tool."""
    if event.get("hook_event_name") == "UserPromptSubmit":
        prompt = event.get("prompt")
        return isinstance(prompt, str) and prompt.lstrip().startswith("/" + SKILL_PREFIX)
    if event.get("tool_name") == "Skill":
        data = event.get("tool_input")
        name = data.get("skill") if isinstance(data, dict) else None
        return isinstance(name, str) and name.lstrip("/").startswith(SKILL_PREFIX)
    return False


def refuse(event):
    """A reason, when this tool call must be refused. Only ever a browser tool, in a conversation that has
    started a workdecero skill, for a person who has not turned browser use on."""
    if not is_browser_tool(event.get("tool_name")):
        return None
    if not session_marked(event.get("session_id")) or browser_enabled():
        return None
    return ("workdecero: browser use is off in your profile. This conversation is running workdecero, so the browser "
            "is not opened. Do not try another way to open one. Carry on with public web pages, and name what could "
            "not be read under not covered. Browser use can be turned on with /workdecero:setup.")


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
    if not isinstance(event, dict):
        return 0
    if starts_a_skill(event):
        mark_session(event.get("session_id"))
        return 0                                   # noted. The normal prompt decides whether the skill starts
    if event.get("hook_event_name") not in (None, "PreToolUse"):
        return 0
    reason = refuse(event)
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
        return 0
    if decide(event):
        mark_session(event.get("session_id"))      # one of our helpers is running here: this is a workdecero session
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "workdecero helper command",
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
