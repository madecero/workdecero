import datetime
import hashlib
import os
import tempfile
import unittest

import _paths
import bases
import miniyaml
import model
import vault

import sys
sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "dev"))
import make_demo_vault  # noqa: E402

TODAY = datetime.date(2026, 11, 9)
EM, EN = chr(0x2014), chr(0x2013)


def tree_hashes(root, skip=None):
    """{relative path: sha1} for every file under root, leaving out one subfolder."""
    out = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root)
            if skip and (relative == skip or relative.startswith(skip + os.sep)):
                continue
            with open(path, "rb") as handle:
                out[relative] = hashlib.sha1(handle.read()).hexdigest()
    return out


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault_dir = os.path.join(self.tmp.name, "vault")
        self.backups = os.path.join(self.tmp.name, "backups")
        os.makedirs(os.path.join(self.vault_dir, ".obsidian"))
        with open(os.path.join(self.vault_dir, ".obsidian", "app.json"), "w") as handle:
            handle.write("{}")
        with open(os.path.join(self.vault_dir, "My note.md"), "w") as handle:
            handle.write("---\nstatus: reading\ntype: book\ntags:\n  - personal\ncompany: Penguin\n---\nMine.\n")
        self.store = self.new_store()

    def new_store(self, **kwargs):
        return vault.VaultStore(self.vault_dir, "workdecero", backup_dir=self.backups, today=TODAY, **kwargs)

    def note_path(self, row):
        return os.path.join(self.vault_dir, row.path)

    def read(self, row):
        with open(self.note_path(row), encoding="utf-8") as handle:
            return handle.read()

    def write(self, row, text):
        with open(self.note_path(row), "w", encoding="utf-8") as handle:
            handle.write(text)

    def make_job(self, **extra):
        values = {"role": "Head of Product", "fit": 80, "pay_max": 240000, "first_seen": "2026-11-05",
                  "posted": "2026-11-04"}
        values.update(extra)
        return self.store.upsert("job", "J-1", "Head of Product, Mile High Analytics", values,
                                 section="Why it fits: level and focus match.")


