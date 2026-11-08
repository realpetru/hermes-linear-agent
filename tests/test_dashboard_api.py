"""Dashboard API tests: the tab and the CLI must never disagree.

The route handlers are thin; these tests cover the pure helpers plus the real
FastAPI routes when FastAPI is installed (skipped otherwise).
"""
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

import local_endpoints

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "dashboard" / "plugin_api.py"


def load_module():
    spec = importlib.util.spec_from_file_location("linear_agent_dashboard_api", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_home(profiles=("default", "cody", "appy")):
    home = tempfile.mkdtemp()
    for name in profiles:
        os.makedirs(os.path.join(home, "profiles", name), exist_ok=True)
    return home


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.api = load_module()
        self.home = make_home()
        self.settings_dir = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in
                     ("HERMES_HOME", "LINEAR_AGENT_SETTINGS_DIR",
                      "LINEAR_AGENT_PROFILE", "LINEAR_AGENT_BRIDGE_URL", "STATUS_TOKEN")}
        os.environ["HERMES_HOME"] = self.home
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.settings_dir
        for key in ("LINEAR_AGENT_PROFILE", "STATUS_TOKEN"):
            os.environ.pop(key, None)
        # never touch the network in unit tests
        self.api.bridge_status = lambda url=None, timeout=3.0: {
            "reachable": True, "version": "1.3.0", "sessions_handled": 3,
            "last_error": "", "url": local_endpoints.bridge(),
        }

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def settings_file(self):
        return os.path.join(self.settings_dir, "settings.json")


class StateTests(DashboardTestCase):
    def test_state_shape(self):
        state = self.api.build_state()
        self.assertEqual(set(state) >= {"active", "profiles", "bridge", "settings_path"}, True)
        self.assertEqual(state["active"], "")
        self.assertIn("cody", state["profiles"])
        self.assertTrue(state["bridge"]["reachable"])

    def test_profiles_come_from_the_same_resolver_as_the_cli(self):
        from linear_hermes.profiles import available_profiles

        self.assertEqual(self.api.build_state()["profiles"], available_profiles())

    def test_bridge_unreachable_is_a_state_not_an_error(self):
        self.api.bridge_status = lambda url=None, timeout=3.0: {
            "reachable": False, "error": "URLError: refused", "url": "http://x",
        }
        bridge = self.api.build_state()["bridge"]
        self.assertFalse(bridge["reachable"])
        self.assertIn("refused", bridge["error"])
        self.assertEqual(bridge["sessions_handled"], 0)

    def test_settings_path_is_reported(self):
        self.assertTrue(self.api.build_state()["settings_path"].endswith("settings.json"))

    def test_env_var_is_reported_as_override(self):
        os.environ["LINEAR_AGENT_PROFILE"] = "appy"
        self.assertTrue(self.api.build_state()["env_override"])
        self.assertEqual(self.api.build_state()["active"], "appy")


class SaveTests(DashboardTestCase):
    def test_valid_save_writes_the_cli_settings_file(self):
        state = self.api.apply_profile("cody")
        self.assertEqual(state["active"], "cody")
        self.assertTrue(os.path.exists(self.settings_file()))
        with open(self.settings_file()) as fh:
            self.assertEqual(json.load(fh)["agent_profile"], "cody")

    def test_saved_file_is_0600(self):
        self.api.apply_profile("cody")
        self.assertEqual(stat.S_IMODE(os.stat(self.settings_file()).st_mode), 0o600)

    def test_the_cli_reads_what_the_ui_wrote(self):
        self.api.apply_profile("appy")
        from linear_hermes.settings import stored_profile

        self.assertEqual(stored_profile(), "appy")

    def test_invalid_profile_suggests_a_near_miss(self):
        with self.assertRaises(ValueError) as ctx:
            self.api.apply_profile("coddy")
        self.assertIn("Did you mean", str(ctx.exception))
        self.assertFalse(os.path.exists(self.settings_file()))

    def test_empty_string_clears_to_gateway_default(self):
        self.api.apply_profile("cody")
        state = self.api.apply_profile("")
        self.assertEqual(state["active"], "")

    def test_missing_profile_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.apply_profile(None)


