#!/usr/bin/env python3
"""The helper behind /workdecero:setup.

The helper owns the draft. It keeps the profile being built in a private
state file that the assistant never reads or rewrites. The assistant sends
each step's answers as a small file written with its Write tool, and the
helper absorbs and deletes that file at the start of the next command. No
answer ever travels on a command line, so every helper call is a fixed
command with no arguments.

    The answers file (written by the assistant, a few lines per step):
        ~/.cache/workdecero/setup-answers.json
    The state file (the helper's own, never touched by the assistant):
        ~/.cache/workdecero/setup-state.json

Both live in a folder only the user can open, hold answers only while setup
is running, and are deleted after a save or a cancel. One left behind by an
earlier session is cleared on the next run. Neither is ever copied to backups.

An answers file is one JSON object:

    "_run": "ab12cd34"                   the run id from status. Required
    "set": {"jobs.salary_floor": 210000, "location.work_modes": ["remote"]}
                                         settings to set, by dotted name. A whole section may be set
                                         at once ("person": {...}). null removes a setting
    "append": {"jobs.titles": ["Head of Product"], "exclusions": [{...}]}
                                         items to add to a list
    "remove": ["jobs.deal_breakers"]     settings to remove
    "from_resume": true                  the values in "set" were read from the resume
    "_dropped": ["person.summary"]       resume values the person chose to leave out
    "_confirmed": ["jobs.salary_floor"]  unusual values the person was asked about and kept
    "_new_vault": true                   the person agreed to create a new vault folder
    "_resume_path": "..."                the resume file to read (for read-resume)
    "_connections_path": "...", "_include_emails": false
                                         for import-connections

Commands (none takes an argument). Each one first absorbs a waiting answers file:
    status               open a setup run: says what exists already (profile, vaults) and gives the run id
    fields               every profile setting with its description, for drafting
    save                 absorb the answers file and say which settings are now filled in
    read-resume          the text of the .txt, .md or .docx file named in "_resume_path"
    check-vault          whether the vault folder in the draft can hold the tracker
    preview              the short summary card and any open questions
    preview-details      the full summary card
    preview-file         the profile file as it would be written
    write                saves the profile and sets up the vault folder. Refused unless the draft
                         is exactly what the last preview showed
    cancel               deletes the draft
    takeover             discards another session's unfinished setup
    pick-up              carries on here with another session's unfinished setup, keeping its answers
    import-connections   adds the LinkedIn file named in "_connections_path" to the People notes

Exit codes: 0 fine, 1 the draft has problems to fix first, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "trackers"))

import miniyaml  # noqa: E402
import schema as schema_check  # noqa: E402
import vault  # noqa: E402
import warmpath  # noqa: E402
import wdprofile  # noqa: E402

OBSIDIAN_CONFIG_ENV = "WORKDECERO_OBSIDIAN_CONFIG"   # for tests: where to look for Obsidian's vault list
DRAFT_DIR_ENV = "WORKDECERO_DRAFT_DIR"               # for tests: where the draft file lives
DRAFT_NAME = "setup-state.json"                       # the helper's own copy of the draft
ANSWERS_NAME = "setup-answers.json"                  # what the assistant writes, one step at a time
SHOWN_NAME = "setup-preview.sha256"                  # a checksum of the last draft shown. No answers in it
RUN_NAME = "setup-run.json"                          # which setup run owns the draft. No answers in it
RUN_ACTIVE_MINUTES = 30
DRAFT_MAX_AGE_HOURS = 24
HELPER_KEYS = ("_run", "_inferred", "_from_resume", "_dropped", "_new_vault", "_confirmed", "_resume_path",
               "_connections_path", "_include_emails")
PRIVACY = "Nothing was applied for, sent or posted. Your resume was read, not copied."


# --------------------------------------------------------------------------
# Reading what is already there
# --------------------------------------------------------------------------

def draft_dir():
    return os.path.expanduser(os.environ.get(DRAFT_DIR_ENV) or os.path.join("~", ".cache", "workdecero"))


def draft_path():
    return os.path.join(draft_dir(), DRAFT_NAME)


def answers_path():
    return os.path.join(draft_dir(), ANSWERS_NAME)


def _shown_path():
    return os.path.join(draft_dir(), SHOWN_NAME)


def _run_path():
    return os.path.join(draft_dir(), RUN_NAME)


def current_run():
    """The run that owns the draft: {'run', 'started'} or None."""
    try:
        with open(_run_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and data.get("run") else None
    except (OSError, ValueError):
        return None


def minutes_since_activity():
    """Minutes since any setup file was last touched, or None when there are none."""
    stamps = [os.path.getmtime(path) for path in (draft_path(), answers_path(), _run_path(), _shown_path())
              if os.path.isfile(path)]
    return max(0.0, (time.time() - max(stamps)) / 60.0) if stamps else None


def start_run():
    """Begin a new setup run and return its id. The id goes into every draft this run writes."""
    folder = draft_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    run = hashlib.sha256(("%s-%s" % (time.time(), os.getpid())).encode("utf-8")).hexdigest()[:8]
    handle = os.open(_run_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump({"run": run, "started": datetime.datetime.now().isoformat(timespec="seconds")}, out)
    return run


def touch_run():
    if os.path.isfile(_run_path()):
        os.utime(_run_path(), None)


def _lock_down():
    """Owner-only on the folder and the draft. Called on every read, in case a write replaced the file."""
    folder = draft_dir()
    if os.path.isdir(folder):
        os.chmod(folder, 0o700)
    for name in (DRAFT_NAME, ANSWERS_NAME, SHOWN_NAME, RUN_NAME):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            os.chmod(path, 0o600)


def prepare_draft(content="{}\n"):
    """Create the private folder and the draft file before any answer exists, with owner-only permissions."""
    folder = draft_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    handle = os.open(draft_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(content)
    os.chmod(draft_path(), 0o600)


def delete_draft():
    """Remove the draft, its checksum and the folder if nothing else is in it. Returns True if a draft was removed."""
    removed = False
    for path in (draft_path(), answers_path(), _shown_path(), _run_path()):
        if os.path.isfile(path):
            os.remove(path)
            removed = removed or path == draft_path()
    folder = draft_dir()
    if os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
    return removed


def draft_state():
    """('none' | 'empty' | 'resumable' | 'stale' | 'unreadable', age in hours, draft or None)."""
    path = draft_path()
    if not os.path.isfile(path):
        return "none", 0.0, None
    age = max(0.0, (time.time() - os.path.getmtime(path)) / 3600.0)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.loads(handle.read() or "{}")
    except (OSError, ValueError):
        return "unreadable", age, None
    if not isinstance(data, dict) or not [key for key in data if not key.startswith("_")]:
        return "empty", age, data if isinstance(data, dict) else None
    if age > DRAFT_MAX_AGE_HOURS:
        return "stale", age, data
    return "resumable", age, data


LIST_HELPERS = ("_dropped", "_confirmed")
# Settings a resume often shows that are easy to pass over. Preview names the empty ones once more.
RESUME_EXTRAS = ("person.business_name", "person.website", "person.linkedin_url", "skills.certifications")


def draft_has_resume(result):
    return bool(result.get("resume_read"))
PLAIN_HELPERS = ("_new_vault", "_resume_path", "_connections_path", "_include_emails")


def _set_path(draft, dotted, value):
    keys = [key for key in str(dotted).split(".") if key]
    if not keys:
        return
    node = draft
    for key in keys[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    if value is None:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value


def _get_path(draft, dotted):
    node = draft
    for key in str(dotted).split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _save_state(draft):
    handle = os.open(draft_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(draft, out, indent=1, ensure_ascii=False)
    os.chmod(draft_path(), 0o600)


def absorb(draft):
    """Merge a waiting answers file into the draft and delete it. Returns the names that changed."""
    path = answers_path()
    if not os.path.isfile(path):
        return []
    os.chmod(path, 0o600)
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        answers = json.loads(text or "{}")
    except ValueError as exc:
        os.remove(path)
        raise ValueError("The answers file was not valid JSON (%s). Write this step's answers again as one "
                         "JSON object." % exc)
    if not isinstance(answers, dict):
        os.remove(path)
        raise ValueError("The answers file must hold one JSON object.")
    owner = current_run()
    if owner is None:
        os.remove(path)
        raise ValueError("No setup run is open. Run the status command first.")
    if answers.get("_run") != owner["run"]:
        os.remove(path)
        raise ValueError(
            "The answers file was written for setup run %s, but the open run is %s. If %s is the run id you were "
            "given, another session wrote over your answers: write this step's answers again. If it is not, "
            "another setup session has taken over: do not save and do not cancel, tell the person, and run the "
            "status command. Every answers file must carry \"_run\": \"<the run id status gave you>\"."
            % (answers.get("_run") or "(none)", owner["run"], owner["run"]))
    changed = []
    resume = draft.setdefault("_from_resume", {}) if answers.get("from_resume") is True else None
    for dotted, value in (answers.get("set") or {}).items() if isinstance(answers.get("set"), dict) else []:
        _set_path(draft, dotted, value)
        changed.append(str(dotted))
        if resume is not None and value not in (None, "", [], {}):
            if isinstance(value, dict) and "." not in str(dotted):
                for key, item in value.items():           # a whole section: record each setting in it
                    if item not in (None, "", [], {}) and not isinstance(item, dict):
                        resume["%s.%s" % (dotted, key)] = item
            else:
                resume[str(dotted)] = value
    for dotted, items in (answers.get("append") or {}).items() if isinstance(answers.get("append"), dict) else []:
        current = _get_path(draft, dotted)
        current = list(current) if isinstance(current, list) else []
        for item in items if isinstance(items, list) else [items]:
            if item not in current:
                current.append(item)
        _set_path(draft, dotted, current)
        changed.append(str(dotted))
    for dotted in answers.get("remove") or []:
        _set_path(draft, dotted, None)
        changed.append(str(dotted))
    for key in LIST_HELPERS:
        if isinstance(answers.get(key), list):
            draft[key] = sorted(set(list(draft.get(key) or []) + [str(item) for item in answers[key]]))
    for key in PLAIN_HELPERS:
        if key in answers:
            draft[key] = answers[key]
    if not draft.get("_from_resume"):
        draft.pop("_from_resume", None)
    os.remove(path)
    _save_state(draft)
    return changed


def read_draft():
    """The draft as it stands, after absorbing any answers the assistant has just written."""
    path = draft_path()
    owner = current_run()
    idle = minutes_since_activity()
    if owner is None or not os.path.isfile(path) or idle is None or idle > DRAFT_MAX_AGE_HOURS * 60:
        # Nothing changes outside a setup run that is open and recent. A stray call does nothing.
        if os.path.isfile(answers_path()):
            os.remove(answers_path())
        raise ValueError("No setup run is open. Run the status command first.")
    _lock_down()
    with open(path, "r", encoding="utf-8") as handle:
        draft = json.loads(handle.read() or "{}")
    draft["_run"] = owner["run"]
    read_draft.changed = absorb(draft)
    touch_run()
    return draft


read_draft.changed = []


def _checksum(draft):
    return hashlib.sha256(json.dumps(draft, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def obsidian_config_files():
    override = os.environ.get(OBSIDIAN_CONFIG_ENV)
    if override:
        return [override]
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Library", "Application Support", "obsidian", "obsidian.json"),
        os.path.join(home, ".config", "obsidian", "obsidian.json"),
        os.path.join(os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "obsidian", "obsidian.json"),
    ]


def known_vaults():
    """Vaults Obsidian already knows, most recently opened first. Reads Obsidian's list and never writes it."""
    found = []
    for path in obsidian_config_files():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        for entry in (data.get("vaults") or {}).values():
            folder = entry.get("path") if isinstance(entry, dict) else None
            if folder and os.path.isdir(folder) and folder not in [item["path"] for item in found]:
                found.append({"name": os.path.basename(folder.rstrip("/\\")), "path": folder,
                              "shown": _tilde(folder), "opened": entry.get("ts") or 0})
    found.sort(key=lambda item: -item["opened"])
    return [dict((key, item[key]) for key in ("name", "path", "shown")) for item in found]


