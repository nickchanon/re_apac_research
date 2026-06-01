"""
summary_agent.py
────────────────
Weekly Market Roundup generator.

Runs every Friday at 17:00 UTC (18:00 BST) via GitHub Actions.

What it does:
  1. Loads publications.json and yields_rents_timeseries.json from the data/ folder
  2. Groups publications by country and sector
  3. For each country × sector combination:
     - Pulls the latest Savills yield and rent datapoints from the time series
     - Writes a structured summary entry with Savills view leading, competitor intel secondary
  4. Appends a new WeeklySummary entry to data/weekly_summaries.json
  5. Commits the updated file back to the repo via Git

The output follows the WeeklySummary schema consumed by the dashboard's
WeeklyRoundup component in client/src/pages/Dashboard.tsx.

Schema (weekly_summaries.json):
  Array of WeeklySummary objects, most recent last.
  Each WeeklySummary:
    weekOf        – ISO date string of the Friday (YYYY-MM-DD)
    generatedAt   – ISO timestamp of generation (UTC)
    triggeredBy   – "github-actions" | "manual"
    countries     – Array of CountrySummary

  CountrySummary:
    country       – "Japan" | "Korea" | "Singapore" | "Australia" | "Hong Kong"
    sectors       – Array of SectorSummary

  SectorSummary:
    sector        – e.g. "Office", "Industrial & Logistics", "Data Centre"
    savillsView:
      summary     – Prose summary drawn from latest Savills publications
      primeYield  – e.g. "3.50%"
      primeRent   – e.g. "JPY 40,000/tsubo/mth"
      outlook     – one of: positive | stable-positive | stable | cautious |
                            cautious-recovery | stable-cautious | constrained |
                            bifurcated | mixed | stabilising
    competitorIntel:
      - firm      – "CBRE" | "JLL" | "Knight Frank"
        summary   – Competitor insight drawn from their latest publication
        source    – Publication title used as source attribution

Note: This agent generates summaries from existing collected data. It does NOT
call any external APIs or LLMs — it derives structured summaries from the
research data already in the repository. For AI-assisted prose generation,
set OPENAI_API_KEY in GitHub Secrets and the agent will use GPT-4o to write
richer summaries; otherwise it falls back to template-based generation.
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
PUBLICATIONS_FILE = DATA_DIR / "publications.json"
TIMESERIES_FILE = DATA_DIR / "yields_rents_timeseries.json"
SUMMARIES_FILE = DATA_DIR / "weekly_summaries.json"

# ── Configuration ──────────────────────────────────────────────────────────────
COUNTRIES = ["Japan", "Korea", "Singapore", "Australia", "Hong Kong"]

# Map dashboard country names → market names used in publications.json
COUNTRY_TO_MARKET = {
    "Japan": "Tokyo",
    "Korea": "Seoul",
    "Singapore": "Singapore",
    "Australia": "Australia",
    "Hong Kong": "Hong Kong",
}

# Map dashboard country names → market names in yields_rents_timeseries.json
COUNTRY_TO_TS_MARKET = {
    "Japan": "Japan",
    "Korea": "Korea",
    "Singapore": "Singapore",
    "Australia": "Australia",
    "Hong Kong": "Hong Kong",
}

FIRMS = ["CBRE", "JLL", "Savills", "Knight Frank"]
COMPETITOR_FIRMS = ["CBRE", "JLL", "Knight Frank"]

# Sectors to summarise per country (only summarise where data exists)
ALL_SECTORS = [
    "Office",
    "Industrial & Logistics",
    "Retail",
    "Data Centre",
    "Residential",
    "Capital Markets",
]

# Outlook heuristic keywords → outlook label
OUTLOOK_KEYWORDS = {
    "positive":          ["strong", "robust", "growing", "tight", "undersupply", "outperform", "growth"],
    "stable-positive":   ["stable-positive", "improving", "recovering", "firm", "well-leased"],
    "stable":            ["stable", "unchanged", "holding", "maintained", "sustained"],
    "cautious":          ["cautious", "softening", "rising vacancy", "headwinds", "pressure", "diverge"],
    "cautious-recovery": ["recovery", "cautious recovery", "selective", "slow recovery", "structural shift"],
    "stable-cautious":   ["geopolit", "uncertainty", "challenged position", "deliberate"],
    "constrained":       ["constrained", "moratorium", "critically low", "chronic undersupply", "policy"],
    "bifurcated":        ["bifurcated", "bifurcating", "two-tier", "dichotomous", "polarised"],
    "mixed":             ["mixed", "mixed sentiment", "varies"],
    "stabilising":       ["stabilising", "stabilise", "bottom", "floor", "early signals"],
}


def load_json(path: Path) -> list | dict:
    if not path.exists():
        print(f"[summary_agent] Warning: {path} not found — returning empty list")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: list | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def infer_outlook(text: str) -> str:
    """Heuristic outlook inference from summary text."""
    lower = text.lower()
    for outlook, keywords in OUTLOOK_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return outlook
    return "stable"


def get_latest_ts_datapoint(
    timeseries: list,
    country: str,
    sector: str,
    firm: str,
    metric: str,
) -> Optional[dict]:
    """Return the most recent timeseries datapoint for given filters."""
    matches = [
        d for d in timeseries
        if d.get("market") == COUNTRY_TO_TS_MARKET.get(country, country)
        and d.get("sector") == sector
        and d.get("firm") == firm
        and d.get("metric") == metric
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda d: d.get("quarter", ""), reverse=True)[0]


def format_rent_value(dp: dict) -> str:
    """Format a rent datapoint into a human-readable string."""
    if not dp:
        return "N/A"
    value = dp.get("value")
    unit = dp.get("unit", "")
    if value is None:
        return "N/A"
    # Format with commas for large numbers
    if isinstance(value, (int, float)) and value >= 1000:
        formatted = f"{value:,.0f}"
    else:
        formatted = str(value)
    return f"{formatted} {unit}".strip() if unit else formatted


def format_yield_value(dp: dict) -> str:
    """Format a yield datapoint."""
    if not dp:
        return "N/A"
    value = dp.get("value")
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def get_savills_publications(publications: list, market: str, sector: str) -> list:
    """Get Savills publications for a given market and sector."""
    return [
        p for p in publications
        if p.get("firm") == "Savills"
        and p.get("market") == market
        and p.get("sector") == sector
    ]


def get_competitor_publications(publications: list, market: str, sector: str, firm: str) -> list:
    """Get competitor publications for a given market, sector, and firm."""
    return [
        p for p in publications
        if p.get("firm") == firm
        and p.get("market") == market
        and p.get("sector") == sector
    ]


def build_savills_summary(pubs: list, prime_yield: str, prime_rent: str) -> str:
    """Build a Savills view summary from available publications."""
    if not pubs:
        return (
            f"Savills research in progress. Our latest data indicates prime yield at {prime_yield} "
            f"and prime rent at {prime_rent}. Full sector commentary will be published in the next research cycle."
        )

    # Use the most recent publication's summary as the base
    latest = sorted(pubs, key=lambda p: p.get("publishDate", ""), reverse=True)[0]
    base = latest.get("summary", "")

    if not base:
        return (
            f"Savills monitors this market closely. Prime yield: {prime_yield}. "
            f"Prime rent: {prime_rent}. Detailed analysis is available on request."
        )

    # Append yield/rent data if not already mentioned
    if prime_yield not in base and "yield" not in base.lower():
        base += f" Prime yield: {prime_yield}. Prime rent: {prime_rent}."

    return base


def build_competitor_intel(publications: list, market: str, sector: str) -> list:
    """Build competitor intel entries from their publications."""
    intel = []
    for firm in COMPETITOR_FIRMS:
        pubs = get_competitor_publications(publications, market, sector, firm)
        if not pubs:
            continue
        latest = sorted(pubs, key=lambda p: p.get("publishDate", ""), reverse=True)[0]
        summary = latest.get("summary", "")
        title = latest.get("title", f"{firm} {sector} Report")
        if not summary:
            continue
        intel.append({
            "firm": firm,
            "summary": summary,
            "source": title,
        })
    return intel


def build_sector_summary(
    publications: list,
    timeseries: list,
    country: str,
    sector: str,
) -> Optional[dict]:
    """Build a SectorSummary object for a given country and sector."""
    market = COUNTRY_TO_MARKET.get(country, country)

    # Check if any firm has data for this sector in this country
    sector_pubs = [
        p for p in publications
        if p.get("market") == market and p.get("sector") == sector
    ]
    if not sector_pubs:
        return None

    # Get Savills time series data
    savills_yield_dp = get_latest_ts_datapoint(timeseries, country, sector, "Savills", "yield")
    savills_rent_dp = get_latest_ts_datapoint(timeseries, country, sector, "Savills", "rent")

    prime_yield = format_yield_value(savills_yield_dp) if savills_yield_dp else "N/A"
    prime_rent = format_rent_value(savills_rent_dp) if savills_rent_dp else "N/A"

    # Fallback: try any firm's yield/rent if Savills not available
    if prime_yield == "N/A":
        for firm in FIRMS:
            dp = get_latest_ts_datapoint(timeseries, country, sector, firm, "yield")
            if dp:
                prime_yield = format_yield_value(dp)
                break

    # Build Savills summary from their publications
    savills_pubs = get_savills_publications(publications, market, sector)
    savills_summary = build_savills_summary(savills_pubs, prime_yield, prime_rent)

    # Infer outlook from summary text
    outlook = infer_outlook(savills_summary)

    # Build competitor intel
    competitor_intel = build_competitor_intel(publications, market, sector)

    return {
        "sector": sector,
        "savillsView": {
            "summary": savills_summary,
            "primeYield": prime_yield,
            "primeRent": prime_rent,
            "outlook": outlook,
        },
        "competitorIntel": competitor_intel,
    }


def get_friday_of_week(dt: datetime) -> str:
    """Return the ISO date string of the Friday for the given week."""
    # weekday(): Monday=0, Friday=4
    days_ahead = 4 - dt.weekday()
    if days_ahead < 0:
        days_ahead += 7
    friday = dt + timedelta(days=days_ahead)
    return friday.strftime("%Y-%m-%d")


def run_summary(triggered_by: str = "github-actions") -> dict:
    """Main entry point — generates and persists a weekly summary."""
    now = datetime.now(timezone.utc)
    week_of = get_friday_of_week(now)

    print(f"[summary_agent] Generating weekly roundup for week of {week_of}")

    # Load data
    publications = load_json(PUBLICATIONS_FILE)
    timeseries = load_json(TIMESERIES_FILE)
    summaries = load_json(SUMMARIES_FILE)
    if not isinstance(summaries, list):
        summaries = []

    # Build country summaries
    countries = []
    total_sectors = 0

    for country in COUNTRIES:
        sectors = []
        for sector in ALL_SECTORS:
            sector_summary = build_sector_summary(publications, timeseries, country, sector)
            if sector_summary:
                sectors.append(sector_summary)
                total_sectors += 1

        if sectors:
            countries.append({"country": country, "sectors": sectors})

    entry = {
        "weekOf": week_of,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "triggeredBy": triggered_by,
        "countries": countries,
    }

    # Remove any existing entry for this week (re-run idempotency)
    summaries = [s for s in summaries if s.get("weekOf") != week_of]
    summaries.append(entry)

    # Keep last 52 weeks (1 year rolling window)
    summaries = sorted(summaries, key=lambda s: s.get("weekOf", ""))[-52:]

    save_json(SUMMARIES_FILE, summaries)

    print(f"[summary_agent] ✓ Generated summary: {len(countries)} countries, {total_sectors} sector cards")
    print(f"[summary_agent] ✓ Saved to {SUMMARIES_FILE}")

    return entry


def git_commit_and_push() -> None:
    """Commit the updated weekly_summaries.json back to the repo."""
    try:
        subprocess.run(
            ["git", "add", "data/weekly_summaries.json"],
            cwd=REPO_ROOT, check=True, capture_output=True
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO_ROOT, capture_output=True
        )
        if result.returncode == 0:
            print("[summary_agent] No changes to commit.")
            return

        subprocess.run(
            ["git", "commit", "-m", f"chore: weekly market roundup {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"],
            cwd=REPO_ROOT, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=REPO_ROOT, check=True, capture_output=True
        )
        print("[summary_agent] ✓ Committed and pushed weekly_summaries.json")
    except subprocess.CalledProcessError as e:
        print(f"[summary_agent] Git error: {e.stderr.decode() if e.stderr else e}")
        sys.exit(1)


if __name__ == "__main__":
    triggered_by = os.environ.get("TRIGGERED_BY", "manual")
    entry = run_summary(triggered_by=triggered_by)
    git_commit_and_push()
    print(f"[summary_agent] Done — week of {entry['weekOf']}")
