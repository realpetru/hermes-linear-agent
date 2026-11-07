# Installing the Hermes Linear agent

Written for the operator. Every step below is a click in Linear's UI or one command in the
terminal — there is **no public API to create a Linear OAuth application**, so the
first five minutes must be done by a human with workspace-admin rights.

You will need: workspace **admin** on the target Linear workspace, and the deployment
`.env` open in an editor.

---

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

## 3. Put the credentials in the deployment `.env`

On the host that runs the service:

```bash
cd /path/to/hermes-linear-agent
cp .env.example .env
chmod 600 .env
$EDITOR .env
```

Fill in:

| Key | Value |
|---|---|
| `LINEAR_WEBHOOK_SECRET` | the signing secret from step 2.3 |
| `LINEAR_CLIENT_ID` | from step 1 |
| `LINEAR_CLIENT_SECRET` | from step 1 |
| `LINEAR_REDIRECT_URI` | the **exact** callback URL registered in step 1; it is sent in the authorize request and the token exchange, and a mismatch gives `redirect_uri` |
| `HERMES_GATEWAY_URL` | `http://127.0.0.1:8642` — wherever your Hermes API server listens |
| `HERMES_GATEWAY_TOKEN` | the Hermes `API_SERVER_KEY` — ask @hermes to inject it at deploy, do not paste it in chat |
| `HEARTBEAT_SECONDS` | optional, default `45`: emit an interim "still working" thought on long runs; `0` disables |
| `LINEAR_MAX_SKEW_SECONDS` | optional, default `0` (off): reject deliveries whose `webhookTimestamp` is further away than this. Off by default because Linear retries at 1 min / 1 h / 6 h, which would otherwise be rejected as replays |

`LINEAR_API_KEY` is a **dev fallback only**. Activities posted with a personal key
appear under your own name, which is the exact problem phase 2 exists to fix — so a
production install should run with an app token and no `LINEAR_API_KEY`.

## 4. Install the app into your workspace

Installing with `actor=app` is what creates the *app user* — the agent's own
identity. Without it you get a normal user login instead.

```bash
make authorize
```

That prints a URL of the form:

```
https://linear.app/oauth/authorize?client_id=...&redirect_uri=...&response_type=code
  &scope=read,write,app:assignable,app:mentionable&actor=app
```

Open it as a **workspace admin** and approve. Linear redirects to your callback URL,
and the service answers that redirect with a page that says **"Hermes installed"**
(or shows the exact error). No copy-paste needed when the service is reachable.

If the redirect lands somewhere the service cannot see it (for example a laptop
behind NAT), the code is still in the address bar. Exchange it manually:

```bash
# on the host, from the repo:
make exchange CODE=paste_the_code_or_full_redirect_url

# inside the running container (scripts/ is in the image too):
docker exec <container> python -m linear_hermes.exchange "paste_the_code"
```

Tokens land in `data/linear-tokens.json` with mode `0600` and refresh automatically
five minutes before expiry.

## 5. Verify

```bash
make test          # no network, no secrets
make dev           # loads .env (mode 0600), serves http://127.0.0.1:8650/healthz
```

`make dev` reads `.env` from the repo root, so the credentials written in step 3 are the
credentials the service sees — no exporting by hand.

Then, in Linear:

1. Open any issue and **assign it to `Hermes`** (this sets the issue's *delegate*,
   not its assignee — you stay the owner).
2. Within 10 seconds you should see a `thought` activity: "Picked this up — reading
   the issue now."
3. When the run finishes, the final answer appears as a `response` activity **posted
   by Hermes**, not by you.

If step 2 never happens, the session is marked unresponsive — check the service log
for a signature rejection.

## 6. Rotating secrets

- **Client secret:** Linear → application page → rotate; update
  `LINEAR_CLIENT_SECRET`; restart the service. Stored tokens keep working until
  their refresh fails, then `make authorize` again.
- **Webhook signing secret:** Linear → webhook settings → rotate; update
  `LINEAR_WEBHOOK_SECRET`; restart. Until you do, every delivery is rejected with
  `401` and nothing runs.

## 7. What breaks if Linear changes the agent API

| Symptom | File to look at |
|---|---|
| activities rejected as invalid shape | `src/linear_hermes/linear.py` (`content.type` rules) |
| nothing spawns on mention | `src/linear_hermes/handler.py` (`should_handle`) |
| every webhook 401s | `src/linear_hermes/signature.py` (header/encoding) |
| runs start but never report | `src/linear_hermes/adapters.py` (`/v1/runs` contract) |

Linear publishes the full agent webhook schema SDL at
`https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql`
and documents the flow at https://linear.app/developers/agent-interaction.
