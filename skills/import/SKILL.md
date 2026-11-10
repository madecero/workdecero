---
name: import
description: Move a tracker the person already keeps in a spreadsheet into their workdecero vault. Use when the user runs /workdecero:import, or says "import my tracker", "bring in my spreadsheet", "move my job tracker over", "I already have a list of jobs, contracts, groups or contacts in a spreadsheet".
allowed-tools: Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" fields), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" check), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" run), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" cancel), Write(~/.cache/workdecero/import-plan.json), AskUserQuestion
---

# workdecero import

You are moving one person's existing tracker into their vault. They export each table of their spreadsheet as a CSV file. You write a mapping that says what each column means. The helper reads the files, tells you what would happen, and writes the notes only after the person says yes.

## Rules that are never broken

1. **Never open, read or print the CSV files yourself.** They hold the person's private tracker: employers, pay, people, their own notes. Only the helper reads them. You see column names, counts, status words and dates that could not be read. That is all you need.
2. **Never put a path, a column name or a status word on a command line.** Everything goes in the hand-off file with the Write tool. Every helper command is one fixed line with no arguments.
3. **Never guess what a status word means.** If the helper lists a status word with no meaning, ask the person. Offer the plugin's statuses for that kind of note as the choices.
4. **Check first, and get a clear yes before `run`.** Show the person the counts from `check`. Do not run on "ok" to something else.
5. **Nothing is left behind.** A column you do not map is kept word for word in each note's body. Say so. Never tell the person a column will be dropped.
6. **Do not read the person's profile or their notes.** The helper applies their confidential names. Rows at a confidential name are counted and not written. Never ask which rows those were.
7. Use plain hyphens and commas in everything you write. No long dashes.
8. **Never open a browser,** and do not use web search. This skill needs neither.
9. **Do not start subagents or background tasks.** Do every step in this conversation. The one exception: if this session has no page fetch tool of its own, the built-in page reader (the `web-fetch` agent) may be used to read a public page, in the foreground.

## The helper and the hand-off file

Run the helper with Bash, always as exactly one of these lines and nothing else: no arguments, no pipes, no `&&`, no `cd`, nothing before `python3`.

```
python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" fields
python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" check
python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" run
python3 "${CLAUDE_SKILL_DIR}/scripts/import_tool.py" cancel
```

Below, `TOOL check` means the second line, and so on.

The mapping goes into this one file, written whole with the **Write tool** before each `TOOL check`:

```
~/.cache/workdecero/import-plan.json
```

## Step 1: what they have

Ask the person, in one message:

* which tables they have (jobs, contract gigs, platforms they have profiles on, groups, events, people, companies),
* the full path of the CSV file for each. If their tracker is in Numbers or Excel, tell them: File, Export To, CSV. Each table becomes one file.

Tell them once: **"Your spreadsheet is not changed, and nothing is written to your vault until you have seen the counts and said yes."**

## Step 2: see the columns

Run `TOOL fields` once. It lists the kinds of note, the properties each takes, and their statuses.

Write a first plan with only the file and the kind of note for each table, and run `TOOL check`:

```json
{"tables": [{"csv": "/Users/me/Exports/Jobs.csv", "type": "job"},
            {"csv": "/Users/me/Exports/Contacts.csv", "type": "person"}]}
```

The helper answers with each file's column names and row count. Nothing else from the files is shown.

## Step 3: the mapping

For each table, decide from the column names:

* **`title`**: the column that holds each row's name. For jobs, the role. For gigs, the gig. For people, the name.
* **`columns`**: column name to property, for the columns that match a property in `TOOL fields`. A column mapped to `company`, `contact`, `warm_path`, `people_met` or `group` makes a note for the person, company or group it names and links to it.
* Leave every other column out. It is kept in the note's body under the heading "From my old tracker". Use `"keep_heading"` if the person wants other words.
* If a column's meaning is not clear from its name, ask the person. Do not guess.

Show the person the mapping as a short table (column, what it becomes) and ask them to confirm or correct it. Then write the full plan and run `TOOL check`:

```json
{"tables": [{"csv": "/Users/me/Exports/Jobs.csv", "type": "job", "title": "Role",
             "columns": {"Employer": "company", "Low": "pay_min", "High": "pay_max", "Status": "status",
                         "Applied": "applied_on", "Link": "apply_url", "Hiring manager": "contact"},
             "statuses": {},
             "date_formats": []}]}
```

## Step 4: fix what the check found

`TOOL check` prints, for each table: rows, how many would be written, how many are already in the vault, how many would not be imported and why, the columns kept as text, dates and numbers it could not read, and status words with no meaning.

* **Status words with no meaning.** Ask the person what each one means, with a picker of that kind of note's statuses. Add each answer to `"statuses"`, such as `{"sent": "Applied", "ghosted": "Closed"}`. Their own word is kept in the note as well.
* **Dates it could not read.** If the examples show a pattern, add it to `"date_formats"` (`"%d.%m.%Y"` for 05.11.2026). If they are not dates at all ("next week"), leave them. They are kept as text.
* **Rows not imported.** Say how many and why, in the helper's words. The row-by-row detail is in a private file the helper names. Tell the person where it is. Do not open it.

Write the corrected plan and run `TOOL check` again. Repeat until it says READY.

## Step 5: yes, then run

Show the person the final counts per table and ask: **"Write these notes to your vault?"** Only on a clear yes, run `TOOL run`. It refuses if a CSV file changed since the check. If it does, run `TOOL check` again.

Then tell them, briefly:

* how many notes were written per table, and that every row is accounted for,
* that their old columns are in each note under "From my old tracker", and that workdecero never rewrites that part,
* that running the same import again adds nothing twice,
* that their spreadsheet was not changed, and to keep it until they have looked through the vault.

If they change their mind at any point, run `TOOL cancel`.