def load_existing():
    """(profile as written, or None; problems)."""
    path = wdprofile.profile_path()
    if not os.path.isfile(path):
        return None, []
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        raw = miniyaml.loads(wdprofile.normalize_pasted(text))
    except miniyaml.YamlError as exc:
        return None, ["The existing profile cannot be read (line %s: %s)." % (exc.line, exc.problem)]
    if not isinstance(raw, dict):
        return None, ["The existing profile is empty."]
    return raw, [problem.render() for problem in wdprofile.check(raw)]


def read_resume(path):
    """Plain text from a .txt, .md or .docx file. PDFs are read by the assistant directly."""
    full = os.path.expanduser(path)
    if not os.path.isfile(full):
        raise ValueError("No file at %s. Check the path, or paste the resume text instead." % path)
    lower = full.lower()
    if lower.endswith(".pdf"):
        raise ValueError("PDF: read this file directly with the Read tool instead of this command.")
    if lower.endswith(".docx"):
        try:
            with zipfile.ZipFile(full) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", "replace")
        except (zipfile.BadZipFile, KeyError):
            raise ValueError("That .docx file could not be opened. Save it as PDF or paste the text instead.")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
            text = text.replace(entity, char)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    if lower.endswith(".doc"):
        raise ValueError("Old .doc files cannot be read. Save it as .docx or PDF, or paste the text instead.")
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()


