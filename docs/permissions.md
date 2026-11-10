# What workdecero runs without asking

[Back to the README](../README.md)

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
* The approval holds in any Claude Code conversation while the plugin is enabled. **To turn it off, disable the plugin:** `claude plugin disable workdecero@workdecero`. The code that does this is one short file, [`hooks/allow_helper.py`](../hooks/allow_helper.py), if you want to read it.

**What still asks, on purpose:**

* **Reading a PDF resume** that sits outside the folder you started Claude Code in. It is your file, so you get to say yes.
* **Starting by plain words.** "Run the job sweep" asks once. The slash command does not.
* **Web search later in a conversation.** If you ask for a refresh or a second sweep later in the same conversation, Claude Code may ask once to allow web search. Say yes, or start it with the slash command.
* **Every optional connection** below: your browser, your calendar, your task tracker.
