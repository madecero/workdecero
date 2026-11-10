#!/usr/bin/env python3
"""Leak guard: keep private names and personal data out of this repository.

Two layers:

1. A private denylist kept OUTSIDE the repo (one case-insensitive regular
   expression per line). Hits are reported by rule number only, so the
   matched term never reaches a terminal log, a CI log or a commit.
2. Generic patterns that need no private knowledge: home-directory paths,
   email addresses, phone numbers, and long dashes (house style).

Modes:
    leak_guard.py --staged          scan the staged version of staged files
    leak_guard.py --tree            scan every tracked file in the work tree
    leak_guard.py --message FILE    scan a commit message file
    leak_guard.py --branch          scan the current branch name
    leak_guard.py --push            scan what a push sends (the pre-push hook: reads git's lines on standard input)

Exit codes: 0 clean, 1 findings, 2 usage or configuration error.
Runs on the Python standard library only (3.9+).
"""

import argparse
import os
import re
import subprocess
import sys

DEFAULT_DENYLIST = os.path.join("~", ".config", "workdecero-dev", "denylist.txt")
DENYLIST_ENV = "WORKDECERO_DENYLIST"

# A line carrying this marker is exempt from the GENERIC patterns only.
# Denylist rules can never be waived from inside the repo.
ALLOW_MARKER = "leak-guard: allow"

ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "users.noreply.github.com",
)
ALLOWED_EMAILS = ("git@github.com", "noreply@github.com", "noreply@anthropic.com")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE_RE = re.compile(r"(?<![\w.-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w-])")
HOME_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[A-Za-z0-9._-]+")
LONG_DASH_RE = re.compile("[" + chr(0x2013) + chr(0x2014) + "]")

# Placeholder account names that are fine inside a home path.
PLACEHOLDER_USERS = ("you", "yourname", "username", "user", "me", "runner", "example")


class Finding(object):
    def __init__(self, where, line_no, rule, detail):
        self.where = where
        self.line_no = line_no
        self.rule = rule
        self.detail = detail

    def render(self):
        location = self.where if self.line_no is None else "%s:%d" % (self.where, self.line_no)
        if self.detail:
            return "%s  [%s]  %s" % (location, self.rule, self.detail)
        return "%s  [%s]" % (location, self.rule)


def load_denylist(path):
    """Return a list of (rule_number, compiled_regex). Raises ValueError on a bad rule."""
    rules = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            try:
                rules.append((number, re.compile(text, re.IGNORECASE)))
            except re.error as exc:
                # Do not echo the rule text: it is private.
                raise ValueError("denylist line %d is not a valid regular expression (%s)" % (number, exc.msg))
    return rules


def resolve_denylist_path():
    return os.path.expanduser(os.environ.get(DENYLIST_ENV) or DEFAULT_DENYLIST)


def _email_allowed(address):
    lowered = address.lower()
    if lowered in ALLOWED_EMAILS:
        return True
    domain = lowered.rsplit("@", 1)[-1]
    return any(domain == ok or domain.endswith("." + ok) for ok in ALLOWED_EMAIL_DOMAINS)


def _phone_allowed(number):
    digits = re.sub(r"\D", "", number)[-10:]
    # 555-01xx is the range reserved for fiction.
    return len(digits) == 10 and digits[3:8] == "55501"


def _home_path_allowed(match_text):
    user = re.split(r"[/\\]", match_text)[-1].lower()
    return user in PLACEHOLDER_USERS


def scan_generic(line):
    """Return a list of (rule, detail) for one line of text."""
    if ALLOW_MARKER in line:
        return []
    hits = []
    for match in HOME_PATH_RE.finditer(line):
        if not _home_path_allowed(match.group(0)):
            hits.append(("home-path", match.group(0)))
    for match in EMAIL_RE.finditer(line):
        if not _email_allowed(match.group(0)):
            hits.append(("email", match.group(0)))
    for match in PHONE_RE.finditer(line):
        if not _phone_allowed(match.group(0)):
            hits.append(("phone", match.group(0)))
    if LONG_DASH_RE.search(line):
        hits.append(("long-dash", "use a comma, period or parentheses"))
    return hits


def scan_text(where, text, rules, generic=True):
    findings = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for number, regex in rules:
            if regex.search(line):
                findings.append(Finding(where, line_no, "denylist rule %d" % number, ""))
        if generic:
            for rule, detail in scan_generic(line):
                findings.append(Finding(where, line_no, rule, detail))
    return findings


def scan_path_name(path, rules):
    findings = []
    for number, regex in rules:
        if regex.search(path):
            # The path itself would reveal the term, so name it by rule only.
            findings.append(Finding("(a file path)", None, "denylist rule %d" % number, ""))
    for rule, detail in scan_generic(path):
        if rule != "long-dash":
            findings.append(Finding(path, None, rule + " in file name", detail))
    return findings


def _git(args, cwd=None):
    return subprocess.run(["git"] + args, cwd=cwd, check=True, stdout=subprocess.PIPE).stdout


def _decode(data):
    """Return text, or None for binary content."""
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def staged_files(cwd=None):
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd)
    return [p for p in out.decode("utf-8").split("\x00") if p]


