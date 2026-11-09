import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _paths
import miniyaml
import wdprofile as wd_profile
import schema as schema_check

EXAMPLES = os.path.join(_paths.REPO_ROOT, "examples", "profiles")
BROKEN = os.path.join(_paths.REPO_ROOT, "tests", "fixtures", "broken-profile.yaml")
EXAMPLE_FILES = sorted(name for name in os.listdir(EXAMPLES) if name.endswith(".yaml"))


def minimal():
    return {
        "schema_version": 1,
        "person": {"name": "Sam Example", "headline": "Office manager"},
        "location": {"home_base": "Tulsa, OK", "work_modes": ["onsite"]},
        "tracks": {"jobs": True, "contracts": False, "networking": False},
        "skills": {"core": ["Scheduling"]},
        "jobs": {"titles": ["Office Manager"], "salary_floor": 50000},
        "storage": {"vault_path": "~/Vault"},
    }


def paths(problems):
    return [problem.path for problem in problems]


class SchemaFile(unittest.TestCase):
    def setUp(self):
        self.schema = wd_profile.load_schema()

    def test_uses_only_keywords_the_checker_understands(self):
        self.assertEqual(schema_check.unsupported_keywords(self.schema), [])

    def test_every_setting_has_a_description_and_an_example_or_default(self):
        missing = []

        def walk(node, path):
            if path and not node.get("description"):
                missing.append(path + " (description)")
            if node.get("type") == "object":
                for name, child in node.get("properties", {}).items():
                    walk(child, (path + "." if path else "") + name)
            elif path and not ("examples" in node or "default" in node or "enum" in node):
                missing.append(path + " (example)")
            if node.get("type") == "array" and node["items"].get("type") == "object":
                walk(node["items"], path + "[]")

        walk(self.schema, "")
        self.assertEqual(missing, [])

    def test_every_example_and_default_in_the_schema_is_itself_valid(self):
        bad = []

        def walk(node, path):
            for sample in node.get("examples", []) + ([node["default"]] if "default" in node else []):
                if schema_check.validate(sample, node, path):
                    bad.append(path)
            for name, child in node.get("properties", {}).items():
                walk(child, (path + "." if path else "") + name)
            if "items" in node:
                walk(node["items"], path + "[]")

        walk(self.schema, "")
        self.assertEqual(bad, [])

    def test_objects_reject_unknown_settings(self):
        loose = []

        def walk(node, path):
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    loose.append(path or "(root)")
                for name, child in node.get("properties", {}).items():
                    walk(child, (path + "." if path else "") + name)
            if "items" in node:
                walk(node["items"], path + "[]")

        walk(self.schema, "")
        self.assertEqual(loose, [])


class ExampleProfiles(unittest.TestCase):
    def test_there_are_three_examples(self):
        self.assertEqual(len(EXAMPLE_FILES), 3)

    def test_examples_are_valid(self):
        for name in EXAMPLE_FILES:
            profile, problems = wd_profile.load(os.path.join(EXAMPLES, name))
            self.assertEqual([p.render() for p in problems], [], name)
            self.assertIsNotNone(profile)

    def test_examples_cover_different_track_mixes(self):
        mixes = set()
        for name in EXAMPLE_FILES:
            profile, _ = wd_profile.load(os.path.join(EXAMPLES, name))
            mixes.add(tuple(profile["tracks"][track] for track in wd_profile.TRACKS))
        self.assertEqual(mixes, {(True, False, False), (False, True, True), (True, True, True)})

    def test_examples_keep_risky_and_heavy_options_off(self):
        for name in EXAMPLE_FILES:
            profile, _ = wd_profile.load(os.path.join(EXAMPLES, name))
            browser = profile.get("integrations", {}).get("browser", {})
            self.assertFalse(browser.get("linkedin_signed_in"), name)
            self.assertFalse(browser.get("linkedin_risk_acknowledged"), name)
            self.assertEqual(profile["mode"], "lite", name)

    def test_examples_raise_no_warnings(self):
        for name in EXAMPLE_FILES:
            profile, _ = wd_profile.load(os.path.join(EXAMPLES, name))
            self.assertEqual(wd_profile.warnings(profile), [], name)

    def test_examples_survive_a_write_and_read_round_trip(self):
        for name in EXAMPLE_FILES:
            with open(os.path.join(EXAMPLES, name), encoding="utf-8") as handle:
                raw = miniyaml.loads(handle.read())
            self.assertEqual(miniyaml.loads(miniyaml.dumps(raw)), raw, name)

    def test_defaults_are_filled_in(self):
        profile, _ = wd_profile.load(os.path.join(EXAMPLES, "nurse-manager-texas.yaml"))
        self.assertEqual(profile["location"]["country"], "US")
        self.assertEqual(profile["jobs"]["currency"], "USD")
        self.assertEqual(profile["storage"]["vault_folder"], "workdecero")
        self.assertEqual(profile["mode"], "lite")
        self.assertNotIn("contracts", profile)
        portland, _ = wd_profile.load(os.path.join(EXAMPLES, "freelance-designer-portland.yaml"))
        self.assertEqual(portland["digest"]["max_new_items"], 10)
        self.assertEqual(profile["jobs"]["search_visibility"], "quiet")


