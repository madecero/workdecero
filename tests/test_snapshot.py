"""The optional spreadsheet snapshot: a zip of small XML files, checked by opening it as one."""

import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.parse
import xml.dom.minidom
import zipfile

import _paths

sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "skills", "contract-sweep", "scripts"))
sys.path.insert(0, os.path.join(_paths.REPO_ROOT, "trackers"))
import contract_tool  # noqa: E402
import miniyaml  # noqa: E402
import snapshot  # noqa: E402
import sources  # noqa: E402
import vault  # noqa: E402
import wdprofile  # noqa: E402

EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")
TODAY = datetime.date(2026, 11, 9)


def cells(sheet_xml):
    """{cell reference: (text or number, style number)} for one worksheet."""
    found = {}
    for ref, style, rest in re.findall(r'<c r="([A-Z]+\d+)" s="(\d+)"([^>]*?/>|[^>]*>.*?</c>)', sheet_xml):
        text = re.search(r"<t[^>]*>(.*?)</t>", rest, re.DOTALL)
        number = re.search(r"<v>(.*?)</v>", rest)
        value = text.group(1) if text else (float(number.group(1)) if number else "")
        found[ref] = (value, int(style))
    return found


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "config")
        self.cache = os.path.join(self.tmp.name, "cache")
        self.vault_dir = os.path.join(self.tmp.name, "My Notes")
        os.makedirs(os.path.join(self.vault_dir, ".obsidian"))
        os.makedirs(self.home)
        self._env = dict((key, os.environ.get(key)) for key in ("WORKDECERO_HOME", contract_tool.DRAFT_DIR_ENV))
        os.environ["WORKDECERO_HOME"] = self.home
        os.environ[contract_tool.DRAFT_DIR_ENV] = self.cache
        self.addCleanup(self.restore_env)
        with open(os.path.join(EXAMPLES, "freelance-designer-portland.yaml"), encoding="utf-8") as handle:
            self.raw = miniyaml.loads(handle.read())
        self.raw["storage"] = {"vault_path": self.vault_dir, "vault_folder": "workdecero", "spreadsheet_snapshot": True}
        self.write_profile()
        self.store = vault.VaultStore(self.vault_dir, today=TODAY)
        self.store.ensure_layout()

    def write_profile(self):
        with open(wdprofile.profile_path(), "w", encoding="utf-8") as handle:
            handle.write(miniyaml.dumps(self.raw))
        self.profile = wdprofile.load()[0]

    def restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def seed(self):
        store = self.store
        store.upsert("contract", "C-1", "Label design for a brewery", {"client": "Example Brewing", "fit": 61,
                                                                       "rate_max": 85, "link": "https://gigs.example.com/b"})
        store.upsert("contract", "C-2", "Brand identity for a cafe group", {"fit": 72, "rate_min": 80, "rate_max": 95,
                                                                            "remote": True})
        store.upsert("contract", "C-3", "Packaging system <tea & co>", {"fit": 90, "client": "Example Tea Company"})
        store.upsert("contract", "C-3", "Packaging system <tea & co>", {"status": "Applied"}, force=["status"])
        store.upsert("contract", "C-4", "Rebrand for a cold brew maker", {"client": "Cascadia Cold Brew Co.", "fit": 50})
        store.upsert("channel", "H-1", "Design marketplace", {"why": "Brand projects."})
        store.upsert("group", "G-1", "Portland Brand Meetup", {"fit": 70, "next_meeting": "2026-11-18"})
        store.upsert("event", "E-1", "Northwest Food Brand Summit", {"date": "2026-12-03", "cost": 25})
        store.upsert("person", "P-1", "Imani Brooks", {"company": store.link("company", "Example Tea Company"),
                                                       "relationship": "1st"})
        store.upsert("person", "P-2", "Aaron Zed", {"relationship": "2nd"})

    def book(self):
        return zipfile.ZipFile(snapshot.path_for(self.store))


