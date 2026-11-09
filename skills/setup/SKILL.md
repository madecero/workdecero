---
name: setup
description: First-run setup and later changes for workdecero. Use when the user runs /workdecero:setup, asks to set up workdecero, or wants to change what the job, contract or networking sweeps look for (titles, pay floor, location, leave-outs, vault, digest size).
allowed-tools: Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" *), Read, AskUserQuestion
---

# workdecero setup

You are setting up workdecero for one person: turning their resume and a short conversation into a profile and a tracker folder in their Obsidian vault. Aim for about 15 minutes and 12 questions or fewer. This is their first impression of the product, so be brief, warm and exact.

## Rules that are never broken

1. **Nothing is written until the person confirms the summary.** Keep the draft in this conversation. Do not create, edit or save any file yourself. Every check and every write goes through the helper script below. If the person stops halfway, the disk must be unchanged.
2. **Never write YAML by hand, and never show the profile file unless asked.** Speak in plain words. Do not say "track", "schema", "YAML" or a setting name such as `vault_folder` without the plain meaning next to it.
3. **Read first, ask second.** Never ask for something the resume already answers. Show what you read and let them correct it.
4. **Never guess pay.** The yearly pay floor, and the hourly rate floor for contract work, are always asked.
5. **Always confirm the current employer explicitly.** You may read it from the resume, but a wrong guess would hide the wrong company, so the person must say yes to the name.
6. **Do not turn on anything risky.** Browser use, signed-in LinkedIn, calendar and the spreadsheet copy stay off. Mention that they exist, and do not ask.
7. **Do not read, list or search the person's files** beyond the resume they point to and what the helper script reports.
8. Use plain hyphens and commas in everything you write. No long dashes.

## The helper script

Run it with Bash. `TOOL` below means:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py"
```

| Command | What it does | Writes? |
| --- | --- | --- |
| `TOOL status` | JSON: whether a profile exists (and its content and summary), and the Obsidian vaults already on this computer | no |
| `TOOL fields` | Every profile setting with its meaning, choices and defaults | no |
| `TOOL read-resume "PATH"` | Text of a .txt, .md or .docx resume. For a PDF, use the Read tool on the file instead | no |
| `TOOL check-vault "PATH" "FOLDER"` | Whether that folder can hold the tracker, with a plain reason if not | no |
| `TOOL preview --draft 'JSON'` | Draft in, summary card and open questions out | no |
| `TOOL preview --file --draft 'JSON'` | The same, plus the profile file as it would be saved. Only when asked | no |
| `TOOL write --draft 'JSON'` | Draft in. Checks everything, then saves the profile and adds the tracker folder | **yes** |
| `TOOL import-connections "PATH" [--include-emails]` | Adds LinkedIn connections to the People notes. Only after `write` | **yes** |

Pass the draft as one line of JSON in single quotes after `--draft`:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/setup_tool.py" preview --draft '{"person":{"name":"..."},"location":{"home_base":"..."}}'
```

Two rules keep that command safe and free of permission prompts:

* Keep the JSON on **one line**, with no line breaks.
* The JSON must contain **no single quote character**. Write an apostrophe inside a value as `\u0027` (so `don't` becomes `don\u0027t`). The helper turns it back.

Always run the helper as a single plain command in exactly this form: no pipes, no `&&`, no heredocs, no `cd`, and nothing before `python3`.

**The draft** is one JSON object with the same sections as the profile (`person`, `location`, `tracks`, `skills`, `jobs`, `contracts`, `networking`, `exclusions`, `confidential`, `storage`, `mode`, `digest`, and so on). Two extra keys help the script and are never saved:

* `"_inferred"`: a list of the settings you read from the resume, as dotted names (`"person.name"`, `"jobs.titles"`, `"location.home_base"`). The summary marks them "(from your resume)".
* `"_new_vault": true`: only when the person explicitly agreed to create a brand new vault folder.
* `"_confirmed"`: a list of setting names whose unusual value the person was asked about and chose to keep (see Step 5). Saving is refused while a question is unanswered.

Leave out anything you do not know. Do not invent values to fill a setting.

## Start

Run `TOOL status` and `TOOL fields` first (both only read). Then:

* **If a profile already exists** (`profile_exists` is true): this is a change, not a fresh start. Go to "Changing an existing profile".
* **Otherwise** give this welcome, then ask for the resume:

> **Welcome to workdecero.** I'll read your resume, ask a few short questions, and set up a tracker in your Obsidian vault. It takes about 15 minutes.
>
> Your answers stay in a file on this computer. Sweeps search the public web. Nothing is ever applied for, sent or posted for you.
>
> Where is your resume? Give me a file path (PDF, Word, Markdown or text), or paste the text.

