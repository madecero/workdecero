# workdecero

A Claude Code plugin that runs weekly job, contract and networking sweeps from your own profile and keeps the results in your Obsidian vault.

**Under construction.** Setup, the local map and the job sweep work. The contract and networking sweeps are still being built.

## Install

In Claude Code:

```
/plugin marketplace add madecero/workdecero
/plugin install workdecero@workdecero
```

Or from a terminal:

```
claude plugin marketplace add madecero/workdecero
claude plugin install workdecero@workdecero
```

Then start a new session and run `/workdecero:setup`.

## Setup

`/workdecero:setup` takes about 15 minutes. It reads your resume, asks a dozen or so short questions (what you want, where, what to leave out, how much to show you, which Obsidian vault), shows you a plain-language summary, and only then saves anything. Stop at any point before that and nothing has changed on your computer. Run it again later to change an answer.

Your answers stay in a file on your computer. Sweeps search the public web. Nothing is ever applied for, sent or posted for you.

While this repository is private you need read access to it and working git credentials for GitHub (an SSH key, or `gh auth login` followed by `gh auth setup-git`).

## Update

```
claude plugin marketplace update workdecero
claude plugin update workdecero@workdecero
```

Restart Claude Code (or run `/reload-plugins`) to pick up the new version.

## Where your data lives

Nothing personal is stored in this repository or in the plugin folder.

| What | Where | Notes |
| --- | --- | --- |
| Profile (`profile.yaml`), local map (`sources.yaml`) and backups | `~/.config/workdecero/` | Readable by you alone. Location can be overridden. Untouched by updates and by uninstall. |
| Trackers (postings, contracts, groups, events, people, companies) | Your Obsidian vault | Plain Markdown notes that you own. |
| Spreadsheet snapshot (only if you turn it on) | `workdecero snapshot.xlsx` in your tracker folder | Read-only picture of the trackers, rebuilt every sweep. Readable by you alone. |
| Setup draft | `~/.cache/workdecero/` (`setup-answers.json`, `setup-state.json`) | Temporary files in a private folder. They hold your answers only while setup is running, in a folder only you can open, and are deleted when setup is saved or cancelled. `setup-answers.json` is written by Claude Code a few lines at a time and is taken in and deleted at the next step. |
| Source hand-off | `~/.cache/workdecero/` (`sources-entries.json`, `sources-run.json`) | Temporary. Holds public names and links found while your local map is being built. Each batch is deleted as soon as it is checked, and the folder goes when the run ends. |
| Sweep hand-off and state | `~/.cache/workdecero/` (`sweep-handoff.json`, `sweep-state.json`, `contract-handoff.json`, `contract-state.json`, `networking-handoff.json`, `networking-state.json`) | Temporary. Hold public postings found during a sweep, and are deleted when the sweep finishes. |
| Browser setting | `~/.config/workdecero/flags.json` | One line: whether you turned browser use on. Kept in step with your profile, so the hook never reads the profile itself. |
| Conversation marks | `~/.cache/workdecero-sessions/` | One empty file per conversation that started a workdecero skill, named by its id, in a folder only you can open. Removed after a day. |
| Cache | The plugin data folder managed by Claude Code | Safe to lose. Removed when the plugin is uninstalled. |

## Your local map

Run **`/workdecero:sources`** after setup, or just say "build my local map". In about 3 minutes it finds where your sweeps should look: employers in your field and area and the job site each one uses, state and field job boards, the places contract work is posted, and local groups and associations. Every entry is checked with a live call before it is kept. The result is `~/.config/workdecero/sources.yaml`, which you can edit. It is re-checked every few weeks.

## The job sweep

Run **`/workdecero:job-sweep`** about once a week, or just say "run the job sweep". In Lite mode it takes a few minutes. It re-checks which of your open postings are still live, reads the job lists of the employers in your local map, searches a little further inside a small budget, scores what it finds against your profile, and writes the results into your tracker with a digest note that puts deadlines first.

It never applies, messages, follows or saves anything, and it does not sign in anywhere. The part that reads web pages is never told your pay floor, your exclusions, your current employer or who you know. Those are applied on your computer after the reading is done.

## The contract sweep