def check_vault(path, folder="workdecero", allow_new=False):
    """Return (ok, message) for a folder the person named as their vault."""
    target = os.path.expanduser(path or "")
    if not target.strip():
        return False, "No folder was given."
    if not os.path.isdir(target):
        if allow_new:
            return True, "%s does not exist yet. It will be created as a new vault folder." % _tilde(target)
        return False, ("%s does not exist. Check the path. To start a brand new vault there, say so and it "
                       "will be created." % _tilde(target))
    if not vault.looks_like_vault(target) and not allow_new:
        inside = [name for name in sorted(os.listdir(target))
                  if vault.looks_like_vault(os.path.join(target, name))][:5]
        hint = (" It contains %s: did you mean %s?" % (
            "this vault" if len(inside) == 1 else "these vaults", ", ".join(inside))) if inside else ""
        return False, ("%s is not an Obsidian vault (it has no .obsidian folder), so nothing will be put there.%s "
                       "Pick the vault folder itself." % (_tilde(target), hint))
    try:
        store = vault.VaultStore(target, folder) if os.path.isdir(target) else None
    except vault.VaultError as exc:
        return False, str(exc)
    exists = store is not None and os.path.isdir(store.root)
    return True, ("%s already has a %s folder. It will be kept and completed, and nothing in it is overwritten."
                  if exists else "%s is an Obsidian vault. A %s folder will be added, and nothing else is touched.") \
        % (_tilde(target), folder)


# --------------------------------------------------------------------------
# From a draft to a profile
# --------------------------------------------------------------------------

def _prune(value):
    """Drop empty settings so the file stays short. False and 0 are kept: they are answers."""
    if isinstance(value, dict):
        cleaned = dict((key, _prune(item)) for key, item in value.items())
        return dict((key, item) for key, item in cleaned.items() if item not in (None, "", [], {}))
    if isinstance(value, list):
        return [item for item in (_prune(entry) for entry in value) if item not in (None, "", [], {})]
    if isinstance(value, str):
        return value.strip()
    return value


def _ordered(value, schema):
    """Sections and settings in the order the schema lists them, so every profile reads the same way."""
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        ordered = {}
        for key in list(properties) + [key for key in value if key not in properties]:
            if key in value:
                ordered[key] = _ordered(value[key], properties.get(key, {}))
        return ordered
    if isinstance(value, list) and "items" in schema:
        return [_ordered(item, schema["items"]) for item in value]
    return value


def profile_from_draft(draft):
    """Return (profile, inferred paths, new_vault flag). Helper keys are split off and never saved."""
    if not isinstance(draft, dict):
        raise ValueError("The draft must be a JSON object with the profile sections.")
    from_resume = draft.get("_from_resume") if isinstance(draft.get("_from_resume"), dict) else {}
    inferred = [str(item) for item in draft.get("_inferred") or []] + [str(key) for key in from_resume]
    new_vault = bool(draft.get("_new_vault"))
    profile = dict((key, value) for key, value in draft.items() if key not in HELPER_KEYS)
    profile.setdefault("schema_version", 1)
    profile = _ordered(_prune(profile), wdprofile.load_schema())
    # Paths under the home folder are saved in their short form, which also reads better in the summary.
    for section, key in (("storage", "vault_path"), ("person", "resume_path"), ("person", "linkedin_connections_path")):
        value = profile.get(section, {}).get(key) if isinstance(profile.get(section), dict) else None
        if isinstance(value, str) and os.path.isabs(value):
            profile[section][key] = _tilde(value)
    return profile, inferred, new_vault


def profile_text(profile):
    header = ("# Your workdecero profile. Everything the sweeps know about what you want.\n"
              "# Change it with /workdecero:setup, or edit this file by hand.\n"
              "# Check it with: python3 engine/wdprofile.py validate\n\n")
    return header + miniyaml.dumps(profile)


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return "${:,.0f}".format(value) if float(value).is_integer() else "${:,.2f}".format(value)


def _join(items):
    return ", ".join(str(item) for item in items)


