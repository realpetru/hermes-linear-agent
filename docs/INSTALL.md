# Installing the Hermes Linear agent

Written for the operator. Every step below is a click in Linear's UI or one command in the
terminal — there is **no public API to create a Linear OAuth application**, so the
first five minutes must be done by a human with workspace-admin rights.

You will need: workspace **admin** on the target Linear workspace, and the deployment
`.env` open in an editor.

---

## 0. A public HTTPS URL

Linear posts webhooks from its own servers, so the bridge must be reachable over public
HTTPS. Linear refuses `localhost`, `127.0.0.1` and private address ranges — there is no
"just test it locally" option for the webhook half.

Everything below needs this URL, so do it first.

**Quick tunnel (first install, laptop).**

```bash
# terminal 1 — leave running
cloudflared tunnel --url http://127.0.0.1:8650
```

It prints `https://<random-words>.trycloudflare.com`. That is your public base. Note it
and keep the terminal open.

> The hostname changes every time you restart a quick tunnel. When it changes, you must
> edit **two** fields in Linear: the app's Callback URL and the Webhook URL. For anything
> past a test, use a named Cloudflare tunnel with a DNS record, an ngrok reserved domain,
> or a VPS with Caddy — the hostname must survive a reboot.

**If the tunnel is not on the bridge machine:** the bridge must listen on more than
loopback. Use `hermes linear-agent serve --bind 0.0.0.0` (or set `BIND=0.0.0.0` in
`.env` — `serve` honours it), and let a firewall be the boundary.

**Two URLs, two jobs.** They are often the same host, but they do not have to be:

| URL | Who connects | Localhost allowed? |
|---|---|---|
| `https://HOST/webhook` | Linear's servers | **No** |
| `https://HOST/oauth/callback` | the admin's browser, once | Yes, if the browser is on the bridge host |

Whatever you choose must be byte-identical to what you type into the app page.
`localhost` and `127.0.0.1` are different strings to Linear.

## 1. Create the OAuth application

1. Open **https://linear.app/settings/api/applications/new** (Settings → API →
   Applications → **New application**). You must be a workspace admin.
