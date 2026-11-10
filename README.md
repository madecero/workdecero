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
| Profile (`profile.yaml`) and local map (`sources.yaml`) | `~/.config/workdecero/` | Location can be overridden. Untouched by updates and by uninstall. |
| Trackers (postings, contracts, groups, events, people, companies) | Your Obsidian vault | Plain Markdown notes that you own. |
| Setup draft | `~/.cache/workdecero/` (`setup-answers.json`, `setup-state.json`) | Temporary. Holds your answers only while setup is running, in a folder only you can open, and is deleted when setup is saved or cancelled. |
| Source hand-off | `~/.cache/workdecero/` (`sources-entries.json`, `sources-run.json`) | Temporary. Holds public names and links found while your local map is being built. Each batch is deleted as soon as it is checked, and the folder goes when the run ends. |
| Sweep hand-off and state | `~/.cache/workdecero/sweep-handoff.json`, `sweep-state.json` | Temporary. Hold public postings found during a sweep, and are deleted when the sweep finishes. |
| Cache | The plugin data folder managed by Claude Code | Safe to lose. Removed when the plugin is uninstalled. |

## Your local map

Say **build my local map** after setup. In about 3 minutes it finds where your sweeps should look: employers in your field and area and the job site each one uses, state and field job boards, the places contract work is posted, and local groups and associations. Every entry is checked with a live call before it is kept. The result is `~/.config/workdecero/sources.yaml`, which you can edit. It is re-checked every few weeks.

## The job sweep

Say **run the job sweep**, about once a week. In Lite mode it takes a few minutes. It re-checks which of your open postings are still live, reads the job lists of the employers in your local map, searches a little further inside a small budget, scores what it finds against your profile, and writes the results into your tracker with a digest note that puts deadlines first. In Lite mode it takes about 10 minutes.

It never applies, messages, follows or saves anything, and it does not sign in anywhere. The part that reads web pages is never told your pay floor, your exclusions, your current employer or who you know. Those are applied on your computer after the reading is done.

## Your profile

One file, `~/.config/workdecero/profile.yaml`, holds what you are looking for, where, what to leave out and where your trackers live. Setup will write it for you. Set `WORKDECERO_HOME` to keep it in another folder.

See [examples/profiles](examples/profiles) for three complete examples, and [engine/profile.schema.json](engine/profile.schema.json) for every setting. To check a profile:

```
python3 engine/wdprofile.py validate path/to/profile.yaml
```

## Your tracker

Trackers are plain notes in your Obsidian vault, with table views that update as you work. Everything stays in one folder, and your own writing in a note is never touched. See [trackers/README.md](trackers/README.md) for the layout, the promises the plugin keeps, and how to build a demo vault to look around.

![The Start here note](docs/screenshots/start-here.png)

## What workdecero is allowed to do without asking

Claude Code normally asks before it runs a command or writes a file. Setup and the sweeps are conversations with many steps, and being asked a dozen times would make them unusable. So while this plugin is enabled, a small hook (`hooks/allow_helper.py`) approves exactly the following, and nothing else:

| What | Exactly |
| --- | --- |
| Run the setup helper | `python3 "<plugin folder>/skills/setup/scripts/setup_tool.py"` followed by one of: `status`, `takeover`, `fields`, `save`, `read-resume`, `check-vault`, `preview`, `preview-details`, `preview-file`, `write`, `cancel`, `import-connections`. Nothing may follow the subcommand |
| Run the local map helper | `python3 "<plugin folder>/skills/sources/scripts/sources_tool.py"` followed by one of: `plan`, `plan-refresh`, `open`, `add`, `reverify`, `remove`, `finish`, `close`, `show`. Nothing may follow the subcommand |
| Run the job sweep helper | `python3 "<plugin folder>/skills/job-sweep/scripts/sweep_tool.py"` followed by one of: `start`, `restart`, `intake`, `warm`, `finish`, `cancel`. Nothing may follow the subcommand |
| Write one temporary file per helper | `~/.cache/workdecero/setup-answers.json`, `sources-entries.json` and `sweep-handoff.json` in the same folder, and only while that folder is a real folder that you own and nobody else can open |

What that means in practice:

* The helpers take no arguments, so an approved command cannot be aimed at another file. They read and write your profile, your local map, the workdecero folder in your vault, and the temporary folder above. A command that changes anything only works while its skill has a run open, so a stray call in another conversation changes nothing.
* Any other command, any extra word on those command lines, any other file: Claude Code asks you as usual. The hook never blocks anything and never approves anything it does not recognise in full.
* One thing still asks on purpose: reading your resume if it is a PDF outside the folder you started in. That is your file, so you get to say yes.
* The approval applies in any conversation while the plugin is enabled, not only during setup. To turn it off, disable the plugin: `claude plugin disable workdecero@workdecero`.

## What it does not do

Research only. It never applies, messages, registers or creates accounts for you.

## Requirements

Claude Code and `python3` 3.9 or newer (the version macOS ships). No other installs.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