def summary_card(profile, inferred=()):
    """The plain-language summary a person checks before anything is written."""
    full = schema_check.apply_defaults(profile, wdprofile.load_schema())
    inferred = set(inferred)

    def mark(path):
        return " (from your resume)" if path in inferred else ""

    def get(*keys):
        node = full
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    lines = ["YOU"]
    person = full.get("person") or {}
    lines.append("  %s%s" % (person.get("name", "(name missing)"), mark("person.name")))
    if person.get("headline"):
        lines.append("  %s%s" % (person["headline"], mark("person.headline")))
    if person.get("years_experience") is not None:
        lines.append("  %s years of experience%s" % (person["years_experience"], mark("person.years_experience")))
    skills = full.get("skills") or {}
    if skills.get("core"):
        lines.append("  Strongest skills: %s%s" % (_join(skills["core"]), mark("skills.core")))
    if skills.get("certifications"):
        lines.append("  Certifications: %s%s" % (_join(skills["certifications"]), mark("skills.certifications")))
    if person.get("business_name"):
        lines.append("  Business: %s%s" % (person["business_name"], mark("person.business_name")))
    if person.get("resume_path"):
        lines.append("  Resume: %s (read each sweep, never changed)" % person["resume_path"])

    tracks = full.get("tracks") or {}
    names = {"jobs": "salaried jobs", "contracts": "contract work", "networking": "networking"}
    on = [names[key] for key in wdprofile.TRACKS if tracks.get(key)]
    off = [names[key] for key in wdprofile.TRACKS if not tracks.get(key)]
    lines += ["", "LOOKING FOR"]
    lines.append("  Sweeps on: %s%s" % (_join(on) or "none", " (off: %s)" % _join(off) if off else ""))
    jobs = full.get("jobs") or {}
    if tracks.get("jobs") and jobs:
        lines.append("  Job titles: %s%s" % (_join(jobs.get("titles") or []), mark("jobs.titles")))
        if jobs.get("levels"):
            lines.append("  Levels: %s%s" % (_join(jobs["levels"]), mark("jobs.levels")))
        if jobs.get("salary_floor") is not None:
            target = ", aiming for %s" % _money(jobs["salary_target"]) if jobs.get("salary_target") else ""
            lines.append("  Pay floor: %s a year%s. Postings that top out below it are dropped." %
                         (_money(jobs["salary_floor"]), target))
        if jobs.get("preferred_industries"):
            lines.append("  Preferred industries: %s%s" % (_join(jobs["preferred_industries"]),
                                                          mark("jobs.preferred_industries")))
        if jobs.get("exclude_title_keywords"):
            lines.append("  Skip titles containing: %s" % _join(jobs["exclude_title_keywords"]))
    contracts = full.get("contracts") or {}
    if tracks.get("contracts") and contracts:
        lines.append("  Contract services: %s%s" % (_join(contracts.get("services") or []), mark("contracts.services")))
        if contracts.get("rate_floor_hourly") is not None:
            target = ", aiming for %s" % _money(contracts["rate_target_hourly"]) \
                if contracts.get("rate_target_hourly") else ""
            lines.append("  Hourly floor: %s%s" % (_money(contracts["rate_floor_hourly"]), target))
        if contracts.get("engagement_types"):
            lines.append("  Ways of working: %s" % _join(contracts["engagement_types"]))
    networking = full.get("networking") or {}
    if tracks.get("networking") and networking:
        lines.append("  Networking, to meet: %s%s" % (_join(networking.get("who_to_meet") or []),
                                                      mark("networking.who_to_meet")))
        if networking.get("goals"):
            lines.append("  Networking goals: %s%s" % (_join(networking["goals"]), mark("networking.goals")))
        if networking.get("unavailable"):
            lines.append("  Cannot attend: %s" % _join(networking["unavailable"]))

    location = full.get("location") or {}
    lines += ["", "WHERE"]
    metro = " (%s area)" % location["metro"] if location.get("metro") else ""
    lines.append("  Home base: %s%s%s" % (location.get("home_base", "(missing)"), metro, mark("location.home_base")))
    modes = location.get("work_modes") or []
    reach = ""
    if any(mode in modes for mode in ("hybrid", "onsite")):
        reach = ", within %s miles for hybrid or onsite" % location.get("radius_miles")
    lines.append("  Work modes: %s%s" % (_join(modes) or "(missing)", reach))
    if location.get("other_areas"):
        lines.append("  Also fine: %s" % _join(location["other_areas"]))
    if location.get("open_to_relocation"):
        lines.append("  Relocation: yes%s" % (", to %s" % _join(location["relocation_targets"])
                                              if location.get("relocation_targets") else ""))
    else:
        lines.append("  Relocation: no")

    lines += ["", "LEAVE OUT AND KEEP QUIET"]
    employer = person.get("current_employer")
    quiet = jobs.get("search_visibility") == "quiet"
    if employer:
        lines.append("  Current employer: %s%s. Always left out, and never written in your notes."
                     % (employer, mark("person.current_employer")))
    lines.append("  Quiet search: %s" % ("yes. Nothing is suggested that could signal your search in public."
                                         if quiet else "no"))
    exclusions = full.get("exclusions") or []
    skipped = ["%s (%s%s)" % (item.get("name"), item.get("kind"),
                              ", until %s" % item["until"] if item.get("until") else "")
               for item in exclusions if item.get("action", "skip") == "skip"]
    flagged = ["%s (%s)" % (item.get("name"), item.get("kind")) for item in exclusions if item.get("action") == "flag"]
    lines.append("  Left out: %s" % (_join(skipped) or "nothing"))
    if flagged:
        lines.append("  Kept but flagged to check first: %s" % _join(flagged))
    confidential = (full.get("confidential") or {}).get("names") or []
    if confidential:
        lines.append("  Never named in your notes: %s" % _join(confidential))

    lines += ["", "TIME AND TOOLS"]
    mode = full.get("mode")
    lines.append("  Each digest shows up to %s new items. Mode: %s."
                 % (get("digest", "max_new_items"), "Lite (sized for a Pro plan)" if mode == "lite" else "Full"))
    browser = get("integrations", "browser") or {}
    lines.append("  Browser: %s. Signed-in LinkedIn: %s. Calendar: %s."
                 % ("on" if browser.get("enabled") else "off", "on" if browser.get("linkedin_signed_in") else "off",
                    get("integrations", "calendar")))

    storage = full.get("storage") or {}
    lines += ["", "TRACKER"]
    lines.append("  Obsidian vault: %s" % storage.get("vault_path", "(missing)"))
    lines.append("  Folder inside it: %s. Nothing outside that folder is written." % storage.get("vault_folder"))
    lines.append("  Spreadsheet copy after each sweep: %s" % ("on" if storage.get("spreadsheet_snapshot") else "off"))
    return "\n".join(lines)


