# Contributing

## One-time setup after cloning

```
make dev-setup
```

This runs `git config core.hooksPath hooks`, which turns on three git hooks. Without it nothing guards your commits, so do it before your first commit.

## The leak guard

This repository must never contain a real person's name, a real employer or client, a home-directory path, a personal email address or a phone number. `dev/leak_guard.py` enforces that in two layers:

1. **Your private denylist.** A text file kept outside the repository, one case-insensitive regular expression per line (blank lines and lines starting with `#` are ignored). Default location `~/.config/workdecero-dev/denylist.txt`. Set `WORKDECERO_DENYLIST` to use another file. Put in it the names you must never publish. A hit is reported by rule number only, so the term itself is never printed. If the file is missing the guard warns and runs the generic patterns only.
2. **Generic patterns**, which also run in CI: home-directory paths, email addresses (except `example.com`, `example.org`, `example.net` and GitHub noreply addresses), phone numbers (except the fictional 555-01xx range) and long dashes.

| Hook | When | Scans |
| --- | --- | --- |
| `pre-commit` | every commit | the staged version of each staged file |
| `commit-msg` | every commit | the commit message |
| `pre-push` | every push | the branch name and every tracked file |

Run it by hand at any time with `make leak-check`.

A false positive on a generic pattern can be waived by putting the marker `leak-guard: allow` on that line. Denylist rules cannot be waived from inside the repository.

Never copy denylist contents into a file, commit message, pull request or issue. Pull request titles and descriptions are not scanned, so check those by hand.

## Commit identity

Commit metadata becomes public. Before your first commit, set a handle and your GitHub noreply address for this repository:

```
git config user.name <your GitHub handle>
git config user.email <your GitHub noreply address>
```

CI runs `dev/check_authors.py` on every pull request and on `main`. It fails if any commit's author or committer is not an allowed handle with a GitHub noreply address, and it reports the commit hash only. Merges use rebase, because a squash or merge commit made on the website is stamped with the account's profile name.

## Fixtures and examples

Invented people and companies only, with `example.com` addresses and 555-01xx phone numbers.

## Checks

```
make check
```

runs the leak guard over the tree, the manifest validator, the commit identity check and the tests. Everything runs on the Python standard library, 3.9 or newer, with no installs.

## Versioning

Installed copies of the plugin only update when `version` in `.claude-plugin/plugin.json` goes up. A pull request that changes shipped files (`.claude-plugin/`, `skills/`, `engine/`, `trackers/`) must raise the version, and CI fails if it does not. Docs, tests, CI and examples need no bump.

## Style

No long dashes in repository text. Use a comma, a period or parentheses.
