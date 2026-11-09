#!/usr/bin/env python3
"""The helper behind /workdecero:setup.

The setup conversation gathers answers as a draft, held in the conversation
and never on disk. This script turns the draft into things a person can
check, and does every write, so nothing is written by hand and nothing is
written before the person says yes.

A draft is JSON on standard input: the profile itself (the same sections as
profile.yaml), plus two optional helper keys that are never saved:

    "_inferred": ["person.name", "jobs.titles", ...]   settings read from the resume
    "_new_vault": true                                  the person agreed to create a new vault folder
    "_confirmed": ["jobs.salary_floor"]                 unusual values the person was asked about and kept

Commands:
    status                     what exists already: profile, known Obsidian vaults (reads only)
    fields                     every profile setting with its description, for drafting
    read-resume PATH           the text of a .txt, .md or .docx resume (reads only)
    check-vault PATH [FOLDER]  whether a folder can hold the tracker (reads only)
    preview                    draft in, plain-language summary and open questions out (writes nothing)
    preview --file             the same, plus the profile file as it would be written
    write                      draft in. Validates, then writes the profile and sets up the vault folder
    import-connections PATH [--include-emails]
                               add LinkedIn's Connections.csv to the People notes

Exit codes: 0 fine, 1 the draft has problems to fix first, 2 the command could not run.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import re
import sys
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
HELPER_KEYS = ("_inferred", "_new_vault", "_confirmed")
PRIVACY = "Nothing was applied for, sent or posted. Your resume was read, not copied."


# --------------------------------------------------------------------------
# Reading what is already there
# --------------------------------------------------------------------------

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
    inferred = [str(item) for item in draft.get("_inferred") or []]
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
    return {"profile": profile, "inferred": inferred, "new_vault": new_vault, "problems": problems,
            "questions": questions, "vault_ok": vault_ok, "vault_message": vault_message}


def _print_review(result, show_file=False, out=None):
    out = out or sys.stdout
    print(summary_card(result["profile"], result["inferred"]), file=out)
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
    if not (result["problems"] or result["questions"]) and result["vault_ok"]:
        print("READY TO SAVE. Nothing has been written yet.", file=out)
    if show_file:
        print("\n----- profile file as it would be written -----", file=out)
        print(profile_text(result["profile"]), file=out)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


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
    if result["problems"] or not result["vault_ok"] or result["questions"]:
        _print_review(result, out=out)
        if result["questions"] and not result["problems"] and result["vault_ok"]:
            print("\nAsk the person each question above. If they keep a value as it is, add its name to "
                  "\"_confirmed\" in the draft and save again.", file=out)
        print("\nNothing was written.", file=out)
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
            os.makedirs(backups, exist_ok=True)
            with open(os.path.join(backups, "profile-%s.yaml" % stamp), "w", encoding="utf-8") as handle:
                handle.write(previous)
    if changed:
        _write_atomic(path, text)
    if not os.path.isdir(target):
        os.makedirs(target)
    store = vault.VaultStore(target, folder, backup_dir=backups)
    created = store.ensure_layout()
    store.scan()

    link = obsidian_link(target, folder)
    name = os.path.basename(os.path.realpath(target))
    on = [key for key in wdprofile.TRACKS if profile.get("tracks", {}).get(key)]
    first = {"jobs": "run the job sweep", "contracts": "run the contract sweep",
             "networking": "run the networking sweep"}[on[0]]
    mode = "Lite" if (profile.get("mode") or "lite") == "lite" else "Full"
    print("workdecero is set up.", file=out)
    print("", file=out)
    print("  Profile      %s   (your answers; %s)" % (_tilde(path), "saved" if changed else "unchanged"), file=out)
    print("  Tracker      %s / %s / Start here   (%s)" % (
        name, folder, "%d files added" % len(created) if created else "already in place, nothing overwritten"), file=out)
    print("  Local map    not built yet. It will be offered after your first sweep.", file=out)
    print("", file=out)
    print("Next:   say \"%s\". The first one takes about 10 minutes in %s mode." % (first, mode), file=out)
    if link:
        print("Open:   %s   (opens Start here in Obsidian)" % link, file=out)
    else:
        print("Open:   in Obsidian, choose \"Open folder as vault\", pick %s, then open %s/Start here."
              % (_tilde(target), folder), file=out)
    print("Change anything later:   /workdecero:setup", file=out)
    print(PRIVACY, file=out)
    for line in store.skipped_report():
        print(line, file=out)
    return 0


def import_connections(path, include_emails=False, out=None):
    out = out or sys.stdout
    try:
        profile, problems = wdprofile.load()
    except OSError:
        profile, problems = None, []
    if profile is None or problems:
        print("Finish setup first: there is no saved profile yet.", file=out)
        return 1
    full = os.path.expanduser(path)
    if not os.path.isfile(full):
        print("No file at %s. In LinkedIn: Settings, Data privacy, Get a copy of your data, Connections. "
              "The download contains Connections.csv." % path, file=out)
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
    return 0


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def status(out=None):
    out = out or sys.stdout
    existing, problems = load_existing()
    report = {
        "profile_path": _tilde(wdprofile.profile_path()),
        "profile_exists": existing is not None or bool(problems),
        "profile_problems": problems,
        "profile": existing,
        "summary": summary_card(existing) if existing and not problems else None,
        "known_vaults": known_vaults(),
        "today": datetime.date.today().isoformat(),
    }
    print(json.dumps(report, indent=1), file=out)
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


def _read_draft():
    text = sys.stdin.read()
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("The draft is not valid JSON (%s). Send the draft as one JSON object." % exc)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    try:
        if command == "status":
            return status()
        if command == "fields":
            return fields()
        if command == "read-resume" and len(argv) == 2:
            print(read_resume(argv[1]))
            return 0
        if command == "check-vault" and len(argv) in (2, 3):
            ok, message = check_vault(argv[1], argv[2] if len(argv) == 3 else "workdecero")
            print(("OK: " if ok else "REFUSED: ") + message)
            return 0 if ok else 1
        if command == "preview":
            result = review(_read_draft())
            _print_review(result, show_file="--file" in argv)
            return 1 if (result["problems"] or not result["vault_ok"] or result["questions"]) else 0
        if command == "write":
            return write(_read_draft())
        if command == "import-connections" and len(argv) >= 2:
            return import_connections(argv[1], include_emails="--include-emails" in argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