Run **`/workdecero:contract-sweep`** when you want freelance, contract, fractional or consulting work, or just say "run the contract sweep". It looks in the places your local map lists for your field (marketplaces, talent networks, agencies, expert networks, and public-bid portals if you turned bids on), keeps only the ways of working your profile takes, and writes what fits into your tracker with a digest note.

* **Your rates stay private.** The assistant that reads the web is never told your hourly floor, your minimum project or retainer price, or the hours you want. It records what a posting says, and the engine decides what clears your terms.
* **Deadlines first.** Proposal and bid deadlines in the next 7 days lead the digest.
* **Channels.** Platforms where you need a live profile before work comes through are listed at the end of every digest until you mark each one Live or Skip.
* **One gig, one note.** The same gig listed by several agencies becomes one note, with the others under "also listed by".
* **Tell it what you did.** "I applied to C-12", "I won the cafe one", "my profile there is live". It works at any time.

It never bids, applies, signs up or sends anything.

## The networking sweep

Run **`/workdecero:networking-sweep`** to find where to show up, or just say "run the networking sweep". It looks at the groups in your local map and searches for more, ranked by who you said you want to meet.

* **Groups and events are kept apart.** A group that meets every month is one note with its next meeting. An event gets its own note only when it is special: a conference, a speaker squarely in your field, a call for speakers.
* **Only live groups.** A group needs a meeting in the last six months or one on the calendar. One that has gone quiet is marked Inactive with the evidence, unless you have joined it, in which case you are told.
* **One question, a two-minute read.** Every digest leads with the follow-ups you owe people, then this month's picks: at most the number of events a month you asked for. A meeting at a time you said you cannot do is never a pick. It is listed under "Also coming up" so you can swap it in.
* **Said plainly.** When no group exists for a kind of person you want to meet, the digest says so.
* **Tidied by date.** An event you never planned to go to is marked Did not go once it has passed. One you registered for or were speaking at is never changed for you. The digest asks whether you went.
* **Your spending limits stay private.** The assistant that reads the web is never told what you will pay. It records the price on the page, and the engine flags what is above your limit.
* **Speaking angles.** For groups that take speakers, a draft talk idea from your topics goes in the note. A draft only.
* **Tell it what you did.** "I'm going to the product meetup", "I registered for E-12", "I met Priya at the product meetup". People you met get a note and a follow-up date a week out.
* **Prep.** "Prep me for E-4" writes a ten-line brief into the event's note: who you know that may be there, two openers, what to bring.
* **Discreet.** Networking notes describe interests. They never say you are looking for work, and never name your employer.

It never registers, RSVPs, joins, pays or sends anything. Calendar is off: you add events to your calendar yourself.

## Your profile

One file, `~/.config/workdecero/profile.yaml`, holds what you are looking for, where, what to leave out and where your trackers live. Setup will write it for you. Set `WORKDECERO_HOME` to keep it in another folder.

See [examples/profiles](examples/profiles) for three complete examples, and [engine/profile.schema.json](engine/profile.schema.json) for every setting. To check a profile:

```
python3 engine/wdprofile.py validate path/to/profile.yaml
```

## Your tracker

Trackers are plain notes in your Obsidian vault, with table views that update as you work. Everything stays in one folder, and your own writing in a note is never touched. See [trackers/README.md](trackers/README.md) for the layout, the promises the plugin keeps, and how to build a demo vault to look around.

![The Start here note](docs/screenshots/start-here.png)

## A spreadsheet to glance at (optional)

Off by default. Turn it on with `/workdecero:setup` ("turn on the spreadsheet copy"). After each sweep, a file called `workdecero snapshot.xlsx` is rebuilt in your tracker folder: a Summary sheet with counts by status, then Jobs, Contracts, Channels, Groups, Events and People. Rows are in the same order as the main views and shaded by stage, and the first cell of each row opens its note in Obsidian.

It is a picture of your notes, never a second copy to keep up. It is written fresh every sweep and never read back, so anything typed into it is lost. The first row of every sheet says so. Names you keep confidential do not appear in it, and only you can read the file.

## Optional connections

All off until you turn them on with `/workdecero:setup`. Each one is skipped cleanly, with a line in the digest, when its tool is not available in a session.

