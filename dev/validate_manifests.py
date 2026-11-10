#!/usr/bin/env python3
"""Validate the plugin and marketplace manifests and every skill, without Claude Code.

Checks the rules that break an install when they are wrong:
  * .claude-plugin/plugin.json: name (kebab-case), version (MAJOR.MINOR.PATCH)
  * .claude-plugin/marketplace.json: name, owner.name, a plugin entry that
    points at this plugin with a source path that exists
  * skills/<dir>/SKILL.md: front matter with name and description, and a
    name that matches its folder

Usage: validate_manifests.py [REPO_ROOT]
Exit codes: 0 valid, 1 problems found. Standard library only (3.9+).
"""

import json
import os
import re
import sys

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_front_matter(text):
    """Return the top-level 'key: value' pairs of a SKILL.md front matter block, or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if line[:1] in (" ", "\t", "#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip("\"'")
    return None


def _load_json(path, problems):
    if not os.path.isfile(path):
        problems.append("%s is missing" % path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        problems.append("%s is not valid JSON: %s" % (path, exc))
        return None


def validate(root):
    problems = []
    rel = lambda *parts: os.path.join(root, *parts)

    plugin = _load_json(rel(".claude-plugin", "plugin.json"), problems)
    plugin_name = None
    if isinstance(plugin, dict):
        plugin_name = plugin.get("name")
        if not isinstance(plugin_name, str) or not NAME_RE.match(plugin_name):
            problems.append("plugin.json: name must be kebab-case")
        if not isinstance(plugin.get("version"), str) or not VERSION_RE.match(plugin["version"]):
            problems.append("plugin.json: version must be MAJOR.MINOR.PATCH")
        if not plugin.get("description"):
            problems.append("plugin.json: description is empty")

    market = _load_json(rel(".claude-plugin", "marketplace.json"), problems)
    if isinstance(market, dict):
        if not isinstance(market.get("name"), str) or not NAME_RE.match(market["name"]):
            problems.append("marketplace.json: name must be kebab-case")
        if not (isinstance(market.get("owner"), dict) and market["owner"].get("name")):
            problems.append("marketplace.json: owner.name is required")
        entries = market.get("plugins")
        if not isinstance(entries, list) or not entries:
            problems.append("marketplace.json: plugins must list at least one plugin")
        else:
            names = [entry.get("name") for entry in entries if isinstance(entry, dict)]
            if plugin_name and plugin_name not in names:
                problems.append("marketplace.json: no entry named %r" % plugin_name)
            for entry in entries:
                source = entry.get("source") if isinstance(entry, dict) else None
                if not source:
                    problems.append("marketplace.json: an entry has no source")
                elif isinstance(source, str) and not os.path.isdir(rel(source)):
                    problems.append("marketplace.json: source %r is not a folder" % source)

    skills_dir = rel("skills")
    if os.path.isdir(skills_dir):
        for folder in sorted(os.listdir(skills_dir)):
            if not os.path.isdir(os.path.join(skills_dir, folder)):
                continue
            real_files = [name for _, _, names in os.walk(os.path.join(skills_dir, folder)) for name in names
                          if not name.endswith(".pyc")]
            if not real_files:
                continue      # an empty folder, or compiled leftovers from another branch
            skill_path = os.path.join(skills_dir, folder, "SKILL.md")
            label = "skills/%s/SKILL.md" % folder
            if not os.path.isfile(skill_path):
                problems.append("%s is missing" % label)
                continue
            with open(skill_path, "r", encoding="utf-8") as handle:
                fields = read_front_matter(handle.read())
            if fields is None:
                problems.append("%s: no front matter block" % label)
                continue
            if fields.get("name") != folder:
                problems.append("%s: name must equal the folder name %r" % (label, folder))
            if not fields.get("description"):
                problems.append("%s: description is empty" % label)
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else "."
    problems = validate(root)
    if problems:
        print("manifest check: %d problem(s):" % len(problems), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    print("manifest check: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
