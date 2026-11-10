#!/usr/bin/env python3
"""Release audit: look for private names and personal data in everything a public repository would show.

Run this before the repository is made public. It only reads. It never
pushes, edits, comments, closes or deletes anything, on GitHub or locally.
It makes one throwaway mirror clone in a temporary folder to read from.

What it reads:
  1. Every commit reachable from any branch, tag or pull request on GitHub:
     the message, the author and committer, every version of every file,
     and every file path.
  2. Commits that sit on no branch but that GitHub may still serve: the
     merge commit of every pull request and its ancestors, commits replaced
     by a force-push to a pull request, and every commit named by the
     repository's push events of the last 90 days or so.
  3. Every pull request and issue: title, earlier titles, body, earlier
     versions of the body, comments, reviews and review comments, with the
     edit history of each.
  4. Branch names, tag names, releases and the repository description.
  5. Every identity: commit authors and committers, and the accounts that
     opened or commented on pull requests and issues.
  6. Every binary file in history, copied to a folder for a check by eye.

What it prints: counts and places only ("commit 1a2b3c4 message, line 3:
denylist line 7"). It never prints the text that matched. Identities are
printed in full, because checking them is the point.

Usage:
    python3 dev/release_audit.py                    audit the repository this checkout came from
    python3 dev/release_audit.py --repo owner/name  name the repository
    python3 dev/release_audit.py --local            only this checkout's own history, no network
    python3 dev/release_audit.py --style            also report long dashes (house style, not a leak)
    python3 dev/release_audit.py --binaries DIR     where to copy binary files (default: a new temp folder)

Needs git, and for the GitHub parts the gh command, signed in with read access.
Exit codes: 0 nothing found, 1 findings, 2 it could not run.
Standard library only, Python 3.9+.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leak_guard  # noqa: E402

# GitHub caps how much one query may ask for, so each list is read in modest pages. A list longer than its
# limit is named in the report, never silently cut.
PAGE = 10
LIMITS = {"edits": 20, "comments": 50, "reviews": 20, "review_comments": 30, "timeline": 100}


class Audit(object):
    def __init__(self, rules, style=False):
        self.rules = rules
        self.style = style
        self.findings = []          # (section, place, rule)
        self.notes = []             # things the audit could not see or finish
        self.identities = {}        # (kind, name, email or login) -> count
        self.counts = {}            # what was scanned

    def count(self, what, number=1):
        self.counts[what] = self.counts.get(what, 0) + number

    def scan(self, section, place, text, generic=True):
        """Scan one piece of text. Records where a rule matched and which rule. Never the matched text."""
        if not text:
            return
        for finding in leak_guard.scan_text(place, text, self.rules, generic=generic):
            if finding.rule == "long-dash" and not self.style:
                continue
            where = place if finding.line_no is None else "%s, line %d" % (place, finding.line_no)
            self.findings.append((section, where, finding.rule))

    def scan_name(self, section, place, name):
        """A branch, tag or file name. The name itself may be the leak, so the place never repeats it."""
        for number, regex in self.rules:
            if regex.search(name or ""):
                self.findings.append((section, place, "denylist line %d" % number))
        for rule, _ in leak_guard.scan_generic(name or ""):
            if rule != "long-dash" or self.style:
                self.findings.append((section, place, rule))

    def identity(self, kind, name, contact):
        key = (kind, (name or "").strip(), (contact or "").strip())
        self.identities[key] = self.identities.get(key, 0) + 1


# --------------------------------------------------------------------------
# Git history
# --------------------------------------------------------------------------

def git(args, cwd, check=True):
    result = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args[:2]), result.stderr.decode("utf-8", "replace").strip()[:300]))
    return result.stdout


def audit_history(audit, repo_dir, revisions, binaries_dir, label="history"):
    """Every commit reachable from `revisions`: messages, identities, file names, and every version of every file."""
    listing = git(["rev-list"] + list(revisions), repo_dir).decode("utf-8").split()
    commits = [sha for sha in listing if sha]
    for sha in commits:
        raw = git(["show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha], repo_dir).decode("utf-8", "replace")
        author, author_email, committer, committer_email, message = (raw.split("\x00", 4) + [""] * 5)[:5]
        audit.identity("commit author", author, author_email)
        audit.identity("committer", committer, committer_email)
        audit.scan(label, "commit %s message" % sha[:10], message)
        audit.count("commits")
    # Every blob any of those commits ever held, with one path it was seen at.
    objects = git(["rev-list", "--objects"] + list(revisions), repo_dir).decode("utf-8", "replace").splitlines()
    seen_paths = set()
    binaries = []
    for line in objects:
        sha, _, path = line.partition(" ")
        if not path:
            continue
        if git(["cat-file", "-t", sha], repo_dir).strip() != b"blob":
            continue
        if path not in seen_paths:
            seen_paths.add(path)
            audit.scan_name(label, "a file path, first seen in %s" % _first_commit(repo_dir, sha, revisions), path)
        data = git(["cat-file", "blob", sha], repo_dir)
        audit.count("file versions")
        text = leak_guard._decode(data)
        if text is None:
            hidden = any(regex.search(path) for _, regex in audit.rules)
            binaries.append((sha, "(path matches the denylist)" if hidden else path, len(data)))
            if binaries_dir:
                os.makedirs(binaries_dir, exist_ok=True)
                target = os.path.join(binaries_dir, "%s-%s" % (sha[:10], re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))))
                with open(target, "wb") as handle:
                    handle.write(data)
            continue
        before = len(audit.findings)
        # A path that itself matches the denylist is never printed.
        shown = "a file whose path matches the denylist" if any(regex.search(path) for _, regex in audit.rules) \
            else "file %s" % path
        audit.scan(label, "%s (blob %s)" % (shown, sha[:10]), text)
        if len(audit.findings) > before:
            first = _first_commit(repo_dir, sha, revisions)
            audit.findings[before:] = [(section, "%s, first in commit %s" % (place, first), rule)
                                       for section, place, rule in audit.findings[before:]]
    return commits, binaries


def _first_commit(repo_dir, blob, revisions):
    out = git(["log", "--format=%h", "--reverse", "--find-object=%s" % blob] + list(revisions), repo_dir, check=False)
    lines = out.decode("utf-8", "replace").split()
    return lines[0] if lines else "an unknown commit"


# --------------------------------------------------------------------------
# GitHub, read only
# --------------------------------------------------------------------------

def gh_json(args):
    """Run one read-only gh command and return its JSON. Only GET requests and GraphQL queries are ever sent."""
    result = subprocess.run(["gh"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("gh %s failed: %s" % (" ".join(args[:3]), result.stderr.decode("utf-8", "replace").strip()[:300]))
    return json.loads(result.stdout.decode("utf-8") or "null")


THREAD_FIELDS = """
        number title body author { login }
        userContentEdits(first: %(edits)d) { pageInfo { hasNextPage } nodes { diff editor { login } } }
        comments(first: %(comments)d) { pageInfo { hasNextPage } nodes {
          body author { login } userContentEdits(first: %(edits)d) { pageInfo { hasNextPage } nodes { diff } } } }
        timelineItems(first: %(timeline)d, itemTypes: [%(events)s]) { pageInfo { hasNextPage } nodes {
          __typename
          ... on RenamedTitleEvent { previousTitle currentTitle }
          %(pushed)s } }