| Connection | What it does | Rules it keeps |
| --- | --- | --- |
| Browser pass (Claude in Chrome) | Reads job boards and sites that a script cannot | Its own tab, closed at the end. Paced like a person reading. Read only: never a form, never apply, save or follow. A CAPTCHA is never attempted: the site is named under "Not covered" |
| Signed-in LinkedIn | Finds who you already know at an employer, and the hiring team on a posting | Needs two switches: the connection, and your acknowledgement of the risk. **Automated activity can get a LinkedIn account restricted or closed.** workdecero paces itself and keeps to a small daily number of pages, and cannot remove that risk. Read only. In a quiet search it never opens a profile, because a profile view can be shown to its owner |
| Calendar (Outlook or Google) | Adds a networking event when you say you are going | Checks for a clash first and tells you. One event, no attendees. The entry holds the link and why to go |
| Task tracker (Linear) | Posts a short comment after each sweep on an issue you name | Counts only. Never a title, an employer or a name |

**No standing permission for these.** The hook described below covers only workdecero's own helper commands. Browser, calendar and tracker tools are asked for by Claude Code each time, like any other tool. If you want fewer prompts, allow them yourself in your own Claude Code settings.

## What workdecero is allowed to do without asking

Claude Code normally asks before it runs a command or writes a file. Setup and the sweeps are conversations with many steps, and being asked a dozen times would make them unusable. So while this plugin is enabled, a small hook (`hooks/allow_helper.py`) approves exactly the following, and nothing else:

| What | Exactly |
| --- | --- |
| Run the setup helper | `python3 "<plugin folder>/skills/setup/scripts/setup_tool.py"` followed by one of: `status`, `takeover`, `pick-up`, `fields`, `save`, `read-resume`, `check-vault`, `preview`, `preview-details`, `preview-file`, `write`, `cancel`, `import-connections`. Nothing may follow the subcommand |
| Run the local map helper | `python3 "<plugin folder>/skills/sources/scripts/sources_tool.py"` followed by one of: `plan`, `plan-refresh`, `open`, `add`, `reverify`, `remove`, `finish`, `close`, `show`. Nothing may follow the subcommand |
| Run the job sweep helper | `python3 "<plugin folder>/skills/job-sweep/scripts/sweep_tool.py"` followed by one of: `start`, `restart`, `intake`, `warm`, `finish`, `cancel`. Nothing may follow the subcommand |
| Run the contract sweep helper | `python3 "<plugin folder>/skills/contract-sweep/scripts/contract_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `close`. Nothing may follow the subcommand |
| Run the networking sweep helper | `python3 "<plugin folder>/skills/networking-sweep/scripts/networking_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `prep`, `close`. Nothing may follow the subcommand |
| Write one temporary file per helper | `~/.cache/workdecero/setup-answers.json`, `sources-entries.json`, `sweep-handoff.json`, `contract-handoff.json` and `networking-handoff.json` in the same folder, and only while that folder is a real folder that you own and nobody else can open |

What that means in practice:

* The helpers take no arguments, so an approved command cannot be aimed at another file. They read and write your profile, your local map, the workdecero folder in your vault, and the temporary folder above. A command that changes anything only works while its skill has a run open, so a stray call in another conversation changes nothing.
* Any other command, any extra word on those command lines, any other file: Claude Code asks you as usual. The hook never approves anything it does not recognise in full.
* One thing is refused outright. In a conversation where you have started a workdecero skill, while browser use is off in your profile (it is off unless you turn it on), any tool that drives a browser, such as Claude in Chrome, is refused with the reason "workdecero: browser use is off in your profile". Nothing else is ever refused, and your other conversations are never affected. To know which conversation is which, the hook keeps an empty file named by the conversation's id for a day.
* Three things still ask on purpose:
  * Reading your resume if it is a PDF outside the folder you started in. That is your file, so you get to say yes.
  * Starting a skill by plain words. A slash command such as `/workdecero:job-sweep` asks nothing. If you say "run the job sweep" instead, Claude Code asks once to allow it, and you can allow it permanently from that prompt.
  * Web search later in a conversation. A refresh or re-run asked for later in the same conversation may ask once to allow web search. Start it with the slash command or allow it permanently.
* The approval applies in any conversation while the plugin is enabled, not only during setup. To turn it off, disable the plugin: `claude plugin disable workdecero@workdecero`.

## What it does not do

Research only. It never applies, messages, registers or creates accounts for you.

## Requirements

Claude Code and `python3` 3.9 or newer (the version macOS ships). No other installs.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
