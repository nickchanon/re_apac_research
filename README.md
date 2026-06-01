# APAC Research Intelligence

Automated competitor research tracker for APAC commercial real estate markets.
Tracks publications from **CBRE**, **JLL**, **Savills**, and **Knight Frank** across
Tokyo, Seoul, Singapore, Australia, and Hong Kong.

---

## Repository Structure

```
re_apac_research/
├── data/
│   └── publications.json       ← Single source of truth (auto-updated daily)
├── agents/
│   ├── cbre_agent.py           ← CBRE daily fetcher
│   ├── jll_agent.py            ← JLL daily fetcher
│   ├── savills_agent.py        ← Savills daily fetcher
│   ├── knightfrank_agent.py    ← Knight Frank daily fetcher
│   └── run_all.py              ← Run all 4 agents sequentially
├── shared/
│   └── utils.py                ← Shared: load/save DB, dedup, normalise, git commit
├── dashboard/
│   └── index.html              ← Self-contained dashboard (fetches live from this repo)
├── .github/
│   └── workflows/
│       └── daily.yml           ← GitHub Actions: runs agents at 07:00 BST weekdays
└── requirements.txt
```

---

## How It Works

1. **GitHub Actions** triggers `daily.yml` every weekday at 07:00 BST
2. Each agent queries the Perplexity API to find new publications from its firm
3. New publications are deduplicated (by URL, then by title) against `data/publications.json`
4. Only genuinely new entries are appended
5. The workflow commits and pushes `data/publications.json` back to `main`
6. The dashboard (`dashboard/index.html`) fetches the raw JSON from GitHub on load — it always shows the latest data

---

## Setup (One-Time)

### 1. Add the Perplexity API Key

Go to **Settings → Secrets and variables → Actions** in this repo and add:

| Secret name | Value |
|---|---|
| `PERPLEXITY_API_KEY` | Your Perplexity API key from [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api) |

### 2. Enable GitHub Actions

Actions are enabled by default. Go to the **Actions** tab to verify. The first automatic run will happen at 07:00 BST on the next weekday.

### 3. Manual Run

To trigger a run immediately:
1. Go to **Actions → Daily APAC Research Agents**
2. Click **Run workflow**
3. Optionally select a specific agent (blank = all four)

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
export PERPLEXITY_API_KEY=your_key_here

# Run a single agent
python agents/cbre_agent.py

# Run all agents
python agents/run_all.py
```

---

## Data Schema

`data/publications.json` is an array of objects with these fields:

| Field | Type | Description |
|---|---|---|
| `id` | int | Sequential ID (auto-assigned on save) |
| `publishDate` | string | YYYY-MM-DD (approximate if exact date unknown) |
| `firm` | string | CBRE / JLL / Savills / Knight Frank |
| `market` | string | Tokyo / Seoul / Singapore / Australia / Hong Kong / APAC |
| `sector` | string | Office / Industrial & Logistics / Retail / Residential / Investment / Data Centre / Mixed/Cross-Sector |
| `title` | string | Report title |
| `summary` | string | 2–3 sentence summary of key findings |
| `primeYield` | string\|null | Prime yield (e.g. "4.25%") if mentioned |
| `primeRent` | string\|null | Prime rent with units if mentioned |
| `savills_yield` | string\|null | Savills' own yield opinion (populated manually or via Savills agent) |
| `savills_rent` | string\|null | Savills' own rent opinion (populated manually or via Savills agent) |
| `url` | string\|null | Direct link to the report |
| `notes` | string\|null | Additional commentary |

---

## Adding a New Firm

1. Copy `agents/cbre_agent.py` to `agents/newcorp_agent.py`
2. Update `FIRM`, the search queries, and the extraction prompt
3. Add a step for it in `.github/workflows/daily.yml`
4. Add it to `agents/run_all.py`

---

## Dashboard

The dashboard is a self-contained HTML file at `dashboard/index.html`.

- It contains static data (the last 124 publications) baked in as a fallback
- On load, it also fetches the latest `data/publications.json` directly from this repo (GitHub raw URL) — so it always reflects the most recent agent run
- Deploy it anywhere: S3, GitHub Pages, Netlify, or share the raw file

**GitHub Pages setup** (optional — makes the dashboard publicly accessible):
1. Go to **Settings → Pages**
2. Source: `Deploy from a branch`
3. Branch: `main`, Folder: `/dashboard`
4. Your dashboard will be live at `https://nickchanon.github.io/re_apac_research/`

---

## Caveats

- Agents rely on the Perplexity API's web search to discover new publications. Results quality depends on how well competitors index their research pages.
- Yields and rents are extracted where explicitly stated in reports — many reports discuss trends without publishing benchmark figures.
- `savills_yield` and `savills_rent` fields are for Savills' own published positions and must be populated manually (or via the Savills agent when Savills publishes quantified benchmarks).
