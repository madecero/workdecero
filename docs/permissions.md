# What workdecero runs without asking

[Back to the README](../README.md)

Claude Code normally asks you before it runs a command or writes a file. Setup and the sweeps have many small steps, and being asked a dozen times would make them unusable. So while workdecero is enabled, it approves a short, fixed list of its own actions for you. Nothing else.

To do this, a small check runs each time you send a prompt and each time Claude Code is about to run a shell command, write a file, start a skill or use a connected tool. It is a short Python script on your own computer. It takes a few hundredths of a second, reads nothing but the name of what is about to happen, and sends nothing anywhere. For a shell command it compares the whole command line with its fixed list, and for a file it compares the path. It does not keep or log either.

**Exactly what is approved:**

| What | Exactly |
| --- | --- |
| Run the setup helper | `python3 "<plugin folder>/skills/setup/scripts/setup_tool.py"` followed by one of: `status`, `takeover`, `pick-up`, `fields`, `save`, `read-resume`, `check-vault`, `preview`, `preview-details`, `preview-file`, `write`, `cancel`, `import-connections`. Nothing may follow that word |
| Run the local map helper | `python3 "<plugin folder>/skills/sources/scripts/sources_tool.py"` followed by one of: `plan`, `plan-refresh`, `open`, `add`, `reverify`, `remove`, `finish`, `close`, `show`. Nothing may follow that word |
| Run the job sweep helper | `python3 "<plugin folder>/skills/job-sweep/scripts/sweep_tool.py"` followed by one of: `start`, `restart`, `intake`, `warm`, `finish`, `cancel`. Nothing may follow that word |
| Run the contract sweep helper | `python3 "<plugin folder>/skills/contract-sweep/scripts/contract_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `close`. Nothing may follow that word |
| Run the networking sweep helper | `python3 "<plugin folder>/skills/networking-sweep/scripts/networking_tool.py"` followed by one of: `start`, `restart`, `intake`, `finish`, `cancel`, `list`, `open`, `update`, `prep`, `close`. Nothing may follow that word |
| Run the import helper | `python3 "<plugin folder>/skills/import/scripts/import_tool.py"` followed by one of: `fields`, `check`, `run`, `cancel`. Nothing may follow that word |
| Write one temporary file per helper | `setup-answers.json`, `sources-entries.json`, `sweep-handoff.json`, `contract-handoff.json` `networking-handoff.json` and `import-plan.json`, each in `~/.cache/workdecero/`, and only while that folder is a real folder that you own and nobody else can open |

**What that means.**

* The helpers take no extra words, so an approved command cannot be pointed at another file. They read and write your profile, your local map, the `workdecero` folder in your vault and the temporary folder. That is all they can reach.
* A helper command that changes anything only works while its part of workdecero is in the middle of a run. A stray call in some other conversation changes nothing.
* Any other command, any extra word on those lines, any other file: Claude Code asks you as usual. The approval never approves anything it does not recognise in full.
* One thing is refused outright. In a conversation where you have started a workdecero skill, while browser use is off in your profile (it is off unless you turn it on), any tool that drives a browser, such as Claude in Chrome, is refused with the reason "workdecero: browser use is off in your profile". This holds from the moment the skill starts. Nothing else is ever refused, and your other conversations are never affected.
* To do that, the hook notes which conversations have started a workdecero skill: one empty file per conversation, named by its id, in `~/.cache/workdecero-sessions/`, a folder only you can open. Each is removed after a day. It reads whether browser use is on from `~/.config/workdecero/flags.json`, a one-line file kept in step with your profile, so it never reads the profile itself.
* The approval holds in any Claude Code conversation while the plugin is enabled. **To turn it off, disable the plugin:** `claude plugin disable workdecero@workdecero`. The code that does this is one short file, [`hooks/allow_helper.py`](../hooks/allow_helper.py), if you want to read it.

**What still asks, on purpose:**

* **Reading a PDF resume** that sits outside the folder you started Claude Code in. It is your file, so you get to say yes.
* **Starting by plain words.** "Run the job sweep" asks once. The slash command does not.
* **Web search and page reads later in a conversation.** A sweep started with its slash command searches and reads pages without asking, for that one request. If you ask for a refresh or a second sweep later in the same conversation, Claude Code asks for each web search and each page it reads, one at a time. Say yes to each, or start again with the slash command, which asks nothing.
* **Adding a source by its address.** "Add example.org to my sources" in a later message can ask before that one page is read, for the same reason.
* **Installing python3 on a new Mac.** That prompt comes from macOS, the first time python3 is needed. See "Before you start" in the README.
* **Every optional connection** below: your browser, your calendar, your task tracker.
