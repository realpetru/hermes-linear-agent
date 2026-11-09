"""Release hygiene: one version everywhere, and a catalog entry that passes its own schema."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_manifest = (pathlib.Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
EXPECTED = re.search(r"^version: (\S+)$", _manifest, re.M).group(1)
VALID_KINDS = {"standalone", "backend", "exclusive", "platform", "model-provider"}
REQUIRED_CATALOG_KEYS = ("name", "repo", "sha", "description", "maintainer")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class VersionTests(unittest.TestCase):
    def test_plugin_yaml_version(self):
        self.assertIn("version: %s" % EXPECTED, read("plugin.yaml"))

    def test_entrypoint_version(self):
        self.assertIn('PLUGIN_VERSION = "%s"' % EXPECTED, read("__init__.py"))

    def test_server_version(self):
        self.assertIn('SERVICE_VERSION = "%s"' % EXPECTED, read("src/linear_hermes/server.py"))

    def test_catalog_version(self):
        self.assertIn('version: "%s"' % EXPECTED, read("plugin-catalog/linear-agent.yaml"))

    def test_dashboard_manifest_version(self):
        import json

        manifest = json.loads(read("dashboard/manifest.json"))
        self.assertEqual(manifest["version"], EXPECTED,
                         "the tab shows this version; it must match plugin.yaml")

    def test_server_user_agent_version(self):
        self.assertIn('server_version = "hermes-linear-agent/%s"' % EXPECTED, read("src/linear_hermes/server.py"))

    def test_package_version(self):
        self.assertIn('__version__ = "%s"' % EXPECTED, read("src/linear_hermes/__init__.py"))

    def test_the_authorize_link_uses_the_state_key(self):
        """The bug behind "Install link expired" straight after setup."""
        text = read("src/linear_hermes/plugin_api.py")
        self.assertIn('make_state(state_key)', text)
        self.assertIn('read_setting("LINEAR_STATE_KEY"', text)
        self.assertNotIn("make_state(webhook_secret)", text)
        makefile = read("Makefile")
        self.assertIn("LINEAR_STATE_KEY", makefile)
        self.assertNotIn("make_state(os.environ['LINEAR_WEBHOOK_SECRET'])", makefile)

    def test_no_stale_old_versions(self):
        for rel in ("__init__.py", "src/linear_hermes/server.py",
                    "src/linear_hermes/__init__.py", "dashboard/manifest.json"):
            self.assertNotIn("0.1.0", read(rel), rel)
        self.assertNotIn("1.4.3", read("src/linear_hermes/__init__.py"))


class PackagingTests(unittest.TestCase):
    """A [project] table makes Hermes' plugin admission attempt an editable install.

    On a PM-managed install whose managed venv predates the workspace path, `hermes
    plugins enable` then dies inside uv before the plugin ever loads. The plugin is
    stdlib-only and Hermes imports __init__.py directly, so it must ship no
    distribution metadata at all.
    """

    def test_no_pyproject_distribution(self):
        p = ROOT / "pyproject.toml"
        if not p.exists():
            return  # ideal
        text = p.read_text(encoding="utf-8")
        self.assertNotIn("[project]", text, "a [project] table triggers an editable install")
        self.assertNotIn("[build-system]", text, "a build backend creates a distribution")

    def test_package_imports_without_being_installed(self):
        import importlib
        import sys

        src = str(ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        mod = importlib.import_module("linear_hermes.plugin_api")
        self.assertTrue(hasattr(mod, "register_plugin_surface"))


class ComposeTests(unittest.TestCase):
    """The Compose project derives from the service name; renaming it orphans the
    running container and an upgrade dies with "port is already allocated"."""

    def test_service_name_is_the_stable_one(self):
        text = read("docker-compose.yml")
        self.assertIn("linear-hermes-agent:", text)
        self.assertNotIn("hermes-linear-agent:", text)

    def test_stability_is_documented_next_to_it(self):
        text = read("docker-compose.yml")
        self.assertIn("port is already allocated", text)


class ManifestTests(unittest.TestCase):
    def test_kind_is_valid(self):
        kind = re.search(r"^kind:\s*(\S+)", read("plugin.yaml"), re.M).group(1)
        self.assertIn(kind, VALID_KINDS, "unknown kind is treated as standalone with a warning")

    def test_status_token_is_declared_as_a_secret(self):
        """The review asked for STATUS_TOKEN in the manifest, not only in the code."""
        block = re.search(r"(?ms)^  - name: STATUS_TOKEN\n(.*?)(?=^  - name:|\Z)",
                          read("plugin.yaml"))
        self.assertIsNotNone(block, "STATUS_TOKEN must be declared in optional_env")
        self.assertIn("secret: true", block.group(1))
        self.assertIn("password: true", block.group(1))


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