"""
PUSHED = "... on HeadRefForcePushedEvent { beforeCommit { oid } afterCommit { oid } }"
REVIEWS = """
        mergeCommit { oid }
        reviews(first: %(reviews)d) { pageInfo { hasNextPage } nodes {
          body author { login } userContentEdits(first: %(edits)d) { pageInfo { hasNextPage } nodes { diff } }
          comments(first: %(review_comments)d) { pageInfo { hasNextPage } nodes {
            body author { login } userContentEdits(first: %(edits)d) { pageInfo { hasNextPage } nodes { diff } } } } } }
"""
QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    %(field)s(first: %(page)d, after: $after%(extra)s) {
      pageInfo { hasNextPage endCursor }
      nodes { %(fields)s }
    }
  }
}
"""


def threads(owner, name, kind):
    """Every pull request or issue, with its text, its edit history and its timeline. Yields plain dicts."""
    if kind == "pull request":
        fields = THREAD_FIELDS % dict(LIMITS, events="RENAMED_TITLE_EVENT, HEAD_REF_FORCE_PUSHED_EVENT",
                                      pushed=PUSHED) + REVIEWS % LIMITS
        query = QUERY % {"field": "pullRequests", "page": PAGE, "extra": ", states: [OPEN, CLOSED, MERGED]", "fields": fields}
        key = "pullRequests"
    else:
        fields = THREAD_FIELDS % dict(LIMITS, events="RENAMED_TITLE_EVENT", pushed="")
        query = QUERY % {"field": "issues", "page": PAGE, "extra": ", states: [OPEN, CLOSED]", "fields": fields}
        key = "issues"
    after = None
    while True:
        args = ["api", "graphql", "-f", "query=%s" % query, "-f", "owner=%s" % owner, "-f", "name=%s" % name]
        if after:
            args += ["-f", "after=%s" % after]
        page = gh_json(args)["data"]["repository"][key]
        for node in page["nodes"]:
            yield node
        if not page["pageInfo"]["hasNextPage"]:
            return
        after = page["pageInfo"]["endCursor"]


