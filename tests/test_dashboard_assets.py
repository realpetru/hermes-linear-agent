"""Static guarantees for the dashboard half: no build, no external assets, consistent."""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((DASH / "manifest.json").read_text(encoding="utf-8"))

    def test_required_keys(self):
        for key in ("name", "label", "description", "icon", "version", "tab", "entry", "css", "api"):
            self.assertIn(key, self.manifest)

    def test_tab_points_at_a_page(self):
        self.assertEqual(self.manifest["tab"]["path"], "/linear-agent")
        self.assertTrue(self.manifest["tab"]["position"].startswith("after:"))

    def test_version_matches_the_plugin_release(self):
        plugin_yaml = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        match = re.search(r"^version:\s*(\S+)", plugin_yaml, re.M)
        self.assertEqual(self.manifest["version"], match.group(1))

    def test_entry_and_api_files_exist(self):
        self.assertTrue((DASH / self.manifest["entry"]).is_file())
        self.assertTrue((DASH / self.manifest["css"]).is_file())
        self.assertTrue((DASH / self.manifest["api"]).is_file())


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.js = (DASH / "dist" / "index.js").read_text(encoding="utf-8")
        self.css = (DASH / "dist" / "style.css").read_text(encoding="utf-8")

    def test_registers_under_its_plugin_name(self):
        self.assertIn('__HERMES_PLUGINS__.register("linear-agent"', self.js)

    def test_plain_iife_no_bundler(self):
        self.assertTrue(self.js.lstrip().startswith("/**"))
        self.assertIn("(function ()", self.js)
        for banned in ("import ", "require(", "export ", "=>{"):
            self.assertNotIn(banned, self.js, "ESM/bundler syntax would need a build step")

    def test_react_comes_from_the_sdk(self):
        self.assertIn("window.__HERMES_PLUGIN_SDK__", self.js)
        self.assertIn("SDK.React", self.js)
        self.assertNotIn("cdn.", self.js)

    def test_no_external_assets(self):
        for text in (self.js, self.css):
            self.assertNotRegex(text, r"https?://(?!127\.0\.0\.1)[a-z]", "external URL in the tab")
        self.assertNotIn("@import", self.css)

    def test_calls_only_its_own_api_prefix(self):
        self.assertIn('"/api/plugins/linear-agent"', self.js)

    def test_degrades_when_components_are_missing(self):
        for name in ("Card", "Button", "Badge", "Input"):
            self.assertIn("C.%s ||" % name, self.js)

    def test_theme_variables_only(self):
        hardcoded = re.findall(r"(?<!var\()#[0-9a-fA-F]{3,6}", self.css)
        # status colours are allowed, but they must sit behind a theme variable fallback
        for value in hardcoded:
            self.assertIn(value, (
                "#111", "#fff", "#9aa0aa", "#16a34a", "#d97706", "#b45309",
                "#111318", "#e5e7eb", "#f3f4f6", "#6b7280", "#2563eb",
            ), "unexpected hardcoded colour %s" % value)
        for token in ("--card", "--border", "--accent", "--foreground", "--muted-foreground"):
            self.assertIn("var(%s" % token, self.css)


if __name__ == "__main__":
    unittest.main()
