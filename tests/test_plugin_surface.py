"""The tests the old suite lacked: plugin registration and tool dispatch.

`hermes plugins validate` stubs register_skill and never dispatches a handler, so it
reported "capability probe OK" while two shipped bugs made the plugin unusable:
  B1 register_skill was given SKILL.md *contents* (it wants a Path)
  B2 handlers were `lambda **kwargs` but the registry calls handler(args, **kwargs)
Both are covered below by a StrictCtx that mirrors the real signatures.
"""
import inspect
import unittest
from pathlib import Path

from linear_hermes import plugin_api


class StrictCtx:
    """Mirrors hermes_cli.plugins_content.register_skill and register_tool's contract."""

    def __init__(self):
        self.skills = []
        self.tools = []
        self.cli = []

    def register_tool(self, name, toolset, schema, handler, **_kw):
        self.tools.append((name, handler))

    def register_skill(self, name, path, description="", frontmatter=None):
        # real code does: path = Path(path); if not path.exists(): raise FileNotFoundError
        self.skills.append((name, Path(path)))

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli.append((name, setup_fn, handler_fn))

    def get_config(self):
        return {}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.ctx = StrictCtx()
        plugin_api.register_plugin_surface(self.ctx)

    def test_skill_is_registered_with_an_existing_path(self):
        """B1: passing file contents raised FileNotFoundError('... at ---')."""
        self.assertEqual(len(self.ctx.skills), 1)
        name, path = self.ctx.skills[0]
        self.assertEqual(name, "linear-agent")
        self.assertIsInstance(path, Path)
        self.assertTrue(path.exists(), "SKILL.md path must exist: %s" % path)
        self.assertTrue(str(path).endswith("SKILL.md"))

    def test_cli_registers_even_if_a_skill_is_broken(self):
        """B1 fallout: one failing surface must not swallow the CLI command."""

        class BrokenSkillCtx(StrictCtx):
            def register_skill(self, *a, **k):
                raise FileNotFoundError("SKILL.md not found at ---")

        ctx = BrokenSkillCtx()
        plugin_api.register_plugin_surface(ctx)
        self.assertEqual([c[0] for c in ctx.cli], ["linear-agent"])
        self.assertEqual(len(ctx.tools), 2)

    def test_tools_are_dispatched_like_the_real_registry(self):
        """B2: registry calls entry.handler(args, **kwargs)."""
        handlers = dict(self.ctx.tools)
        for name in ("linear_agent_status", "linear_agent_authorize_url"):
            result = handlers[name]({}, task_id="t1")
            self.assertIsInstance(result, str)
            self.assertIn("{", result)

    def test_status_handler_accepts_a_real_args_dict(self):
        handlers = dict(self.ctx.tools)
        out = handlers["linear_agent_status"]({"unused": 1})
        self.assertIn("reachable", out)

    def test_cli_is_registered(self):
        self.assertEqual([c[0] for c in self.ctx.cli], ["linear-agent"])
        self.assertTrue(callable(self.ctx.cli[0][1]))


class AgentCommandTests(unittest.TestCase):
    """`hermes linear-agent agent set|list` — switching with zero config editing."""

    def setUp(self):
        import os
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in
                     ("LINEAR_AGENT_SETTINGS_DIR", "LINEAR_AGENT_PROFILE", "HERMES_HOME")}
        os.environ["LINEAR_AGENT_SETTINGS_DIR"] = self.tmp
        os.environ.pop("LINEAR_AGENT_PROFILE", None)
        home = os.path.join(self.tmp, "home")
        for name in ("default", "cody", "appy"):
            os.makedirs(os.path.join(home, "profiles", name), exist_ok=True)
        os.environ["HERMES_HOME"] = home

    def tearDown(self):
        import os

        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stdout

        from linear_hermes import plugin_api

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = plugin_api.main_for_tests(list(argv))
        return code, buf.getvalue()

    def test_set_writes_the_plugin_config(self):
        code, out = self.run_cli("agent", "set", "cody")
        self.assertEqual(code, 0)
        from linear_hermes.settings import stored_profile

        self.assertEqual(stored_profile(), "cody")
        self.assertIn("no restart", out)

    def test_set_validates_and_suggests(self):
        code, out = self.run_cli("agent", "set", "coddy")
        self.assertEqual(code, 2)
        self.assertIn("Did you mean", out)
        from linear_hermes.settings import stored_profile

        self.assertEqual(stored_profile(), "")

    def test_set_requires_a_name(self):
        code, _ = self.run_cli("agent", "set")
        self.assertEqual(code, 2)

    def test_list_marks_the_active_profile(self):
        self.run_cli("agent", "set", "cody")
        code, out = self.run_cli("agent", "list")
        self.assertEqual(code, 0)
        self.assertIn("* cody", out)
        self.assertIn("  appy", out)

    def test_env_var_wins_and_is_reported(self):
        import os

        os.environ["LINEAR_AGENT_PROFILE"] = "appy"
        self.run_cli("agent", "set", "cody")
        code, out = self.run_cli("agent", "list")
        self.assertIn("LINEAR_AGENT_PROFILE", out)


class RealHermesShapeTests(unittest.TestCase):
    """When Hermes is importable, assert our call shape matches the real API."""

    def test_real_register_skill_expects_a_path(self):
        try:
            from hermes_cli.plugins import PluginContext
        except Exception:  # noqa: BLE001 - standalone checkout
            self.skipTest("hermes not importable here")
        params = list(inspect.signature(PluginContext.register_skill).parameters)
        self.assertEqual(params[:3], ["self", "name", "path"])
        self.assertNotIn("content", " ".join(params).lower())


if __name__ == "__main__":
    unittest.main()
