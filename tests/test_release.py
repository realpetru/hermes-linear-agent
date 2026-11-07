"""Release hygiene: one version everywhere, and a catalog entry that passes its own schema."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED = "1.1.0"
VALID_KINDS = {"standalone", "backend", "exclusive", "platform", "model-provider"}
REQUIRED_CATALOG_KEYS = ("name", "repo", "sha", "description", "maintainer")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class VersionTests(unittest.TestCase):
    def test_plugin_yaml_version(self):
        self.assertIn("version: %s" % EXPECTED, read("plugin.yaml"))

    def test_pyproject_version(self):
        self.assertRegex(read("pyproject.toml"), r'version\s*=\s*"%s"' % re.escape(EXPECTED))

    def test_entrypoint_version(self):
        self.assertIn('PLUGIN_VERSION = "%s"' % EXPECTED, read("__init__.py"))

    def test_server_version(self):
        self.assertIn('SERVICE_VERSION = "%s"' % EXPECTED, read("src/linear_hermes/server.py"))

    def test_catalog_version(self):
        self.assertIn('version: "%s"' % EXPECTED, read("plugin-catalog/linear-agent.yaml"))

    def test_no_stale_old_versions(self):
        for rel in ("__init__.py", "src/linear_hermes/server.py", "pyproject.toml"):
            self.assertNotIn("0.1.0", read(rel), rel)


class ManifestTests(unittest.TestCase):
    def test_kind_is_valid(self):
        kind = re.search(r"^kind:\s*(\S+)", read("plugin.yaml"), re.M).group(1)
        self.assertIn(kind, VALID_KINDS, "unknown kind is treated as standalone with a warning")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.text = read("plugin-catalog/linear-agent.yaml")

    def test_required_keys_present(self):
        for key in REQUIRED_CATALOG_KEYS:
            self.assertRegex(self.text, r"(?m)^%s:" % key, "catalog entry needs %s:" % key)

    def test_unknown_keys_removed(self):
        for banned in ("author:", "tags:"):
            self.assertNotRegex(self.text, r"(?m)^%s" % banned)

    def test_sha_is_a_full_pin(self):
        sha = re.search(r'^sha:\s*"([0-9a-f]+)"', self.text, re.M).group(1)
        self.assertEqual(len(sha), 40)

    def test_tier_and_category(self):
        self.assertIn("tier: community", self.text)
        self.assertRegex(self.text, r"(?m)^category: \w+")


if __name__ == "__main__":
    unittest.main()