class Workbook(SnapshotCase):
    def setUp(self):
        super(Workbook, self).setUp()
        self.seed()
        self.path = snapshot.write(self.store, self.profile, TODAY)

    def test_it_is_a_well_formed_workbook_only_its_owner_can_read(self):
        self.assertEqual(self.path, os.path.join(os.path.realpath(self.vault_dir), "workdecero",
                                                 "workdecero snapshot.xlsx"))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        book = self.book()
        self.assertIsNone(book.testzip())
        names = book.namelist()
        self.assertEqual(names[0], "[Content_Types].xml")
        for name in names:
            xml.dom.minidom.parseString(book.read(name))               # every part is well-formed XML
        workbook = book.read("xl/workbook.xml").decode("utf-8")
        self.assertEqual(re.findall(r'<sheet name="([^"]+)"', workbook),
                         ["Summary", "Jobs", "Contracts", "Channels", "Groups", "Events", "People"])
        types = book.read("[Content_Types].xml").decode("utf-8")
        for index in range(1, 8):
            self.assertIn("xl/worksheets/sheet%d.xml" % index, names)
            self.assertIn("/xl/worksheets/sheet%d.xml" % index, types)
        rels = book.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        self.assertEqual(rels.count("relationships/worksheet"), 7)
        self.assertIn('Target="styles.xml"', rels)

    def test_every_sheet_has_the_banner_a_frozen_header_and_widths(self):
        book = self.book()
        for index in range(1, 8):
            sheet = book.read("xl/worksheets/sheet%d.xml" % index).decode("utf-8")
            found = cells(sheet)
            self.assertEqual(found["A1"], ("Read-only snapshot from 2026-11-09. Edit in Obsidian; this file is rebuilt "
                                           "every sweep.", 2))
            self.assertEqual(found["A2"][1], 1)                         # the header style
            self.assertIn('<pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/>', sheet)
            self.assertIn("<cols><col min=\"1\" max=\"1\" width=", sheet)
            self.assertIn('<mergeCell ref="A1:', sheet)
        contracts = cells(book.read("xl/worksheets/sheet3.xml").decode("utf-8"))
        self.assertEqual([contracts[ref][0] for ref in ("A2", "B2", "C2")], ["Gig", "Status", "Win odds"])

    def test_rows_are_in_view_order_and_filled_by_stage(self):
        found = cells(self.book().read("xl/worksheets/sheet3.xml").decode("utf-8"))
        titles = [found["A%d" % row][0] for row in (3, 4, 5)]
        # New first (best odds first), then Applied. The confidential client's gig is listed without its name.
        self.assertEqual(titles[:2], ["Brand identity for a cafe group", "Label design for a brewery"])
        self.assertEqual(found["B3"][0], "New")
        self.assertEqual(found["C3"], (72.0, snapshot._fill_style(1)))  # a number stays a number
        self.assertEqual(found["A3"][1], snapshot._fill_style(1, link=True))
        applied = [row for row in range(3, 8) if found.get("B%d" % row, ("",))[0] == "Applied"][0]
        self.assertEqual(found["A%d" % applied], ("Packaging system tea &amp; co", snapshot._fill_style(3, link=True)))
        self.assertEqual(found["B%d" % applied][1], snapshot._fill_style(3))
        self.assertNotEqual(snapshot._fill_style(1), snapshot._fill_style(3))
        styles = self.book().read("xl/styles.xml").decode("utf-8")
        self.assertEqual(styles.count("<xf "), 1 + 4 + 2 * len(snapshot.STAGE_FILLS))
        people = cells(self.book().read("xl/worksheets/sheet7.xml").decode("utf-8"))
        self.assertEqual([people["A3"][0], people["A4"][0]], ["Imani Brooks", "Aaron Zed"])      # 1st before 2nd
        self.assertEqual(people["B3"][0], "Example Tea Company")        # a link shows as its label

    def test_the_first_cell_of_each_row_opens_its_note_in_obsidian(self):
        book = self.book()
        sheet = book.read("xl/worksheets/sheet3.xml").decode("utf-8")
        rels = book.read("xl/worksheets/_rels/sheet3.xml.rels").decode("utf-8")
        self.assertIn('<hyperlink ref="A3" r:id="rId1"/>', sheet)
        targets = re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)" TargetMode="External"', rels)
        self.assertEqual(len(targets), 4)
        first = dict(targets)["rId1"]
        self.assertTrue(first.startswith("obsidian://open?path="))
        note = urllib.parse.unquote(first.split("path=", 1)[1])
        self.assertEqual(note, os.path.join(os.path.realpath(self.vault_dir), "workdecero", "Contracts",
                                            "Brand identity for a cafe group.md"))
        self.assertTrue(os.path.isfile(note))
        self.assertNotIn(" ", first)                                  # the space in the vault's name is encoded
        self.assertNotIn("sheet1.xml.rels", " ".join(book.namelist())) # the summary links to nothing

    def test_the_summary_counts_by_status(self):
        found = cells(self.book().read("xl/worksheets/sheet1.xml").decode("utf-8"))
        rows = [(found["A%d" % row][0], found["B%d" % row][0], found["C%d" % row][0])
                for row in range(3, 20) if "A%d" % row in found]
        self.assertIn(("Contracts", "New", 3.0), rows)
        self.assertIn(("Contracts", "Applied", 1.0), rows)
        self.assertIn(("Jobs", "None yet", 0.0), rows)
        self.assertIn(("People", "All", 2.0), rows)
        self.assertEqual([found["A2"][0], found["B2"][0], found["C2"][0]], ["Tracker", "Status", "Notes"])

    def test_confidential_names_never_appear(self):
        book = self.book()
        everything = " ".join(book.read(name).decode("utf-8") for name in book.namelist())
        self.assertNotIn("Cascadia", everything)
        self.assertIn("[confidential]", everything)
        self.assertNotIn(self.profile["person"]["name"], everything)

    def test_it_is_rebuilt_whole_and_never_read_back(self):
        with open(self.path, "wb") as handle:                          # someone typed into it, or it was damaged
            handle.write(b"not a workbook any more")
        os.chmod(self.path, 0o644)
        self.store.upsert("contract", "C-5", "Menu redesign", {"fit": 40})
        snapshot.write(self.store, self.profile, datetime.date(2026, 11, 16))
        found = cells(self.book().read("xl/worksheets/sheet3.xml").decode("utf-8"))
        self.assertIn("Menu redesign", [value for value, _ in found.values()])
        self.assertIn("2026-11-16", found["A1"][0])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        # The same notes on the same day give the same file.
        first = open(self.path, "rb").read()
        snapshot.write(self.store, self.profile, datetime.date(2026, 11, 16))
        self.assertEqual(open(self.path, "rb").read(), first)

    def test_the_snapshot_is_not_mistaken_for_a_note(self):
        self.assertEqual(len(self.store.rows("contract")), 4)
        self.store.scan()
        self.assertEqual(self.store.skipped, [])


