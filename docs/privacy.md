# Privacy and your data

[Back to the README](../README.md)

**What stays on your computer.** Your profile, your resume, your pay floor and rates, your current employer, the names you asked to keep confidential, your notes and your connections. None of it is stored in this repository or sent to me.

**What goes to the web.** Search queries and page requests. A search may name your field and your area, for example "VP of Product Denver". It never contains your name, your current employer or a name you keep confidential. The part of workdecero that reads web pages is deliberately not told your pay floor, your rates, your exclusions or who you know. Filtering by those happens on your computer, after the reading is done.

**What goes to Claude.** workdecero runs inside Claude Code, so your conversation with it, including your resume when you give it during setup, is handled by Claude under your own Claude account and its terms. workdecero adds no other service.

### Where your data lives

| What | Where | Notes |
| --- | --- | --- |
| Your profile, your local map and backups | `~/.config/workdecero/` | `profile.yaml`, `sources.yaml` and a `backups` folder holding earlier versions of your profile and of your tracker notes (the last 30 runs). Readable by you alone. Updates and uninstalling do not touch them. |
| Your trackers | The `workdecero` folder in your Obsidian vault | Plain notes that you own: jobs, contracts, channels, groups, events, people, companies and digests. |
| Spreadsheet snapshot, only if you turn it on | `workdecero snapshot.xlsx` in that same folder | A read-only picture of your trackers, rebuilt after every sweep. Anything typed into it is lost. Readable by you alone. |
| Temporary working files | `~/.cache/workdecero/` | Exist while setup or a sweep is running, in a folder only you can open, and are deleted when it ends. During setup they hold your answers. A setup that was cut off keeps its answers there for at most a day, so you can pick it up. During a sweep they hold public postings. |
| Whether browser use is on | `~/.config/workdecero/flags.json` | One line, yes or no, kept in step with your profile. |
| Which conversations started a workdecero skill | `~/.cache/workdecero-sessions/` | One empty file per conversation, named by its id, in a folder only you can open. Removed after a day. Nothing about you or the conversation is in it. |
| A small count of LinkedIn pages viewed, only if you turn that connection on | `~/.config/workdecero/usage.json` | Numbers per day for the last week. |

`~` means your home folder. Folders whose names start with a dot are hidden by default in Finder.

## Deleting your data

Uninstalling the plugin leaves your data alone. To delete it, remove these yourself:

* The folder `~/.config/workdecero` (your profile, your map and backups).
* The `workdecero` folder inside your Obsidian vault (your trackers). Delete it in Obsidian or in Finder.
* The folders `~/.cache/workdecero` and `~/.cache/workdecero-sessions`, if they exist.
