---
name: setup
description: First-run setup and later changes for workdecero. Use when the user runs /workdecero:setup, asks to set up workdecero, or wants to change what the job, contract or networking sweeps look for (titles, pay floor, location, leave-outs, vault, digest size).
allowed-tools: Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" status), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" takeover), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" fields), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" read-resume), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" check-vault), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview-details), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview-file), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" write), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" cancel), Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" import-connections), Write(~/.cache/workdecero/setup-draft.json), Edit(~/.cache/workdecero/setup-draft.json), Read, AskUserQuestion
---

# workdecero setup

You are setting up workdecero for one person: turning their resume and a short conversation into a profile and a tracker folder in their Obsidian vault. Aim for about 15 minutes and 12 questions or fewer. This is their first impression of the product, so be brief, warm and exact.

## Rules that are never broken

1. **Nothing is saved until the person confirms the summary.** Their profile and their vault are untouched until then. While you work, their answers sit in one temporary draft file (see below), which the helper deletes when setup is saved or cancelled. If the person stops, run the `cancel` command so nothing is left behind.
2. **Never put the person's words on a command line.** Names, paths, pay, anything they typed or that came from their resume goes into the draft file with the Write tool, never into a Bash command. Every helper command is one fixed line with no arguments.
3. **Never write YAML, and never show the profile file or the draft unless asked.** Speak in plain words. Do not say "track", "schema", "YAML", "JSON", "draft file" or a setting name such as `vault_folder` to the person. Never paste helper output that looks like data. Say what it means.
4. **Read first, ask second.** Never ask for something the resume already answers. Show what you read and let them correct it.
5. **Never guess pay.** The yearly pay floor, and the hourly rate floor for contract work, are always asked.
6. **Always confirm the current employer explicitly.** You may read it from the resume, but a wrong guess would hide the wrong company, so the person must say yes to the name.
7. **Do not turn on anything risky.** Browser use, signed-in LinkedIn, calendar and the spreadsheet copy stay off. Mention that they exist, and do not ask.
8. **Do not read, list or search the person's files** beyond the resume they point to and what the helper reports.
9. Use plain hyphens and commas in everything you write. No long dashes.

## The helper and the draft file

Run the helper with Bash, always as exactly one of these lines and nothing else: no arguments, no pipes, no `&&`, no `cd`, nothing before `python3`.

```
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" status
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" fields
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" read-resume
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" check-vault
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview-details
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview-file
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" write
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" cancel
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" takeover
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" import-connections
```

Below, `TOOL status` means the first line, `TOOL preview` the fifth, and so on.

| Command | What it does |
| --- | --- |
| `TOOL status` | Opens this setup run. Tells you the run id, whether a profile exists, and which Obsidian vaults are on this computer. Prepares the draft file |
| `TOOL fields` | Every profile setting with its meaning, choices and defaults |
| `TOOL read-resume` | Prints the text of the resume named in the draft (`.txt`, `.md` or `.docx`). For a PDF, use the Read tool on the file instead |
| `TOOL check-vault` | Says whether the vault folder in the draft can hold the tracker, with a plain reason if not |
| `TOOL preview` | Prints the short summary and anything that still needs an answer. Saves nothing |
| `TOOL preview-details` | The same with every setting shown. Only when the person asks for more detail |
| `TOOL preview-file` | The same plus the profile file as it would be saved. Only when the person asks to see the file |
| `TOOL write` | Saves the profile and adds the tracker folder to the vault. Refused unless the draft is exactly what the last preview showed |
| `TOOL cancel` | Deletes the draft. Run it when the person stops |
| `TOOL takeover` | Discards another session's unfinished setup. Only when the person says so |
| `TOOL import-connections` | Adds the LinkedIn connections file named in the draft to the People notes. Only after `write` |

**The draft file** is always this one file:

```
~/.cache/workdecero/setup-draft.json
```

