#!/usr/bin/env python3
"""Fail when a commit carries an identity that must not become public.

Commit author and committer names are public history. Every commit in this
repository must be made under an allowed handle with a GitHub noreply
address. A violation is reported by commit hash only, so the offending name
never reaches a log.

Usage: check_authors.py [REVISION_RANGE]   (default: HEAD, the whole history)
Exit codes: 0 ok, 1 violations, 2 git error. Standard library only (3.9+).
"""

import re
import subprocess
import sys

ALLOWED_HANDLES = ("madecero",)
HANDLE_EMAIL_RE = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$")
# GitHub commits rebase merges and web edits under its own identity.
GITHUB_COMMITTER = ("GitHub", "noreply@github.com")


def identity_allowed(name, email, allow_github=False):
    if allow_github and (name, email) == GITHUB_COMMITTER:
        return True
    match = HANDLE_EMAIL_RE.match(email or "")
    return bool(match) and name in ALLOWED_HANDLES and match.group(1) == name


def violations(records):
    """`records` is a list of (sha, author_name, author_email, committer_name, committer_email)."""
    bad = []
    for sha, author_name, author_email, committer_name, committer_email in records:
        if not identity_allowed(author_name, author_email):
            bad.append((sha, "author"))
        if not identity_allowed(committer_name, committer_email, allow_github=True):
            bad.append((sha, "committer"))
    return bad


def read_records(revision_range):
    out = subprocess.run(
        ["git", "log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce", revision_range],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.decode("utf-8", errors="replace")
    return [tuple(line.split("\x1f")) for line in out.splitlines() if line]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    revision_range = argv[0] if argv else "HEAD"
    try:
        records = read_records(revision_range)
    except subprocess.CalledProcessError:
        print("author check: git log failed for the given range", file=sys.stderr)
        return 2
    bad = violations(records)
    if bad:
        for sha, role in bad:
            print("%s  %s identity not allowed" % (sha[:12], role), file=sys.stderr)
        print("author check: %d problem(s). See CONTRIBUTING.md, 'Commit identity'." % len(bad), file=sys.stderr)
        return 1
    print("author check: %d commit(s), all identities allowed" % len(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