class RoundTrip(VaultCase):
    def test_create_gives_typed_properties_and_a_marked_block(self):
        row = self.make_job()
        text = self.read(row)
        properties, body = miniyaml.split_front_matter(text)
        self.assertEqual(properties["wd_id"], "J-1")
        self.assertEqual(properties["wd_type"], "job")
        self.assertEqual(properties["wd_status"], "New")
        self.assertEqual(properties["wd_fit"], 80)
        self.assertIsInstance(properties["wd_pay_max"], int)
        self.assertIn("wd_first_seen: 2026-11-05\n", text)       # a bare date, which Obsidian reads as a date
        self.assertIn("wd_next_step:\n", text)                    # ready to fill in
        self.assertEqual(body, "%s\nWhy it fits: level and focus match.\n%s\n"
                         % (model.SECTION_START, model.SECTION_END))
        self.assertEqual(row.path, "workdecero/Jobs/Head of Product, Mile High Analytics.md")

    def test_update_keeps_the_users_body_properties_and_choices(self):
        row = self.make_job()
        properties, body = miniyaml.split_front_matter(self.read(row))
        # The user works on the note in Obsidian.
        properties["wd_status"] = "Applied"
        properties["wd_next_step"] = "Call Jonas"
        properties["wd_applied_on"] = "2026-11-08"
        properties["status"] = "my own status"
        properties["tags"] = ["dream-job", "ai"]
        properties["company"] = "my own company field"
        properties["rating"] = 4.5
        properties["nested"] = {"keeps": ["working", 1, True]}
        mine_above = "# My thoughts\n\nGreat team %s met them in 2019.\n\n- [ ] ask about equity\n\n" % EM
        mine_below = "\n\n## Interview notes\n\nPanel went well.\n"
        self.write(row, miniyaml.join_front_matter(properties, mine_above + body.rstrip("\n") + mine_below))

        updated = self.store.upsert(
            "job", "J-1", "ignored on update",
            {"fit": 91, "status": "New", "next_step": "sweep must not set this", "last_seen": "2026-11-09",
             "first_seen": "2026-11-09"},
            section="Why it fits: even better now.\n\nCaveats: none.")

        after_properties, after_body = miniyaml.split_front_matter(self.read(updated))
        self.assertEqual(after_properties["wd_fit"], 91)                    # sweep-owned: rewritten
        self.assertEqual(after_properties["wd_last_seen"], "2026-11-09")    # sweep-owned: added
        self.assertEqual(after_properties["wd_status"], "Applied")          # the user's change stands
        self.assertEqual(after_properties["wd_next_step"], "Call Jonas")
        self.assertEqual(after_properties["wd_applied_on"], "2026-11-08")
        self.assertEqual(after_properties["wd_first_seen"], "2026-11-05")   # seeded once
        for key in ("status", "tags", "company", "rating", "nested"):
            self.assertEqual(after_properties[key], properties[key], key)
        self.assertTrue(after_body.startswith(mine_above))                   # byte for byte, long dash included
        self.assertTrue(after_body.endswith(mine_below))
        self.assertIn("even better now", after_body)
        self.assertNotIn("level and focus match", after_body)
        self.assertEqual(after_body.count(model.SECTION_START), 1)
        self.assertEqual(updated.extra["tags"], ["dream-job", "ai"])
        self.assertEqual(updated.values["status"], "Applied")

    def test_property_order_is_kept_on_update(self):
        row = self.make_job()
        before = [line.split(":")[0] for line in self.read(row).split("---")[1].strip().splitlines()
                  if not line.startswith(" ")]
        self.store.upsert("job", "J-1", "", {"fit": 50, "closes": "2026-12-01"})
        after = [line.split(":")[0] for line in self.read(row).split("---")[1].strip().splitlines()
                 if not line.startswith(" ")]
        self.assertEqual(after, before + ["wd_closes"])

    def test_force_changes_a_user_owned_property(self):
        self.make_job()
        row = self.store.upsert("job", "J-1", "", {"status": "Closed", "reason": "Posting closed"},
                                force=["status", "reason"])
        self.assertEqual(row.values["status"], "Closed")
        self.assertEqual(row.values["reason"], "Posting closed")

    def test_append_adds_a_line_and_keeps_the_rest(self):
        row = self.make_job()
        self.write(row, self.read(row) + "\nMy own line.\n")
        row = self.store.append("job", "J-1", "2026-11-09: posting still live.")
        self.assertEqual(row.section, "Why it fits: level and focus match.\n2026-11-09: posting still live.")
        self.assertTrue(row.body.endswith("\nMy own line.\n"))

    def test_block_is_added_at_the_end_when_the_user_removed_the_markers(self):
        row = self.make_job()
        properties, _ = miniyaml.split_front_matter(self.read(row))
        self.write(row, miniyaml.join_front_matter(properties, "Only my text now.\n"))
        row = self.store.upsert("job", "J-1", "", {}, section="Fresh block.")
        self.assertEqual(row.body, "Only my text now.\n\n%s\nFresh block.\n%s\n"
                         % (model.SECTION_START, model.SECTION_END))

    def test_unchanged_note_is_not_rewritten(self):
        row = self.make_job()
        stamp = os.stat(self.note_path(row)).st_mtime_ns
        store = self.new_store()
        store.upsert("job", "J-1", "", {"fit": 80}, section="Why it fits: level and focus match.")
        self.assertEqual(store.changed, [])
        self.assertEqual(os.stat(self.note_path(row)).st_mtime_ns, stamp)

    def test_note_is_found_by_id_after_the_user_renames_it(self):
        row = self.make_job()
        renamed = os.path.join(os.path.dirname(self.note_path(row)), "My favourite role.md")
        os.rename(self.note_path(row), renamed)
        again = self.store.upsert("job", "J-1", "Head of Product, Mile High Analytics", {"fit": 99})
        self.assertEqual(again.title, "My favourite role")
        self.assertEqual(len(self.store.rows("job")), 1)

    def test_unknown_property_is_refused(self):
        with self.assertRaises(KeyError):
            self.store.upsert("job", "J-1", "Role", {"salary": 1})