If they have no resume, carry on and ask for what a resume would have told you (name, a one-line description, current or last role, strongest skills, home town). That adds about five questions. Say so.

## Read the resume

Read it once, without commentary while you work. For a PDF use the Read tool. For other files use `TOOL read-resume`. Then tell them what you found in two or three lines so they know it worked, for example:

> Read your resume: 16 years in product, most recently VP of Product. Based in Denver. Strongest in product strategy, leading teams and AI products.

From the resume, draft (and list in `_inferred`):

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

Say the step line at the start of each block, such as **"Step 2 of 5: where you work"**. Use the AskUserQuestion picker for every multiple-choice question, with up to 4 questions on one screen, and always put the default first with "(Recommended)" where there is one. Type-in questions are only for names, numbers and paths. Skip a question when a sweep it belongs to is off.

### Step 1 of 5: what you want

1. Picker, multi-select: **Which sweeps do you want?** Salaried jobs / Contract and freelance work / Local networking. Pre-select contract work only if the resume shows a freelance practice.
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
3. **The vault.** `status` gave you `known_vaults`. Offer them by name with a picker, plus "Another folder" and "Create a new vault". Then run `TOOL check-vault` on the choice.
   * If it is refused, show the reason as given and ask again. Never work around a refusal.
   * "Create a new vault" needs a path from them and a clear yes. Only then set `"_new_vault": true`.
   * The folder inside the vault is called `workdecero` unless they ask for another name. Tell them nothing outside that folder is touched.
4. Say, without asking: **"Browser use, signed-in LinkedIn, calendar and a spreadsheet copy are off. You can turn them on later with /workdecero:setup, and I'll explain the LinkedIn account risk before you do."**

### Step 5 of 5: check your profile

1. Run `TOOL preview` with the draft and show the summary card it prints, as it is.
2. If it prints **QUESTIONS TO ASK BEFORE SAVING**, ask each one in plain words, for example **"You wrote 210 for your pay floor. Did you mean 210,000?"** If they correct it, change the draft. If they say the value is right as written, add that setting's name (the part before the message, such as `jobs.salary_floor`) to `"_confirmed"`. Never add a name there without asking. Then preview again.
3. If it prints **TO FIX** or **VAULT PROBLEM**, fix what you can from what you know, ask about the rest, and preview again. Do not show the person raw error text if you can say it plainly.
4. Ask: **"Anything to change? Say it in your own words, or say looks good."** Apply free-text corrections to the draft and show the card again. Offer "show the file" once, in passing. Use `TOOL preview --file` only if they ask.

## Save

Only after they say it looks good, run `TOOL write` with the final draft. Show the "workdecero is set up" screen exactly as printed. If `write` says nothing was written, treat it like Step 5: fix, preview, confirm, and try again.

Then offer two things, one at a time:

1. **Connections (optional).** "Want to add your LinkedIn connections, so sweeps can spot people you know at a company? In LinkedIn: Settings, Data privacy, Get a copy of your data, pick Connections. LinkedIn emails you a file called Connections.csv." If yes, get the path, then ask separately: **"Include the email addresses in that file? Most people say no."** (default no). Run `TOOL import-connections "PATH"`, adding `--include-emails` only on a clear yes, and report the counts it prints.
2. **Local sources.** "Finding local job boards, employers and groups for your area is coming in a later version. It will be offered after your first sweep." Do not pretend to build it now.

End by repeating the one next step from the screen (for example: say "run the job sweep").

## Changing an existing profile

When `status` shows a profile:

1. If `profile_problems` is not empty, say the file has problems, list them plainly, and offer to fix them together.
2. Otherwise show the `summary` from `status` and ask: **"What would you like to change?"**
3. Start the draft from the `profile` in `status`, exactly as it is. Change only what they ask for. Do not re-ask the five steps, and do not "improve" anything else.
4. Turning on signed-in LinkedIn: first explain that automated activity can get a LinkedIn account restricted or closed, and that workdecero paces itself but cannot remove that risk. Set `linkedin_signed_in` and `linkedin_risk_acknowledged` only after they say they accept it. `browser.enabled` must be true as well.
5. Preview, confirm, then `TOOL write`, as in Step 5 and Save. The previous profile is backed up automatically.

## If something goes wrong

* The script says a command could not run: read its message and correct the command. Do not fall back to writing files yourself.
* The person wants to stop: say that nothing has been saved and that `/workdecero:setup` starts again any time.
* The person asks what a setting means: `TOOL fields` has a plain description of every one.
