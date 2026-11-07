## linear-agent is installed. It is not configured yet.

Order matters — the plugin needs values that only exist after two other steps.

1. **Give it a public URL.** Linear will not POST to localhost. On a laptop:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8650
   # => https://calm-fox-1234.trycloudflare.com
   ```

   Keep that terminal open. A quick tunnel changes hostname on restart — fine for a
   test, not for daily use.

2. **Create the Linear app** at <https://linear.app/settings/api/applications/new>
   (workspace admin). Callback URL `https://HOST/oauth/callback`; enable Webhooks with
   `https://HOST/webhook` and tick **Agent session events**. Copy the client ID, client
   secret and webhook signing secret.

3. **Store them in Hermes:**

   ```bash
   hermes plugins install realpetru/hermes-linear-agent --force   # re-prompts
   # or:
   $EDITOR "$(hermes config env-path)"
   ```

4. **Run and prove it:**

   ```bash
   hermes linear-agent serve        # terminal 2
   hermes linear-agent authorize    # terminal 3; approve as workspace admin
   hermes linear-agent doctor       # every line must say ok
   ```

Full walk-through: docs/INSTALL.md in the repository.