class Switch(SnapshotCase):
    def test_off_by_default_and_on_only_when_asked(self):
        self.assertTrue(snapshot.wanted(self.profile))
        self.raw["storage"].pop("spreadsheet_snapshot")
        self.write_profile()
        self.assertFalse(snapshot.wanted(self.profile))
        self.assertIsNone(snapshot.refresh(self.store, self.profile, TODAY))
        self.assertFalse(os.path.exists(snapshot.path_for(self.store)))
        with open(os.path.join(_paths.REPO_ROOT, "engine", "profile.schema.json"), encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertIs(schema["properties"]["storage"]["properties"]["spreadsheet_snapshot"]["default"], False)

    def test_a_sweep_rebuilds_it_at_the_end_and_says_so(self):
        sources.save(sources.empty_map())

        class Pages(object):
            def get(self, url, accept=None):
                import liveness
                return liveness.Response(200, "<html><body>Gig</body></html>", url, None)

        out = io.StringIO()
        contract_tool.start(out=out, fetcher=Pages(), today=TODAY)
        os.makedirs(self.cache, exist_ok=True)
        with open(contract_tool.handoff_path(), "w", encoding="utf-8") as handle:
            json.dump({"finds": [{"title": "Brand identity for a cafe group", "url": "https://gigs.example.com/a",
                                  "engagement": "1099", "rate_max": 95, "fit_assessment": 30, "remote": True}],
                       "searches": 9}, handle)
        contract_tool.intake(out=io.StringIO(), today=TODAY)
        done = io.StringIO()
        self.assertEqual(contract_tool.finish(out=done, today=TODAY), 0)
        self.assertIn("Spreadsheet snapshot rebuilt: workdecero snapshot.xlsx, in the tracker folder. It is read-only: "
                      "edit in Obsidian.", done.getvalue())
        found = cells(self.book().read("xl/worksheets/sheet3.xml").decode("utf-8"))
        self.assertEqual(found["A3"][0], "Brand identity for a cafe group")
        # With the switch off, a sweep writes no spreadsheet and says nothing about one.
        os.remove(snapshot.path_for(self.store))
        self.raw["storage"]["spreadsheet_snapshot"] = False
        self.write_profile()
        contract_tool.start(out=io.StringIO(), fetcher=Pages(), today=TODAY)
        quiet = io.StringIO()
        contract_tool.finish(out=quiet, today=TODAY)
        self.assertNotIn("snapshot", quiet.getvalue())
        self.assertFalse(os.path.exists(snapshot.path_for(self.store)))

    def test_a_snapshot_that_cannot_be_written_never_fails_the_sweep(self):
        os.makedirs(snapshot.path_for(self.store) + ".tmp")           # something is in the way
        line = snapshot.refresh(self.store, self.profile, TODAY)
        self.assertIn("could not be written", line)
        self.assertIn("Your notes are unaffected.", line)


if __name__ == "__main__":
    unittest.main()