def short_card(profile, inferred=()):
    """The summary a person checks before saving: about twenty lines. The full version is summary_card."""
    full = schema_check.apply_defaults(profile, wdprofile.load_schema())
    inferred = set(inferred)

    def mark(*paths):
        return " (from your resume)" if any(path in inferred for path in paths) else ""

    person, location = full.get("person") or {}, full.get("location") or {}
    tracks, jobs = full.get("tracks") or {}, full.get("jobs") or {}
    contracts, networking = full.get("contracts") or {}, full.get("networking") or {}
    lines = []

    def row(label, text):
        lines.append("%-12s %s" % (label, text))

    row("You", "%s. %s%s" % (person.get("name", "(name missing)"), person.get("headline", ""),
                             mark("person.name", "person.headline")))
    skills = (full.get("skills") or {}).get("core") or []
    if skills:
        row("", "Strongest in: %s%s" % (_join(skills[:5]), mark("skills.core")))
    names = {"jobs": "salaried jobs", "contracts": "contract work", "networking": "networking"}
    row("Sweeps", _join([names[key] for key in wdprofile.TRACKS if tracks.get(key)]) or "none")
    if tracks.get("jobs") and jobs:
        row("Jobs", "%s%s" % (_join((jobs.get("titles") or [])[:6]), mark("jobs.titles")))
        bits = []
        if jobs.get("levels"):
            bits.append("Levels: %s" % _join(jobs["levels"]))
        if jobs.get("salary_floor") is not None:
            bits.append("Pay floor: %s a year%s" % (_money(jobs["salary_floor"]),
                                                    ", aiming for %s" % _money(jobs["salary_target"])
                                                    if jobs.get("salary_target") else ""))
        if bits:
            row("", ". ".join(bits) + ".")
    if tracks.get("contracts") and contracts:
        row("Contracts", "%s%s" % (_join((contracts.get("services") or [])[:5]), mark("contracts.services")))
        bits = []
        if contracts.get("rate_floor_hourly") is not None:
            bits.append("Hourly floor: %s%s" % (_money(contracts["rate_floor_hourly"]),
                                                ", aiming for %s" % _money(contracts["rate_target_hourly"])
                                                if contracts.get("rate_target_hourly") else ""))
        if contracts.get("engagement_types"):
            bits.append("Ways of working: %s" % _join(contracts["engagement_types"]))
        if bits:
            row("", ". ".join(bits) + ".")
    if tracks.get("networking") and networking:
        row("Networking", "Meet: %s%s" % (_join((networking.get("who_to_meet") or [])[:4]),
                                          mark("networking.who_to_meet")))
    modes = location.get("work_modes") or []
    reach = ", within %s miles" % location.get("radius_miles") if any(m in modes for m in ("hybrid", "onsite")) else ""
    row("Where", "%s%s. %s%s. Relocation: %s." % (
        location.get("home_base", "(missing)"), mark("location.home_base"), _join(modes) or "(work modes missing)",
        reach, "yes" if location.get("open_to_relocation") else "no"))
    employer = person.get("current_employer")
    quiet = jobs.get("search_visibility") == "quiet"
    if employer:
        row("Keep quiet", "Current employer %s is always left out and never written in your notes." % employer)
    row("" if employer else "Keep quiet", "Quiet search: %s." % ("yes" if quiet else "no"))
    exclusions = full.get("exclusions") or []
    left = ["%s%s" % (item.get("name"), " (until %s)" % item["until"] if item.get("until") else "")
            for item in exclusions if item.get("action", "skip") == "skip"]
    flagged = [str(item.get("name")) for item in exclusions if item.get("action") == "flag"]
    row("Leave out", (_join(left) or "nothing") + (". Flag first: %s" % _join(flagged) if flagged else "") + ".")
    confidential = (full.get("confidential") or {}).get("names") or []
    if confidential:
        row("", "Never named in your notes: %s." % _join(confidential))
    mode = full.get("mode")
    row("Each sweep", "Up to %s new items in the digest. %s mode." % (
        (full.get("digest") or {}).get("max_new_items"), "Lite" if mode == "lite" else "Full"))
    row("", "Browser, signed-in LinkedIn, calendar and spreadsheet copy: off unless you turned them on.")
    storage = full.get("storage") or {}
    row("Tracker", "%s, in a folder called %s. Nothing outside that folder is written." % (
        storage.get("vault_path", "(vault missing)"), storage.get("vault_folder")))
    return "\n".join(lines)


def review(draft):
    """Check a draft. Returns a dict with the summary, blocking problems and questions to ask."""
    profile, inferred, new_vault = profile_from_draft(draft)
    problems = wdprofile.check(profile)
    full = schema_check.apply_defaults(profile, wdprofile.load_schema())
    confirmed = set(str(item) for item in draft.get("_confirmed") or [])
    questions = [] if problems else [item for item in wdprofile.warnings(full) if item.path not in confirmed]
    vault_ok, vault_message = True, ""
    storage = profile.get("storage") or {}
    if isinstance(storage, dict) and storage.get("vault_path") and not any(p.path.startswith("storage") for p in problems):
        vault_ok, vault_message = check_vault(storage["vault_path"], storage.get("vault_folder") or "workdecero",
                                              allow_new=new_vault)
    from_resume = draft.get("_from_resume") if isinstance(draft.get("_from_resume"), dict) else {}
    dropped_ok = set(str(item) for item in draft.get("_dropped") or [])
    missing = []
    for dotted, value in from_resume.items():
        node = profile
        for key in str(dotted).split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if node in (None, "", [], {}) and value not in (None, "", [], {}) and dotted not in dropped_ok:
            missing.append((str(dotted), value))
    return {"profile": profile, "inferred": inferred, "new_vault": new_vault, "problems": problems,
            "questions": questions, "vault_ok": vault_ok, "vault_message": vault_message, "missing": missing,
            "dropped": dropped_ok, "resume_read": bool(from_resume)}


def _blocked(result):
    return bool(result["problems"] or result["questions"] or result.get("missing") or not result["vault_ok"])


