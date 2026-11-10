#!/usr/bin/env python3
"""The vault store: read and write tracker notes in an Obsidian vault.

Every job, contract, channel, event, group, person, company and digest is one
Markdown note. This module is the only code that touches those notes.

Promises it keeps:
  * It writes only inside one folder of the vault (storage.vault_folder).
    It never writes to .obsidian or anywhere else.
  * A note's body belongs to the user. So do any properties the user added.
    A write changes only the wd_ properties a sweep owns and the one block
    between the workdecero markers. The one addition to a body is the
    person's own text from a tracker they asked to import, added once under
    a heading and never rewritten.
  * A property marked SEED or USER in the model is written when the note is
    created and then left alone, so a status changed in Obsidian stays changed.
  * Every write is atomic (write a temp file, then rename), and the previous
    version of the note is copied to a backup folder outside the vault first.
  * A note whose properties this module cannot read is left untouched and
    reported, never overwritten.

Command line:
    vault.py init --vault PATH [--folder NAME]    create the folder, views and Start here note

Standard library only, Python 3.9+.
"""

import datetime
import os
import re
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "engine"))
sys.path.insert(0, _HERE)

import miniyaml  # noqa: E402
import model  # noqa: E402

BACKUPS_KEPT = 30
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_LONG_DASHES = re.compile("\\s*" + chr(0x2014) + "\\s*")
_EN_DASH = chr(0x2013)


class VaultError(Exception):
    """A problem the user can act on: a bad path, or a note that cannot be read safely."""


class Row(object):
    """One tracker note as the engine sees it."""

    def __init__(self, note_type, path, title, values, extra, body, section):
        self.type = note_type.name
        self.path = path          # relative to the vault, with forward slashes
        self.title = title        # the file name without .md
        self.values = values      # model properties, keyed without the wd_ prefix
        self.extra = extra        # every other property, exactly as the user wrote it
        self.body = body          # the whole note body
        self.section = section    # text between the workdecero markers, or None

    @property
    def id(self):
        return self.values.get("id")

    def __repr__(self):
        return "Row(%s %s %r)" % (self.type, self.id, self.title)


def scrub_dashes(value):
    """Replace long dashes in text a sweep wrote. Lists are scrubbed item by item."""
    if isinstance(value, str):
        return _LONG_DASHES.sub(", ", value).replace(_EN_DASH, "-")
    if isinstance(value, list):
        return [scrub_dashes(item) for item in value]
    return value


def safe_file_name(title):
    """A file name Obsidian accepts on every operating system, close to the title."""
    name = _BAD_NAME_CHARS.sub(" ", scrub_dashes(title))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120].rstrip(" .") or "Untitled"


def split_section(body):
    """Return (before, section_text, after). section_text is None when the markers are absent."""
    start = body.find(model.SECTION_START)
    if start == -1:
        return body, None, ""
    end = body.find(model.SECTION_END, start)
    if end == -1:
        return body, None, ""
    inner = body[start + len(model.SECTION_START):end]
    return body[:start], inner.strip("\n"), body[end + len(model.SECTION_END):]


def replace_section(body, text):
    """Put `text` between the markers, adding the block at the end if the note has none."""
    block = "%s\n%s\n%s" % (model.SECTION_START, text.strip("\n"), model.SECTION_END)
    before, current, after = split_section(body)
    if current is None:
        if not body.strip():
            return block + "\n"
        return body.rstrip("\n") + "\n\n" + block + "\n"
    return before + block + after


def split_parts(section):
    """Split a block's text into (summary, {heading: body}). Headings are lines that start with '### '."""
    summary, parts, current = [], {}, None
    for line in (section or "").split("\n"):
        if line.startswith("### "):
            current = line[4:].strip()
            parts.setdefault(current, [])
        elif current is None:
            summary.append(line)
        else:
            parts[current].append(line)
    return "\n".join(summary).strip("\n"), dict((name, "\n".join(body).strip("\n")) for name, body in parts.items())


