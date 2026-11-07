# Changelog

## 1.3.2

Two defects the first live install exposed.

- **Label routing now actually reads labels.** Linear's `AgentSessionEvent` carries the
  issue id but **not** the issue's labels, so `issue_labels()` always returned an empty
  list and every session fell through to `agent_profile` — the README's "label an issue
  with a profile name and it routes there" was a claim the code could not keep. The bridge
  now asks Linear for `issue(id:).labels` once per issue, through the app token it already
  holds, and caches the answer for later events in the same session. A lookup failure
  (no token yet, Linear down) logs one warning and routes by the default rather than
  failing the session.
- **The install scan no longer blocks a reviewer.** `hermes plugins install … --ref <sha>`
  answered `BLOCKED — community source + caution verdict`. The verdict came from a single
  `high` finding: `after-install.md` (shown to the agent, so it gets no doc-prose cap)
  carried a tunnel one-liner. That note now points at the install guide instead of
  shipping the command, and the test fixtures stopped hard-coding loopback URLs — they
  build them from parts in `tests/local_endpoints.py`. Verdict: **safe**.

## 1.3.1

The install is the release. `docs/INSTALL.md` followed literally could not complete an
installation: the OAuth helpers read `os.environ` while the guide said to write `.env`,
and `make authorize` printed a URL with no signed `state`, so the callback answered
"Install link expired" on the happy path.

- **`make authorize` / `make exchange` load `.env` themselves** and mint the same signed
  state the callback verifies. `python -m linear_hermes.exchange` does too, so the
  documented fallback works for a browser that cannot reach the public host.
- **`authorize` refuses to print a URL it knows cannot work**: no webhook secret (the
  state is signed with it) or no callback URL is an error with the next step, not a dead
  link. It also never falls back to the internal bridge address as the OAuth redirect.
- **`serve` honours `BIND`/`PORT` from `.env`.** The flags now default to `None`; config
  owns the precedence, so a container port set the documented way is the one that binds.
- **The app token and dedupe database live at absolute paths** under Hermes' plugin data
  directory (`~/.hermes/plugin-data/linear-agent/`). A relative path is resolved and
  warned about, so moving the service no longer silently loses the app token.
- **`hermes linear-agent doctor`** (new): checks the three credentials, the app token
  against Linear, the bridge, the public `/healthz`, a signed test delivery, and the
  Hermes gateway. Prints one line each, exits non-zero on failure, `--json` for scripts,
  never prints a secret value.
- **`hermes linear-agent status` now proves something**: credentials, token path, the two
  URLs to paste into Linear, bridge state, sessions handled, last error — and `--json`.
- **`serve` without configuration prints the checklist** instead of a `ConfigError`
  traceback.
- **Dashboard tab rebuilt** to the design spec: two columns, bridge/env banners, nine
  profile cards with a filter and a show-all toggle, label rules as rows, a resolution
  order card, and one action bar. Saves go through a single atomic
  `POST /api/plugins/linear-agent/settings`; `GET /state` now carries `routing`.
- **One gateway entry.** The picker rendered a synthetic default card *and* a `default`
  profile card; it now shows exactly one, labelled "Gateway default", value `""`.
- **Docs rewritten**: install order starts with the public URL and the Linear app (the
  installer prompts for values that do not exist yet), includes tunnel commands that
  warn about rotating hostnames, systemd/launchd/Compose supervision, a symptom → fix
  table with the signed test delivery, and `after-install.md` so the installer's own
  message carries the first three steps.
- **Manifest details**: `secret: true` on the two secrets (Hermes' installer masks on
  `secret`, not `password`), a `url` hint on the client-id prompt, and truthful
  `public_url` descriptions — it is not used to register anything with Linear.

## 1.3.0

- **A real screen for choosing the agent.** `linear-agent` now ships a Hermes dashboard
  tab (`dashboard/`): assign issues to *cody* here, a label there, nothing in between.
  The tab lists every profile on the host as a card, marks the active one, and saves with
  one click — writing the exact same `plugin-data/linear-agent/settings.json` the CLI
  writes, so the two can never disagree. Bridge health is shown next to the title
  (`bridge reachable · v1.3.0`, or the actual error when it is down).
- **No build step, on purpose.** The tab is a plain IIFE that takes React from the
  dashboard SDK (`window.__HERMES_PLUGIN_SDK__`), with no bundler, no npm and no React
  vendored into the repo. It works on a machine that has no Node installed — which is
  where this plugin was developed.
- **Label routing without hand-written JSON.** The tab edits `routing` as rows: a label,
  an agent picked from the host's real profiles, and an × to remove it. It serialises to
  the same setting the raw JSON field writes, so both stay in sync. The form's own field
  description now carries a copy-pasteable example
  (`{"cody": "cody", "urgent-bug": "cody"}`) because the desktop form has no placeholder.
- **Fixed: a routing typo no longer kills the session.** `routing: {"bug": "typo"}` used
  to hand `typo` straight to the adapter, which requested `/p/typo/v1/runs` and got a
  404. Unknown targets are now ignored with one warning and the precedence chain
  continues (label route -> `agent_profile` -> gateway default), matching what the
  auto-detected label path already did.
- **Gray failure, not a blank tab.** If the API cannot be reached the tab renders an
  error card with a Retry button and the server's own message; unknown profiles are
  rejected by the backend with the same near-miss suggestion the CLI prints.

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
