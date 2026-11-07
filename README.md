# linear-agent — make Hermes a first-class Linear agent

Assign a Linear issue to **Hermes**, or `@mention` it in a comment, and Hermes answers
in the thread **as its own agent identity** — not as you.

That last part is the whole point. The usual webhook trick has a flaw: an automation
that posts through a personal API key appears to be written by a human. Linear's agent
platform fixes it with an app actor (`actor=app`), and this plugin implements that
platform properly: signature-verified webhooks, agent activities, session lifecycle,
and a Hermes run behind it.

```
   Linear issue            ┌──────────── linear-agent bridge ────────────┐
   assign → Hermes         │                                             │
   or @mention             │  POST /webhook                              │
        │                  │   ├─ verify Linear-Signature (HMAC-SHA256)  │
        │   AgentSession   │   ├─ dedupe delivery-id + session-id        │
        └─────────────────►│   ├─ thought  "picked this up" (≤10s)       │
                           │   ├─ run  ──► Hermes gateway /v1/runs       │
                           │   └─ response  (posted as the app user)     │
                           └─────────────────────────────────────────────┘
                                        │
                          Linear thread shows: thought → progress → answer
```

## 60-second quickstart

```bash
hermes plugins install realpetru/hermes-linear-agent
hermes plugins enable linear-agent

hermes linear-agent authorize     # prints the OAuth URL — open as workspace admin
hermes linear-agent serve         # start the bridge (one process, stdlib only)
hermes linear-agent status        # reachability, counters, missing config, active agent
hermes linear-agent agent         # which Hermes agent answers Linear, and what else exists
```

Then assign any issue to the agent in Linear. You should see a `thought` within ten
seconds and the answer posted by **Hermes**, not by you.

Requires Python 3.9+ and nothing else: the bridge has **zero third-party
dependencies**. Point it at a public HTTPS URL with `LINEAR_AGENT_PUBLIC_URL`
(a tunnel is fine — Linear refuses localhost and private hosts).

## What you get

| Surface | What it does |
|---|---|
| `hermes linear-agent serve` | runs the webhook bridge |
| `hermes linear-agent status` | reachability, sessions handled, last error, missing env |
| `hermes linear-agent authorize` | builds the `actor=app` install URL |
| `linear_agent_status` tool | the same status, for the agent itself to check mid-conversation |
| `linear_agent_authorize_url` tool | the install URL, so the agent can hand it to a user |
| bundled `linear-agent` skill | the setup procedure, loaded into any session |

## Design

**Two halves, one install.** The bridge is a normal long-running service; the plugin
is what makes Hermes aware of it. `hermes linear-agent serve` covers the first half so
a user never has to wire up a second deployment.

**One adapter to swap the runner.** Everything that talks to Hermes lives in
`src/linear_hermes/adapters.py`: `GatewayAdapter` posts to the Hermes API server
(`/v1/runs`) and polls; `SubprocessAdapter` shells out to `hermes chat -q`. Pick with
`HERMES_ADAPTER`.

**No credential at boot is normal.** A fresh install has no Linear token yet, so the
server starts, logs one warning, and fails only when Linear is actually called, with a
message telling the operator to run `authorize`.

## Which agent answers

By default the bridge runs whatever agent the Hermes gateway serves by default. If you
run more than one agent (profiles), say which one answers Linear:

```bash
hermes linear-agent agent                       # who answers now, and what else exists
export LINEAR_AGENT_PROFILE=cody                # Linear is handled by the cody agent
```

That is the whole setting. An empty value means "let the gateway decide", which is
exactly what an existing install already does — nothing changes unless you set it.

Check it anytime with `hermes linear-agent agent`, which prints the active agent and
every profile available on the host. A name that does not exist fails at startup with
a list of valid ones and a near-miss suggestion, not a stack trace.

### Routing specific issues elsewhere

If you want one workspace where different issues go to different agents, add a label map:

```bash
export LINEAR_AGENT_ROUTING='{"cody": "cody", "content": "studio"}'
```

Precedence, highest first:

1. **a label route** — an issue carrying the `cody` label is answered by the `cody` agent
2. **`LINEAR_AGENT_PROFILE`** — the default for everything else
3. **the gateway default** — when neither is set

First matching label wins, in the order you wrote the map. Labels are matched
case-insensitively. A payload without labels simply falls through to the default.