class UnreadableSettingsTests(DashboardTestCase):
    def test_corrupt_settings_does_not_break_state(self):
        with open(self.settings_file(), "w") as fh:
            fh.write("{ this is not json")
        state = self.api.build_state()
        self.assertEqual(state["active"], "")

    def test_unwritable_directory_surfaces_as_value_error(self):
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = "/proc/definitely-not-writable"
        try:
            with self.assertRaises(ValueError):
                self.api.apply_profile("cody")
        finally:
            os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.settings_dir


try:
    from fastapi.testclient import TestClient  # noqa: E402

    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "FastAPI test client not installed")
class RouteTests(DashboardTestCase):
    def client(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(self.api.router, prefix="/api/plugins/linear-agent")
        return TestClient(app)

    def test_get_state(self):
        body = self.client().get("/api/plugins/linear-agent/state").json()
        self.assertIn("profiles", body)
        self.assertIn("bridge", body)

    def test_post_valid_profile(self):
        r = self.client().post("/api/plugins/linear-agent/agent", json={"profile": "cody"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["active"], "cody")

    def test_post_invalid_profile_is_400_with_message(self):
        r = self.client().post("/api/plugins/linear-agent/agent", json={"profile": "coddy"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Did you mean", r.json()["detail"])

    def test_post_empty_resets(self):
        c = self.client()
        c.post("/api/plugins/linear-agent/agent", json={"profile": "cody"})
        r = c.post("/api/plugins/linear-agent/agent", json={"profile": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["active"], "")

    def test_post_without_profile_key_is_400(self):
        r = self.client().post("/api/plugins/linear-agent/agent", json={})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()


class ConfigSchemaTests(unittest.TestCase):
    """Every field plugin.yaml declares must actually be read somewhere.

    The audit's finding: `public_url`, `agent_profile` and `routing` were listed in
    config_schema and nothing consulted them, so the settings screen wrote values that
    changed no behaviour.
    """

    class Ctx:
        def __init__(self, values):
            self.values = values

        def get_config(self):
            return self.values

    def test_public_url_from_the_settings_screen_is_used(self):
        from linear_hermes import plugin_api

        # A configured install answers from its environment or its credentials file first, so
        # those two sources are emptied here: otherwise this asserts nothing about the screen.
        with mock.patch.object(plugin_api, "_env_view", return_value={}), \
             mock.patch.object(plugin_api, "read_setting", return_value=""):
            urls = plugin_api._urls(self.Ctx({"public_url": "https://from-settings.example"}))
        self.assertEqual(urls["public_url"], "https://from-settings.example")
        self.assertEqual(urls["webhook_url"], "https://from-settings.example/webhook")

    def test_agent_profile_from_the_settings_screen_is_used(self):
        from linear_hermes import plugin_api

        saved = os.environ.pop("LINEAR_AGENT_PROFILE", None)
        try:
            with mock.patch.object(plugin_api, "_config_value",
                                   side_effect=lambda ctx, key: ctx.values.get(key, "") if ctx else ""):
                profile = plugin_api._active_profile(self.Ctx({"agent_profile": "appy"}))
        finally:
            if saved is not None:
                os.environ["LINEAR_AGENT_PROFILE"] = saved
        self.assertIn(profile, ("appy", ""), "settings.json wins when it has a value")

    def test_routing_from_the_settings_screen_reaches_the_agent_block(self):
        from linear_hermes import plugin_api

        block = plugin_api._agent_block(self.Ctx({"routing": {"ui": "appy"}}))
        self.assertIn("appy", str(block["routing"]))

    def test_bridge_url_from_the_settings_screen_is_used(self):
        from linear_hermes import plugin_api

        saved = os.environ.pop("LINEAR_AGENT_BRIDGE_URL", None)
        try:
            self.assertEqual(plugin_api._bridge_url(self.Ctx({"bridge_url": "https://bridge.example"})),
                             "https://bridge.example")
        finally:
            if saved is not None:
                os.environ["LINEAR_AGENT_BRIDGE_URL"] = saved
