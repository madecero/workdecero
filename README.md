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

Nothing was applied for, sent or posted.
```

## Before you start

You need three things.

* **Claude Code on a paid plan.** Claude Code comes with the paid Claude plans (Pro and Max). workdecero has a **Lite** mode sized for a Pro plan and a **Full** mode that searches wider and suits a Max plan.
* **Obsidian.** A free notes app, from obsidian.md. Install it, open it once and create a vault (a vault is just a folder). Your trackers will live there as ordinary notes.
* **python3.** A small helper program. On a Mac it is very likely already there. On a brand new Mac, macOS shows a window offering to install the "Command Line Developer Tools" the first time it is needed. Click **Install** and wait a few minutes.

I built and tested this on macOS. It should work on Linux, which I have not tested. Windows is not supported yet.

## Install

A "marketplace" is just the place Claude Code downloads a plugin from. Here it is this GitHub repository. In Claude Code, type these two lines, one at a time:

```
/plugin marketplace add madecero/workdecero
/plugin install workdecero@workdecero
```

Then start a new Claude Code session.

From a terminal, `claude plugin marketplace add madecero/workdecero` and `claude plugin install workdecero@workdecero` do the same thing.

> **While this repository is private** (remove this note at public release): you need to have been given access to it, and your computer needs to be signed in to GitHub. The simplest way is to install the GitHub command line tool and run `gh auth login`. If the first command above fails with a message about access or a repository that cannot be found, this is why.

## First run

Three steps, once, in order. Each is a slash command you type in Claude Code.

**1. `/workdecero:setup`** (about 5 to 10 minutes). It reads your resume, then asks about a dozen short questions: what you want and the lowest pay you would consider, where you will work, whether you are searching quietly and what to leave out, and which Obsidian vault to use. Then it shows you a plain summary. **Nothing is saved until you say the summary looks good.** It creates one folder called `workdecero` inside your vault and touches nothing outside it. Run it again any time to change an answer.

**2. `/workdecero:sources`** (about 3 minutes). It builds your local map: the employers, job boards, contract platforms and local groups for your field and your area. Every entry is checked with a live request before it is kept.

**3. Your first sweep.**

| Command | Finds |
| --- | --- |
| `/workdecero:job-sweep` | Salaried jobs |
| `/workdecero:contract-sweep` | Freelance, contract, fractional and consulting work |
| `/workdecero:networking-sweep` | Groups worth trying and events worth going to |

You can also use plain words, such as "run the job sweep". When you do, Claude Code asks once whether it may start that part of workdecero. Say yes. The slash commands ask nothing.

## Weekly use

Run the sweeps you care about about once a week. Each takes a few minutes in Lite mode.

**What a sweep does.** It re-checks what is already in your tracker and closes what has gone. It reads the job lists of the employers and the job boards in your map directly, and searches a little further on the web. It scores what it finds against your profile, leaves out what your profile rules out, and writes a note for each thing that fits. Then it writes a digest note.

**What the digest shows.** Deadlines first. The new roles that fit, best first, with a score, the pay if listed and who to contact first. Near misses, with the reason. What closed. Everything that was left out, each with a plain reason, so you can correct your profile if the tool judged wrongly. What it could not cover.

**Working in Obsidian.** Open the `Start here` note in the `workdecero` folder. It has a table for each tracker. Change a **Status** right in the table, add a next step and a date, or write your own notes in any note. Your own writing is never touched, and a status you set is never changed back by a sweep.

**Or just say it,** at any time:

* "I applied to C-12" or "I won the cafe one"
* "I'm going to the product meetup" or "I met Priya at the product meetup"
* "Prep me for E-4", which writes a short brief into that event's note

The contract sweep keeps your rates private and puts proposal deadlines first. The networking sweep answers one question: which one or two things are worth going to this month, and who do I owe a note?

## What it will never do

* Apply for a job or a gig.
* Send a message, an email or a connection request.
* Register, RSVP, join or pay for anything.
* Post anything anywhere.
* Sign up for an account or create a profile for you.

It reads public pages and writes notes on your computer. Everything after that is yours to do.

## Privacy and your data

**What stays on your computer.** Your profile, your resume, your pay floor and rates, your current employer, the names you asked to keep confidential, your notes and your connections. None of it is stored in this repository or sent to me.

**What goes to the web.** Search queries and page requests. A search may name your field and your area. It never contains your name, your current employer or a name you keep confidential. The part of workdecero that reads web pages is deliberately not told your pay floor, your rates, your exclusions or who you know.

**What goes to Claude.** workdecero runs inside Claude Code, so your conversation with it, including your resume when you give it during setup, is handled by Claude under your own Claude account and its terms. workdecero adds no other service.

Your profile and local map live in `~/.config/workdecero/`. Your trackers live in the `workdecero` folder in your vault. The full table, and how to delete everything, is in [docs/privacy.md](docs/privacy.md).

## What workdecero runs without asking

Claude Code normally asks you before it runs a command or writes a file. Setup and the sweeps have many small steps, so while workdecero is enabled it approves a short, fixed list of its own actions for you: five helper commands that take no extra words, and one temporary file for each. Nothing else. To turn that off, disable the plugin with `claude plugin disable workdecero@workdecero`.

Four things still ask on purpose: reading a PDF resume outside the folder you started in, starting by plain words, web search later in a conversation, and every optional connection.

The exact commands and files are listed in [docs/permissions.md](docs/permissions.md).

## Optional connections

All off until you turn them on with `/workdecero:setup`.

* **Browser pass.** Reads sites a script cannot. Its own tab, read only, never a CAPTCHA.
* **Signed-in LinkedIn.** Finds who you already know at an employer. **Automated activity can get a LinkedIn account restricted or closed.** workdecero paces itself and cannot remove that risk. You do not need it: exporting your own connections from LinkedIn and giving setup that file is the safe way.
* **Calendar.** Adds a networking event when you say you are going, after checking for a clash.
* **Task tracker.** Posts a comment of counts after each sweep.
* **Spreadsheet snapshot.** A read-only spreadsheet of your trackers to glance at, rebuilt after each sweep.

**No standing permission for these.** Claude Code asks for browser, calendar and tracker tools each time, like any other tool. The rules each one keeps, and what Lite and Full cost in searches, are in [docs/connections.md](docs/connections.md).

## Update and uninstall

**Update.** In Claude Code, type `/plugin`, open the **Marketplaces** tab, choose `workdecero`, and choose **Update marketplace**. The new version is used from your next session. To have it update by itself, choose **Enable auto-update** in the same place. It is off by default.

**Uninstall.** `/plugin uninstall workdecero@workdecero` opens the plugin's page, where you choose Uninstall. From a terminal: `claude plugin uninstall workdecero@workdecero`, then `claude plugin marketplace remove workdecero`.

Updating and uninstalling leave your profile, your local map and your Obsidian notes exactly where they are. Claude Code keeps a cached copy of the plugin's own files for about two weeks after an uninstall and then clears it. That copy holds none of your data.

## If something goes wrong

Start a new Claude Code session first: a plugin installed in the middle of a session is not loaded until the next one. The other common cases are in [docs/troubleshooting.md](docs/troubleshooting.md).

## For the curious

* [examples/profiles](examples/profiles) has three complete example profiles, and [engine/profile.schema.json](engine/profile.schema.json) lists every setting.
* [trackers/README.md](trackers/README.md) describes the notes, the views and the promises the plugin keeps about your vault.
* [engine/README.md](engine/README.md) describes the scoring and the sweeps.
* [CONTRIBUTING.md](CONTRIBUTING.md) is for anyone who wants to work on the code. The `dev/` and `tests/` folders ship inside the installed plugin too. They are inert: nothing in them runs unless you run it.

## License

MIT. See [LICENSE](LICENSE).