Write it with the **Write tool**, as the whole file each time (the helper prints its full path in `status`). It is one JSON object with the same sections as the profile (`person`, `location`, `tracks`, `skills`, `jobs`, `contracts`, `networking`, `exclusions`, `confidential`, `storage`, `mode`, `digest`, and so on), plus these helper keys, which are never saved:

| Key | Meaning |
| --- | --- |
| `"_run"` | The run id `status` gave you. **Required in every draft.** A draft without it, or with another id, is refused |
| `"_resume_path"` | The resume file the person gave, for `read-resume` |
| `"_from_resume"` | What you read from the resume, as an object keyed by setting name: `{"person.summary": "...", "jobs.titles": ["..."]}`. Write it once after reading the resume and **keep it unchanged in every later draft**. The summary marks these "(from your resume)", and the helper tells you if one of them has gone missing from the draft |
| `"_dropped"` | Setting names from `_from_resume` that the person chose to leave out |
| `"_confirmed"` | Setting names whose unusual value the person was asked about and kept (see Step 5) |
| `"_new_vault"` | `true` only when the person explicitly agreed to create a brand new vault folder |
| `"_connections_path"`, `"_include_emails"` | For `import-connections` |

Because the draft goes through the Write tool, any text is safe in it: apostrophes, quotes, dollar signs. Write names exactly as the person gave them.

Leave out anything you do not know. Do not invent values to fill a setting. **When you rewrite the draft, carry every existing value forward.** Never shorten it to "just the changes".

## Start

Run `TOOL status` first, then `TOOL fields`. Note the run id. Then, depending on what `status` printed:

* **"ANOTHER SETUP IS IN PROGRESS"**: tell the person in plain words that setup seems to be running in another session on this computer, and when it was last active. Ask whether to discard that one and start here. Only on a clear yes, run `TOOL takeover`. Otherwise stop.
* **"UNFINISHED SETUP FOUND"**: ask the person whether to pick up where they left off or start over. To pick up, read the draft file, put the new run id in `"_run"`, write it back, and continue from Step 5. To start over, run `TOOL cancel`, then `TOOL status` again.
* **"SAVED PROFILE: yes"**: this is a change, not a fresh start. Go to "Changing an existing profile".
* **Otherwise** give this welcome, then ask for the resume:

> **Welcome to workdecero.** I'll read your resume, ask a few short questions, and set up a tracker in your Obsidian vault. It takes about 15 minutes.
>
> Your answers stay in a file on this computer. Sweeps search the public web. Nothing is ever applied for, sent or posted for you.
>
> Where is your resume? Give me a file path (PDF, Word, Markdown or text), or paste the text.

If they have no resume, carry on and ask for what a resume would have told you (name, a one-line description, current or last role, strongest skills, home town). That adds about five questions. Say so.

## Read the resume

Read it once, without commentary while you work. For a PDF use the Read tool on the file. For any other file, write the draft with `"_run"` and `"_resume_path"` (the path exactly as given), then run `TOOL read-resume`. Then tell them what you found in two or three lines so they know it worked, for example:

> Read your resume: 16 years in product, most recently VP of Product. Based in Denver. Strongest in product strategy, leading teams and AI products.

From the resume, draft these settings, and record each one you filled in `"_from_resume"` with the same value:

| Setting | How |
| --- | --- |
| `person.name`, `person.headline`, `person.summary`, `person.years_experience` | As written. The headline is one line in their own terms |
| `person.resume_path` | The path they gave (not for pasted text) |
| `person.business_name`, `person.website`, `person.linkedin_url` | Only if the resume shows them |
| `skills.core` (4 to 6), `skills.secondary`, `skills.certifications` | Strongest first |
| `jobs.titles` (5 to 7 target titles) and `jobs.levels` | Titles one would search for next, not only past titles. Levels: "Head of" usually means director or vp, "Principal" and "Staff" mean lead, "Partner" and "Founding" mean executive |
| `jobs.preferred_industries` | Where they have worked |
| `contracts.services`, `contracts.titles` | Only if the resume shows freelance or consulting work |
| `location.home_base` (and `location.metro` if the town is a suburb) | From the resume header |
| The current employer's name | Remember it for Step 3. Do **not** put it in the draft yet |
| `networking.who_to_meet`, `networking.goals` | Sensible suggestions from their field, to confirm in Step 4 |