class BrokenProfile(unittest.TestCase):
    def setUp(self):
        self.profile, self.problems = wd_profile.load(BROKEN)
        self.by_path = dict((p.path, p) for p in self.problems)

    def test_every_mistake_is_reported_once(self):
        self.assertEqual(sorted(paths(self.problems)), sorted([
            "person.headline", "location.radius_miles", "location.work_modes", "skills.core",
            "jobs.levels[1]", "jobs.salery_floor", "mode", "contracts", "jobs.salary_target",
            "integrations.browser.enabled", "integrations.browser.linkedin_risk_acknowledged",
            "storage.vault_folder",
        ]))

    def test_messages_name_the_problem_in_plain_words(self):
        self.assertIn("required but missing", self.by_path["person.headline"].message)
        self.assertIn('a whole number, but you wrote "thirty"', self.by_path["location.radius_miles"].message)
        self.assertIn("must be a list", self.by_path["location.work_modes"].message)
        self.assertIn("at least one item", self.by_path["skills.core"].message)
        self.assertIn("Did you mean manager?", self.by_path["jobs.levels[1]"].message)
        self.assertIn("Did you mean salary_floor?", self.by_path["jobs.salery_floor"].message)
        self.assertIn("lite, full", self.by_path["mode"].message)
        self.assertIn("lower than salary_floor", self.by_path["jobs.salary_target"].message)
        self.assertIn("risk", self.by_path["integrations.browser.linkedin_risk_acknowledged"].message)

    def test_problems_carry_a_correct_example(self):
        schema = wd_profile.load_schema()
        self.assertEqual(self.by_path["location.radius_miles"].example, "radius_miles: 30")
        self.assertEqual(self.by_path["person.headline"].example.split(":")[0], "headline")
        section = miniyaml.loads(self.by_path["contracts"].example)
        self.assertEqual(schema_check.validate(section["contracts"], schema["properties"]["contracts"]), [])

    def test_rendered_report_is_numbered_and_names_the_file(self):
        report = wd_profile.render_problems(self.problems, "profile.yaml")
        self.assertTrue(report.startswith("profile.yaml has 12 problems to fix:"))
        self.assertIn("\n12. ", report)
        self.assertIn("Example:", report)


