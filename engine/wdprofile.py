#!/usr/bin/env python3
"""Load and check a workdecero profile.

The profile is one YAML file that holds everything personal: what you are
looking for, where, what to leave out, and where your trackers live. It
stays on your machine, outside the plugin, so updates and uninstalls never
touch it.

    Default location:  ~/.config/workdecero/profile.yaml
    Override folder:   set WORKDECERO_HOME to another folder

Command line:
    wdprofile.py validate [PATH]   check a profile and explain any problems
    wdprofile.py path            print where the profile is expected

Exit codes: 0 valid (warnings may still be printed), 1 problems found,
2 file missing or unreadable.
Standard library only, Python 3.9+.
"""

import datetime
import json
import os
import stat
import sys

import miniyaml
import schema as schema_check
from schema import Problem

HOME_ENV = "WORKDECERO_HOME"
DEFAULT_HOME = os.path.join("~", ".config", "workdecero")
PROFILE_NAME = "profile.yaml"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.schema.json")
TRACKS = ("jobs", "contracts", "networking")


def config_home():
    """The folder that holds the profile and the local source map."""
    return os.path.normpath(os.path.expanduser(os.environ.get(HOME_ENV) or DEFAULT_HOME))


def secure_home(folder=None):
    """Create the settings folder if needed and close it to everyone but its owner.

    A folder this creates is always owner-only. One that already exists is
    tightened only when it is the default location, because a person who
    pointed WORKDECERO_HOME at a folder of their own chose its permissions.
    """
    folder = folder or config_home()
    default = os.path.normpath(os.path.expanduser(DEFAULT_HOME))
    if not os.path.isdir(folder):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    elif os.path.normpath(folder) == default and not os.path.islink(folder):
        os.chmod(folder, 0o700)
    return folder


