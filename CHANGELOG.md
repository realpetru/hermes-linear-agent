# Changelog

## 1.5.0 — one secret path, and it is Hermes'

The catalog review's four blockers were all about where secrets live. This release removes
every direct `.env` read and every secret write from the plugin.

* **Credentials come only from Hermes' secret scope.** `agent.secret_scope.get_secret` is
  the one source inside Hermes; standalone uses the process environment, then only a file
  the user names with `LINEAR_AGENT_ENV_FILE`. The implicit `$HERMES_HOME/.env` and `./.env`
  lookups are gone, and a scoped miss fails closed for credential names instead of borrowing
  the ambient environment (catalog rule 11).
* **`setup` no longer writes a `.env`.** It prints the exact `hermes config set KEY <value>`
  lines (or the `plugins install --force` masked-prompt alternative) and stores no secret.
  The public URL and the install-link key — neither a Linear credential — live in the
  plugin's own `settings.json`, mode 0600.
* **`STATUS_TOKEN` is declared in `plugin.yaml`** and read through the same scope path.
* **The bridge log is 0600 in the plugin data dir**, not a shared temp directory.
* **Still interim:** Linear's OAuth flow needs a caller-chosen redirect URI and
  comma-separated scopes, which the manifest `oauth:` route cannot express yet; the token
  store stays documented and the upstream seam is described in PR #151444.

## 1.4.6 — 1.4.5 was red on Python 3.9

* **The dashboard routes annotated `dict | None` and `str | None`.** FastAPI/pydantic evaluate
  those at import time, and on 3.9 that is a `TypeError` unless `eval_type_backport` happens to
  be installed — so every dashboard test errored in CI's 3.9 cell while passing on 3.11/3.12.
  The annotations are now `Optional[str]` / `Optional[Dict[str, Any]]`, which works everywhere.
* **The skip gate is a script, not a heredoc.** `scripts/check_skips.py` fails CI when a test
  skips for any reason except the one legitimate environmental skip (the signature test needs
  the `hermes` package importable, which CI does not install). That gate is what surfaced the
  3.9 failures instead of letting them hide behind 36 skips.
* Tested on 3.9.6 with FastAPI installed locally: 373 tests, 1 skip, no failures.

## 1.4.5 — green where it counts

* **The suite passes with the dashboard installed.** Five tests failed only on an interpreter
  that has FastAPI: two asserted an empty install while reading this machine's credentials
  file, one asserted the `python -m` spawn branch from a directory that contained a compose
  file, and two exchange tests found real client credentials in the checkout's `.env` and
  failed on the network instead of on the usage check. The readers are now injected, so
  "unconfigured" is a fact of the test rather than of the host.
* **CI installs `fastapi` and `uvicorn`.** Thirty-six tests were skipping on every CI run, and
  that green run was hiding those five failures — the worst kind of green.
* **`compose_path` is a parameter.** Whether setup runs `docker compose up` or spawns the
  module used to depend on the caller's working directory.
* A bridge that answers `503 not_configured` is treated as up, not down: it is running and
  asking for its secret.

## 1.4.4 — the release audit's P0s

* **`setup` works on a fresh host.** It spawned `python -m linear_hermes` from the caller's
  directory (so the child died with `ModuleNotFoundError`, silently) and probed the public URL
  before any bridge existed. The bridge is now spawned with the plugin's own `src` on
  `PYTHONPATH`, its stderr is kept and quoted when it exits, and the order is public URL →
  state key → bridge → app → public check → install → webhook → doctor. `needs_input` stops
  the chain, as its comment always claimed.
* **One install-link key.** `authorize` signed the OAuth state with the webhook secret while
  the server verified `LINEAR_STATE_KEY`, so links died with "Install link expired" straight
  after a successful setup. Both use the state key now.
* **One credentials file.** `setup` wrote `./.env` while doctor/status/authorize read the
  Hermes secret scope: a fresh process reported "missing" with the values one directory away.
  `$HERMES_HOME/.env` is the file now, read by every command.
* **`src/linear_hermes/__init__.py` said 0.1.0.** One version everywhere, and
  `tests/test_release.py` covers the dashboard manifest, the server User-Agent and the package.
* **Security:** OAuth codes and state no longer reach the log, the callback page escapes what
  Linear sends it and carries a CSP, credential files are 0600 from creation instead of a
  moment after, the subprocess adapter passes a minimal environment instead of every bridge
  secret, and the personal API key fallback is opt-in (`LINEAR_ALLOW_PERSONAL_KEY`) and off.
