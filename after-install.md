## linear-agent is installed. It is not configured yet.

Order matters — the plugin needs values that only exist after two other steps.

1. **Give it a public URL.** Linear will not POST to localhost, so expose the bridge on
   public HTTPS first — a tunnel is the quickest way on a laptop. docs/INSTALL.md §0 has
   the exact command, the URL shape it prints, and the warning that a quick tunnel gets a
   new hostname on every restart.

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
