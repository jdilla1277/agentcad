# M39: Openness & Licensing Strategy — Decision Record

**Status:** Decided
**Date:** 2026-04-02

---

## Decision

agentcad will be **public and source-available** under the **Business Source License (BSL 1.1)**.

---

## License Parameters

| Parameter | Value |
|-----------|-------|
| **Licensor** | agentcad authors |
| **Licensed work** | agentcad (all versions) |
| **Use grant** | Non-production use is permitted. Production use by individuals and organizations is permitted. |
| **Additional use grant** | You may use the Licensed Work in production, provided you do not offer the Licensed Work as a hosted or managed service to third parties. |
| **Change license** | Apache 2.0 |
| **Change date** | 4 years from each release |

---

## Rationale

### Why public?

- Agent tools live or die by adoption. Agents need to `pip install` frictionlessly.
- Public source builds trust — users and enterprises can audit what they're running.
- Enables community contributions, issue reports, and ecosystem integrations.
- `agentcad context` and `agentcad docs` are designed for discoverability; hiding the source contradicts that philosophy.

### Why BSL, not MIT/Apache?

- **The slop fork problem.** In the AI ecosystem, permissive licenses invite low-effort forks that wrap a thin API layer and compete on price before the original author has launched their own commercial offering.
- agentcad's monetization path is a **hosted service** (background jobs, accounts, cloud rendering). BSL protects this by preventing third parties from offering agentcad-as-a-service while allowing all other use.
- The CLI tool remains free for local use — install it anywhere, use it with any agent, no restrictions.

### Why not proprietary?

- PyPI and the Python ecosystem overwhelmingly expect open or source-available packages. Proprietary creates friction.
- MCP server ecosystem is nascent; trust and inspectability matter for adoption.
- Proprietary limits contributions and community goodwill without meaningfully more protection than BSL for this use case.

### Why Apache 2.0 as the change license?

- Matches CadQuery (Apache 2.0), the primary dependency.
- Includes patent grant, which matters for enterprise adoption.
- Standard and well-understood.

### Why 4-year change date?

- Gives enough runway to establish the commercial offering.
- Signals genuine intent to open-source — not a hollow promise.
- Each release gets its own 4-year clock, so the latest code is always protected.

---

## What's Protected, What's Free

| Use case | Allowed? |
|----------|----------|
| Developer installs agentcad locally, uses with Claude Code | Yes |
| Company installs agentcad for internal engineering team | Yes |
| Someone reads source, submits PRs, builds plugins | Yes |
| Someone forks agentcad and offers "agentcad cloud" as a competing service | **No** |
| Someone embeds agentcad in a product they sell (not as a CAD service) | Yes |

---

## Distribution Channel Implications

- **PyPI:** BSL packages are accepted. Set `License` classifier to `BSL-1.1`. Unusual but not unprecedented (Sentry, CockroachDB ecosystem packages).
- **MCP server:** No license norms yet. Source-available with BSL is a strength — users can read the server code.
- **Claude Code skill / ClawHub skill:** These distribute instructions, not code. License is largely irrelevant for skills.
- **GitHub public repo:** LICENSE file in root. Clear and prominent.

---

## Repo Structure Implications

The current `mountain-climber` repo blends internal business documents (PRDs, roadmap, friction logs, strategy docs) with the CLI application code. Going public requires splitting into two repositories:

- **Public repo** — the agentcad CLI. Contains `app/` contents (promoted to root), LICENSE (BSL), public README, CI, public CLAUDE.md.
- **Private repo** (`mountain-climber`) — internal command center. PRDs, roadmap, friction logs, strategy docs, internal CLAUDE.md.

This split is handled in M45 (Prepare for Public).

---

## References

- [BSL 1.1 full text](https://mariadb.com/bsl11/)
- [Sentry's BSL adoption](https://blog.sentry.io/lets-talk-about-open-source/)
- [CockroachDB BSL rationale](https://www.cockroachlabs.com/blog/oss-relicensing-cockroachdb/)
- [MariaDB BSL FAQ](https://mariadb.com/bsl-faq-mariadb/)
