# workdecero

workdecero is a Claude Code plugin that searches for jobs, contract work and local networking for you, from a profile you set up once, and keeps everything it finds as plain notes in your own Obsidian vault.

It is for one person looking for their next thing: a salaried job, freelance or fractional work, or the right rooms to be in. You do not need to be an engineer to use it. I built it for my own search and then made it work for anyone.

It does research only. It never applies, messages, registers or signs up for anything.

![The Start here note in Obsidian, with this week's new roles](docs/screenshots/start-here.png)

After each sweep you get a short digest note. Here is a shortened example. The employers are invented:

```
## New this sweep
- Director of Product Management, AI/ML, Blue Mesa Cloud. Fit 65. Pay not listed.
  Contact first: Apply through the posting. No contact was looked up for this one.
- Principal Product Manager, AI, Juniper Commerce. Fit 58. $190K to $235K.

## Near misses
- VP of Product, Lakeshore Fintech: the background fit is 14 of 40, under the 20 that counts as a fit.

## Left out, and why
- VP of Product, Payments, Old Posting Co: posted 69 days ago, older than the 21 days your profile allows.

## Not covered
- Browser Built Board (its listing page could not be read)

Nothing was applied for, sent or posted.
```

## Contents

1. [Before you start](#before-you-start)
2. [Install](#install)
3. [First run](#first-run)
4. [Weekly use](#weekly-use)
5. [What it will never do](#what-it-will-never-do)
6. [Privacy and your data](#privacy-and-your-data)
7. [What workdecero runs without asking](#what-workdecero-runs-without-asking)
8. [Optional connections](#optional-connections)
9. [Cost and limits](#cost-and-limits)
10. [Update and uninstall](#update-and-uninstall)
11. [Troubleshooting](#troubleshooting)

## Before you start

You need three things.

| You need | Notes |
| --- | --- |
| **Claude Code on a paid plan** | Claude Code comes with the paid Claude plans (Pro and Max). workdecero has two modes to match. **Lite** keeps each sweep small enough for a Pro plan. **Full** searches wider and suits a Max plan. Setup asks which you want, and you can change it later. |
| **Obsidian** | A free notes app, from obsidian.md. Your trackers live there as ordinary notes. If you have never used it: install it, open it once, and create a vault (a vault is just a folder). Setup will find it. |
| **python3** | A small helper program that workdecero uses. On a Mac it is very likely already there. On a brand new Mac, the first time anything asks for it, macOS shows a window offering to install the "Command Line Developer Tools". Click **Install** and wait a few minutes. That is all. Nothing else needs installing. |

**Which computers.** I built and tested this on macOS. It should work on Linux, which I have not tested. Windows is not supported yet.

## Install

A "marketplace" is just the place Claude Code downloads a plugin from. Here it is this GitHub repository. You add it once, then install the plugin from it.

In Claude Code, type these two lines, one at a time:

```
/plugin marketplace add madecero/workdecero
/plugin install workdecero@workdecero
```

Then start a new Claude Code session.

If you prefer a terminal, these do the same thing:

```
claude plugin marketplace add madecero/workdecero
claude plugin install workdecero@workdecero
```

> **While this repository is private** (remove this note at public release): you need to have been given access to it, and your computer needs to be signed in to GitHub. The simplest way is to install the GitHub command line tool and run `gh auth login`. If the first command above fails with a message about access or a repository that cannot be found, this is why.

## First run

Do these three things once, in order. Each is a slash command you type in Claude Code.

### 1. Set up your profile: `/workdecero:setup`

About 5 to 10 minutes. It reads your resume, then asks about a dozen short questions in five steps:

1. What you want: salaried jobs, contract work, local networking, your target titles and the lowest pay you would consider.
2. Where: your home base, remote or in person, how far you will travel.
3. Discretion: whether you are searching quietly while employed, and any employers or names to leave out.
4. Time and tools: how much to show you each week, Lite or Full, and which Obsidian vault to use.
5. A plain summary of everything, for you to check.

**Nothing is saved until you say the summary looks good.** Stop at any point before that and nothing on your computer has changed. Run `/workdecero:setup` again any time to change an answer.

Setup creates one folder called `workdecero` inside your vault. Nothing outside that folder is touched.

### 2. Build your local map: `/workdecero:sources`

About 3 minutes. It finds where your sweeps should look, for your field and your area: employers and the job site each one uses, job boards, places that list contract work, and local groups. Every entry is checked with a live request before it is kept. If it cannot find something, it says so.

You can say "show my sources", "remove NAME from my sources" or "add NAME to my sources" at any time.

### 3. Run your first sweep

| Command | Finds |
| --- | --- |
| `/workdecero:job-sweep` | Salaried jobs |
| `/workdecero:contract-sweep` | Freelance, contract, fractional and consulting work |
| `/workdecero:networking-sweep` | Groups worth trying and events worth going to |

**Slash commands or plain words.** You can also just say "run the job sweep" or "build my local map". When you do, Claude Code asks once whether it may start that part of workdecero. Say yes. The slash commands ask nothing.

## Weekly use

Run the sweeps you care about about once a week. Each takes a few minutes in Lite mode.

**What a sweep does.** It re-checks what is already in your tracker and closes what has gone. It reads the job lists of the employers in your map and the job boards in your map directly. It searches a little further on the web. It scores what it finds against your profile, leaves out what your profile rules out, and writes a note for each thing that fits. Then it writes a digest note and gives you a short summary.

**What the digest shows.**

* Deadlines first.
* The new roles that fit, best first, each with a score, the pay if listed, and who to contact first.
* Near misses: roles that look like a fit and are not, with the reason. For example one that requires a language you do not speak.
* What closed since last time.
* Everything that was left out, each with a plain reason, so you can see the tool's judgement and correct your profile if it is wrong.
* What it could not cover.

**Working in Obsidian.** Open the `Start here` note in the `workdecero` folder. It has table views for each tracker. Change a role's **Status** right in the table (New, Interested, Applied, Interviewing, and so on), add a next step and a date, or write your own notes in any note. Your own writing is never touched. A status you set is never changed back by a sweep.

**Or just say it.** In Claude Code, at any time:

* "I applied to C-12" or "I won the cafe one" (contract work)
* "I joined the product meetup", "I'm going to the product meetup", "I met Priya at the product meetup" (networking)
* "Prep me for E-4" writes a short brief into that event's note

**Each sweep in one line.**

* **Job sweep.** Roles from employer job lists and job boards, scored for fit, with a warm path for the best few: the likely hiring manager, the recruiter if named, and anyone you already know there.
* **Contract sweep.** Gigs from the marketplaces, agencies and networks in your map. Your rates stay private: the part that reads the web is never told your floor. Proposal deadlines in the next 7 days come first. Platforms where you still need a live profile are listed until you mark each one Live or Skip.
* **Networking sweep.** Answers one question: which one or two things are worth going to this month, and who do I owe a note? Follow-ups come first, then at most the number of events a month you asked for. A meeting at a time you said you cannot do is never picked.

## What it will never do

* Apply for a job or a gig.
* Send a message, an email or a connection request.
* Register, RSVP, join or pay for anything.
* Post anything anywhere.
* Sign up for an account or create a profile for you.
* Sign in anywhere, unless you turn on a connection that does (see [Optional connections](#optional-connections)).

It reads public pages and writes notes on your computer. Everything after that is yours to do.

## Privacy and your data

**What stays on your computer.** Your profile, your resume, your pay floor and rates, your current employer, the names you asked to keep confidential, your notes and your connections. None of it is stored in this repository or sent to me.

**What goes to the web.** Search queries and page requests. A search may name your field and your area, for example "VP of Product Denver". It never contains your name, your current employer or a name you keep confidential. The part of workdecero that reads web pages is deliberately not told your pay floor, your rates, your exclusions or who you know. Filtering by those happens on your computer, after the reading is done.

**What goes to Claude.** workdecero runs inside Claude Code, so your conversation with it, including your resume when you give it during setup, is handled by Claude under your own Claude account and its terms. workdecero adds no other service.

### Where your data lives

| What | Where | Notes |
| --- | --- | --- |
| Your profile, your local map and backups | `~/.config/workdecero/` | `profile.yaml`, `sources.yaml` and a `backups` folder. Readable by you alone. Updates and uninstalling do not touch them. |
| Your trackers | The `workdecero` folder in your Obsidian vault | Plain notes that you own: jobs, contracts, channels, groups, events, people, companies and digests. |
| Spreadsheet snapshot, only if you turn it on | `workdecero snapshot.xlsx` in that same folder | A read-only picture of your trackers, rebuilt after every sweep. Anything typed into it is lost. Readable by you alone. |
| Temporary working files | `~/.cache/workdecero/` | Exist only while setup or a sweep is running, in a folder only you can open, and are deleted when it ends. During setup they hold your answers. During a sweep they hold public postings. |
| A small count of LinkedIn pages viewed, only if you turn that connection on | `~/.config/workdecero/usage.json` | Numbers per day for the last week. |

`~` means your home folder. Folders whose names start with a dot are hidden by default in Finder.

## What workdecero runs without asking

Claude Code normally asks you before it runs a command or writes a file. Setup and the sweeps have many small steps, and being asked a dozen times would make them unusable. So while workdecero is enabled, it approves a short, fixed list of its own actions for you. Nothing else.

**Exactly what is approved:**

| What | Exactly |
| --- | --- |
| Run the setup helper | `python3 "<plugin folder>/skills/setup/scripts/setup_tool.py"` followed by one of: `status`, `takeover`, `pick-up`, `fields`, `save`, `read-resume`, `check-vault`, `preview`, `preview-details`, `preview-file`, `write`, `cancel`, `import-connections`. Nothing may follow that word |
| Run the local map helper | `python3 "<plugin folder>/skills/sources/scripts/sources_tool.py"` followed by one of: `plan`, `plan-refresh`, `open`, `add`, `reverify`, `remove`, `finish`, `close`, `show`. Nothing may follow that word |
| Run the job sweep helper | `python3 "<plugin folder>/skills/job-sweep/scripts/sweep_tool.py"` followed by one of: `start`, `restart`, `intake`, `warm`, `finish`, `cancel`. Nothing may follow that word |
| Run the contract sweep helper | `python3 "<plugin folder>/skills/contract-sweep/scripts/contract_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `close`. Nothing may follow that word |
| Run the networking sweep helper | `python3 "<plugin folder>/skills/networking-sweep/scripts/networking_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `prep`, `close`. Nothing may follow that word |
| Write one temporary file per helper | `setup-answers.json`, `sources-entries.json`, `sweep-handoff.json`, `contract-handoff.json` and `networking-handoff.json`, each in `~/.cache/workdecero/`, and only while that folder is a real folder that you own and nobody else can open |

**What that means.**

* The helpers take no extra words, so an approved command cannot be pointed at another file. They read and write your profile, your local map, the `workdecero` folder in your vault and the temporary folder. That is all they can reach.
* A helper command that changes anything only works while its part of workdecero is in the middle of a run. A stray call in some other conversation changes nothing.
* Any other command, any extra word on those lines, any other file: Claude Code asks you as usual. The approval never blocks anything and never approves anything it does not recognise in full.
* The approval holds in any Claude Code conversation while the plugin is enabled. **To turn it off, disable the plugin:** `claude plugin disable workdecero@workdecero`. The code that does this is one short file, [`hooks/allow_helper.py`](hooks/allow_helper.py), if you want to read it.

**What still asks, on purpose:**

* **Reading a PDF resume** that sits outside the folder you started Claude Code in. It is your file, so you get to say yes.
* **Starting by plain words.** "Run the job sweep" asks once. The slash command does not.
* **Web search later in a conversation.** If you ask for a refresh or a second sweep later in the same conversation, Claude Code may ask once to allow web search. Say yes, or start it with the slash command.
* **Every optional connection** below: your browser, your calendar, your task tracker.

## Optional connections

All off until you turn them on with `/workdecero:setup`. Each one is skipped cleanly, with a line in the digest, when its tool is not available in a session.

| Connection | What it does | Rules it keeps |
| --- | --- | --- |
| Browser pass (Claude in Chrome) | Reads job boards and sites that a script cannot | Its own tab, closed at the end. Paced like a person reading. Read only: never a form, never apply, save or follow. A CAPTCHA is never attempted: the site is named under "Not covered" |
| Signed-in LinkedIn | Finds who you already know at an employer, and the hiring team on a posting | Needs two switches: the connection, and your acknowledgement of the risk. **Automated activity can get a LinkedIn account restricted or closed.** workdecero paces itself and keeps to a small daily number of pages, and cannot remove that risk. Read only. In a quiet search it never opens a profile, because a profile view can be shown to its owner |
| Calendar (Outlook or Google) | Adds a networking event when you say you are going | Checks for a clash first and tells you. One event, no attendees. The entry holds the link and why to go |
| Task tracker (Linear) | Posts a short comment after each sweep on an issue you name | Counts only. Never a title, an employer or a name |
| Spreadsheet snapshot | Rebuilds `workdecero snapshot.xlsx` in your tracker folder after each sweep, with one sheet per tracker | A picture to glance at, never a second copy to keep up. Anything typed into it is lost at the next sweep |

**No standing permission for these.** The approval described above covers only workdecero's own helper commands. Browser, calendar and tracker tools are asked for by Claude Code each time, like any other tool. If you want fewer prompts, allow them yourself in your own Claude Code settings.

**About LinkedIn, plainly.** You do not need it. Without it, workdecero uses only what is public. If you export your connections from LinkedIn yourself (Settings, Data privacy, Get a copy of your data, Connections), setup can read that file so sweeps know who you know, with no signed-in browsing at all. That is the safe way, and it is the one I recommend.

## Cost and limits

workdecero itself is free. What it uses is your Claude Code plan.

| | Lite | Full |
| --- | --- | --- |
| Meant for | A Pro plan | A Max plan |
| Building your local map | Up to 12 web searches | Up to 40 |
| Refreshing your local map | Up to 4 | Up to 12 |
| A job sweep | Up to 10 web searches, up to 12 postings judged, contact research for the best 3 | Up to 30, 30 and 8 |
| A contract or networking sweep | Up to 10 web searches | Up to 30 |

**Typical times in Lite**, from my own test runs: setup 5 to 10 minutes, the local map about 2 to 3 minutes, each sweep about 2 minutes. Full takes longer.

Reading a page that is already known (an employer's job list, a board in your map) does not count as a search, so much of a sweep costs no searches at all.

**Plan limits.** Every Claude plan has usage limits, and a sweep counts against yours like any other work in Claude Code. If you hit a limit in the middle of a sweep, nothing is lost: notes already written stay, and you can run the sweep again later. If Lite sweeps strain your plan, run fewer of them or run them on different days.

## Update and uninstall

**Update.** In Claude Code, type `/plugin`, open the **Marketplaces** tab, choose `workdecero`, and choose **Update marketplace**. That refreshes the marketplace and updates the plugin in one go.

From a terminal, the same thing is two commands:

```
claude plugin marketplace update workdecero
claude plugin update workdecero@workdecero
```

The new version is used from your next Claude Code session. Your profile, your map and your notes are not touched by an update.

**Update by itself.** Auto-update is off by default for a marketplace like this one. To turn it on: `/plugin`, **Marketplaces** tab, choose `workdecero`, then **Enable auto-update**.

**Turn it off for a while.**

```
claude plugin disable workdecero@workdecero
```

**Uninstall.**

In Claude Code, `/plugin uninstall workdecero@workdecero` opens the plugin's page, where you choose Uninstall. Or from a terminal:

```
claude plugin uninstall workdecero@workdecero
claude plugin marketplace remove workdecero
```

The first line removes the plugin. The second also forgets where it came from. Uninstalling leaves your profile, your local map and your Obsidian notes exactly where they are. Claude Code keeps a cached copy of the plugin's own files for about two weeks and then clears it by itself. That copy holds none of your data.

**To delete your data as well**, remove these yourself:

* The folder `~/.config/workdecero` (your profile, your map and backups).
* The `workdecero` folder inside your Obsidian vault (your trackers). Delete it in Obsidian or in Finder.
* The folder `~/.cache/workdecero`, if it exists. It normally does not.

## Troubleshooting

**"Unknown command" after installing.** Start a new Claude Code session. A plugin installed in the middle of a session is not loaded until the next one.

**The install command fails with an access or "not found" message.** While the repository is private, your computer must be signed in to GitHub with an account that has been given access. See the note under [Install](#install).

**A window about "Command Line Developer Tools" appears.** That is macOS offering to install python3. Click Install, wait for it to finish, and run the command again.

**I said "run the job sweep" and Claude Code asked for permission.** That is expected when you start by plain words. Say yes, or use the slash command, which does not ask.

**Setup says another setup is in progress.** A setup was left open in another window, or was cut off. You are offered three choices: leave it, pick it up here with every answer kept, or discard it. Nothing was saved by the unfinished one.

**A sweep found little or nothing.** Three things to check. First, run `/workdecero:sources` and say "refresh my sources": it names any job board in your map that a sweep gets nothing from. Second, look at "Left out, and why" in the digest: if good roles are being left out, your profile is stricter than you meant, and `/workdecero:setup` changes it. Third, look at "Not covered": some sites can only be read with the browser connection turned on.

**A second sweep in the same conversation asks to allow web search.** Expected. Allow it, or start the sweep with its slash command.

**Something else.** Say "what went wrong?" in the same conversation and Claude will read the helper's message back in plain words. Your notes are never left half written: each note is written whole or not at all.

## For the curious

* [examples/profiles](examples/profiles) has three complete example profiles, and [engine/profile.schema.json](engine/profile.schema.json) lists every setting.
* [trackers/README.md](trackers/README.md) describes the notes, the views and the promises the plugin keeps about your vault, and how to build a demo vault to look at.
* [engine/README.md](engine/README.md) describes the scoring and the sweeps.
* [CONTRIBUTING.md](CONTRIBUTING.md) is for anyone who wants to work on the code.

## License

MIT. See [LICENSE](LICENSE).
