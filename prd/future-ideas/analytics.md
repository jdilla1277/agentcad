# Analytics & Usage Measurement

How to understand whether agentcad is getting real adoption, and what "success" looks like at each stage.

## Current state (2026-04-17)

- Published on PyPI as `agentcad`, version 0.1.4
- GitHub repo is **private** — intentionally deferred; no urgency to go public
- First week stats: ~495 "without mirrors" downloads, estimated 50-150 real human installs
- No telemetry, no opt-in analytics, no usage tracking built into the CLI

## Interpreting PyPI download numbers

Raw download counts are noisy. Key filters:

- **"with mirrors" vs "without mirrors"**: Mirrors/CDNs account for ~65% of our downloads. Always use the `without_mirrors` number from pypistats.
- **CI pipelines**: pip/uv report a `ci` flag in BigQuery. Filter with `details.ci IS NULL` to exclude automated builds.
- **Bots/scanners**: New packages get baseline noise from security scanners and indexers. For a brand-new package, subtract ~25-50/day as floor noise.
- **The "null" problem**: Downloads from tools that don't report user-agent metadata show up as `null` system. These are uninterpretable — could be anything.

**Rule of thumb**: Our real human installs are roughly 10-30% of the raw "with mirrors" number. Use downloads for **relative trends over time**, not absolute user counts.

### Comparison benchmarks (niche CAD/3D Python tools)

| Package | Monthly downloads | Context |
|---------|-------------------|---------|
| cadquery | ~230K | Established since 2014, our upstream dependency |
| build123d | ~135K | Newer CadQuery alternative, ~2 years old |
| trimesh | ~5.5M | Widely-used mesh library, mostly transitive dependency pulls |
| agentcad | ~495 (week 1) | Brand new, novel category |

## What to track

### High signal (prioritize these)

1. **Issues from strangers** — one substantive bug report from an unknown user is worth more than 1,000 downloads. Track who filed, what they were trying to do, whether they came back.
2. **Repeat installs / version upgrades** — BigQuery can show same IP ranges downloading new versions. This is our retention metric.
3. **Dependent packages** — other PyPI packages listing agentcad as a dependency (Libraries.io tracks this). Even 1-2 dependents is meaningful for a niche tool.
4. **Depth of usage** — issues shifting from "how do I install" to edge cases in specific commands/helpers.
5. **Time to second PR** — if an external contributor comes back, that's the strongest community health signal.

### Medium signal

- "Without mirrors" download trends (weekly rolling average)
- GitHub unique cloners (not total — unique)
- Unique monthly contributors (issues + PRs + comments)
- Documentation page views from distinct visitors

### Low signal / vanity (don't over-index)

- Raw total download counts (never decrease, include all noise)
- GitHub stars (gameable, weakly correlated with actual usage)
- Total contributor count alone

## Stage benchmarks

| Stage | Without-mirrors downloads | Qualitative signals |
|-------|---------------------------|---------------------|
| Week 1 (done) | 50-150 real installs | Anyone outside your network tried it |
| Month 1 | 500-1K/month | 5-10 GitHub issues, 1-3 external contributors |
| Quarter 1 | 2-5K/month | 1-2 dependents, organic blog posts/tweets from strangers |
| Month 6 | 5-10K/month | Users answering each other's questions, feature requests showing deep usage |

## Free tools to use

### Set up now

- **pypistats.org/packages/agentcad** — daily/weekly/monthly with and without mirrors
- **clickpy.clickhouse.com/dashboard/agentcad** — ClickHouse-powered, more granular than pypistats
- **pepy.tech/projects/agentcad** — clean charts, good for README download badges
- **GitHub Traffic tab** — unique cloners and page views. **Only retains 14 days** — archive it weekly via the API or it's lost.

### Set up when ready

- **Google BigQuery** — the canonical source. Free tier (1 TiB/month) is plenty for this kind of query. Setup:

  ```bash
  # one-time: install gcloud, auth, set project
  brew install --cask google-cloud-sdk
  gcloud auth login
  gcloud auth application-default login
  gcloud projects create agentcad-analytics  # or reuse an existing project
  gcloud config set project agentcad-analytics
  # enable the BigQuery API (free tier — no cost unless you exceed 1 TiB/month of query scans)
  gcloud services enable bigquery.googleapis.com
  ```

  Canonical query — non-CI installs by day, OS, and installer:

  ```sql
  SELECT
    DATE(timestamp) AS date,
    details.system.name AS os,
    details.installer.name AS installer,
    COUNT(*) AS downloads
  FROM `bigquery-public-data.pypi.file_downloads`
  WHERE file.project = 'agentcad'
    AND DATE(timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND details.ci IS NULL                         -- exclude CI runners
    AND details.installer.name IN ('pip', 'uv')    -- drop scanners/mirrors
  GROUP BY date, os, installer
  ORDER BY date DESC
  ```

  Run it: `bq query --use_legacy_sql=false < query.sql` or via the BigQuery web console.