def tracked_files(cwd=None):
    out = _git(["ls-files", "-z"], cwd)
    return [p for p in out.decode("utf-8").split("\x00") if p]


def scan_staged(rules, cwd=None):
    findings, skipped = [], []
    for path in staged_files(cwd):
        findings.extend(scan_path_name(path, rules))
        text = _decode(_git(["show", ":" + path], cwd))
        if text is None:
            skipped.append(path)
            continue
        findings.extend(scan_text(path, text, rules))
    return findings, skipped


def scan_tree(rules, cwd=None):
    findings, skipped = [], []
    root = cwd or "."
    for path in tracked_files(cwd):
        findings.extend(scan_path_name(path, rules))
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            continue
        with open(full, "rb") as handle:
            text = _decode(handle.read())
        if text is None:
            skipped.append(path)
            continue
        findings.extend(scan_text(path, text, rules))
    return findings, skipped


def scan_commit(commit, rules, cwd=None):
    """Scan every file as it is in one commit. This is what a push sends, whatever is checked out."""
    findings, skipped = [], []
    out = _git(["ls-tree", "-r", "-z", "--name-only", commit], cwd)
    for path in [p for p in out.decode("utf-8").split("\x00") if p]:
        findings.extend(scan_path_name(path, rules))
        text = _decode(_git(["show", "%s:%s" % (commit, path)], cwd))
        if text is None:
            skipped.append(path)
            continue
        findings.extend(scan_text(path, text, rules))
    return findings, skipped


def scan_push(lines, rules, cwd=None):
    """Scan what a push is about to send, from the lines git gives a pre-push hook.

    Each line is "<local ref> <local commit> <remote ref> <remote commit>". The
    name the branch will have on the remote is scanned, and the tree of the
    commit being sent. A push by commit id has no local branch name, and the
    working tree may be on another branch altogether, so neither is used.
    """
    findings, skipped, seen = [], [], set()
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        commit, remote_ref = parts[1], parts[2]
        if set(commit) == {"0"}:
            continue                      # a branch being deleted sends nothing
        name = remote_ref.split("/", 2)[2] if remote_ref.startswith("refs/") and remote_ref.count("/") >= 2 else remote_ref
        findings.extend(scan_text("branch name", name, rules))
        if commit in seen:
            continue
        seen.add(commit)
        found, binary = scan_commit(commit, rules, cwd)
        findings.extend(found)
        skipped.extend(path for path in binary if path not in skipped)
    return findings, skipped


def scan_message(message_path, rules):
    with open(message_path, "r", encoding="utf-8", errors="replace") as handle:
        lines = [line for line in handle.read().splitlines() if not line.startswith("#")]
    return scan_text("commit message", "\n".join(lines), rules)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan for private names and personal data.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="scan staged files (pre-commit)")
    mode.add_argument("--tree", action="store_true", help="scan every tracked file")
    mode.add_argument("--message", metavar="FILE", help="scan a commit message file")
    mode.add_argument("--branch", action="store_true", help="scan the current branch name")
    mode.add_argument("--push", action="store_true",
                      help="scan what a push sends: reads git's pre-push lines from standard input")
    parser.add_argument("--no-denylist", action="store_true",
                        help="generic patterns only (CI has no private denylist)")
    parser.add_argument("--require-denylist", action="store_true",
                        help="fail instead of warn when the denylist file is missing")
    args = parser.parse_args(argv)

    rules = []
    if not args.no_denylist:
        path = resolve_denylist_path()
        if os.path.isfile(path):
            try:
                rules = load_denylist(path)
            except ValueError as exc:
                print("leak guard: %s" % exc, file=sys.stderr)
                return 2
        elif args.require_denylist:
            print("leak guard: denylist file not found. Set %s or create the default file." % DENYLIST_ENV,
                  file=sys.stderr)
            return 2
        else:
            print("leak guard: WARNING no denylist file found, running generic patterns only. "
                  "Set %s to point at one." % DENYLIST_ENV, file=sys.stderr)

    skipped = []
    if args.staged:
        findings, skipped = scan_staged(rules)
    elif args.tree:
        findings, skipped = scan_tree(rules)
    elif args.push:
        findings, skipped = scan_push(sys.stdin.read().splitlines(), rules)
    elif args.branch:
        # Branch names are public once pushed. Report by label only.
        name = _git(["rev-parse", "--abbrev-ref", "HEAD"]).decode("utf-8").strip()
        findings = scan_text("branch name", name, rules)
    else:
        findings = scan_message(args.message, rules)

    for path in skipped:
        print("leak guard: note, binary file not scanned: %s" % path, file=sys.stderr)

    if findings:
        print("leak guard: BLOCKED, %d finding(s):" % len(findings), file=sys.stderr)
        for finding in findings:
            print("  " + finding.render(), file=sys.stderr)
        print("Denylist hits show the rule number only. Look up the line in your private denylist file.\n"
              "A false positive on a generic pattern can be waived by putting '%s' on that line." % ALLOW_MARKER,
              file=sys.stderr)
        return 1

    print("leak guard: clean (%d denylist rules, generic patterns on)" % len(rules))
    return 0


if __name__ == "__main__":
    sys.exit(main())
