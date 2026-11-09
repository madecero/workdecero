import unittest

import _paths  # noqa: F401
import check_version_bump as bump


class VersionBump(unittest.TestCase):
    def test_shipped_paths(self):
        for path in (".claude-plugin/plugin.json", "skills/setup/SKILL.md", "engine/x.py",
                     "trackers/vault.py", "hooks/hooks.json"):
            self.assertTrue(bump.is_shipped(path), path)
        for path in ("README.md", "tests/test_x.py", ".github/workflows/ci.yml", "dev/leak_guard.py",
                     "examples/profiles/a.yaml", "hooks/pre-commit", "CONTRIBUTING.md"):
            self.assertFalse(bump.is_shipped(path), path)

    def test_docs_only_change_needs_no_bump(self):
        ok, _ = bump.evaluate(["README.md", "tests/a.py"], "0.1.0", "0.1.0")
        self.assertTrue(ok)

    def test_shipped_change_without_bump_fails(self):
        ok, message = bump.evaluate(["engine/x.py"], "0.1.0", "0.1.0")
        self.assertFalse(ok)
        self.assertIn("engine/x.py", message)

    def test_shipped_change_with_bump_passes(self):
        self.assertTrue(bump.evaluate(["engine/x.py"], "0.1.9", "0.1.10")[0])
        self.assertTrue(bump.evaluate(["engine/x.py"], "0.9.9", "1.0.0")[0])

    def test_version_going_down_fails(self):
        self.assertFalse(bump.evaluate(["skills/a/SKILL.md"], "0.2.0", "0.1.9")[0])

    def test_first_manifest_passes(self):
        self.assertTrue(bump.evaluate([".claude-plugin/plugin.json"], None, "0.0.1")[0])

    def test_bad_version_raises(self):
        with self.assertRaises(ValueError):
            bump.evaluate(["engine/x.py"], "0.1.0", "next")


if __name__ == "__main__":
    unittest.main()
