#!/usr/bin/env python3
"""Self-check for the linear-agent plugin.

Why this exists: PyPI's `hermes-agent` predates the `plugins validate` subcommand,
and installing Hermes from source in CI drags in a large dependency tree that
occasionally conflicts. This script performs the manifest/registration checks that
do not require a Hermes install, so the repo always has a real gate:

  manifest   plugin.yaml parses and carries name/version/description
  requires   requires_hermes parses
  schema     config_schema is a mapping of field -> mapping with a type
  env        requires_env / optional_env entries are UPPER_SNAKE
  loadable   the repo root imports and exposes register(ctx)
  probe      register() runs against a recording context without raising
  declared   declared tools/hooks match what register() actually registers
  security   no eval/exec, no dynamic core rebinding

Exit codes: 0 = all checks passed, 1 = at least one failed.

Needs one YAML parser: PyYAML (`pip install pyyaml`) or ruamel.yaml.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _load_yaml(path):
    try:
        import yaml  # type: ignore

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML  # type: ignore

        with open(path, encoding="utf-8") as fh:
            return YAML(typ="safe").load(fh)
    except ImportError:
        raise SystemExit(
            "selfcheck needs a YAML parser: pip install pyyaml (or ruamel.yaml)"
        )


def _manifest():
    data = _load_yaml(os.path.join(ROOT, "plugin.yaml"))
    if not isinstance(data, dict):
        raise AssertionError("plugin.yaml is not a mapping")
    return data


def _env_names(manifest):
    names = []
    for key in ("requires_env", "optional_env"):
        entries = manifest.get(key) or []
        if isinstance(entries, dict):
            entries = list(entries)
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("name")
                if not name:
                    raise AssertionError(f"{key} entry without a name: {entry!r}")
                names.append(str(name))
            else:
                names.append(str(entry))
    return names


class Check:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn

    def run(self):
        try:
            return True, self.fn() or "ok"
        except Exception as exc:  # noqa: BLE001 - failing is the point
            if os.environ.get("SELFCHECK_TRACE"):
                traceback.print_exc()
            return False, f"{type(exc).__name__}: {exc}"


def c_manifest():
    m = _manifest()
    for field in ("name", "version", "description"):
        if not str(m.get(field) or "").strip():
            raise AssertionError(f"manifest missing {field}")
    return f"{m['name']} {m['version']}"


def c_requires():
    spec = _manifest().get("requires_hermes")
    if not spec:
        raise AssertionError("requires_hermes missing")
    if not re.match(r"^[<>=!~\s\d.]+$", str(spec)):
        raise AssertionError(f"requires_hermes does not look like a version spec: {spec!r}")
    return str(spec)


def c_schema():
    schema = _manifest().get("config_schema")
    if not schema:
        return "no config_schema declared (allowed)"
    if not isinstance(schema, dict):
        raise AssertionError("config_schema must be a mapping")
    for field, spec in schema.items():
        if not isinstance(spec, dict) or "type" not in spec:
            raise AssertionError(f"config_schema.{field} must be a mapping with a type")
    return f"{len(schema)} field(s)"


def c_env():
    names = _env_names(_manifest())
    for name in names:
        if not UPPER_SNAKE.match(name):
            raise AssertionError(f"{name!r} is not UPPER_SNAKE")
    return f"{len(names)} declared"


class Recorder:
    """Stand-in for Hermes' PluginContext that only records, never executes."""

    def __init__(self):
        self.tools, self.hooks, self.skills, self.cli_commands = [], [], [], []
        self.config = {}

    def register_tool(self, name=None, **_kw):
        self.tools.append(name)

    def register_hook(self, event=None, **_kw):
        self.hooks.append(event)

    def register_skill(self, name=None, path=None, **_kw):
        if path is None:
            raise AssertionError("register_skill called without a path")
        if not os.path.exists(str(path)):
            raise AssertionError(f"register_skill path does not exist: {path!r}")
        self.skills.append(name)

    def register_cli_command(self, name=None, **_kw):
        self.cli_commands.append(name)

    def register_middleware(self, *_a, **_kw):
        pass


def c_loadable():
    src = os.path.join(ROOT, "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)
    entry = os.path.join(ROOT, "__init__.py")
    if not os.path.exists(entry):
        raise AssertionError("no __init__.py entrypoint at the repo root")
    spec = importlib.util.spec_from_file_location("selfcheck_entry", entry)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "register"):
        raise AssertionError("entrypoint has no register(ctx)")
    globals()["_ENTRY"] = mod
    return "register() present"


def c_probe():
    mod = globals().get("_ENTRY")
    if mod is None:
        raise AssertionError("loadable check did not run")
    rec = Recorder()
    mod.register(rec)
    globals()["_REC"] = rec
    return f"tools={rec.tools} skills={rec.skills} cli={rec.cli_commands}"


def c_declared():
    rec = globals().get("_REC")
    if rec is None:
        raise AssertionError("probe check did not run")
    m = _manifest()
    declared_tools = sorted(str(t) for t in (m.get("provides_tools") or []))
    actual_tools = sorted(str(t) for t in rec.tools)
    if declared_tools != actual_tools:
        raise AssertionError(f"declared tools {declared_tools} != registered {actual_tools}")
    declared_hooks = sorted(str(h) for h in (m.get("provides_hooks") or []))
    if declared_hooks != sorted(str(h) for h in rec.hooks):
        raise AssertionError(f"declared hooks {declared_hooks} != registered {rec.hooks}")
    if not rec.skills:
        raise AssertionError("no skill registered (register_skill must receive a real path)")
    if not rec.cli_commands:
        raise AssertionError("no CLI command registered")
    return f"{len(actual_tools)} tools, {len(rec.skills)} skill, {len(rec.cli_commands)} cli"


DANGEROUS = (
    (re.compile(r"(?<![\w.])eval\s*\("), "eval()"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec()"),
    (re.compile(r"sys\.modules\[[^\]]+\]\s*="), "sys.modules rebinding"),
    (re.compile(r"setattr\(\s*(hermes|gateway|tools)\b"), "setattr on Hermes modules"),
)
SKIP_DIRS = {".git", "tests", "scripts", "__pycache__", ".venv", "docs"}


def c_security():
    hits = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            if os.path.abspath(path) == os.path.abspath(__file__):
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for pattern, label in DANGEROUS:
                if pattern.search(text):
                    hits.append(f"{os.path.relpath(path, ROOT)}: {label}")
    if hits:
        raise AssertionError("; ".join(hits))
    return "no eval/exec/core rebinding"


CHECKS = [
    Check("manifest", c_manifest),
    Check("requires", c_requires),
    Check("schema", c_schema),
    Check("env", c_env),
    Check("loadable", c_loadable),
    Check("probe", c_probe),
    Check("declared", c_declared),
    Check("security", c_security),
]


def main():
    print(f"selfcheck: {ROOT}")
    failures = 0
    for check in CHECKS:
        ok, detail = check.run()
        print(f"  {'ok  ' if ok else 'FAIL'}  {check.name:9s} {detail}")
        if not ok:
            failures += 1
    print()
    if failures:
        print(f"selfcheck FAILED ({failures} check(s))")
        return 1
    print("selfcheck passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