def private_folder(path):
    """A folder inside the settings folder (backups), owner-only."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.islink(path):
        os.chmod(path, 0o700)
    return path


def write_private(path, text):
    """Write a settings file so that only its owner can read it, replacing any old version in one step."""
    temp = path + ".tmp"
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


FLAGS_NAME = "flags.json"


def flags_path():
    return os.path.join(config_home(), FLAGS_NAME)


def write_flags(profile):
    """Keep flags.json in step with the profile. Returns True when browser use is on.

    The plugin hook must know one thing about the profile: whether the person
    turned browser use on. It reads this small file so that it never has to
    read the profile itself. The file holds that one yes or no and nothing else.
    """
    browser = (((profile or {}).get("integrations") or {}).get("browser") or {}).get("enabled") is True
    text = json.dumps({"browser_enabled": browser}) + "\n"
    path = flags_path()
    try:
        info = os.lstat(path)
        same = stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        if same:
            with open(path, "r", encoding="utf-8") as handle:
                same = handle.read() == text
        if same:
            return browser
        if not stat.S_ISREG(info.st_mode):
            os.remove(path)                        # a link put there is not followed: it is replaced
    except OSError:
        pass
    write_private(path, text)
    return browser


def profile_path():
    return os.path.join(config_home(), PROFILE_NAME)


def load_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _section_example(schema, name):
    """A minimal correct section, built from the examples of its required settings."""
    section = schema["properties"][name]
    sample = {}
    for key in section.get("required", []):
        examples = section["properties"][key].get("examples")
        if examples:
            sample[key] = examples[0]
    return miniyaml.dumps({name: sample}).rstrip("\n") if sample else None


def cross_checks(profile, schema):
    """Rules that involve more than one setting. Assumes each setting has the right type."""
    problems = []

    def get(*keys):
        node = profile
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    def is_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    tracks = get("tracks") if isinstance(get("tracks"), dict) else {}
    if tracks and not any(tracks.get(name) is True for name in TRACKS):
        problems.append(Problem("tracks", "every sweep is switched off. Turn on at least one.",
                                "tracks:\n  jobs: true\n  contracts: false\n  networking: false"))
    for name in TRACKS:
        if tracks.get(name) is True and get(name) is None:
            problems.append(Problem(name, "tracks.%s is true, so the %s section is required but missing." % (name, name),
                                    _section_example(schema, name)))

    pairs = (
        ("jobs", "salary_floor", "salary_target", "salary_target: 115000"),
        ("contracts", "rate_floor_hourly", "rate_target_hourly", "rate_target_hourly: 110"),
        ("contracts", "hours_per_week_min", "hours_per_week_max", "hours_per_week_max: 30"),
    )
    for section, low_key, high_key, example in pairs:
        low, high = get(section, low_key), get(section, high_key)
        if is_number(low) and is_number(high) and high < low:
            problems.append(Problem("%s.%s" % (section, high_key),
                                    "is %s, which is lower than %s (%s). It must be the same or higher."
                                    % (high, low_key, low), example))

    if get("integrations", "browser", "linkedin_signed_in") is True:
        if get("integrations", "browser", "enabled") is not True:
            problems.append(Problem("integrations.browser.enabled",
                                    "must be true when linkedin_signed_in is true, because signed-in LinkedIn "
                                    "searches run through your browser.", "enabled: true"))
        if get("integrations", "browser", "linkedin_risk_acknowledged") is not True:
            problems.append(Problem("integrations.browser.linkedin_risk_acknowledged",
                                    "must be true before signed-in LinkedIn searches are allowed. Automated "
                                    "activity can get a LinkedIn account restricted. Set it to true only if "
                                    "you accept that risk, or set linkedin_signed_in to false.",
                                    "linkedin_risk_acknowledged: true"))

    folder = get("storage", "vault_folder")
    if isinstance(folder, str) and folder.strip():
        parts = folder.replace("\\", "/").split("/")
        if folder.startswith(("/", "~", "\\")) or ".." in parts or (len(folder) > 1 and folder[1] == ":"):
            problems.append(Problem("storage.vault_folder",
                                    "must be a folder name inside your vault, not a full path. You wrote \"%s\"."
                                    % folder, "vault_folder: workdecero"))

    engagement = get("contracts", "engagement_types")
    if isinstance(engagement, list) and "corp-to-corp" in engagement and get("contracts", "has_business_entity") is not True:
        problems.append(Problem("contracts.has_business_entity",
                                "must be true when engagement_types includes corp-to-corp, because corp-to-corp "
                                "work is invoiced through your own company. Set it to true, or remove corp-to-corp.",
                                "has_business_entity: true"))

    if get("location", "open_to_relocation") is not True and get("location", "relocation_targets"):
        problems.append(Problem("location.relocation_targets",
                                "lists places, but open_to_relocation is not true. Set open_to_relocation to true "
                                "or remove the list.", "open_to_relocation: true"))
    return problems


# Characters that word processors and web pages swap in, and their plain forms.
_PASTED = {
    0x00A0: " ", 0x202F: " ", 0x2007: " ", 0x2009: " ", 0x200B: "", 0xFEFF: "",
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x2033: '"',
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x2032: "'",
}


def normalize_pasted(text):
    """Swap curly quotes and special spaces for plain ones.

    A profile is typed by hand or pasted from a document, where these look
    identical to the plain characters but would not be read the same way.
    """
    return text.translate(_PASTED)


def _guess_message(value, guess):
    return "Did you mean %s?" % guess if guess != value else "Check the number."


def warnings(profile, today=None):
    """Things that are allowed but look like mistakes. They never block a sweep."""
    found = []
    if not isinstance(profile, dict):
        return found
    today = today or datetime.date.today()

    def number(section, key):
        value = (profile.get(section) or {}).get(key) if isinstance(profile.get(section), dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    for key in ("salary_floor", "salary_target"):
        value = number("jobs", key)
        if value is None or value == 0:
            continue
        path = "jobs.%s" % key
        if value < 1000:
            found.append(Problem(path, "is %s, which is very low for a yearly salary. Write the full amount. "
                                 "Did you mean %d?" % (value, value * 1000), "%s: %d" % (key, value * 1000)))
        elif value < 25000:
            found.append(Problem(path, "is %s, which is low for a yearly salary. Is a digit missing? "
                                 "Did you mean %d?" % (value, value * 10), "%s: %d" % (key, value * 10)))
        elif value > 5000000:
            found.append(Problem(path, "is %s, which is very high for a yearly salary. Is there an extra digit? "
                                 "Did you mean %d?" % (value, value // 10), "%s: %d" % (key, value // 10)))

    for key in ("rate_floor_hourly", "rate_target_hourly"):
        value = number("contracts", key)
        if value is None or value == 0:
            continue
        path = "contracts.%s" % key
        if value >= 2000:
            hourly = int(round(value / 2080.0))
            slipped = int(round(value / 1000.0))
            # Two honest readings: extra zeros on an hourly rate, or a yearly amount in an hourly setting.
            found.append(Problem(path, "is %s, which is far too high for an hourly rate. Did you mean %d an hour? "
                                 "Or, if that was a yearly amount, it is about %d an hour."
                                 % (value, slipped, hourly), "%s: %d" % (key, slipped)))
        elif value < 5:
            found.append(Problem(path, "is %s, which is very low for an hourly rate. Check the number." % value, None))

    exclusions = profile.get("exclusions")
    if isinstance(exclusions, list):
        for index, item in enumerate(exclusions, start=1):
            until = item.get("until") if isinstance(item, dict) else None
            if isinstance(until, str):
                try:
                    ended = datetime.date(*[int(part) for part in until.split("-")])
                except (ValueError, TypeError):
                    continue
                if ended < today:
                    found.append(Problem("exclusions[%d].until" % index,
                                         "is %s, which has passed, so this exclusion no longer applies. "
                                         "Remove it or set a later date." % until, None))
    return found


def effective_exclusions(profile):
    """The exclusions list plus the current employer, which is always skipped."""
    items = list(profile.get("exclusions") or [])
    employer = (profile.get("person") or {}).get("current_employer")
    if employer and not any(item.get("kind") == "employer" and item.get("name", "").lower() == employer.lower()
                            for item in items):
        items.append({"name": employer, "kind": "employer", "action": "skip", "reason": "Current employer"})
    return items


def confidential_names(profile):
    """Every name that must never be written into a note, digest or draft."""
    names = list((profile.get("confidential") or {}).get("names") or [])
    employer = (profile.get("person") or {}).get("current_employer")
    extra = [employer] if employer else []
    # An exclusion marked confidential is listed once and covers both jobs: left out, and never named.
    extra += [item.get("name") for item in profile.get("exclusions") or []
              if isinstance(item, dict) and item.get("confidential") is True and item.get("name")]
    for name in extra:
        if name.lower() not in [known.lower() for known in names]:
            names.append(name)
    return names


def check(profile, schema=None):
    """Return a list of Problem for an already parsed profile."""
    schema = schema or load_schema()
    if not isinstance(profile, dict):
        return [Problem("", "the profile must be a set of 'section:' blocks, but the file holds something else.",
                        "schema_version: 1")]
    return schema_check.validate(profile, schema) + cross_checks(profile, schema)


def load(path=None):
    """Read a profile file.

    Returns (profile, problems). `profile` has defaults filled in and is None
    when the file cannot be read as YAML. `problems` is empty when all is well.
    Raises OSError if the file does not exist or cannot be opened.
    """
    own = path is None                             # the person's own profile, not an example being checked
    path = path or profile_path()
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        raw = miniyaml.loads(normalize_pasted(text))
    except miniyaml.YamlError as exc:
        where = "line %d" % exc.line if exc.line else "the file"
        return None, [Problem(where, "this line cannot be read: %s." % exc.problem, None)]
    if raw is None:
        return None, [Problem("", "the profile file is empty. Run setup to create one.", None)]
    schema = load_schema()
    problems = check(raw, schema)
    profile = schema_check.apply_defaults(raw, schema) if isinstance(raw, dict) else None
    if own and profile is not None and not problems:
        try:
            write_flags(profile)                   # every helper reads the profile first, so the flag never goes stale
        except OSError:
            pass
    return profile, problems


def render_problems(problems, path, kind="problem"):
    count = len(problems)
    if kind == "problem":
        lines = ["%s has %d problem%s to fix:" % (path, count, "" if count == 1 else "s"), ""]
    else:
        lines = ["%d thing%s to double-check (the profile still works):" % (count, "" if count == 1 else "s"), ""]
    for number, problem in enumerate(problems, start=1):
        rendered = problem.render().splitlines()
        lines.append("%d. %s" % (number, rendered[0]))
        lines.extend("   " + line for line in rendered[1:])
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "path":
        print(profile_path())
        return 0
    if not argv or argv[0] != "validate" or len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = argv[1] if len(argv) == 2 else profile_path()
    shown = path
    home = os.path.expanduser("~")
    if shown.startswith(home + os.sep):
        shown = "~" + shown[len(home):]
    try:
        profile, problems = load(path)
    except OSError:
        print("No profile found at %s. Run /workdecero:setup to create one." % shown, file=sys.stderr)
        return 2
    if problems:
        print(render_problems(problems, shown), file=sys.stderr)
        return 1
    on = [name for name in TRACKS if profile["tracks"].get(name)]
    print("%s is valid. Sweeps on: %s. Mode: %s." % (shown, ", ".join(on), profile["mode"]))
    notes = warnings(profile)
    if notes:
        print("\n" + render_problems(notes, shown, kind="warning"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
