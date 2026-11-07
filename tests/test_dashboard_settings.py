"""POST /settings: one atomic write for agent + routing, and 409 when env owns the value."""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "dashboard" / "plugin_api.py"


def load_module():
    spec = importlib.util.spec_from_file_location("linear_agent_dashboard_settings", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.api = load_module()
        self.home = tempfile.mkdtemp()
        for name in ("default", "cody", "appy", "studio", "fixy", "servery"):
            os.makedirs(os.path.join(self.home, "profiles", name), exist_ok=True)
        self.settings_dir = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in (
            "HERMES_HOME", "LINEAR_AGENT_SETTINGS_DIR", "LINEAR_AGENT_PROFILE", "LINEAR_AGENT_ROUTING",
        )}
        os.environ["HERMES_HOME"] = self.home
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.settings_dir
        os.environ.pop("LINEAR_AGENT_PROFILE", None)
        os.environ.pop("LINEAR_AGENT_ROUTING", None)
        self.api.bridge_status = lambda url=None, timeout=3.0: {"reachable": True, "version": "1.3.1"}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def settings_file(self):
        return os.path.join(self.settings_dir, "settings.json")

    def saved(self):
        with open(self.settings_file()) as fh:
            return json.load(fh)

    # ------------------------------------------------------------------ happy path
    def test_writes_both_in_one_operation(self):
        calls = []
        original = self.api.write_settings

        def counting(values, path=None):
            calls.append(values)
            return original(values, path)

        self.api.write_settings = counting
        try:
            state = self.api.apply_settings({
                "agent_profile": "cody",
                "routing": {"urgent-bug": "fixy", "infra": "servery"},
            })
        finally:
            self.api.write_settings = original

        self.assertEqual(len(calls), 1, "one request must mean one write")
        self.assertEqual(state["active"], "cody")
        self.assertEqual(state["routing"], {"infra": "servery", "urgent-bug": "fixy"})
        self.assertEqual(self.saved()["agent_profile"], "cody")
        self.assertEqual(self.saved()["routing"], {"infra": "servery", "urgent-bug": "fixy"})

    def test_profile_only_leaves_routing_alone(self):
        self.api.apply_settings({"agent_profile": "cody", "routing": {"infra": "servery"}})
        self.api.apply_settings({"agent_profile": "appy"})
        self.assertEqual(self.saved()["routing"], {"infra": "servery"})
        self.assertEqual(self.saved()["agent_profile"], "appy")

    def test_routing_only_leaves_profile_alone(self):
        self.api.apply_settings({"agent_profile": "cody"})
        self.api.apply_settings({"routing": {"infra": "servery"}})
        self.assertEqual(self.saved()["agent_profile"], "cody")

    def test_gateway_default_is_the_empty_string(self):
        state = self.api.apply_settings({"agent_profile": ""})
        self.assertEqual(state["active"], "")
        self.assertEqual(self.saved()["agent_profile"], "")

    # ------------------------------------------------------------------ env conflicts
    def test_env_profile_blocks_the_write_with_409_semantics(self):
        os.environ["LINEAR_AGENT_PROFILE"] = "cody"
        with self.assertRaises(self.api.SettingsConflict):
            self.api.apply_settings({"agent_profile": "appy"})
        self.assertFalse(os.path.exists(self.settings_file()))

    def test_env_routing_blocks_the_write(self):
        os.environ["LINEAR_AGENT_ROUTING"] = '{"bug": "fixy"}'
        with self.assertRaises(self.api.SettingsConflict):
            self.api.apply_settings({"routing": {"infra": "servery"}})

    def test_env_profile_does_not_block_a_routing_only_save(self):
        os.environ["LINEAR_AGENT_PROFILE"] = "cody"
        state = self.api.apply_settings({"routing": {"infra": "servery"}})
        self.assertEqual(state["routing"], {"infra": "servery"})

    # ------------------------------------------------------------------ validation
    def test_unknown_profile_is_rejected_with_a_suggestion(self):
        with self.assertRaises(ValueError) as ctx:
            self.api.apply_settings({"agent_profile": "coddy"})
        self.assertIn("Did you mean", str(ctx.exception))

    def test_unknown_routing_target_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.apply_settings({"routing": {"bug": "nope"}})

    def test_labels_are_normalised_and_duplicates_rejected(self):
        self.assertEqual(self.api.normalise_routing({"  BUG  ": "cody"}), {"bug": "cody"})
        with self.assertRaises(ValueError):
            self.api.normalise_routing({"bug": "cody", "BUG": "appy"})

    def test_routing_limits(self):
        with self.assertRaises(ValueError):
            self.api.normalise_routing({"x" * 51: "cody"})
        with self.assertRaises(ValueError):
            self.api.normalise_routing({"label%d" % i: "cody" for i in range(51)})

    def test_empty_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.apply_settings({})

    # ------------------------------------------------------------------ state contract
    def test_state_carries_the_contract_keys(self):
        state = self.api.build_state()
        for key in ("active", "profiles", "routing", "bridge", "settings_path", "env_override", "env_routing"):
            self.assertIn(key, state)
        self.assertEqual(state["routing"], {})
        self.assertFalse(state["env_routing"])

    def test_settings_file_is_0600(self):
        self.api.apply_settings({"agent_profile": "cody"})
        import stat

        self.assertEqual(stat.S_IMODE(os.stat(self.settings_file()).st_mode), 0o600)


try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "FastAPI test client not installed")
class SettingsRouteTests(SettingsTests):
    def client(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(self.api.router, prefix="/api/plugins/linear-agent")
        return TestClient(app)

    def test_post_settings_ok(self):
        r = self.client().post(
            "/api/plugins/linear-agent/settings",
            json={"agent_profile": "cody", "routing": {"infra": "servery"}},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["active"], "cody")

    def test_post_settings_conflict_is_409(self):
        os.environ["LINEAR_AGENT_PROFILE"] = "cody"
        r = self.client().post("/api/plugins/linear-agent/settings", json={"agent_profile": "appy"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("LINEAR_AGENT_PROFILE", r.json()["detail"])

    def test_post_settings_invalid_is_400(self):
        r = self.client().post("/api/plugins/linear-agent/settings", json={"agent_profile": "coddy"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Did you mean", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