class Rules(unittest.TestCase):
    def check(self, profile):
        return wd_profile.check(profile)

    def test_minimal_profile_is_valid(self):
        self.assertEqual(self.check(minimal()), [])

    def test_all_tracks_off(self):
        profile = minimal()
        profile["tracks"]["jobs"] = False
        self.assertEqual(paths(self.check(profile)), ["tracks"])

    def test_section_for_an_off_track_is_optional_but_still_checked(self):
        profile = minimal()
        profile["contracts"] = {"services": ["Bookkeeping"], "engagement_types": ["retainer"], "rate_floor_hourly": "lots"}
        self.assertEqual(paths(self.check(profile)), ["contracts.rate_floor_hourly"])

    def test_corp_to_corp_needs_a_business_entity(self):
        profile = minimal()
        profile["contracts"] = {"services": ["Bookkeeping"], "engagement_types": ["corp-to-corp"], "rate_floor_hourly": 60.5}
        self.assertEqual(paths(self.check(profile)), ["contracts.has_business_entity"])
        profile["contracts"]["has_business_entity"] = True
        self.assertEqual(self.check(profile), [])

    def test_vault_folder_must_stay_inside_the_vault(self):
        for bad in ("/abs", "~/x", "../up", "a/../../b", "C:\\x"):
            profile = minimal()
            profile["storage"]["vault_folder"] = bad
            self.assertEqual(paths(self.check(profile)), ["storage.vault_folder"], bad)
        profile = minimal()
        profile["storage"]["vault_folder"] = "Career/workdecero"
        self.assertEqual(self.check(profile), [])

    def test_exclusion_date_format(self):
        profile = minimal()
        profile["exclusions"] = [{"name": "Acme", "kind": "employer", "until": "June 2027"}]
        problems = self.check(profile)
        self.assertEqual(paths(problems), ["exclusions[1].until"])
        self.assertIn("YEAR-MONTH-DAY", problems[0].message)

    def test_relocation_targets_need_the_switch(self):
        profile = minimal()
        profile["location"]["relocation_targets"] = ["Denver, CO"]
        self.assertEqual(paths(self.check(profile)), ["location.relocation_targets"])

    def test_wrong_schema_version(self):
        profile = minimal()
        profile["schema_version"] = 2
        self.assertEqual(paths(self.check(profile)), ["schema_version"])

    def test_blank_text_and_duplicates(self):
        profile = minimal()
        profile["person"]["name"] = "   "
        profile["location"]["work_modes"] = ["remote", "remote"]
        self.assertEqual(sorted(paths(self.check(profile))), ["location.work_modes", "person.name"])

    def test_not_a_mapping(self):
        self.assertEqual(len(self.check(["a", "b"])), 1)

    def test_text_with_a_colon_gets_a_quotes_hint(self):
        profile = minimal()
        profile["jobs"]["deal_breakers"] = [{"Note": "onsite more than two days a week"}]
        problems = self.check(profile)
        self.assertEqual(paths(problems), ["jobs.deal_breakers[1]"])
        self.assertIn("Put the whole value in quotes", problems[0].message)
        self.assertEqual(problems[0].example, '"Note: onsite more than two days a week"')

    def test_search_visibility_and_digest_size(self):
        profile = minimal()
        profile["jobs"]["search_visibility"] = "secret"
        profile["digest"] = {"max_new_items": 0}
        self.assertEqual(sorted(paths(self.check(profile))), ["digest.max_new_items", "jobs.search_visibility"])


class Warnings(unittest.TestCase):
    def warn(self, profile):
        import datetime
        return wd_profile.warnings(profile, today=datetime.date(2026, 11, 9))

    def test_plausible_numbers_raise_nothing(self):
        profile = minimal()
        profile["jobs"].update({"salary_floor": 98000, "salary_target": 120000})
        profile["contracts"] = {"rate_floor_hourly": 70, "rate_target_hourly": 250.5}
        self.assertEqual(self.warn(profile), [])

    def test_salary_written_in_thousands(self):
        profile = minimal()
        profile["jobs"]["salary_floor"] = 210
        found = self.warn(profile)
        self.assertEqual(paths(found), ["jobs.salary_floor"])
        self.assertIn("Did you mean 210000?", found[0].message)
        self.assertEqual(found[0].example, "salary_floor: 210000")

    def test_salary_missing_a_digit(self):
        profile = minimal()
        profile["jobs"]["salary_floor"] = 21000
        self.assertIn("Did you mean 210000?", self.warn(profile)[0].message)

    def test_salary_with_an_extra_digit(self):
        profile = minimal()
        profile["jobs"]["salary_target"] = 21000000
        self.assertIn("Did you mean 2100000?", self.warn(profile)[0].message)

    def test_yearly_amount_in_an_hourly_setting(self):
        profile = minimal()
        profile["contracts"] = {"rate_floor_hourly": 160000}
        found = self.warn(profile)
        self.assertEqual(paths(found), ["contracts.rate_floor_hourly"])
        self.assertIn("per hour", found[0].message)
        self.assertIn("Did you mean 77?", found[0].message)

    def test_expired_exclusion(self):
        profile = minimal()
        profile["exclusions"] = [{"name": "A", "kind": "employer", "until": "2026-11-08"},
                                 {"name": "B", "kind": "employer", "until": "2026-11-09"},
                                 {"name": "C", "kind": "employer"}]
        self.assertEqual(paths(self.warn(profile)), ["exclusions[1].until"])

    def test_warnings_do_not_fail_validation(self):
        profile = minimal()
        profile["jobs"]["salary_floor"] = 210
        self.assertEqual(wd_profile.check(profile), [])