def audit_thread(audit, node, kind):
    """One pull request or issue. Returns the commits force-pushed away from it."""
    section = "%ss" % kind
    place = "%s #%s" % (kind, node["number"])
    audit.count(section)
    audit.identity("%s author" % kind, (node.get("author") or {}).get("login"), "")
    audit.scan(section, "%s title" % place, node.get("title"))
    audit.scan(section, "%s body" % place, node.get("body"))
    edits = node.get("userContentEdits") or {}
    for index, edit in enumerate(edits.get("nodes") or [], start=1):
        audit.scan(section, "%s body, earlier version %d" % (place, index), edit.get("diff"))
        audit.identity("%s editor" % kind, ((edit or {}).get("editor") or {}).get("login"), "")
        audit.count("earlier versions of a body")
    more = [("the body's edit history", edits)]
    comments = node.get("comments") or {}
    more.append(("comments", comments))
    more += [("the edit history of a comment", comment.get("userContentEdits")) for comment in comments.get("nodes") or []]
    for index, comment in enumerate(comments.get("nodes") or [], start=1):
        audit.identity("commenter", (comment.get("author") or {}).get("login"), "")
        audit.scan(section, "%s comment %d" % (place, index), comment.get("body"))
        for number, edit in enumerate((comment.get("userContentEdits") or {}).get("nodes") or [], start=1):
            audit.scan(section, "%s comment %d, earlier version %d" % (place, index, number), edit.get("diff"))
        audit.count("comments")
    reviews = node.get("reviews") or {}
    if reviews:
        more.append(("reviews", reviews))
    for index, review in enumerate(reviews.get("nodes") or [], start=1):
        audit.identity("reviewer", (review.get("author") or {}).get("login"), "")
        audit.scan(section, "%s review %d" % (place, index), review.get("body"))
        for number, edit in enumerate((review.get("userContentEdits") or {}).get("nodes") or [], start=1):
            audit.scan(section, "%s review %d, earlier version %d" % (place, index, number), edit.get("diff"))
        inner = review.get("comments") or {}
        more.append(("review comments", inner))
        more.append(("the edit history of a review", review.get("userContentEdits")))
        more += [("the edit history of a review comment", comment.get("userContentEdits"))
                 for comment in inner.get("nodes") or []]
        for number, comment in enumerate(inner.get("nodes") or [], start=1):
            audit.scan(section, "%s review %d, comment %d" % (place, index, number), comment.get("body"))
            for version, edit in enumerate((comment.get("userContentEdits") or {}).get("nodes") or [], start=1):
                audit.scan(section, "%s review %d, comment %d, earlier version %d" % (place, index, number, version),
                           edit.get("diff"))
    pushed_away = []
    merged = (node.get("mergeCommit") or {}).get("oid")
    if merged:
        # The commit a merge made. After a later history rewrite it can sit on no branch at all and still be served.
        pushed_away.append(("the merge of pull request #%s" % node["number"], merged))
    timeline = node.get("timelineItems") or {}
    more.append(("the timeline", timeline))
    for event in timeline.get("nodes") or []:
        if event.get("__typename") == "RenamedTitleEvent":
            audit.scan(section, "%s, an earlier title" % place, event.get("previousTitle"))
            audit.count("earlier titles")
        elif event.get("__typename") == "HeadRefForcePushedEvent":
            before = (event.get("beforeCommit") or {}).get("oid")
            if before:
                pushed_away.append(("a force-push to pull request #%s" % node["number"], before))
            else:
                audit.notes.append("%s was force-pushed and GitHub no longer names the commit that was replaced." % place)
    for what, block in more:
        if ((block or {}).get("pageInfo") or {}).get("hasNextPage"):
            note = "%s has more entries in %s than one query returns. The rest were not read." % (place, what)
            if note not in audit.notes:
                audit.notes.append(note)
    return pushed_away