2. **Name:** `Hermes` (or anything you like — it becomes the agent's display name)
   - This name is what people see when they @mention the agent, so it is the
     agent's identity in the workspace. Do not name it `Bot` or `Hermes Bot`.
3. **Description:** `Hermes — your agent. Delegates work, answers in-thread.`
4. **Developer name / URL:** your own details. Optional.
5. **Callback URLs** — add both, one per line:
   ```
   https://YOUR-PUBLIC-HOST/oauth/callback
   http://localhost:8650/oauth/callback
   ```
   The localhost one is what lets you finish a dev install without the tunnel.
6. Leave the app **unpublished** — this is a private, workspace-only agent.

> The client ID and client secret are shown after you save. Copy them now; the
> secret is not shown again.

## 2. Turn on webhooks and the agent-session category

Still on the application page:

1. Enable **Webhooks**.
2. **Webhook URL:**
   ```
   https://YOUR-PUBLIC-HOST/webhook
   ```
   (Use `http://localhost:8650/webhook` for a local-only test, with a tunnel URL
   instead of localhost if Linear must reach your laptop.)
3. Copy the **signing secret** Linear generates. This is `LINEAR_WEBHOOK_SECRET`.
4. Under webhook event categories, tick **Agent session events**.
   - ⚠️ This is the switch that makes the agent real: as soon as it is enabled,
     workspace members start seeing Agent Session UI in Linear even if the service
     is not running yet.
   - *Inbox notifications* and *Permission changes* are optional and useful.
5. Save.

## 3. Install the plugin and store the credentials

```bash
hermes plugins install realpetru/hermes-linear-agent
hermes plugins enable linear-agent
```

The installer prompts for the three values from steps 1–2 and writes them to
`$(hermes config env-path)`. Paste them there; this is the only credential store the
`hermes linear-agent …` commands use.

If you skipped the prompts, add them now:

```bash
$EDITOR "$(hermes config env-path)"
```

```dotenv
LINEAR_CLIENT_ID=…
LINEAR_CLIENT_SECRET=…
LINEAR_WEBHOOK_SECRET=…
LINEAR_AGENT_PUBLIC_URL=https://YOUR-PUBLIC-HOST
```

A repo `.env` is **not** used by `hermes linear-agent serve`; it exists only for Docker
Compose and `make dev`. If you use one, its values win over Hermes' — including empty
ones, so do not leave blank keys for values you set in Hermes.

`LINEAR_API_KEY` is a development fallback only: activities posted with it appear under
your own name, which is the bug this project exists to fix. Leave it empty.

## 4. Install the app into your workspace

```bash
hermes linear-agent serve        # terminal 2
hermes linear-agent authorize    # terminal 3
```

Open the printed URL **as a workspace admin** and approve. You should land on
**"Hermes installed"**; the app token is written `0600` and refreshed automatically.

The URL's `client_id` and `redirect_uri` must match the app page. If the browser cannot
reach the public host, finish with the redirect instead:

```bash
# open the authorize URL, approve, then paste the whole redirect URL you land on:
hermes linear-agent exchange "https://YOUR-PUBLIC-HOST/oauth/callback?code=…"
```

`hermes linear-agent exchange` reads the same Hermes credential store, so it works for a
catalogue install with no repo checkout. Inside a container, the equivalent is
`python -m linear_hermes.exchange "<redirect URL>"`.

If the callback pages says **"Install link expired"**, the link is older than 15 minutes
or the bridge restarted with a different webhook secret — re-run `authorize` and use the
new URL immediately.

## 5. Verify before you assign anything

```bash
hermes linear-agent serve        # terminal 2
hermes linear-agent doctor       # terminal 3
```

`doctor` prints one line per check and exits non-zero if any fails:

- the three credentials are present (names only, never values);
- the app token exists, is mode 0600, and Linear accepts it;
- the bridge answers on its bind address;
- `$LINEAR_AGENT_PUBLIC_URL/healthz` answers from the public internet;
- a signed test delivery to `$LINEAR_AGENT_PUBLIC_URL/webhook` returns 200 (a 401 here
  means `LINEAR_WEBHOOK_SECRET` is not this webhook's secret);
- the Hermes gateway answers with `HERMES_GATEWAY_TOKEN`.

It ends by printing the two URLs that must be on the Linear app page. If any line is not
`ok`, stop here — nothing you do in Linear will fix it.

Then assign an issue to **Hermes** (delegate, not assignee). You should see a `thought`
within ten seconds and a `response` under Hermes' name when the run finishes.

## 5b. Upgrading an existing deployment

The Compose service name is intentionally stable (`linear-hermes-agent`); do not rename
it, because the Compose project derives from it and a rename orphans the running
container.

If you upgraded from 1.2.0 (which did rename it) and `docker compose up -d` failed with
`Bind for 0.0.0.0:8650 failed: port is already allocated`:

```bash
docker compose down        # removes the old container; ./data (your tokens) is untouched
docker compose up -d --build
```

## 6. Keep it running

`hermes linear-agent serve` is a foreground process. Close the terminal, log out, or
reboot, and the agent stops answering while still appearing in Linear's delegate picker.
Pick one of these before you rely on it.

### 6a. Docker Compose (simplest, survives reboot)

```bash
git clone https://github.com/realpetru/hermes-linear-agent
cd hermes-linear-agent
cp .env.example .env && chmod 600 .env && $EDITOR .env
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` brings it back after a reboot. The token lives in `./data`,
not in the container.

If the bridge must reach a Hermes API server on the host, `127.0.0.1` inside the
container is the container. The shipped compose file already adds
`host.docker.internal:host-gateway`, so set:

```dotenv
HERMES_GATEWAY_URL=http://host.docker.internal:8642
```

The API server must also listen on a reachable interface (`API_SERVER_HOST=0.0.0.0`);
it defaults to loopback. On Linux, `network_mode: host` avoids both problems at the cost
of `ports:`.

### 6b. systemd (Linux, native install)

Write `~/.config/systemd/user/hermes-linear-agent.service`:

```ini
[Unit]
Description=Hermes Linear agent bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h
EnvironmentFile=%h/.hermes/.env
Environment=LINEAR_TOKEN_PATH=%h/.hermes/plugin-data/linear-agent/linear-tokens.json
Environment=STATE_DB=%h/.hermes/plugin-data/linear-agent/state.sqlite3
ExecStart=%h/.local/bin/hermes linear-agent serve --bind 127.0.0.1 --port 8650
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Replace `%h/.local/bin/hermes` with the output of `command -v hermes`. Then:

```bash
mkdir -p ~/.hermes/plugin-data/linear-agent
# if you already authorized from a checkout, move the token so it is not lost:
[ -f data/linear-tokens.json ] && mv data/linear-tokens.json ~/.hermes/plugin-data/linear-agent/
systemctl --user daemon-reload
systemctl --user enable --now hermes-linear-agent
loginctl enable-linger "$USER"          # keep running after logout
journalctl --user -u hermes-linear-agent -f
```

The two `Environment=` lines matter: they pin absolute paths, so a service started from
a different directory still finds the token (1.3.1 also defaults to an absolute path
under Hermes' plugin data directory, but being explicit is better).

### 6c. launchd (macOS)

`~/Library/LaunchAgents/com.hermes.linear-agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hermes.linear-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string><string>-lc</string>
    <string>set -a; . "$HOME/.hermes/.env"; exec "$HOME/.local/bin/hermes" linear-agent serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/hermes-linear-agent.log</string>
  <key>StandardErrorPath</key><string>/tmp/hermes-linear-agent.err</string>
</dict></plist>
```

```bash
launchctl load -w ~/Library/LaunchAgents/com.hermes.linear-agent.plist
```

Note that launchd does not read `~/.hermes/.env` by itself; the `sh -lc` wrapper is what
loads it.

## 7. When nothing happens

Work down this list; each row is a different failure with a different fix.

| What you see | Cause | Do this |
|---|---|---|
| `serve` prints `linear-agent is not configured yet` | credentials were skipped at install | `hermes linear-agent doctor`; add them to `$(hermes config env-path)` |
| Linear shows nothing; the bridge log has **no** `POST /webhook` line | Linear never reached you: wrong URL, quick-tunnel hostname changed, or the category is not ticked | `curl -fsS "$LINEAR_AGENT_PUBLIC_URL/healthz"` from outside; re-check the webhook URL in the app |
| Log: `rejected webhook: bad signature` | `LINEAR_WEBHOOK_SECRET` is not the secret of *that* webhook (recreated? rotated?) | copy it again from app → Webhooks; run `doctor` |
| Log: `event ignored: not_agent_session` | deliveries arrive but not agent-session events: wrong category, or the issue was **assigned** rather than delegated | tick **Agent session events**; use the delegate field |
| Log: `no Linear credential yet` | `authorize` never completed | `hermes linear-agent authorize` |
| A `thought` appears, then nothing | the Hermes run failed; the error is also posted as an `error` activity in the thread | read the thread; `journalctl --user -u hermes-linear-agent` or `docker compose logs` |
| `gateway /v1/runs -> HTTP 401` | `HERMES_GATEWAY_TOKEN` ≠ `API_SERVER_KEY` | make them identical, restart |
| `Connection refused … :8642` | Hermes API server is not enabled, or (Docker) not reachable from the container | enable `API_SERVER_ENABLED=true`; in Docker set `host.docker.internal` + `API_SERVER_HOST=0.0.0.0` |
| Agent never appears in the delegate picker | install was not `actor=app`, or the app is not installed in this workspace | re-open `hermes linear-agent authorize` as an admin; confirm `actor=app` in the URL |
| Callback page: **"Install link expired"** | the state is missing or older than 15 minutes, or the bridge restarted with a different webhook secret | rerun `hermes linear-agent authorize`; use the new URL immediately |
| Callback page: **"Exchange failed … redirect_uri"** | `LINEAR_REDIRECT_URI` is not byte-identical to a Callback URL on the app page | copy it from Linear; `localhost` ≠ `127.0.0.1` |
| `agentActivityCreate` rejected / activities fail | the token is a **user** token, not an app token (authorize without `actor=app`, or app not installed) | redo authorize; confirm `actor=app` |
| Activities appear under **your** name | `LINEAR_API_KEY` is set and there is no app token | complete authorize; remove `LINEAR_API_KEY` |

### The signed test delivery

This proves the public route, the secret, and that the process Linear will hit is the one
you are looking at — without waiting for Linear's retry schedule (1 min / 1 h / 6 h).
`hermes linear-agent doctor` runs exactly this; the manual form:

```bash
BODY='{"action":"created","type":"AgentSessionEvent","webhookTimestamp":0}'
SIG=$(python3 - "$LINEAR_WEBHOOK_SECRET" "$BODY" <<'PY'
import hashlib, hmac, sys
print(hmac.new(sys.argv[1].encode(), sys.argv[2].encode(), hashlib.sha256).hexdigest())
PY
)
curl -sS -o /dev/null -w 'webhook: %{http_code}\n' \
  -X POST "$LINEAR_AGENT_PUBLIC_URL/webhook" \
  -H 'Content-Type: application/json' \
  -H "Linear-Signature: $SIG" \
  --data-raw "$BODY"
```

`200` means route and secret are correct (the log will show `event ignored:
no_session_id` — expected). `401` means the secret is wrong. `000` means the URL is not
reachable from where you ran it.

## 8. Rotating secrets

- **Client secret:** Linear → application page → rotate; update
  `LINEAR_CLIENT_SECRET`; restart the service. Stored tokens keep working until
  their refresh fails, then `make authorize` again.
- **Webhook signing secret:** Linear → webhook settings → rotate; update
  `LINEAR_WEBHOOK_SECRET`; restart. Until you do, every delivery is rejected with
  `401` and nothing runs.

## 9. What breaks if Linear changes the agent API

| Symptom | File to look at |
|---|---|
| activities rejected as invalid shape | `src/linear_hermes/linear.py` (`content.type` rules) |
| nothing spawns on mention | `src/linear_hermes/handler.py` (`should_handle`) |
| every webhook 401s | `src/linear_hermes/signature.py` (header/encoding) |
| runs start but never report | `src/linear_hermes/adapters.py` (`/v1/runs` contract) |

Linear publishes the full agent webhook schema SDL at
`https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql`
and documents the flow at https://linear.app/developers/agent-interaction.