def _print_review(result, show_file=False, out=None, details=False):
    out = out or sys.stdout
    card = summary_card if details else short_card
    print(card(result["profile"], result["inferred"]), file=out)
    print("", file=out)
    if result["problems"]:
        print("TO FIX BEFORE THIS CAN BE SAVED (%d)" % len(result["problems"]), file=out)
        for problem in result["problems"]:
            for line in problem.render().splitlines():
                print("  " + line, file=out)
    if not result["vault_ok"]:
        print("VAULT PROBLEM", file=out)
        print("  " + result["vault_message"], file=out)
    elif result["vault_message"]:
        print("VAULT CHECK: " + result["vault_message"], file=out)
    if result["questions"]:
        print("QUESTIONS TO ASK BEFORE SAVING (%d)" % len(result["questions"]), file=out)
        for question in result["questions"]:
            print("  %s %s" % (question.path, question.message), file=out)
    if result.get("missing"):
        print("READ FROM THE RESUME BUT NO LONGER IN THE DRAFT (%d)" % len(result["missing"]), file=out)
        for dotted, value in result["missing"]:
            shown = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
            print("  %s: %s" % (dotted, shown if len(shown) <= 90 else shown[:87] + "..."), file=out)
        print("  Put each one back in the draft, or ask the person whether to leave it out and then list its "
              "name under \"_dropped\".", file=out)
    unset = [dotted for dotted in RESUME_EXTRAS
             if draft_has_resume(result) and dotted not in result.get("dropped", ()) and not _get_path(result["profile"], dotted)]
    if unset:
        print("NOT SET, LOOK AT THE RESUME ONCE MORE: %s" % ", ".join(unset), file=out)
        print("  If the resume shows one of these, send it now with \"from_resume\": true. If it does not, carry on. "
              "Do not ask the person about them.", file=out)
    if _blocked(result):
        pass
    else:
        print("READY TO SAVE. Nothing has been saved yet.", file=out)
    if show_file:
        print("\n----- profile file as it would be written -----", file=out)
        print(profile_text(result["profile"]), file=out)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _write_atomic(path, text):
    """The profile holds pay floors and the current employer, so only its owner may read it."""
    wdprofile.secure_home(os.path.dirname(path))
    wdprofile.write_private(path, text)


def obsidian_link(vault_path, folder):
    """An obsidian:// link to the Start here note, or None when Obsidian does not know the vault yet."""
    real = os.path.realpath(os.path.expanduser(vault_path))
    known = [os.path.realpath(item["path"]) for item in known_vaults()]
    if real not in known:
        return None
    note = os.path.join(real, *(folder.split("/") + ["Start here.md"]))
    return "obsidian://open?path=" + urllib.parse.quote(note, safe="")


def write(draft, out=None):
    """Validate the draft and, only if everything is fine, write the profile and set up the vault folder."""
    out = out or sys.stdout
    result = review(draft)
    if _blocked(result):
        _print_review(result, out=out)
        if result["questions"] and not result["problems"] and result["vault_ok"]:
            print("\nAsk the person each question above. If they keep a value as it is, add its name to "
                  "\"_confirmed\" in the draft, preview again and then save.", file=out)
        print("\nNothing was saved.", file=out)
        return 1
    shown = None
    if os.path.isfile(_shown_path()):
        with open(_shown_path(), "r", encoding="utf-8") as handle:
            shown = handle.read().strip()
    if shown != _checksum(draft):
        print("The draft has changed since the person last saw the summary (or no summary was shown yet). "
              "Run the preview command, show them the summary, and save only after they agree to it.", file=out)
        print("\nNothing was saved.", file=out)
        return 1
    profile = result["profile"]
    storage = profile["storage"]
    folder = storage.get("vault_folder") or "workdecero"
    target = os.path.expanduser(storage["vault_path"])
    home = wdprofile.config_home()
    backups = os.path.join(home, "backups")

    # Everything that can refuse has been checked. From here on, files are written.
    path = wdprofile.profile_path()
    text = profile_text(profile)
    changed = True
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            previous = handle.read()
        changed = previous != text
        if changed:
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            wdprofile.private_folder(backups)
            wdprofile.write_private(os.path.join(backups, "profile-%s.yaml" % stamp), previous)
    if changed:
        _write_atomic(path, text)
    else:
        wdprofile.secure_home(home)
        os.chmod(path, 0o600)                # a profile written by an older version was readable by others
    if not os.path.isdir(target):
        os.makedirs(target)
    store = vault.VaultStore(target, folder, backup_dir=backups)
    created = store.ensure_layout()
    store.scan()

    link = obsidian_link(target, folder)
    name = os.path.basename(os.path.realpath(target))
    on = [key for key in wdprofile.TRACKS if profile.get("tracks", {}).get(key)]
    # Name a sweep's command only if this version of the plugin has that skill, so the screen is never wrong.
    skills = os.path.join(_ROOT, "skills")
    sweeps = [("jobs", "job-sweep", "run the job sweep"), ("contracts", "contract-sweep", "run the contract sweep"),
              ("networking", "networking-sweep", "run the networking sweep")]
    ready = [(skill, words) for key, skill, words in sweeps
             if key in on and os.path.isfile(os.path.join(skills, skill, "SKILL.md"))]
    has_sources = os.path.isfile(os.path.join(skills, "sources", "SKILL.md"))
    mode = "Lite" if (profile.get("mode") or "lite") == "lite" else "Full"
    print("workdecero is set up.", file=out)
    print("", file=out)
    print("  Profile      %s   (your answers; %s)" % (_tilde(path), "saved" if changed else "unchanged"), file=out)
    print("  Tracker      %s / %s / Start here   (%s)" % (
        name, folder, "%d files added" % len(created) if created else "already in place, nothing overwritten"), file=out)
    if has_sources:
        print("  Local map    not built yet. Run /workdecero:sources (about 3 minutes) to find job boards,", file=out)
        print("               employers and groups for %s." % (profile.get("location", {}).get("home_base") or "your area"),
              file=out)
    print("", file=out)
    if ready:
        skill, words = ready[0]
        print("Next:   run /workdecero:%s. The first one takes a few minutes in %s mode." % (skill, mode), file=out)
        print("        Or just say \"%s\"%s. Claude Code asks once to allow it." % (
            words, " or \"build my local map\"" if has_sources else ""), file=out)
    else:
        print("Next:   the sweeps you turned on are not in this version of workdecero yet. They arrive with an "
              "update, and your profile is ready for them.", file=out)
    if link:
        print("Open:   %s   (opens Start here in Obsidian)" % link, file=out)
    else:
        print("Open:   in Obsidian, choose \"Open folder as vault\", pick %s, then open %s/Start here."
              % (_tilde(target), folder), file=out)
    print("Change anything later:   /workdecero:setup", file=out)
    print(PRIVACY, file=out)
    for line in store.skipped_report():
        print(line, file=out)
    delete_draft()          # the answers now live in the profile. The temporary copy goes
    return 0