def audit_orphans(audit, mirror, owner, name, orphans, binaries_dir):
    """Commits GitHub may still serve that sit on no branch, tag or pull request: a merge commit left behind by
    a history rewrite, a commit replaced by a force-push. `orphans` is [(why it is known, commit id)].

    Each is fetched by its id and read like any other commit, together with every ancestor that is not already
    reachable. If git cannot fetch it, it is read through the API, parent by parent, until a known commit is met.
    """
    section = "commits on no branch"
    known = set(git(["rev-list", "--all"], mirror).decode("utf-8").split())
    queue = [(why, sha) for why, sha in orphans if sha and set(sha) != {"0"}]
    while queue:
        why, sha = queue.pop(0)
        if sha in known:
            continue
        known.add(sha)
        fetched = subprocess.run(["git", "fetch", "--quiet", "origin", sha], cwd=mirror, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE).returncode == 0
        if fetched:
            commits, _ = audit_history(audit, mirror, [sha, "--not", "--all"], binaries_dir, label=section)
            audit.count(section, len(commits))
            known.update(commits)
            # Keep it reachable inside the throwaway mirror, so a later orphan built on it is not read twice.
            git(["update-ref", "refs/audit/%s" % sha, sha], mirror, check=False)
            continue
        try:
            data = gh_json(["api", "repos/%s/%s/commits/%s" % (owner, name, sha)])
        except RuntimeError:
            audit.notes.append("Commit %s (%s) could not be fetched or read. GitHub may still hold it." % (sha[:10], why))
            continue
        commit = data.get("commit") or {}
        audit.scan(section, "commit %s message (%s)" % (sha[:10], why), commit.get("message"))
        audit.identity("commit author", (commit.get("author") or {}).get("name"), (commit.get("author") or {}).get("email"))
        audit.identity("committer", (commit.get("committer") or {}).get("name"), (commit.get("committer") or {}).get("email"))
        audit.count("commits")
        audit.count(section)
        for item in data.get("files") or []:
            audit.scan_name(section, "a file path in commit %s" % sha[:10], item.get("filename"))
            if item.get("patch"):
                audit.scan(section, "commit %s, changes to a file" % sha[:10], item["patch"])
            else:
                audit.notes.append("Commit %s: the changes to one file were not returned by GitHub (binary or too large)."
                                   % sha[:10])
        audit.notes.append("Commit %s (%s) was read through the API, because git could not fetch it. Only its message, "
                           "identities and changes were read, not every file it held." % (sha[:10], why))
        for parent in data.get("parents") or []:
            if parent.get("sha") and parent["sha"] not in known:
                queue.append(("a parent of %s, from %s" % (sha[:10], why), parent["sha"]))


def events_orphans(audit, owner, name):
    """Commit ids named by the repository's recent events (GitHub keeps about 90 days): what each push replaced
    and what it added. A branch that was reset or force-pushed shows here even with no pull request."""
    found = []
    try:
        events = gh_json(["api", "repos/%s/%s/events" % (owner, name), "--paginate"]) or []
    except RuntimeError as exc:
        audit.notes.append("The repository's event list could not be read (%s). Force-pushes to a branch with no pull "
                           "request were not checked." % str(exc)[:120])
        return found
    for event in events:
        audit.count("repository events")
        payload = event.get("payload") or {}
        audit.identity("event actor", (event.get("actor") or {}).get("login"), "")
        if event.get("type") != "PushEvent":
            continue
        audit.count("push events")
        where = "a push to %s" % str(payload.get("ref") or "a branch").replace("refs/heads/", "")
        for key in ("before", "head"):
            if payload.get(key):
                found.append((where, payload[key]))
        for commit in payload.get("commits") or []:
            if commit.get("sha"):
                found.append((where, commit["sha"]))
    return found