class Safety(VaultCase):
    def test_nothing_outside_the_folder_changes(self):
        before = tree_hashes(self.vault_dir, skip="workdecero")
        self.store.ensure_layout()
        self.make_job()
        self.store.upsert("company", "O-1", "Mile High Analytics", {"work_policy": "Remote"})
        self.store.refresh_rollups()
        self.assertEqual(tree_hashes(self.vault_dir, skip="workdecero"), before)
        self.assertTrue(all(path.startswith("workdecero/") for path in self.store.changed))

    def test_bad_vault_folders_are_refused(self):
        for bad in ("..", "../x", "a/../../b", ".obsidian", ".obsidian/plugins", "x/.hidden", "", "/"):
            with self.assertRaises(vault.VaultError, msg=bad):
                vault.VaultStore(self.vault_dir, bad)

    def test_missing_vault_is_refused(self):
        with self.assertRaises(vault.VaultError):
            vault.VaultStore(os.path.join(self.tmp.name, "nope"))

    def test_folder_that_is_a_link_to_outside_the_vault_is_refused(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.vault_dir, "sneaky"))
        with self.assertRaises(vault.VaultError):
            vault.VaultStore(self.vault_dir, "sneaky")
        self.store.ensure_layout()
        os.rmdir(os.path.join(self.store.root, "Jobs"))
        os.symlink(outside, os.path.join(self.store.root, "Jobs"))
        with self.assertRaises(vault.VaultError):
            self.make_job()
        self.assertEqual(os.listdir(outside), [])

    def test_note_with_unreadable_properties_is_left_alone_and_reported(self):
        row = self.make_job()
        broken = "---\nwd_id: J-1\nwd_type: job\nnotes: |\n  a block the reader does not support\n---\nBody.\n"
        self.write(row, broken)
        store = self.new_store()
        self.assertEqual(store.rows("job"), [])
        self.assertEqual(len(store.skipped), 1)
        self.assertIn("left untouched", store.skipped[0][1])
        with self.assertRaises(vault.VaultError):
            store.upsert("job", "J-1", "Head of Product, Mile High Analytics", {"fit": 1})
        self.assertEqual(self.read(row), broken)
        self.assertEqual(len(os.listdir(os.path.dirname(self.note_path(row)))), 1)

    def test_previous_version_is_backed_up_and_no_temp_files_remain(self):
        row = self.make_job()
        original = self.read(row)
        store = self.new_store()
        store.upsert("job", "J-1", "", {"fit": 5})
        runs = os.listdir(self.backups)
        self.assertEqual(len(runs), 1)
        with open(os.path.join(self.backups, runs[0], row.path), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)
        leftovers = [name for name in os.listdir(os.path.dirname(self.note_path(row))) if name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_old_backups_are_pruned(self):
        for index in range(vault.BACKUPS_KEPT + 5):
            os.makedirs(os.path.join(self.backups, "20260101-0000%02d" % index))
        self.store.ensure_layout()
        self.assertEqual(len(os.listdir(self.backups)), vault.BACKUPS_KEPT)
        self.assertNotIn("20260101-000000", os.listdir(self.backups))

    def test_long_dashes_are_scrubbed_from_what_a_sweep_writes(self):
        row = self.store.upsert("job", "J-2", "Lead %s Platform" % EM,
                                {"role": "Lead %s Platform" % EM, "location": "Austin %s Dallas" % EN,
                                 "warm_path": ["a %s b" % EM]},
                                section="Range 2019%s2021 %s strong." % (EN, EM))
        text = self.read(row)
        self.assertNotIn(EM, text)
        self.assertNotIn(EN, text)
        self.assertEqual(row.values["role"], "Lead, Platform")
        self.assertEqual(row.values["location"], "Austin - Dallas")
        self.assertEqual(row.title, "Lead, Platform")
        kept = self.new_store(scrub=False).upsert("job", "J-3", "Other", {"role": "A %s B" % EM})
        self.assertEqual(kept.values["role"], "A %s B" % EM)

    def test_file_names_are_safe_and_do_not_collide(self):
        self.assertEqual(vault.safe_file_name('A/B: "C" <D> | E? #1 [x]^'), "A B C D E 1 x")
        self.assertEqual(vault.safe_file_name("  ...  "), "Untitled")
        first = self.store.upsert("job", "J-1", "VP of Product", {})
        second = self.store.upsert("job", "J-2", "VP of Product", {})
        self.assertEqual(first.title, "VP of Product")
        self.assertEqual(second.title, "VP of Product (2)")

    def test_users_note_in_a_tracker_folder_is_ignored(self):
        self.store.ensure_layout()
        with open(os.path.join(self.store.root, "Jobs", "Scratch.md"), "w") as handle:
            handle.write("Just my scribbles, no properties.\n")
        self.make_job()
        self.assertEqual([row.id for row in self.store.rows("job")], ["J-1"])


class Layout(VaultCase):
    def test_layout_is_created_once_and_user_edits_to_views_are_kept(self):
        created = self.store.ensure_layout()
        self.assertEqual(sorted(name for name in os.listdir(self.store.root) if not os.path.isdir(
            os.path.join(self.store.root, name))),
            ["Companies.base", "Contracts.base", "Digests.base", "Events.base", "Groups.base", "Jobs.base",
             "People.base", "Start here.md"])
        self.assertEqual(len(created), 8)
        for note_type in model.TYPES:
            self.assertTrue(os.path.isdir(os.path.join(self.store.root, note_type.folder)))
        jobs = os.path.join(self.store.root, "Jobs.base")
        with open(jobs, "a") as handle:
            handle.write("# resized by the user in Obsidian\n")
        start = os.path.join(self.store.root, "Start here.md")
        with open(start, "a") as handle:
            handle.write("My own pinned reminders.\n")
        self.assertEqual(self.new_store().ensure_layout(), [])
        with open(jobs) as handle:
            self.assertIn("resized by the user", handle.read())
        refreshed = self.new_store().ensure_layout(refresh_views=True)
        self.assertEqual(refreshed, ["workdecero/Jobs.base"])
        with open(start) as handle:
            self.assertIn("My own pinned reminders.", handle.read())

    def test_custom_and_nested_folder(self):
        store = vault.VaultStore(self.vault_dir, "Career/Search tracker")
        store.ensure_layout()
        with open(os.path.join(store.root, "Jobs.base")) as handle:
            self.assertIn("file.inFolder('Career/Search tracker/Jobs')", handle.read())
        self.assertEqual(store.link("person", "Priya Raman"),
                         "[[Career/Search tracker/People/Priya Raman|Priya Raman]]")

    def test_rollups_count_open_roles_and_people(self):
        link = self.store.link
        self.store.upsert("company", "O-1", "Acme Robotics", {})
        self.store.upsert("company", "O-2", "Quiet Co", {})
        self.store.upsert("job", "J-1", "A", {"company": link("company", "Acme Robotics")})
        self.store.upsert("job", "J-2", "B", {"company": link("company", "Acme Robotics"), "status": "Passed"})
        self.store.upsert("contract", "C-1", "C", {"client": "Acme", "status": "Applied"})
        self.store.upsert("person", "P-1", "Pat Example", {"company": link("company", "Acme Robotics")})
        self.store.refresh_rollups()
        counts = dict((row.title, (row.values["open_roles"], row.values["people_known"]))
                      for row in self.store.rows("company"))
        self.assertEqual(counts, {"Acme Robotics": (1, 1), "Quiet Co": (0, 0)})


class DemoVault(unittest.TestCase):
    def test_demo_vault_builds_and_leaves_user_notes_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "demo")
            store = make_demo_vault.build(target, today=TODAY)
            self.assertEqual(len(store.rows("job")), 9)
            self.assertEqual(len(store.rows("event")), 6)
            self.assertEqual(store.skipped, [])
            self.assertFalse(os.path.exists(os.path.join(target, ".obsidian")))
            with open(os.path.join(target, "Reading list.md")) as handle:
                self.assertEqual(handle.read(), make_demo_vault.USER_NOTES["Reading list.md"])
            again = make_demo_vault.build(target, today=TODAY)
            self.assertEqual(again.changed, [])   # building twice changes nothing