One honest limitation: the profile decides **which agent thinks**, not what Linear sees.
The actor in the thread is always the Linear app, because that is what `actor=app`
means. Two profiles are two brains behind one Linear identity.

## Security model

- **Two** public routes, both guarded:
  - `POST /webhook` is useless without a valid `Linear-Signature`: hex HMAC-SHA256 over
    the **raw** body, constant-time compared. A bad or missing signature is rejected
    with `401` before JSON parsing and before any thread is spawned. There is no
    unauthenticated path that can start a run.
  - `GET /oauth/callback` changes state (it exchanges a code and writes the token
    file), so it requires a signed `state` value minted by `make authorize` and valid
    for 15 minutes. Without a matching state it answers `400` and touches nothing.
- **Idempotency** on both the delivery id and the session id, so Linear's retries
  (1 min, 1 h, 6 h) can never double-spawn.
- **Concurrency cap** with a fixed slot pool; over-capacity sessions get a message in
  the thread instead of a queue that silently never drains. Stale slots are swept so a
  crashed worker cannot cap the service forever.
- **Secrets stay in Hermes.** Credentials are declared in the manifest and read from
  the environment Hermes provides. Nothing is written into the repo, and startup logs
  print `set`/`MISSING`, never a value.
- **Honest limitation:** `LINEAR_API_KEY` exists as a development fallback. When it is
  set without an app token, the bridge posts activities with it and they appear under
  that person's identity — the exact bug this project exists to fix. It is declared as a
  fallback, logged loudly at startup, and should be empty in a real install. There is no
  way to post as the agent without the Linear app token from the `actor=app` install.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `LINEAR_CLIENT_ID` | yes | OAuth app client id |
| `LINEAR_CLIENT_SECRET` | yes | OAuth app client secret |
| `LINEAR_WEBHOOK_SECRET` | yes | webhook signing secret |
| `LINEAR_AGENT_PUBLIC_URL` | for a real install | public HTTPS base URL (builds the authorize URL only) |
| `LINEAR_REDIRECT_URI` | must match the app | exact callback URL sent in the authorize request **and** the token exchange; a mismatch is the usual `redirect_uri` install failure |
| `LINEAR_TOKEN_PATH` | no | where the app token is stored, default `data/linear-tokens.json`, mode 0600 |
| `LINEAR_API_KEY` | no, discouraged | personal-key fallback; posts under a human identity — see the limitation note |
| `LINEAR_AGENT_BRIDGE_URL` | no | default `http://127.0.0.1:8650` |
| `HERMES_GATEWAY_URL` / `HERMES_GATEWAY_TOKEN` | for the gateway adapter | Hermes API server |
| `LINEAR_AGENT_PROFILE` | no | Hermes agent profile that answers Linear; empty = gateway default |
| `LINEAR_AGENT_ROUTING` | no | JSON label → profile map; beats `LINEAR_AGENT_PROFILE` |
| `HERMES_ADAPTER` | no | `gateway` (default) or `subprocess` |
| `HEARTBEAT_SECONDS` | no | default 45; interim "still working" thought, `0` disables |
| `MAX_CONCURRENCY` | no | default 3 |
| `BIND` / `PORT` | no | default `127.0.0.1:8650` |

## Deployment note

If your tunnel runs on a **different machine** from the container, do not bind
loopback: publish the port on the LAN and let the firewall be the boundary, or the
public URL returns nothing.

## What breaks if Linear changes their agent API

| Symptom | File |
|---|---|
| activities rejected as invalid | `src/linear_hermes/linear.py` (`content.type` rules) |
| nothing spawns on mention | `src/linear_hermes/handler.py` (`should_handle`) |
| every webhook 401s | `src/linear_hermes/signature.py` |
| runs start but never report | `src/linear_hermes/adapters.py` (`/v1/runs`) |

Schema reference:
`https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql`

Two known sharp edges, documented so nobody chases them:

- `AgentActivity { type }` fails GraphQL validation. The kind lives in the activity
  **content**, not as a queryable field.
- Activity types are five: `thought`, `action`, `response`, `elicitation`, `error`.
  `prompt` is user-generated only; an agent cannot send it.

## Tests

```bash
make test     # 60+ tests, no network, no secrets
```

## License

MIT.