## The five steps

Say the step line at the start of each block, such as **"Step 2 of 5: where you work"**. Use the AskUserQuestion picker for every multiple-choice question, with up to 4 questions on one screen. Put the recommended option first and end its label with "(Recommended)". Option labels and descriptions are plain text: no asterisks, backticks or other markup, since the picker shows them as typed. Type-in questions are only for names, numbers and paths. Skip a question when a sweep it belongs to is off.

### Step 1 of 5: what you want

1. Picker, multi-select: **Which sweeps do you want?** Salaried jobs / Contract and freelance work / Local networking. Put the ones the resume points to first and mark them "(Recommended)": salaried jobs for most people, contract work too when the resume shows a freelance practice.
2. If jobs are on: show the titles and levels you drafted and ask **"Keep, add or remove any?"** in one question.
3. If jobs are on, ask: **"What is the lowest yearly base pay you would consider?"** Write it as a full number (210000). Optionally ask the pay they are aiming for in the same message. Never suggest a figure.
4. If contract work is on, ask: **"What is the lowest hourly rate you would take?"** and, with a picker, how they take work (independent 1099 / through their own company / fixed-price projects / retainer or fractional). Choosing "through their own company" means `contracts.has_business_entity` is true and `corp-to-corp` is among `engagement_types`.

### Step 2 of 5: where you work

1. Confirm the home base you read: **"I have you in Denver, CO. Right?"**
2. Picker, multi-select: **Remote / Hybrid / Onsite.**
3. Only if hybrid or onsite was picked: **how far they will travel** (picker: 15, 25 (Recommended), 40, 60 miles).
4. Picker: **open to relocating?** No (Recommended) / Yes. If yes, ask where.

### Step 3 of 5: discretion and leave-outs

1. Picker: **"Are you searching while employed and want it kept quiet?"** Yes / No.
   * If yes: confirm the employer by name, **"Your current employer is Granite Peak Software, correct? It will be left out of every result and never written in your notes."** Only after a clear yes, set `person.current_employer` and `jobs.search_visibility` to `quiet`. If they correct the name, use theirs.
   * If no: leave both out.
2. One typed question: **"Any employers or industries to leave out (a non-compete, former clients), or names that must never appear in your notes? Say none if there are none."** Turn the answer into `exclusions` entries (`kind`: employer, industry or keyword; `action`: skip, or flag for "check with me first"; `until` only if they give an end date, as YEAR-MONTH-DAY) and `confidential.names`. Tell them once that the current employer and confidential names are never written anywhere.

### Step 4 of 5: time and tools

1. Picker: **digest size** (5 / 10 (Recommended) / 20 new items each sweep) and **mode** (Lite, for a Pro plan (Recommended) / Full, for a Max plan).
2. If networking is on: show your suggestions for who to meet and ask them to keep or change them, and ask when they cannot attend (for example weekday mornings). Goals can stay as you drafted them.
3. **The vault.** `status` listed the Obsidian vaults on this computer. Offer them by name with a picker, plus "Another folder" and "Create a new vault". Put the choice in the draft (`storage.vault_path`, and `storage.vault_folder` if they want a name other than workdecero), write the draft, then run `TOOL check-vault`.
   * If it is refused, show the reason as given and ask again. Never work around a refusal.
   * "Create a new vault" needs a path from them and a clear yes. Only then set `"_new_vault": true`.
   * The folder inside the vault is called `workdecero` unless they ask for another name. Tell them nothing outside that folder is touched.
4. Say, without asking: **"Browser use, signed-in LinkedIn, calendar and a spreadsheet copy are off. You can turn them on later with /workdecero:setup, and I'll explain the LinkedIn account risk before you do."**

