# workdecero

A Claude Code plugin that runs weekly job, contract and networking sweeps from your own profile and keeps the results in your Obsidian vault.

**Under construction.** Setup works. The sweeps themselves are still being built.

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
| Profile and local source map | `~/.config/workdecero/` | Location can be overridden. Untouched by updates and by uninstall. |
| Trackers (postings, contracts, groups, events, people, companies) | Your Obsidian vault | Plain Markdown notes that you own. |
| Cache | The plugin data folder managed by Claude Code | Safe to lose. Removed when the plugin is uninstalled. |

## Your profile

One file, `~/.config/workdecero/profile.yaml`, holds what you are looking for, where, what to leave out and where your trackers live. Setup will write it for you. Set `WORKDECERO_HOME` to keep it in another folder.

See [examples/profiles](examples/profiles) for three complete examples, and [engine/profile.schema.json](engine/profile.schema.json) for every setting. To check a profile:

```
python3 engine/wdprofile.py validate path/to/profile.yaml
```

## Your tracker

Trackers are plain notes in your Obsidian vault, with table views that update as you work. Everything stays in one folder, and your own writing in a note is never touched. See [trackers/README.md](trackers/README.md) for the layout, the promises the plugin keeps, and how to build a demo vault to look around.

![The Start here note](docs/screenshots/start-here.png)

## What it does not do

Research only. It never applies, messages, registers or creates accounts for you.

## Requirements

Claude Code and `python3` 3.9 or newer (the version macOS ships). No other installs.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
