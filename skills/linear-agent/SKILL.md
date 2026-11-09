---
name: linear-agent
description: Set up and operate the Linear agent bridge so Hermes can be assigned Linear issues and answer in-thread as its own identity.
---

# Linear agent setup

Use this when a user wants Hermes to appear in Linear as an agent they can assign
an issue to or @mention, rather than a webhook that posts as a human.

## What has to exist

1. **A Linear OAuth app** with `actor=app`. Scopes: `read,write,app:assignable,app:mentionable`.
   - Create it in Linear: Settings -> API -> Applications -> New application.
   - Enable **Webhooks** and tick the **Agent session events** category. Ticking it
     makes Agent Session UI appear for workspace members immediately.
2. **A public HTTPS URL** Linear can POST to. Linear refuses localhost and private
   hosts. A tunnel is fine.
3. **Credentials in Hermes**, not in files: `LINEAR_CLIENT_ID`,
   `LINEAR_CLIENT_SECRET`, `LINEAR_WEBHOOK_SECRET`. Store them with
   `hermes config set LINEAR_CLIENT_ID <value>` (and the other two) or re-run
   `hermes plugins install realpetru/hermes-linear-agent --force` for masked prompts.
   The plugin reads them through the profile secret scope; never ask the user to paste
   secrets into a `.env`, and never read one yourself.

## Order of operations

```
hermes linear-agent authorize     # prints the install URL; open as workspace admin
hermes linear-agent serve         # runs the bridge (do not run twice)
hermes linear-agent status        # reachability + counters + missing config
```

Then assign any issue to the agent. Expect a `thought` activity within 10 seconds;
Linear marks a session unresponsive if the first activity is slower than that.

## Things that go wrong

- **Nothing happens on mention.** `hermes linear-agent status` -> if unreachable the
  bridge is not running; if credentials are missing the bridge acknowledges nothing.
- **Webhook 401s.** `LINEAR_WEBHOOK_SECRET` does not match the app's webhook secret.
  The signature is over the raw body, hex HMAC-SHA256.
- **Public URL returns nothing.** The tunnel reaches a different machine than the
  container, so the service must not bind loopback-only.
- **Activity rejected.** `content.type` must be one of `thought`, `action`,
  `response`, `elicitation`, `error`, and each type validates its own fields.

## Never

- Never post activities using a personal API key: they appear under that human's
  name, which is the exact problem this integration solves.
- Never expose the bridge without signature verification.