def import_connections(out=None):
    """Add the LinkedIn connections file named in the draft to the People notes, then delete the draft."""
    out = out or sys.stdout
    draft = read_draft()
    path = str(draft.get("_connections_path") or "").strip()
    include_emails = draft.get("_include_emails") is True
    try:
        profile, problems = wdprofile.load()
    except OSError:
        profile, problems = None, []
    if profile is None or problems:
        print("Finish setup first: there is no saved profile yet.", file=out)
        return 1
    full = os.path.expanduser(path)
    if not path or not os.path.isfile(full):
        print("No file at %s. In LinkedIn: Settings, Data privacy, Get a copy of your data, Connections. "
              "The download contains Connections.csv." % (path or "(no path given)"), file=out)
        return 1
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    store = vault.VaultStore.from_profile(profile, wdprofile.config_home())
    try:
        plan = warmpath.plan_network_import(text, store, profile, include_emails=include_emails)
    except ValueError as exc:
        print(str(exc), file=out)
        return 1
    store.apply(plan.operations)
    print("Connections imported: %d added, %d already known and refreshed." % (plan.added, plan.updated), file=out)
    if plan.skipped_confidential:
        print("%d left out because they work at an employer you keep confidential." % plan.skipped_confidential,
              file=out)
    if plan.skipped_unnamed:
        print("%d rows had no name in LinkedIn's file and were skipped." % plan.skipped_unnamed, file=out)
    print("Email addresses: %s." % ("included, as you asked" if include_emails else "not included"), file=out)
    delete_draft()
    return 0


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _open_run(out, seed_from_profile=True):
    """Start a run, prepare the private draft file and describe what the assistant has to work with."""
    existing, problems = load_existing()
    run = start_run()
    seeded = False
    if existing is not None and not problems and seed_from_profile:
        seed = dict(existing)
        seed["_run"] = run
        prepare_draft(json.dumps(seed, indent=1, ensure_ascii=False) + "\n")
        seeded = True
    else:
        prepare_draft(json.dumps({"_run": run}) + "\n")
    _lock_down()
    print("RUN ID: %s" % run, file=out)
    print("  Put \"_run\": \"%s\" in every answers file you write. One without it is refused." % run, file=out)
    print("ANSWERS FILE: %s" % answers_path(), file=out)
    print("  Write each step's answers there, a few lines at a time. The helper reads it and deletes it.", file=out)
    print("", file=out)
    if problems:
        print("SAVED PROFILE: %s has problems:" % _tilde(wdprofile.profile_path()), file=out)
        for line in problems:
            print("  " + line.replace("\n", "\n  "), file=out)
    elif existing is not None:
        print("SAVED PROFILE: yes, at %s. This is a change, not a first setup." % _tilde(wdprofile.profile_path()),
              file=out)
        if seeded:
            print("  The saved profile is loaded as the starting point. Send only what the person wants changed.",
                  file=out)
        print("", file=out)
        print(short_card(existing), file=out)
    else:
        print("SAVED PROFILE: none. This is a first setup.", file=out)
    print("", file=out)
    vaults = known_vaults()
    if vaults:
        print("OBSIDIAN VAULTS ON THIS COMPUTER:", file=out)
        for item in vaults:
            print("  %s   (%s)" % (item["name"], item["shown"]), file=out)
    else:
        print("OBSIDIAN VAULTS ON THIS COMPUTER: none found. Ask for the vault folder, or offer to create one.",
              file=out)
    print("TODAY: %s" % datetime.date.today().isoformat(), file=out)
    return run


def status(out=None):
    """Open a setup run. Never deletes another run's recent work without being told to."""
    out = out or sys.stdout
    state, age_hours, old = draft_state()
    idle = minutes_since_activity()
    owner = current_run()
    if owner is not None and idle is not None and idle < RUN_ACTIVE_MINUTES:
        print("ANOTHER SETUP IS IN PROGRESS (run %s, last activity %d minute%s ago)." % (
            owner["run"], int(idle), "" if int(idle) == 1 else "s"), file=out)
        print("  If %s is the run id you were given earlier in this same conversation, carry on with it." % owner["run"],
              file=out)
        print("  Otherwise tell the person that setup is already running in another session on this computer, "
              "or was cut off there. They choose: leave it alone, pick it up here (the pick-up command keeps "
              "every answer so far), or discard it (the takeover command).", file=out)
        return 1
    note = None
    if state == "resumable":
        # Left by a session that ended without saving. Offer it once, then it is this run's draft.
        name = ((old or {}).get("person") or {}).get("name") or "someone"
        hours = int(age_hours)
        note = ("UNFINISHED SETUP FOUND: started for %s, last touched %s ago. Ask the person whether to pick it up "
                "or start over. To pick it up, run the preview command to see what was answered so far and carry "
                "on from there. To start over, run the cancel command and then status again."
                % (name, "%d hour%s" % (hours, "" if hours == 1 else "s") if hours else "under an hour"))
        run = start_run()
        kept = dict(old)
        kept["_run"] = run
        prepare_draft(json.dumps(kept, indent=1, ensure_ascii=False) + "\n")
        if os.path.isfile(_shown_path()):
            os.remove(_shown_path())
        print("RUN ID: %s" % run, file=out)
        print("  Put \"_run\": \"%s\" in every answers file you write. One without it is refused." % run, file=out)
        print("ANSWERS FILE: %s" % answers_path(), file=out)
        print("", file=out)
        print(note, file=out)
        print("", file=out)
        vaults = known_vaults()
        print("OBSIDIAN VAULTS ON THIS COMPUTER: %s" % (
            "; ".join("%s (%s)" % (item["name"], item["shown"]) for item in vaults) or "none found"), file=out)
        print("TODAY: %s" % datetime.date.today().isoformat(), file=out)
        return 0
    if state in ("stale", "unreadable"):
        delete_draft()
        print("An unfinished setup draft from an earlier session was too old to use and has been deleted.", file=out)
        print("", file=out)
    elif state == "empty" or owner is not None:
        delete_draft()
    _open_run(out)
    return 0


