# 90-second demo shot list

Goal: show the identity switch — the reply is authored by **Hermes**, not by the human.
Record at 1440x900 or larger so the author avatar and name are legible.

| # | Seconds | Shot | On-screen |
|---|---|---|---|
| 1 | 0–8 | Linear board, issue **"Add CSV export to reports"** open | Narration: "Hermes isn't a user in Linear. Until now." |
| 2 | 8–16 | Click **Assign** → the agent **Hermes** appears in the people picker | The picker is the proof: an agent, not a person |
| 3 | 16–24 | Issue shows `Hermes` in the delegate slot; assignee stays human | "Humans keep ownership. Agents do the work." |
| 4 | 24–34 | **thought** activity appears in-thread, under 10 s | "Picked this up — reading the issue now." |
| 5 | 34–50 | Terminal (small window, corner): `hermes linear-agent status` shows sessions 1, gateway running | "The bridge is just a service. Signature-checked, deduped." |
| 6 | 50–58 | Optional: heartbeat thought on a long run | "Still working — reading the code, not stuck." |
| 7 | 58–78 | **response** activity lands; scroll so the author is visible: **Hermes**, app avatar | The money shot. Hold 2 seconds. |
| 8 | 78–88 | Open the same thread in a second browser as a *different* user | "Everyone sees the agent's identity, not mine." |
| 9 | 88–90 | Cut to repo URL + "MIT, zero dependencies" | End card |

## Beats you must not miss

1. **The people picker** (shot 2) — that is the feature.
2. **The author name on the reply** (shot 7) — hold it, zoom if the UI is small.
3. **Ten seconds** between assignment and the first activity (shots 3→4) — do it live,
   do not time-lapse it; the responsiveness rule is part of the pitch.

## Prep before recording

- Pre-create the issue with a short, visually interesting body.
- Warm the model so the run is under ~45 s; otherwise you will record the heartbeat.
- Hide bookmarks bar, notifications, and any personal hostname or tunnel URL.
- Terminal font ≥ 16 pt for legibility on a phone screen.

## Voiceover (short version)

> "Linear agents can be assigned work and answer as themselves. This is Hermes:
> a signature-verified bridge, a ten-second acknowledgement, and a reply that says
> **Hermes** — not the person who set it up. Install it with one command; it ships its
> own service, tools and setup skill."