class QuietSearch(unittest.TestCase):
    def test_current_employer_is_excluded_and_confidential(self):
        profile = minimal()
        profile["person"]["current_employer"] = "Granite Peak Software"
        profile["exclusions"] = [{"name": "Gambling", "kind": "industry"}]
        profile["confidential"] = {"names": ["Old Client Co"]}
        self.assertEqual(wd_profile.check(profile), [])
        self.assertEqual(wd_profile.effective_exclusions(profile)[-1],
                         {"name": "Granite Peak Software", "kind": "employer", "action": "skip",
                          "reason": "Current employer"})
        self.assertEqual(wd_profile.confidential_names(profile), ["Old Client Co", "Granite Peak Software"])

    def test_no_duplicates_when_already_listed(self):
        profile = minimal()
        profile["person"]["current_employer"] = "Granite Peak Software"
        profile["exclusions"] = [{"name": "granite peak software", "kind": "employer", "action": "flag"}]
        profile["confidential"] = {"names": ["GRANITE PEAK SOFTWARE"]}
        self.assertEqual(len(wd_profile.effective_exclusions(profile)), 1)
        self.assertEqual(len(wd_profile.confidential_names(profile)), 1)

    def test_nothing_added_without_a_current_employer(self):
        self.assertEqual(wd_profile.effective_exclusions(minimal()), [])
        self.assertEqual(wd_profile.confidential_names(minimal()), [])


class LoadingAndCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text, name="profile.yaml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wd_profile.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_yaml_mistake_is_reported_with_its_line(self):
        profile, problems = wd_profile.load(self.write("schema_version: 1\nperson:\n\tname: Sam\n"))
        self.assertIsNone(profile)
        self.assertEqual(paths(problems), ["line 3"])
        self.assertIn("spaces, not tabs", problems[0].message)

    def test_curly_quotes_and_special_spaces_are_read_as_plain_ones(self):
        text = miniyaml.dumps(minimal())
        text = text.replace("name: Sam Example", "name: " + chr(0x201C) + "Sam Example" + chr(0x201D))
        text = text.replace("headline: ", "headline:" + chr(0x00A0))
        text = text.replace("- onsite", "- " + chr(0x2018) + "onsite" + chr(0x2019))
        profile, problems = wd_profile.load(self.write(text))
        self.assertEqual(problems, [])
        self.assertEqual(profile["person"]["name"], "Sam Example")
        self.assertEqual(profile["person"]["headline"], "Office manager")
        self.assertEqual(profile["location"]["work_modes"], ["onsite"])

    def test_pasted_bullet_message_reaches_the_user(self):
        text = miniyaml.dumps(minimal()).replace("  - Scheduling", "  " + chr(0x2022) + " Scheduling")
        _, problems = wd_profile.load(self.write(text))
        self.assertIn("must start with '- '", problems[0].message)

    def test_cli_prints_warnings_and_still_exits_zero(self):
        profile = minimal()
        profile["jobs"]["salary_floor"] = 210
        code, out, _ = self.run_cli("validate", self.write(miniyaml.dumps(profile)))
        self.assertEqual(code, 0)
        self.assertIn("is valid.", out)
        self.assertIn("1 thing to double-check", out)
        self.assertIn("Did you mean 210000?", out)

    def test_empty_file(self):
        profile, problems = wd_profile.load(self.write("# nothing yet\n"))
        self.assertIsNone(profile)
        self.assertIn("empty", problems[0].message)

    def test_cli_valid_broken_and_missing(self):
        code, out, _ = self.run_cli("validate", os.path.join(EXAMPLES, "nurse-manager-texas.yaml"))
        self.assertEqual(code, 0)
        self.assertIn("is valid. Sweeps on: jobs. Mode: lite.", out)
        code, _, err = self.run_cli("validate", BROKEN)
        self.assertEqual(code, 1)
        self.assertIn("12 problems to fix", err)
        code, _, err = self.run_cli("validate", os.path.join(self.tmp.name, "nope.yaml"))
        self.assertEqual(code, 2)
        self.assertIn("/workdecero:setup", err)

    def test_profile_location_follows_the_environment(self):
        old = os.environ.get(wd_profile.HOME_ENV)
        try:
            os.environ[wd_profile.HOME_ENV] = self.tmp.name
            self.assertEqual(wd_profile.profile_path(), os.path.join(self.tmp.name, "profile.yaml"))
            os.environ.pop(wd_profile.HOME_ENV)
            self.assertTrue(wd_profile.profile_path().endswith(os.path.join(".config", "workdecero", "profile.yaml")))
        finally:
            if old is not None:
                os.environ[wd_profile.HOME_ENV] = old

    def test_schema_file_is_plain_json(self):
        with open(wd_profile.SCHEMA_PATH, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["title"], "workdecero profile")


if __name__ == "__main__":
    unittest.main()
