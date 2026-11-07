"""Dashboard routing editor: the API behind the label -> profile rows."""
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "dashboard" / "plugin_api.py"


def load_module():
    spec = importlib.util.spec_from_file_location("linear_agent_dashboard_api_routing", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoutingApiTests(unittest.TestCase):
    def setUp(self):
        self.api = load_module()
        self.home = tempfile.mkdtemp()
        for name in ("default", "cody", "appy", "studio"):
            os.makedirs(os.path.join(self.home, "profiles", name), exist_ok=True)
        self.settings_dir = tempfile.mkdtemp()
        self._env = {
            k: os.environ.get(k)
            for k in ("HERMES_HOME", "LINEAR_AGENT_SETTINGS_DIR", "LINEAR_AGENT_ROUTING")
        }
        os.environ["HERMES_HOME"] = self.home
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.settings_dir
        os.environ.pop("LINEAR_AGENT_ROUTING", None)
        self.api.bridge_status = lambda url=None, timeout=3.0: {"reachable": True, "version": "1.3.0"}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def settings_file(self):
        return os.path.join(self.settings_dir, "settings.json")

    def test_state_carries_the_routing_map(self):
        state = self.api.build_state()
        self.assertIn("routing", state)
        self.assertEqual(state["routing"], {})
        self.assertFalse(state["routing_from_env"])

    def test_save_writes_the_cli_setting(self):
        state = self.api.apply_routing({"bug": "cody", "Feature": "studio"})
        self.assertEqual(state["routing"], {"bug": "cody", "feature": "studio"})
        with open(self.settings_file()) as fh:
            self.assertEqual(json.load(fh)["routing"], {"bug": "cody", "feature": "studio"})

    def test_saved_file_is_0600(self):
        self.api.apply_routing({"bug": "cody"})
        self.assertEqual(stat.S_IMODE(os.stat(self.settings_file()).st_mode), 0o600)

    def test_unknown_target_is_rejected_with_a_suggestion(self):
        with self.assertRaises(ValueError) as ctx:
            self.api.apply_routing({"bug": "coddy"})
        self.assertIn("Did you mean", str(ctx.exception))
        self.assertFalse(os.path.exists(self.settings_file()))

    def test_empty_map_clears_routing(self):
        self.api.apply_routing({"bug": "cody"})
        state = self.api.apply_routing({})
        self.assertEqual(state["routing"], {})

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.apply_routing(["bug", "cody"])
        with self.assertRaises(ValueError):
            self.api.apply_routing(None)

    def test_blank_label_or_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.apply_routing({"  ": "cody"})
        with self.assertRaises(ValueError):
            self.api.apply_routing({"bug": "   "})

    def test_env_json_wins_over_the_file(self):
        self.api.apply_routing({"bug": "cody"})
        os.environ["LINEAR_AGENT_ROUTING"] = '{"bug": "studio"}'
        state = self.api.build_state()
        self.assertEqual(state["routing"], {"bug": "studio"})
        self.assertTrue(state["routing_from_env"])

    def test_broken_env_json_falls_back_to_the_file(self):
        self.api.apply_routing({"bug": "cody"})
        os.environ["LINEAR_AGENT_ROUTING"] = "{not json"
        self.assertEqual(self.api.build_state()["routing"], {"bug": "cody"})

    def test_the_bridge_reads_what_the_editor_wrote(self):
        self.api.apply_routing({"bug": "studio"})
        from linear_hermes.settings import stored_routing

        self.assertEqual(stored_routing(), {"bug": "studio"})


try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "FastAPI test client not installed")
class RoutingRouteTests(RoutingApiTests):
    def client(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(self.api.router, prefix="/api/plugins/linear-agent")
        return TestClient(app)

    def test_post_routing(self):
        r = self.client().post("/api/plugins/linear-agent/routing", json={"routing": {"bug": "cody"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["routing"], {"bug": "cody"})

    def test_post_routing_unknown_target_is_400(self):
        r = self.client().post("/api/plugins/linear-agent/routing", json={"routing": {"bug": "coddy"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Did you mean", r.json()["detail"])

    def test_post_routing_without_key_is_400(self):
        r = self.client().post("/api/plugins/linear-agent/routing", json={})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