def pick_up(out=None):
    """Carry on here with a setup that another session left behind, keeping its answers.

    For a session that crashed or was closed. If that session is in fact still
    running, its next answers file is refused, exactly as after a takeover.
    """
    out = out or sys.stdout
    state, _, old = draft_state()
    if state != "resumable" or not isinstance(old, dict):
        print("There are no answers to pick up. Starting fresh.", file=out)
        print("", file=out)
        delete_draft()
        _open_run(out)
        return 0
    run = start_run()
    kept = dict(old)
    kept["_run"] = run
    prepare_draft(json.dumps(kept, indent=1, ensure_ascii=False) + "\n")
    if os.path.isfile(_shown_path()):
        os.remove(_shown_path())          # the person must see the summary again before anything is saved
    _lock_down()
    print("PICKED UP. Every answer given so far is kept.", file=out)
    print("RUN ID: %s" % run, file=out)
    print("  Put \"_run\": \"%s\" in every answers file you write. One without it is refused." % run, file=out)
    print("ANSWERS FILE: %s" % answers_path(), file=out)
    print("", file=out)
    print("Run the preview command to see what was answered, tell the person where they left off, and carry on "
          "from the first step that is not answered.", file=out)
    print("", file=out)
    vaults = known_vaults()
    print("OBSIDIAN VAULTS ON THIS COMPUTER: %s" % (
        "; ".join("%s (%s)" % (item["name"], item["shown"]) for item in vaults) or "none found"), file=out)
    print("TODAY: %s" % datetime.date.today().isoformat(), file=out)
    return 0


def takeover(out=None):
    """Discard another session's unfinished setup, at the person's request, and start fresh."""
    out = out or sys.stdout
    delete_draft()
    print("The other setup was discarded. Starting fresh.", file=out)
    print("", file=out)
    _open_run(out)
    return 0


def cancel(out=None):
    out = out or sys.stdout
    removed = delete_draft()
    print("Setup cancelled. %s Nothing was saved, and your profile and vault are as they were."
          % ("The draft was deleted." if removed else "There was no draft."), file=out)
    return 0


def fields(out=None):
    """Every setting, its description and its choices, so a draft uses the right names."""
    out = out or sys.stdout
    def walk(node, path, depth):
        for name, child in node.get("properties", {}).items():
            here = "%s.%s" % (path, name) if path else name
            kind = child.get("type")
            extra = ""
            if "enum" in child:
                extra = " One of: %s." % ", ".join(str(item) for item in child["enum"])
            elif kind == "array" and "enum" in child.get("items", {}):
                extra = " A list from: %s." % ", ".join(child["items"]["enum"])
            elif kind == "array":
                extra = " A list."
            elif kind in ("integer", "number"):
                extra = " A number."
            elif kind == "boolean":
                extra = " true or false."
            required = " REQUIRED." if name in node.get("required", []) else ""
            default = " Default: %s." % json.dumps(child["default"]) if "default" in child else ""
            print("%s%s: %s%s%s%s" % ("  " * depth, here, child.get("description", ""), extra, required, default), file=out)
            if kind == "object":
                walk(child, here, depth + 1)
            elif kind == "array" and child.get("items", {}).get("type") == "object":
                walk(child["items"], here + "[]", depth + 1)

    walk(wdprofile.load_schema(), "", 0)
    return 0


def _mark_shown(draft):
    """Remember exactly which draft the person was shown, so only that draft can be saved."""
    handle = os.open(_shown_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(_checksum(draft))


def preview(out=None, details=False, show_file=False):
    out = out or sys.stdout
    draft = read_draft()
    result = review(draft)
    _print_review(result, show_file=show_file, out=out, details=details)
    if _blocked(result):
        if os.path.isfile(_shown_path()):
            os.remove(_shown_path())
        return 1
    _mark_shown(draft)
    return 0


COMMANDS = ("status", "takeover", "pick-up", "fields", "save", "read-resume", "check-vault", "preview", "preview-details",
            "preview-file", "write", "cancel", "import-connections")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if len(argv) != 1 or command not in COMMANDS:
        print(__doc__, file=sys.stderr)
        if len(argv) > 1:
            print("This helper takes no arguments. Put paths and answers in the draft file.", file=sys.stderr)
        return 2
    try:
        if command == "status":
            return status()
        if command == "pick-up":
            return pick_up()
        if command == "takeover":
            return takeover()
        if command == "fields":
            return fields()
        if command == "cancel":
            return cancel()
        if command == "save":
            read_draft()
            names = read_draft.changed
            print("Saved: %s." % ", ".join(names) if names else "Nothing new to save.")
            return 0
        if command == "read-resume":
            path = str(read_draft().get("_resume_path") or "").strip()
            if not path:
                raise ValueError("Put the resume file's path in the answers file as \"_resume_path\" first.")
            print(read_resume(path))
            return 0
        if command == "check-vault":
            draft = read_draft()
            storage = draft.get("storage") if isinstance(draft.get("storage"), dict) else {}
            ok, message = check_vault(storage.get("vault_path"), storage.get("vault_folder") or "workdecero",
                                      allow_new=draft.get("_new_vault") is True)
            print(("OK: " if ok else "REFUSED: ") + message)
            return 0 if ok else 1
        if command == "preview":
            return preview()
        if command == "preview-details":
            return preview(details=True)
        if command == "preview-file":
            return preview(show_file=True)
        if command == "write":
            return write(read_draft())
        if command == "import-connections":
            return import_connections()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
