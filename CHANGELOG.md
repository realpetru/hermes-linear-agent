# Changelog

## 1.2.1

Fixes for real, long-lived installs — each of these only shows up outside a throwaway
home directory.

- **`hermes plugins enable` works on PM-managed installs.** The plugin shipped a
  `pyproject.toml` with a `[project]` table, so Hermes' plugin admission attempted an
  editable install into its managed venv; on installs whose venv predates the plugin
  workspace that failed inside `uv` before the plugin ever loaded. The plugin is
  stdlib-only and Hermes imports `__init__.py` directly, so **the packaging metadata is
  gone** — installs now need no dependency step at all.
- **Upgrading no longer fails with "port is already allocated".** The Compose service is
  back to its original name `linear-hermes-agent`, because the Compose *project* derives
  from it and renaming orphaned the running container. **Upgrading from 1.2.0:**
  ```bash
  docker compose down          # removes the old, renamed container (your ./data volume stays)
  docker compose up -d --build
  ```
- **`/status` no longer leaks state.** It returns only `{"status": "ok"}` to anonymous
  callers. Set `STATUS_TOKEN` and send `Authorization: Bearer <token>` to get the full
  document (session counts, last error, active profile). `/healthz` is unchanged and
  always minimal.

## 1.2.0

Switching agents no longer requires editing configuration.

- **Zero-config label routing.** A label whose name matches a Hermes profile routes
  that issue to that profile. `LINEAR_AGENT_ROUTING` is still supported as an
  override but is **no longer required** for routing.
- **Hot reload.** Settings are re-read per incoming session (5-second cache), so a
  change takes effect on the next issue without restarting the bridge. The switch is
  logged once: `agent profile: appy -> cody`.
- **`hermes linear-agent agent set|list`.** Persists the choice to the plugin's own
  settings file (`$HERMES_HOME/plugin-data/linear-agent/settings.json`, mode 0600)
  with the same validation and near-miss suggestions as the rest of the CLI.
- **The acknowledgement names the agent** ("Picked up by cody — reading the issue
  now"), so routing is visible in the thread.

## 1.1.0

- Plugin surface fixed: `register_skill` takes a path, tool handlers take `(args, **kwargs)`
- Per-agent routing via `LINEAR_AGENT_PROFILE` and `/p/<profile>/v1` gateway paths
- Follow-up prompts in a session run again (dedupe keyed on session + delivery)
- Catalog entry, `kind: standalone`, secret-scope reads, `.env` loading, CSRF state on
  the OAuth callback, replay window, token path inside the volume, redirect URI docs

## 1.0.0

- First release: signature-verified Linear agent bridge, agent activities, OAuth app
  tokens, gateway/subprocess adapters, plugin tools, setup skill