class Views(unittest.TestCase):
    def setUp(self):
        self.files = bases.build_all("workdecero")

    def test_expected_files_and_default_views(self):
        defaults = dict((name, content["views"][0]["name"]) for name, content in self.files.items())
        self.assertEqual(defaults, {
            "Jobs.base": "New this week", "Contracts.base": "New gigs", "Events.base": "Next 30 days",
            "Groups.base": "Groups to try", "People.base": "Warm paths",
            "Companies.base": "Companies with openings", "Digests.base": "Digests",
        })

    def test_every_view_has_at_most_eight_columns_and_starts_with_the_note(self):
        for name, content in self.files.items():
            for view in content["views"]:
                label = "%s / %s" % (name, view["name"])
                self.assertLessEqual(len(view["order"]), 8, label)
                self.assertIn(view["order"][0], ("file.name", "formula.role"), label)
                self.assertEqual(view["type"], "table", label)

    def test_every_column_sort_and_group_refers_to_something_real(self):
        known = set(field.key for note_type in model.TYPES for field in note_type.fields)
        for name, content in self.files.items():
            formulas = set("formula." + key for key in content.get("formulas", {}))
            for view in content["views"]:
                used = list(view["order"]) + [item["property"] for item in view.get("sort", [])]
                if "groupBy" in view:
                    used.append(view["groupBy"]["property"])
                for prop in used:
                    ok = prop.startswith("file.") or prop in formulas or prop in known
                    self.assertTrue(ok, "%s / %s uses unknown %s" % (name, view["name"], prop))

    def test_every_column_has_a_friendly_name_and_a_width(self):
        for name, content in self.files.items():
            for view in content["views"]:
                for prop in view["order"]:
                    key = prop if prop.startswith(("file.", "formula.")) else "note." + prop
                    display = content["properties"][key]["displayName"]
                    self.assertFalse(display.startswith(model.PREFIX), key)
                    self.assertIn(key, view["columnSize"], "%s / %s" % (name, view["name"]))

    def test_stage_labels_follow_the_status_order_in_the_model(self):
        formula = bases.stage_formula(model.JOB)
        positions = [formula.index("'%s'" % status.stage) for status in model.JOB.statuses
                     if status.kind != "done"]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(formula.endswith("'6 Closed'" + ")" * 5))
        stages = [status.stage for status in model.JOB.statuses]
        self.assertEqual(stages, sorted(stages))

    def test_view_files_survive_a_yaml_round_trip(self):
        for name, content in self.files.items():
            self.assertEqual(miniyaml.loads(miniyaml.dumps(content)), content, name)

    def test_start_here_embeds_views_that_exist(self):
        import re
        text = bases.start_here("workdecero")
        embeds = re.findall(r"!\[\[workdecero/([^#\]]+)#([^\]]+)\]\]", text)
        self.assertGreaterEqual(len(embeds), 4)
        for file_name, view_name in embeds:
            self.assertIn(view_name, [view["name"] for view in self.files[file_name]["views"]])
        self.assertFalse(text.startswith("# "))   # Obsidian already shows the note title


if __name__ == "__main__":
    unittest.main()
