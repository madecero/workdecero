# trackers

Your trackers live in your Obsidian vault as plain Markdown notes. This folder holds the code that reads and writes them.

| File | What it does |
| --- | --- |
| `vault.py` | The only code that touches the vault. Creates, reads and updates notes. `python3 trackers/vault.py init --vault PATH` sets up the folder. |
| `bases.py` | Builds the Obsidian Bases views (`.base` files) and the Start here note. |
| `../engine/model.py` | The note types, their properties and their status lists, defined once and shared with the sweeps. |

## What ends up in your vault

Everything goes in one folder (default `workdecero`, set by `storage.vault_folder`):

```
workdecero/
  Start here.md          what is new, what you owe, what is coming up
  Jobs.base              New this week, Pipeline, Follow up, Decide or drop, Archive
  Contracts.base         New gigs, Pursuing, Channels
  Events.base            Next 30 days, Going, Met and follow up
  Groups.base            Groups to try, Joined
  People.base            Warm paths, Follow-ups due
  Companies.base         Companies with openings
  Digests.base           what each sweep found
  Jobs/ Contracts/ Channels/ Events/ Groups/ People/ Companies/ Digests/
```

The views use core Bases only. No Dataview and no community plugins. They were checked in Obsidian 1.15.

## What a note looks like

```
---
wd_id: J-1
wd_type: job
wd_status: New
wd_role: Head of Product
wd_company: "[[workdecero/Companies/Mile High Analytics|Mile High Analytics]]"
wd_fit: 92
wd_pay_max: 240000
wd_first_seen: 2026-11-05
wd_next_step:
tags:
  - your-own-tag
---
Anything you write here is yours.

<!-- workdecero:start -->
Why it fits, warm path and caveats, rewritten by each sweep.
<!-- workdecero:end -->

Anything you write here is yours too.
```

Properties start with `wd_` so they never collide with a `status`, `type`, `tags` or `company` property you already use. Tables show them under plain names such as Status and Fit.

## The promises the store keeps

1. **One folder only.** Nothing is written outside `storage.vault_folder`, and never in `.obsidian` or any hidden folder. A folder that is a link to somewhere outside the vault is refused.
2. **Your writing is yours.** The note body and any property you added are kept exactly. A sweep rewrites only the `wd_` properties it owns and the block between the two `workdecero` markers.
3. **Your choices stand.** Status, Next step, Applied on, Going? and similar are written once when the note is created. After that only you change them, in a table or in the note. A sweep changes a status only to record that a posting has closed.
4. **Safe writes.** Each write goes to a temporary file and is then renamed into place. The previous version is first copied to `~/.config/workdecero/backups/` (outside the vault, the last 30 runs are kept).
5. **No guessing.** If a note's properties cannot be read, the note is left untouched and reported.
6. **No long dashes** in anything a sweep writes (on by default).

Notes are found by `wd_id`, so you can rename or move a note inside its folder.

One thing to know: when a sweep updates a note, the properties block is rewritten in a standard layout. Values are kept, but comments inside the properties block and unusual formatting are not. Obsidian does the same when you edit a property.

## What Obsidian writes by itself

Measured on Obsidian 1.15.0 with an empty settings folder:

* The first time a vault is opened, Obsidian creates `.obsidian/app.json`, `appearance.json`, `core-plugins.json` and `workspace.json`.
* After the workdecero folder was added and every view opened, the only file that changed outside the folder was `.obsidian/workspace.json` (open tabs and layout).
* `.obsidian/types.json` was not created. Obsidian works out that `wd_fit` is a number and `wd_posted` is a date from the values, and keeps that in memory. It writes `types.json` only when you set a property type by hand.
* Inside the folder, Obsidian rewrites a `.base` file when you sort, resize or add a column, and a note when you edit a cell. That is expected, and the store keeps those edits.

## Try it

```
python3 dev/make_demo_vault.py ~/Desktop/demo-vault
```

builds a vault full of invented jobs, gigs, events and people with dates around today. Open the folder as a vault in Obsidian and start with `workdecero/Start here`.

## Setting up a real vault

```
python3 trackers/vault.py init --vault "~/Documents/My Vault" --folder workdecero
```

`init` refuses a folder that has no `.obsidian` folder, so a mistyped path cannot scatter tracker files somewhere else. Add `--new-vault` only for a vault Obsidian has not opened yet. The folder name must be a name inside the vault, not a full path. If any tracker note cannot be read, `init` and the sweeps say how many and list the files.

Status values are matched without regard to capitals or stray spaces. A status that matches nothing shows in a **Check status** group in the Pipeline view and is never treated as closed.