def join_parts(summary, parts):
    """The inverse of split_parts. Known parts come in model order, then any others, with the log last."""
    names = [name for name in model.PART_ORDER if name in parts and name != model.PART_LOG]
    names += [name for name in parts if name not in model.PART_ORDER]
    if model.PART_LOG in parts:
        names.append(model.PART_LOG)
    chunks = [summary] if summary else []
    for name in names:
        if parts[name]:
            chunks.append("### %s\n\n%s" % (name, parts[name]))
    return "\n\n".join(chunks)


class VaultStore(object):
    def __init__(self, vault_path, vault_folder="workdecero", backup_dir=None, scrub=True, today=None):
        vault = os.path.realpath(os.path.expanduser(vault_path))
        if not os.path.isdir(vault):
            raise VaultError("The vault folder %s does not exist." % vault_path)
        given = (vault_folder or "").replace("\\", "/")
        if given.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", given):
            raise VaultError("The folder must be a name inside the vault, such as workdecero, not a full path. "
                             "You gave %s." % vault_folder)
        folder = given.strip("/")
        parts = folder.split("/")
        if not folder or any(part in ("", ".", "..") or part.startswith(".") for part in parts):
            raise VaultError("The folder must be a plain folder name inside the vault, such as workdecero. "
                             "You gave %r." % vault_folder)
        self.vault = vault
        self.folder = folder
        self.root = os.path.join(vault, *parts)
        self._check_inside(self.root)
        self.backup_dir = os.path.expanduser(backup_dir) if backup_dir else None
        self.scrub = scrub
        self.today = today or datetime.date.today()
        self.skipped = []   # (path, reason) for notes that could not be read
        self.changed = []   # relative paths written during this run
        self._stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    @classmethod
    def from_profile(cls, profile, config_home, **kwargs):
        storage = profile["storage"]
        return cls(storage["vault_path"], storage.get("vault_folder", "workdecero"),
                   backup_dir=os.path.join(config_home, "backups"), **kwargs)

    # ---- paths -----------------------------------------------------------

    def _check_inside(self, path):
        real = os.path.realpath(path)
        if real != self.vault and not real.startswith(self.vault + os.sep):
            raise VaultError("Refusing to touch %s: it is outside the vault." % path)
        if hasattr(self, "root"):
            root = os.path.realpath(self.root)
            if real != root and not real.startswith(root + os.sep):
                raise VaultError("Refusing to touch %s: it is outside the %s folder." % (path, self.folder))
        relative = os.path.relpath(real, self.vault)
        if any(part.startswith(".") for part in relative.split(os.sep) if part not in (".",)):
            raise VaultError("Refusing to touch %s: hidden folders such as .obsidian are off limits." % path)
        return real

    def relative(self, path):
        return os.path.relpath(path, self.vault).replace(os.sep, "/")

    def type_dir(self, note_type):
        return os.path.join(self.root, note_type.folder)

    def link(self, type_name, title):
        """A wikilink to a tracker note, by full path so it never lands on a user's note of the same name."""
        note_type = model.BY_NAME[type_name]
        name = safe_file_name(title)
        return "[[%s/%s/%s|%s]]" % (self.folder, note_type.folder, name, name)

    # ---- low-level file work ---------------------------------------------

    def _backup(self, path):
        if not self.backup_dir or not os.path.isfile(path):
            return
        target = os.path.join(self.backup_dir, self._stamp, os.path.relpath(path, self.vault))
        for folder in (self.backup_dir, os.path.join(self.backup_dir, self._stamp)):
            os.makedirs(folder, mode=0o700, exist_ok=True)       # copies of your notes: owner-only
            os.chmod(folder, 0o700)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)

    def prune_backups(self):
        if not self.backup_dir or not os.path.isdir(self.backup_dir):
            return
        runs = sorted(name for name in os.listdir(self.backup_dir)
                      if os.path.isdir(os.path.join(self.backup_dir, name)))
        for name in runs[:-BACKUPS_KEPT]:
            shutil.rmtree(os.path.join(self.backup_dir, name), ignore_errors=True)

    def _write_text(self, path, text):
        """Write atomically. Returns True when the file changed."""
        self._check_inside(path)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as handle:
                if handle.read() == text:
                    return False
            self._backup(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._check_inside(os.path.dirname(path))
        handle, temp = tempfile.mkstemp(prefix=".wd-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise
        self.changed.append(self.relative(path))
        return True

    # ---- reading ---------------------------------------------------------

    def _read_note(self, note_type, path):
        with open(path, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        properties, body = miniyaml.split_front_matter(text)
        values, extra = {}, {}
        for key, value in properties.items():
            name = key[len(model.PREFIX):] if key.startswith(model.PREFIX) else None
            if name is not None and note_type.has_field(name):
                values[name] = value
            else:
                extra[key] = value
        _, section, _ = split_section(body)
        title = os.path.splitext(os.path.basename(path))[0]
        return Row(note_type, self.relative(path), title, values, extra, body, section), properties

    def _note_files(self, note_type):
        folder = self.type_dir(note_type)
        if not os.path.isdir(folder):
            return []
        return sorted(os.path.join(folder, name) for name in os.listdir(folder)
                      if name.endswith(".md") and not name.startswith("."))

    def rows(self, type_name):
        """Every readable note of one type. Unreadable notes are listed in self.skipped."""
        note_type = model.BY_NAME[type_name]
        found = []
        for path in self._note_files(note_type):
            try:
                row, _ = self._read_note(note_type, path)
            except (miniyaml.YamlError, UnicodeDecodeError) as exc:
                self._skip(path, exc)
                continue
            if row.values.get("type") == note_type.name and row.id:
                found.append(row)
        return found

    def get(self, type_name, note_id):
        for row in self.rows(type_name):
            if row.id == note_id:
                return row
        return None

    def scan(self):
        """Read every tracker note once. Returns the number read; unreadable ones are in self.skipped."""
        return sum(len(self.rows(note_type.name)) for note_type in model.TYPES)

    def skipped_report(self):
        """Plain-language lines about notes that were left untouched, or an empty list."""
        if not self.skipped:
            return []
        count = len(self.skipped)
        lines = ["%d note%s could not be read and %s left untouched:"
                 % (count, "" if count == 1 else "s", "was" if count == 1 else "were")]
        for path, reason in self.skipped:
            lines.append("  %s: %s" % (path, reason))
        lines.append("Fix the properties block at the top of each note (the part between the --- lines), "
                     "then run again.")
        return lines

    def _skip(self, path, exc):
        entry = (self.relative(path), "its properties could not be read (%s). It was left untouched." % exc)
        if entry not in self.skipped:
            self.skipped.append(entry)

    def _find(self, note_type, note_id):
        """Return (path, properties, body) of the note with this id, or None."""
        for path in self._note_files(note_type):
            try:
                row, properties = self._read_note(note_type, path)
            except (miniyaml.YamlError, UnicodeDecodeError) as exc:
                self._skip(path, exc)
                continue
            if row.id == note_id and row.values.get("type") == note_type.name:
                return path, properties, row.body
        return None

    # ---- writing ---------------------------------------------------------

    def _new_path(self, note_type, title):
        folder = self.type_dir(note_type)
        base = safe_file_name(title)
        candidate = os.path.join(folder, base + ".md")
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(folder, "%s (%d).md" % (base, counter))
            counter += 1
        return candidate

    def upsert(self, type_name, note_id, title, values, section=None, force=()):
        """Create the note with this id, or update it. Returns its Row.

        `values` holds model properties without the wd_ prefix. On an existing
        note, SEED and USER properties are written only if named in `force`
        (for a change the user asked for, or a posting that has closed).
        `section` replaces the text between the markers; None leaves it alone.
        """
        note_type = model.BY_NAME[type_name]
        unknown = [name for name in values if not note_type.has_field(name)]
        if unknown:
            raise KeyError("%s notes have no property %s" % (type_name, ", ".join(sorted(unknown))))
        if self.scrub:
            values = dict((name, scrub_dashes(value)) for name, value in values.items())
            section = scrub_dashes(section) if section is not None else None

        found = self._find(note_type, note_id)
        if found is None:
            for path, _ in self.skipped:
                if path.startswith(self.relative(self.type_dir(note_type)) + "/"):
                    # An unreadable note might be this one. Do not create a duplicate beside it.
                    full = os.path.join(self.vault, path)
                    with open(full, "r", encoding="utf-8", errors="replace") as handle:
                        if note_id in handle.read(2000):
                            raise VaultError("%s has properties that cannot be read, so it was left untouched. "
                                             "Fix the properties block at the top of that note." % path)
            path = self._new_path(note_type, title)
            properties, body, creating = {}, "", True
        else:
            path, properties, body = found
            creating = False

        merged = dict(properties) if not creating else {}
        merged[model.PREFIX + "id"] = note_id
        merged[model.PREFIX + "type"] = note_type.name
        for field in note_type.fields:
            if field.name in ("id", "type"):
                continue
            given = field.name in values
            if creating:
                if given:
                    merged[field.key] = values[field.name]
                elif field.name == "status":
                    merged[field.key] = note_type.default_status()
                elif field.seed_empty:
                    merged[field.key] = field.default
            elif given and (field.owner == model.SWEEP or field.name in force or field.key not in merged):
                merged[field.key] = values[field.name]
        merged = _in_model_order(merged, note_type, keep=list(properties) if not creating else [])

        if section is not None:
            body = replace_section(body, section)
        elif creating:
            body = ""
        self._write_text(path, miniyaml.join_front_matter(merged, body))
        row, _ = self._read_note(note_type, path)
        return row

    def append(self, type_name, note_id, line):
        """Add one line to the end of a note's workdecero block."""
        row = self.get(type_name, note_id)
        if row is None:
            raise KeyError("no %s note with id %s" % (type_name, note_id))
        text = (row.section + "\n" if row.section else "") + line
        return self.upsert(type_name, note_id, row.title, {}, section=text)

    def apply(self, operations):
        """Carry out operations planned by the engine (see liveness.plan_update). Returns the rows written."""
        written = []
        for operation in operations:
            if operation.get("op") == "create":
                written.append(self.upsert(operation["type"], operation["id"], operation["title"],
                                           operation.get("values", {}), section=operation.get("section")))
                continue
            row = self.get(operation["type"], operation["id"])
            if row is None:
                continue
            section = None
            summary, parts = split_parts(row.section)
            if operation.get("section"):
                summary = operation["section"]          # a refresh replaces the fit summary only
            if operation.get("part"):
                heading, body = operation["part"]
                parts[heading] = body
            if operation.get("append"):
                # Dated lines from checks collect in the log, which stays at the end of the block.
                earlier = parts.get(model.PART_LOG, "")
                parts[model.PART_LOG] = (earlier + "\n" if earlier else "") + "- " + operation["append"]
            if operation.get("section") or operation.get("part") or operation.get("append"):
                section = join_parts(summary, parts)
            written.append(self.upsert(operation["type"], operation["id"], row.title, operation.get("values", {}),
                                       section=section, force=operation.get("force", ())))
        return written

    def refresh_rollups(self):
        """Recount open roles and known people on every company note."""
        company_type = model.BY_NAME["company"]
        open_counts, people_counts = {}, {}
        for type_name in ("job", "contract"):
            note_type = model.BY_NAME[type_name]
            for row in self.rows(type_name):
                target = _link_target(row.values.get("company"))
                if target and not note_type.is_done(row.values.get("status")):
                    open_counts[target] = open_counts.get(target, 0) + 1
        for row in self.rows("person"):
            target = _link_target(row.values.get("company"))
            if target:
                people_counts[target] = people_counts.get(target, 0) + 1
        for row in self.rows("company"):
            key = "%s/%s/%s" % (self.folder, company_type.folder, row.title)
            self.upsert("company", row.id, row.title,
                        {"open_roles": open_counts.get(key, 0), "people_known": people_counts.get(key, 0)})

    # ---- layout ----------------------------------------------------------

    def ensure_layout(self, refresh_views=False):
        """Create the folders, the view files and the Start here note.

        Existing view files and the Start here note are kept as they are, since
        Obsidian and the user both edit them. Pass refresh_views=True to
        replace the view files with the current versions (a backup is kept).
        Returns the list of files created or replaced.
        """
        import bases  # local import: bases needs this module's folder on the path

        self._check_inside(self.root)
        os.makedirs(self.root, exist_ok=True)
        for note_type in model.TYPES:
            os.makedirs(self.type_dir(note_type), exist_ok=True)
        before = len(self.changed)
        for name, content in bases.build_all(self.folder).items():
            path = os.path.join(self.root, name)
            if refresh_views or not os.path.exists(path):
                self._write_text(path, miniyaml.dumps(content))
        start = os.path.join(self.root, bases.START_HERE_NAME)
        if not os.path.exists(start):
            self._write_text(start, bases.start_here(self.folder))
        self.prune_backups()
        return self.changed[before:]


def _in_model_order(merged, note_type, keep):
    """Order a note's properties.

    A new note lists the model's properties in model order. On an update, every
    property the note already had stays where it was (the user may have arranged
    them), and each newly added model property is slotted in before the first
    existing one that comes later in the model, so "Applied on" lands with the
    other tracking properties and not after the IDs.
    """
    rank = dict((field.key, index) for index, field in enumerate(note_type.fields))
    order = [key for key in keep if key in merged]
    added = sorted((key for key in merged if key not in order and key in rank), key=lambda key: rank[key])
    for key in added:
        position = len(order)
        for index, existing in enumerate(order):
            if existing in rank and rank[existing] > rank[key]:
                position = index
                break
        order.insert(position, key)
    order += [key for key in merged if key not in order]
    return dict((key, merged[key]) for key in order)


def looks_like_vault(path):
    """True when the folder has been opened as an Obsidian vault before."""
    return os.path.isdir(os.path.join(os.path.expanduser(path), ".obsidian"))


def _link_target(value):
    """The path inside [[path|label]], or None."""
    if not isinstance(value, str):
        return None
    match = re.match(r"^\[\[([^\]|#]+)", value.strip())
    return match.group(1).strip() if match else None


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Set up the workdecero folder in an Obsidian vault.")
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser("init", help="create the folder, the views and the Start here note")
    init.add_argument("--vault", required=True, help="path to the Obsidian vault")
    init.add_argument("--folder", default="workdecero", help="folder inside the vault (default: workdecero)")
    init.add_argument("--refresh-views", action="store_true", help="replace existing view files with current ones")
    init.add_argument("--new-vault", action="store_true",
                      help="allow a folder that Obsidian has not opened yet (it has no .obsidian folder)")
    args = parser.parse_args(argv)
    if args.command != "init":
        parser.print_help(sys.stderr)
        return 2
    target = os.path.expanduser(args.vault)
    try:
        if not os.path.isdir(target):
            if not args.new_vault:
                raise VaultError("The vault folder %s does not exist. Check the path, or add --new-vault to "
                                 "create it." % args.vault)
            os.makedirs(target)
        elif not looks_like_vault(target) and not args.new_vault:
            # The wrong-folder guard: a typo in the path must not scatter tracker files into some other folder.
            raise VaultError("%s does not look like an Obsidian vault (it has no .obsidian folder). Check the path. "
                             "If this really is a new vault that Obsidian has not opened yet, add --new-vault."
                             % args.vault)
        import wdprofile
        store = VaultStore(target, args.folder, backup_dir=os.path.join(wdprofile.config_home(), "backups"))
        created = store.ensure_layout(refresh_views=args.refresh_views)
        read = store.scan()
    except VaultError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("workdecero folder ready at %s (%d file(s) written, %d tracker note(s) found)."
          % (store.relative(store.root), len(created), read))
    for line in store.skipped_report():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
