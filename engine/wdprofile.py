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

Exit codes: 0 valid, 1 problems found, 2 file missing or unreadable.
Standard library only, Python 3.9+.
"""

import json
import os
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
    return os.path.expanduser(os.environ.get(HOME_ENV) or DEFAULT_HOME)


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
    path = path or profile_path()
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        raw = miniyaml.loads(text)
    except miniyaml.YamlError as exc:
        where = "line %d" % exc.line if exc.line else "the file"
        return None, [Problem(where, "this line cannot be read: %s." % exc.problem, None)]
    if raw is None:
        return None, [Problem("", "the profile file is empty. Run setup to create one.", None)]
    schema = load_schema()
    problems = check(raw, schema)
    profile = schema_check.apply_defaults(raw, schema) if isinstance(raw, dict) else None
    return profile, problems


def render_problems(problems, path):
    count = len(problems)
    lines = ["%s has %d problem%s to fix:" % (path, count, "" if count == 1 else "s"), ""]
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
