import json
import os
import tempfile
import unittest

import _paths
import validate_manifests as vm


class RealRepo(unittest.TestCase):
    def test_this_repository_is_valid(self):
        self.assertEqual(vm.validate(_paths.REPO_ROOT), [])


class Broken(unittest.TestCase):
    def build(self, plugin=None, market=None, skill_name="setup", skill_text=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        os.makedirs(os.path.join(root, ".claude-plugin"))
        os.makedirs(os.path.join(root, "skills", "setup"))
        plugin = {"name": "demo", "version": "0.0.1", "description": "d"} if plugin is None else plugin
        market = ({"name": "demo", "owner": {"name": "someone"},
                   "plugins": [{"name": "demo", "source": "./"}]} if market is None else market)
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as handle:
            json.dump(plugin, handle)
        with open(os.path.join(root, ".claude-plugin", "marketplace.json"), "w") as handle:
            json.dump(market, handle)
        if skill_text is None:
            skill_text = "---\nname: %s\ndescription: does a thing\n---\nbody\n" % skill_name
        with open(os.path.join(root, "skills", "setup", "SKILL.md"), "w") as handle:
            handle.write(skill_text)
        return root

    def test_valid_fixture(self):
        self.assertEqual(vm.validate(self.build()), [])

    def test_bad_plugin_name_and_version(self):
        problems = vm.validate(self.build(plugin={"name": "Demo Plugin", "version": "1.0", "description": "d"}))
        self.assertTrue(any("kebab-case" in p for p in problems))
        self.assertTrue(any("MAJOR.MINOR.PATCH" in p for p in problems))

    def test_marketplace_must_list_the_plugin(self):
        market = {"name": "demo", "owner": {"name": "someone"}, "plugins": [{"name": "other", "source": "./"}]}
        self.assertTrue(any("no entry named" in p for p in vm.validate(self.build(market=market))))

    def test_marketplace_source_must_exist(self):
        market = {"name": "demo", "owner": {"name": "someone"}, "plugins": [{"name": "demo", "source": "./missing"}]}
        self.assertTrue(any("not a folder" in p for p in vm.validate(self.build(market=market))))

    def test_skill_name_must_match_folder(self):
        self.assertTrue(any("folder name" in p for p in vm.validate(self.build(skill_name="other"))))

    def test_skill_needs_front_matter(self):
        self.assertTrue(any("front matter" in p for p in vm.validate(self.build(skill_text="# no front matter\n"))))


if __name__ == "__main__":
    unittest.main()
