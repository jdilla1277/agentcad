# agentcad — Future Ideas

Holding pen for ideas that aren't scoped into the roadmap yet. When one firms up, promote it to `roadmap.md` with a milestone number.

---

## Claude can read incoming feedback

Today feedback from `agentcad feedback` lands in two places (Neon `feedback` table + Discord webhook), neither of which Claude Code can see from a working session. The only channel into the loop is the user pasting a Discord message.

**Idea:** give Claude a first-class way to pull the feedback queue so iteration on friction fixes happens inside the repo, not over copy/paste.

Options to evaluate when picking this up:
- `GET /api/feedback` read endpoint behind the same `x-agentcad-key` header, returning unresolved items. Cheapest.
- `agentcad feedback list` / `agentcad feedback show <id>` CLI that calls the read endpoint. Keeps the agent surface CLI-only, matches the rest of the tool.
- Status field on the row (`new` / `triaged` / `resolved`) so the queue actually drains instead of re-surfacing the same items.

Related: the "Friction queue with viewer-prompt TODO" commit (7c1ffe8) already gestures at this — the viewer-prompt side exists, the read-path does not.