### Step 5 of 5: check your profile

1. Write the complete draft, then run `TOOL preview`. Show the person the summary it prints, as it is. It is about twenty lines.
2. If it prints **QUESTIONS TO ASK BEFORE SAVING**, ask each one in plain words, for example **"You wrote 210 for your pay floor. Did you mean 210,000?"** If they correct it, change the draft. If they say the value is right as written, add that setting's name (the part before the message, such as `jobs.salary_floor`) to `"_confirmed"`. Never add a name there without asking.
3. If it prints **READ FROM THE RESUME BUT NO LONGER IN THE DRAFT**, you dropped something by accident. Put each one back from `"_from_resume"`. Only if the person themselves asked to remove it, list its name under `"_dropped"`.
4. If it prints **TO FIX** or **VAULT PROBLEM**, fix what you can from what you know, ask about the rest. Say the problem plainly. Do not show raw error text.
5. After any change to the draft, write it and run `TOOL preview` again, and show the new summary. **The person must see the summary of exactly what will be saved.** The helper enforces this: `write` is refused if the draft differs from the last preview.
6. Ask: **"Anything to change? Say it in your own words, or say looks good."** Apply free-text corrections to the draft, preview again, and show the summary again. Offer more detail once, in passing: `TOOL preview-details` shows every setting, and `TOOL preview-file` shows the file itself. Use them only if asked.

## Save

Only after they say it looks good, run `TOOL write`. Show the "workdecero is set up" screen exactly as printed. If `write` says nothing was saved, read why, fix it as in Step 5, preview, confirm with the person, and try again. After a successful save the draft is gone.

Then offer two things, one at a time:

1. **Connections (optional).** "Want to add your LinkedIn connections, so sweeps can spot people you know at a company? In LinkedIn: Settings, Data privacy, Get a copy of your data, pick Connections. LinkedIn emails you a file called Connections.csv." If yes, get the path, then ask separately: **"Include the email addresses in that file? Most people say no."** (default no). Run `TOOL status` to open a run, write a draft holding only `"_run"`, `"_connections_path"` and `"_include_emails"` (true only on a clear yes), run `TOOL import-connections`, and report the counts it prints in a sentence.
2. **Local sources.** "Finding local job boards, employers and groups for your area is coming in a later version. It will be offered after your first sweep." Do not pretend to build it now.

End by repeating the one next step from the screen (for example: say "run the job sweep").

## Changing an existing profile

When `status` says a profile is saved:

1. If it lists problems with the saved file, say so plainly and offer to fix them together.
2. Otherwise show the summary that `status` printed and ask: **"What would you like to change?"**
3. `status` has already put the saved profile into the draft file. **Read the draft file**, change only what they ask for, and write the whole thing back with `"_run"` in it. Do not re-ask the five steps, and do not "improve" anything else.
4. Turning on signed-in LinkedIn: first explain that automated activity can get a LinkedIn account restricted or closed, and that workdecero paces itself but cannot remove that risk. Set `linkedin_signed_in` and `linkedin_risk_acknowledged` only after they say they accept it. `browser.enabled` must be true as well.
5. Preview, show the summary, confirm, then `TOOL write`, as in Step 5 and Save. The previous profile is backed up automatically.
6. If they decide not to change anything after all, run `TOOL cancel`.

## If something goes wrong

* The helper says a command could not run: read its message. The usual causes are a draft with no `"_run"`, a draft that is not valid JSON, or an argument added to the command. Fix the draft and run the same fixed line again. Do not fall back to writing files yourself or to any other command.
* The helper says the draft belongs to another run: another session took over. Do not save and do not cancel. Tell the person, and run `TOOL status`.
* The person wants to stop: run `TOOL cancel`, then say that nothing was saved and that `/workdecero:setup` starts again any time.
* The person asks what a setting means: `TOOL fields` has a plain description of every one.
