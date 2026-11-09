#!/usr/bin/env python3
"""Fail when shipped plugin files change without a version bump.

Installed copies of the plugin only update when the version in
.claude-plugin/plugin.json goes up. A change to shipped files without a bump
would never reach users, so CI refuses it.

Usage: check_version_bump.py BASE_REF   (for example origin/main)
Exit codes: 0 ok, 1 bump missing, 2 usage or git error.
Standard library only (3.9+).
"""

import json
import re
import subprocess
import sys

MANIFEST = ".claude-plugin/plugin.json"

# Paths that end up running on a user's machine. Docs, tests, CI, examples
# and developer tooling do not need a bump.
SHIPPED_PREFIXES = (".claude-plugin/", "skills/", "engine/", "trackers/", "commands/", "agents/", "bin/")
SHIPPED_FILES = ("hooks/hooks.json",)

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def is_shipped(path):
    return path.startswith(SHIPPED_PREFIXES) or path in SHIPPED_FILES


def parse_version(text):
    match = VERSION_RE.match(text or "")
    if not match:
        raise ValueError("version %r is not MAJOR.MINOR.PATCH" % (text,))
    return tuple(int(part) for part in match.groups())


def needs_bump(changed_paths):
    return sorted(path for path in changed_paths if is_shipped(path))


def evaluate(changed_paths, base_version, head_version):
    """Return (ok, message). base_version is None when the base has no manifest."""
    shipped = needs_bump(changed_paths)
    if not shipped:
        return True, "no shipped files changed, no bump needed"
    if base_version is None:
        return True, "first version of the manifest, no bump needed"
    if parse_version(head_version) > parse_version(base_version):
        return True, "version bumped %s -> %s" % (base_version, head_version)
    listing = "\n".join("  " + path for path in shipped[:20])
    return False, (
        "shipped files changed but the version is still %s (base has %s).\n"
        "Raise \"version\" in %s.\nChanged shipped files:\n%s"
        % (head_version, base_version, MANIFEST, listing)
    )


def _git(args):
    return subprocess.run(["git"] + args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode("utf-8")


def _version_at(ref):
    try:
        raw = _git(["show", "%s:%s" % (ref, MANIFEST)])
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw).get("version")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    base = argv[0]
    try:
        changed = [p for p in _git(["diff", "--name-only", "%s...HEAD" % base]).splitlines() if p]
    except subprocess.CalledProcessError as exc:
        print("version check: git diff failed: %s" % exc.stderr.decode("utf-8").strip(), file=sys.stderr)
        return 2
    with open(MANIFEST, "r", encoding="utf-8") as handle:
        head_version = json.load(handle).get("version")
    try:
        ok, message = evaluate(changed, _version_at(base), head_version)
    except ValueError as exc:
        print("version check: %s" % exc, file=sys.stderr)
        return 1
    print("version check: " + message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