- **Libraries.io** — monitors when other packages depend on agentcad
- **Snyk Advisor** (snyk.io/advisor/python/agentcad) — health score for credibility signaling

## Semi-automated weekly digest

Goal: a weekly stats report in Discord with zero manual effort, alongside the existing feedback stream.

### Minimal setup (recommended)

A GitHub Actions cron that runs the BigQuery query and posts a digest to the same Discord webhook that receives feedback:

1. **Service account for BigQuery** (one-time):
   ```bash
   gcloud iam service-accounts create agentcad-stats \
     --display-name="agentcad stats reporter"
   gcloud projects add-iam-policy-binding agentcad-analytics \
     --member="serviceAccount:agentcad-stats@agentcad-analytics.iam.gserviceaccount.com" \
     --role="roles/bigquery.jobUser"
   gcloud projects add-iam-policy-binding agentcad-analytics \
     --member="serviceAccount:agentcad-stats@agentcad-analytics.iam.gserviceaccount.com" \
     --role="roles/bigquery.dataViewer"
   gcloud iam service-accounts keys create key.json \
     --iam-account=agentcad-stats@agentcad-analytics.iam.gserviceaccount.com
   ```

2. **GitHub secrets**:
   - `GCP_SA_KEY` — paste contents of `key.json`
   - `DISCORD_WEBHOOK_URL` — reuse feedback webhook or a new `#stats` channel

3. **`.github/workflows/stats-digest.yml`** — runs every Monday 09:00 UTC:
   ```yaml
   name: Weekly stats digest
   on:
     schedule:
       - cron: "0 9 * * 1"
     workflow_dispatch:  # allow manual trigger
   jobs:
     digest:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: google-github-actions/auth@v2
           with:
             credentials_json: ${{ secrets.GCP_SA_KEY }}
         - uses: google-github-actions/setup-gcloud@v2
         - run: pip install google-cloud-bigquery requests
         - run: python scripts/stats_digest.py
           env:
             DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
   ```

4. **`scripts/stats_digest.py`** — runs the canonical query, formats a short summary (week-over-week non-CI downloads, top OS/installer, any spikes), POSTs to Discord. Include week-over-week delta so you notice trends without reading numbers.

### Why this shape

- **Reuses infra we already trust** — GitHub Actions + Discord webhook are already load-bearing for feedback.
- **No server to run** — cron lives in GitHub, no cost.
- **Versioned query** — the SQL lives in the repo, so changes show up in git history instead of getting lost in a BigQuery scratch tab.
- **Manually triggerable** — `workflow_dispatch` means you can pull an ad-hoc digest any time.

### Alternatives considered

- **Local cron + launchd** — works, but dies when laptop sleeps; not reliable.
- **Scarf** — replaces the query with a drop-in package redirect, but requires PyPI package config changes and adds a dependency for a tool we don't need yet at this scale.
- **Dashboards (Looker, Metabase)** — overkill for a weekly number. Revisit when we have 5+ metrics worth glancing at simultaneously.

### Consider later (at scale)

- **Scarf** (about.scarf.sh) — privacy-conscious download analytics, org-level identification, retention tracking. Most useful at 500+ monthly active users.
- **Opt-in telemetry** — lightweight first-run phone-home with clear opt-out. Controversial but gives the best real-user data. Defer this decision.

## Common pitfalls

- **Don't compare across categories.** trimesh has 5.5M/month because it's a transitive dependency, not because 5.5M humans use it.
- **Watch trends, not absolutes.** Weekly rolling average matters more than daily peaks.
- **Publicity spikes aren't growth.** An HN front page gives 10x for one day, then it's gone. Sustainable growth is a slow upward trend line.
- **If downloads go up but issues/community stays flat, users are bouncing.** The absence of engagement is itself a signal.
- **Stars can be bought.** Researchers identified 6M+ suspected fake stars on GitHub. Don't optimize for this.

## The ultimate test

> "If you deprecated agentcad tomorrow, how many people would complain?"

That's the number that matters. Everything else is a proxy.
