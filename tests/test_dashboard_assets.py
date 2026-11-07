"""Static guarantees for the dashboard half: exact SDK surface, no build, theme-native."""

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


class SdkContractTests(unittest.TestCase):
    """The page must use the SDK surface from web/src/plugins/registry.ts exactly."""

    def setUp(self):
        self.js = (DASH / "dist" / "index.js").read_text(encoding="utf-8")

    def test_no_bare_fetch(self):
        # bare fetch drops the loopback token / gated cookie and renders a 401 blank tab
        self.assertIsNone(re.search(r"\bfetch\s*\(", self.js), "bare fetch( found; use SDK.fetchJSON")
        self.assertNotIn("window.fetch", self.js)

    def test_uses_fetchjson(self):
        self.assertIn("SDK.fetchJSON", self.js)

    def test_uses_the_sdk_toast_pair(self):
        self.assertIn("hooks.useToast", self.js)
        self.assertIn("showToast(", self.js)
        self.assertIn("toast: toastState.toast", self.js)

    def test_uses_sdk_cn(self):
        self.assertIn("utils.cn", self.js)

    def test_checks_the_sdk_contract_version(self):
        self.assertIn("sdkVersion", self.js)

    def test_registers_through_the_registry(self):
        self.assertIn('registry.register("linear-agent"', self.js)
        self.assertNotIn("__HERMES_PLUGINS__ =", self.js)  # never redefine the host global

    def test_hooks_come_from_the_sdk(self):
        for hook in ("useState", "useEffect", "useCallback", "useMemo"):
            self.assertIn("hooks.%s" % hook, self.js)

    def test_react_is_never_bundled(self):
        self.assertNotIn("cdn.", self.js)
        self.assertIsNone(re.search(r"import\s+React", self.js))

    def test_no_bundler_syntax(self):
        self.assertTrue(self.js.lstrip().startswith("/**"))
        for banned in ("import ", "require(", "export "):
            self.assertNotIn(banned, self.js, "module syntax would need a build step")


class AssetsTests(unittest.TestCase):
    def setUp(self):
        self.js = (DASH / "dist" / "index.js").read_text(encoding="utf-8")
        self.css = (DASH / "dist" / "style.css").read_text(encoding="utf-8")

    def test_no_external_assets(self):
        for text in (self.js, self.css):
            self.assertIsNone(
                re.search(r"https?://(?!127\.0\.0\.1)[a-z]", text), "external URL in the tab"
            )
        self.assertNotIn("@import", self.css)

    def test_calls_only_its_own_api_prefix(self):
        self.assertIn('"/api/plugins/linear-agent"', self.js)

    def test_degrades_when_components_are_missing(self):
        for name in ("Card", "CardContent", "Button", "Input"):
            self.assertIn("C.%s ||" % name, self.js)

    def test_theme_variables_only(self):
        for token in ("--card", "--border", "--accent", "--foreground", "--muted-foreground"):
            self.assertIn("var(%s" % token, self.css)
        theme_only = re.findall(r"(?<!var\()#[0-9a-fA-F]{3,6}", self.css)
        for value in theme_only:
            self.assertIn(
                value,
                ("#111", "#fff", "#16a34a", "#d97706", "#b45309", "#e5e7eb", "#6b7280", "#2563eb", "#f3f4f6"),
                "hardcoded colour %s (use a var fallback)" % value,
            )

    def test_status_colours_are_semantic(self):
        self.assertIn("var(--success", self.css)
        self.assertIn("var(--warning", self.css)


if __name__ == "__main__":
    unittest.main()
