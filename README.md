# moltbot-triage 🦞

**Community-driven triage dashboard for [moltbot/moltbot](https://github.com/moltbot/moltbot).**

🔗 **Live dashboard: [conroywhitney.github.io/moltbot-triage](https://conroywhitney.github.io/moltbot-triage/)**

## What is this?

Moltbot has hundreds of open issues and PRs with very few reviews. This repo provides:

1. **State mirror** — Every open issue and PR synced as flat markdown files with YAML frontmatter
2. **Data pipeline** — Automated aggregation into structured JSON
3. **GitHub Pages dashboard** — Multi-page dashboard for exploring issues, PRs, and repo health

## How it works

```
state/                    # Mirror of GitHub (auto-synced)
├── issues/3658.md        # One file per open issue
└── prs/3705.md           # One file per open PR

scripts/                  # Automation
├── sync-issues.sh        # Pull issues via GitHub GraphQL API
├── sync-prs.sh           # Pull PRs via GitHub GraphQL API
├── scrub-secrets.py      # Remove sensitive data
├── aggregate.py          # Generate JSON data from state/
└── sync-all.sh           # Run everything (cron-safe)

docs/                     # GitHub Pages site (auto-generated)
├── index.html            # Landing page with key stats
├── prs/
│   ├── ready.html        # PRs with passing CI + community approval
│   ├── failing.html      # PRs with failing CI
│   ├── huge.html         # PRs >1000 LOC
│   └── all.html          # Full sortable/filterable table
├── issues/
│   └── trending.html     # Top engagement issues
├── health.html           # Repo health metrics & charts
├── assets/               # Shared CSS/JS
└── data/                 # JSON data (generated)
    ├── issues.json
    ├── prs.json
    ├── stats.json
    └── meta.json

config.yml                # Scoring weights, thresholds, sync settings
```

## Running locally

```bash
# Full sync (requires GITHUB_TOKEN)
bash scripts/sync-all.sh

# Or just regenerate the dashboard from cached state
python3 scripts/aggregate.py

# Then open docs/index.html in a browser
```

## Dashboard pages

| Page | Description |
|------|-------------|
| **Overview** | Key stats, quick links to all subpages |
| **Ready to Merge** | PRs with passing CI and community engagement |
| **CI Failures** | PRs with failing CI that need author attention |
| **Huge PRs** | PRs >1,000 lines — split into "with issue" and "without issue" |
| **All PRs** | Full sortable, searchable, filterable table |
| **Trending Issues** | Issues ranked by reactions + comments |
| **Health** | Size distribution, label stats, top contributors |

## GitHub Pages

The site is served from the `docs/` directory. Configure GitHub Pages:
- Source: **Deploy from a branch**
- Branch: `main`, folder: `/docs`

---

*Built by [Clawd + Conroy](https://github.com/conroywhitney) 🦞*