def actions_runs(audit, owner, name):
    try:
        runs = gh_json(["api", "repos/%s/%s/actions/runs?per_page=1" % (owner, name)]) or {}
    except RuntimeError:
        audit.notes.append("The list of Actions runs could not be read.")
        return
    total = runs.get("total_count") or 0
    audit.count("Actions runs (logs not read)", total)
    if total:
        audit.notes.append("There are %d Actions runs. Their logs, summaries and artifacts become readable by anyone "
                           "when the repository is public. This audit does not read them. Delete the runs, or read "
                           "every log, before release." % total)


def audit_github(audit, owner, name, binaries_dir):
    workdir = tempfile.mkdtemp(prefix="release-audit-")
    mirror = os.path.join(workdir, "mirror.git")
    url = "https://github.com/%s/%s.git" % (owner, name)
    clone = subprocess.run(["gh", "repo", "clone", "%s/%s" % (owner, name), mirror, "--", "--mirror", "--quiet"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if clone.returncode != 0:
        git(["clone", "--mirror", "--quiet", url, mirror], None)
    refs = git(["for-each-ref", "--format=%(refname)"], mirror).decode("utf-8").split()
    for ref in refs:
        kind = "branch" if ref.startswith("refs/heads/") else "tag" if ref.startswith("refs/tags/") else None
        if kind:
            audit.scan_name("names", "a %s name" % kind, ref.split("/", 2)[2])
            audit.count("%s names" % kind)
    audit.count("pull request refs", len([ref for ref in refs if ref.startswith("refs/pull/")]))
    _, binaries = audit_history(audit, mirror, ["--all"], binaries_dir)

    pushed_away = []
    for kind in ("pull request", "issue"):
        for node in threads(owner, name, kind):
            pushed_away += audit_thread(audit, node, kind)
    pushed_away += events_orphans(audit, owner, name)
    audit_orphans(audit, mirror, owner, name, pushed_away, binaries_dir)
    actions_runs(audit, owner, name)

    repo = gh_json(["api", "repos/%s/%s" % (owner, name)])
    audit.scan("names", "the repository description", repo.get("description"))
    audit.scan("names", "the repository homepage", repo.get("homepage"))
    for topic in repo.get("topics") or []:
        audit.scan_name("names", "a repository topic", topic)
    for release in gh_json(["api", "repos/%s/%s/releases" % (owner, name), "--paginate"]) or []:
        audit.scan("names", "release %s name" % release.get("id"), release.get("name"))
        audit.scan("names", "release %s notes" % release.get("id"), release.get("body"))
        audit.identity("release author", (release.get("author") or {}).get("login"), "")
        audit.count("releases")
    for comment in gh_json(["api", "repos/%s/%s/comments" % (owner, name), "--paginate"]) or []:
        audit.scan("names", "a comment on commit %s" % str(comment.get("commit_id"))[:10], comment.get("body"))
        audit.identity("commenter", (comment.get("user") or {}).get("login"), "")
        audit.count("comments on commits")
    if repo.get("has_wiki"):
        audit.notes.append("The wiki is switched on. This audit does not read wiki pages.")
    if repo.get("has_discussions"):
        audit.notes.append("Discussions are switched on. This audit does not read them.")
    return binaries, workdir


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

CANNOT_SEE = (
    "Commits on no branch that nothing names any more: replaced by a force-push more than about 90 days ago on a "
    "branch with no pull request, or dropped from a pull request's timeline. GitHub can keep serving such a commit "
    "to anyone who has its id, until it is garbage-collected. Only GitHub Support can remove one for certain. A "
    "fresh repository pushed from clean history carries none of them.",
    "Events older than GitHub keeps (about 90 days, at most 300 events).",
    "Actions: workflow run logs, job summaries, artifacts and caches. They are counted, not read. They become "
    "readable when the repository is public.",
    "Forks and clones made while the repository was private, and anything already copied out of it.",
    "Wiki pages, Discussions, Projects, and security advisories.",
    "Pictures and other binary files: they are copied out for a check by eye, not read. That includes text inside "
    "images, and data inside files such as spreadsheets or PDFs.",
    "Text that is private but matches no rule: a name missing from the denylist, a figure, a description that "
    "identifies someone without naming them. The denylist is only as complete as its author made it.",
    "Deleted comments and deleted pull requests or issues. GitHub does not return them.",
    "The tail of a very long list (more than 50 comments on one thread, 20 edits of one text, 20 reviews, 30 comments "
    "on one review). Each case is named above if it happened.",
    "The git reflog and unreachable objects in other people's local clones.",
)


def report(audit, binaries, binaries_dir, out=None):
    out = out or sys.stdout
    print("RELEASE AUDIT. Read only. Matched text is never shown.", file=out)
    print("", file=out)
    print("WHAT WAS READ", file=out)
    for what in sorted(audit.counts):
        print("  %-28s %d" % (what, audit.counts[what]), file=out)
    print("", file=out)
    print("FINDINGS: %d" % len(audit.findings), file=out)
    by_section = {}
    for section, place, rule in audit.findings:
        by_section.setdefault(section, []).append((place, rule))
    for section in sorted(by_section):
        print("  %s: %d" % (section, len(by_section[section])), file=out)
        for place, rule in by_section[section]:
            print("    %s: %s" % (place, rule), file=out)
    print("", file=out)
    print("IDENTITIES (check each one is meant to be public)", file=out)
    for (kind, name, contact), number in sorted(audit.identities.items()):
        print("  %-22s %s%s  (%d)" % (kind, name or "(no name)", " <%s>" % contact if contact else "", number), file=out)
    print("", file=out)
    print("BINARY FILES IN HISTORY: %d%s" % (len(binaries), ", copied to %s for a check by eye" % binaries_dir
                                              if binaries and binaries_dir else ""), file=out)
    for sha, path, size in binaries:
        print("  %s  %8d bytes  %s" % (sha[:10], size, path), file=out)
    print("", file=out)
    if audit.notes:
        print("COULD NOT BE READ THIS TIME", file=out)
        for note in audit.notes:
            print("  - %s" % note, file=out)
        print("", file=out)
    print("WHAT THIS AUDIT CANNOT SEE", file=out)
    for line in CANNOT_SEE:
        print("  - %s" % line, file=out)


def repo_from_remote(cwd):
    url = git(["remote", "get-url", "origin"], cwd, check=False).decode("utf-8").strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    return (match.group(1), match.group(2)) if match else (None, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only audit before a repository is made public.")
    parser.add_argument("--repo", help="owner/name on GitHub (default: this checkout's origin)")
    parser.add_argument("--local", action="store_true", help="only this checkout's own history, no network")
    parser.add_argument("--style", action="store_true", help="also report long dashes")
    parser.add_argument("--binaries", metavar="DIR", help="where to copy binary files for a check by eye")
    parser.add_argument("--no-denylist", action="store_true", help="generic patterns only (for a test run)")
    args = parser.parse_args(argv)

    rules = []
    if not args.no_denylist:
        path = leak_guard.resolve_denylist_path()
        if not os.path.isfile(path):
            print("release audit: no denylist at the private location. An audit without one proves little. "
                  "Use --no-denylist only for a test run.", file=sys.stderr)
            return 2
        try:
            rules = leak_guard.load_denylist(path)
        except ValueError as exc:
            print("release audit: %s" % exc, file=sys.stderr)
            return 2
    audit = Audit(rules, style=args.style)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    binaries_dir = args.binaries or tempfile.mkdtemp(prefix="release-audit-binaries-")
    try:
        if args.local:
            _, binaries = audit_history(audit, here, ["--all"], binaries_dir)
            audit.notes.append("Local run: nothing on GitHub was read. Pull requests, issues, edit histories and "
                               "force-pushed commits were not checked.")
        else:
            owner, name = (args.repo.split("/", 1) if args.repo and "/" in args.repo else repo_from_remote(here))
            if not owner:
                print("release audit: name the repository with --repo owner/name.", file=sys.stderr)
                return 2
            binaries, _ = audit_github(audit, owner, name, binaries_dir)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print("release audit: could not finish: %s" % exc, file=sys.stderr)
        return 2
    print("%d denylist rules, generic patterns on%s." % (len(rules), "" if args.style else ", long dashes not reported"))
    report(audit, binaries, binaries_dir)
    return 1 if audit.findings else 0


if __name__ == "__main__":
    sys.exit(main())