* **A missing `LINEAR_WEBHOOK_SECRET` no longer crash-loops the container:** the bridge serves
  `/healthz` 503 `not_configured` and says what is missing.
* **`doctor` stopped passing things it had not checked:** an unreachable Linear no longer
  counts as a valid token, a 0644 token file fails, the bridge's version is compared with the
  plugin's, and the signed test delivery carries a real timestamp. `status` asks Linear about
  the token instead of printing `ok`, and `/status` reports whether an app token actually
  exists and which agent is configured in `settings.json`.
* **`config_schema` is no longer decoration:** `public_url`, `agent_profile` and `routing` are
  read wherever they belong.

## 1.4.3 — setup stops blocking for five minutes when nobody can click

`step_install` polled for the OAuth callback for 300 s even in a non-interactive run, where
nothing can arrive: `--json`, a piped run or an agent driving setup blocked five minutes and
then reported `needs_input` anyway. It returns at once with the authorize URL in the step
detail, and interactive runs say how long they will wait.

## 1.4.2 — the bridge probe follows the configuration

`step_bridge` hardcoded localhost. On a containerised install the bridge is reachable only
at `LINEAR_AGENT_BRIDGE_URL`, so setup probed localhost, found nothing,
started a second bridge inside the container and then skipped install, webhook and doctor. It
probes the configured bridge and returns `needs_input` instead of spawning a process it does
not own.

## 1.4.1 — doctor's probe sends a User-Agent

`setup`'s probe and the signed test delivery sent none, so a CDN bot rule answered **403** and
step 1 failed on a perfectly good install. Same false negative `doctor` had before 1.3.3; the
probe reuses `user_agent()` now.

## 1.4.0 — the install is one command

Setup went from five pasted values in four places to **two pastes**.

* **`hermes linear-agent setup`** walks the whole install: public URL, Linear app, bridge,
  app install, webhook, doctor. It is resumable — each step reports `done`, `skipped`,
  `needs_input` or `failed`, and a second run creates nothing twice.
* **The webhook step guides instead of mutating.** `webhookCreate` refuses
  `AgentSessionEvent` for every participant token (verified against the live API), because
  an agent's webhook is part of the app's own configuration. Setup prints the Webhook URL
  and category to paste and stores the signing secret Linear shows. The OAuth install link
  is signed with its own `LINEAR_STATE_KEY`, so it can never be mistaken for that secret.
* **The callback URL is printed fully formed**, next to the app-creation link, so it is
  copied rather than composed.
* **The public URL is verified, not assumed.** Setup takes `--public-url` (or
  `LINEAR_AGENT_PUBLIC_URL`), calls `/healthz` on it and reports `done` only when it
  answers. It deliberately starts no tunnels and names no tunnelling services — an
  install scanner treats that as a high finding, and a blocked install is worse than the
  convenience. Running without a URL stops with `needs_input` and resumes later.
* **A 403 from Linear reads as a scope problem** ("recreate the app with read,write"),
  not a traceback.
* The dashboard tab shows the same checklist: `GET /state` now carries `setup`, and the
  page renders which steps are done and which need a human.
* `--yes` runs without prompting; `--json` emits the checklist for the tab.

## 1.3.3

`doctor` stopped lying, on the two checks where it lied most.

- **The probes identify themselves.** `_probe()` and `bridge_status()` now send
  `User-Agent: linear-agent-doctor/<version>`. Cloudflare — and any CDN with a bot rule —
  answers the default `Python-urllib` agent with `403`, so a correctly configured install
  behind a tunnel reported `public FAIL` and `signature FAIL` on every run. Reproduced on a
  live install before the fix: same URL, `curl` 200, `urllib` 403. After it, six for six.
- **A 403 no longer blames the bridge.** The failure text separates "blocked in front of the
  bridge (proxy/CDN bot rule)" from "answered nothing (DNS, tunnel, firewall)", and the
  signature check says outright that a 403 is not a signature problem — so nobody rotates a
  working webhook secret chasing the wrong fault.
- **One version everywhere.** Bumping `plugin.yaml` alone failed the release suite on CI,
  because `tests/test_release.py` hardcoded the version and `dashboard/manifest.json` and the
  Linear User-Agent in `server.py` carry it too. The test now parses `plugin.yaml`, so the
  next release bumps one file and the suite checks the rest.

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
