"""
agents/knightfrank_agent.py
--------------------
Daily agent: fetches new Knight Frank APAC research publications.

Strategy:
  1. Search CBRE's research page + Google for new reports
  2. Use the Perplexity API to extract structured data from each result
  3. Deduplicate against the existing publications.json
  4. Append new entries and save

Run manually:  python agents/knightfrank_agent.py
Run via CI:    triggered by .github/workflows/daily.yml
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow imports from shared/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.utils import (
    load_publications,
    save_publications,
    deduplicate,
    make_publication,
    git_commit_and_push,
    normalise_market,
    normalise_sector,
)

try:
    from openai import OpenAI
except ImportError:
    print("openai package not found. Run: pip install openai")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────
FIRM = "Knight Frank"
LOOKBACK_DAYS = 35          # how far back to search (slightly more than a month)
MARKETS = ["Tokyo", "Seoul", "Singapore", "Australia", "Hong Kong", "APAC"]
SECTORS = ["Office", "Industrial & Logistics", "Retail", "Residential", "Investment", "Data Centre"]

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

SEARCH_QUERIES = [
    "Knight Frank Asia Pacific real estate research report 2026",
    "Knight Frank Japan office logistics research 2026",
    "Knight Frank Korea Seoul office market report 2026",
    "Knight Frank Singapore office industrial research 2026",
    "Knight Frank Australia commercial real estate report 2026",
    "Knight Frank Hong Kong office market report 2026",
    "site:knightfrank.com/research Asia Pacific 2026",
]

EXTRACTION_PROMPT = """
You are extracting structured data from CBRE real estate research publications for an APAC market intelligence database.

Search for and return ALL CBRE research publications published in the last {lookback_days} days covering these APAC markets:
- Japan / Tokyo
- South Korea / Seoul  
- Singapore
- Australia (Sydney, Melbourne, Brisbane)
- Hong Kong
- APAC / Asia Pacific (regional)

For EACH publication found, return a JSON array with objects containing EXACTLY these fields:
{{
  "publishDate": "YYYY-MM-DD",   // approximate date, use 1st of month if exact unknown
  "firm": "Knight Frank",
  "title": "exact report title",
  "market": "one of: Tokyo | Seoul | Singapore | Australia | Hong Kong | APAC",
  "sector": "one of: Office | Industrial & Logistics | Retail | Residential | Investment | Data Centre | Mixed/Cross-Sector",
  "summary": "2-3 sentence summary of key findings including any specific data points",
  "primeYield": "e.g. 4.25% or null",
  "primeRent": "e.g. JPY 40,000/tsubo or null",
  "url": "direct URL to the report or null"
}}

Today's date: {today}
Return ONLY the JSON array, no other text.
"""


def fetch_new_publications() -> list[dict]:
    if not PERPLEXITY_API_KEY:
        print("ERROR: PERPLEXITY_API_KEY not set. Add it to GitHub Secrets.")
        return []

    client = OpenAI(
        api_key=PERPLEXITY_API_KEY,
        base_url="https://api.perplexity.ai",
    )

    prompt = EXTRACTION_PROMPT.format(
        lookback_days=LOOKBACK_DAYS,
        today=date.today().isoformat(),
    )

    print(f"[{FIRM}] Querying Perplexity for new publications...")
    response = client.chat.completions.create(
        model="sonar",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        pubs = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[{FIRM}] JSON parse error: {e}")
        print(f"Raw response snippet: {raw[:500]}")
        return []

    if not isinstance(pubs, list):
        print(f"[{FIRM}] Unexpected response format (not a list)")
        return []

    # Normalise and validate each publication
    cleaned = []
    for p in pubs:
        if not p.get("title"):
            continue
        pub = make_publication(
            firm=FIRM,
            title=p.get("title", ""),
            publish_date=p.get("publishDate", date.today().isoformat()),
            market=p.get("market", "APAC"),
            sector=p.get("sector", "Mixed/Cross-Sector"),
            summary=p.get("summary", ""),
            prime_yield=p.get("primeYield"),
            prime_rent=p.get("primeRent"),
            url=p.get("url"),
        )
        cleaned.append(pub)

    print(f"[{FIRM}] Found {len(cleaned)} publications from API")
    return cleaned


def main():
    import time
    start = time.time()

    print(f"
{'='*50}")
    print(f" CBRE Agent — {date.today().isoformat()}")
    print(f"{'='*50}")

    existing = load_publications()
    print(f"Existing publications in DB: {len(existing)}")

    new_pubs = fetch_new_publications()
    to_add = deduplicate(existing, new_pubs)

    if not to_add:
        print(f"[{FIRM}] No new publications found.")
    else:
        print(f"[{FIRM}] Adding {len(to_add)} new publications:")
        for p in to_add:
            print(f"  + [{p['market']}] {p['title'][:70]}")
        updated = existing + to_add
        save_publications(updated)

    # Count new yield/rent datapoints
    new_yield_pts = sum(1 for p in to_add if p.get("primeYield"))
    new_rent_pts  = sum(1 for p in to_add if p.get("primeRent"))
    markets = list({p["market"] for p in to_add}) if to_add else []
    sectors = list({p["sector"] for p in to_add}) if to_add else []

    append_agent_result(
        run_id=f"{date.today().isoformat()}T06:00:00Z",
        firm=FIRM,
        status="success",
        publications_found=len(new_pubs),
        new_publications=len(to_add),
        new_yield_datapoints=new_yield_pts,
        new_rent_datapoints=new_rent_pts,
        markets=markets,
        sectors=sectors,
        duration_seconds=time.time() - start,
        notes=f"Searched {LOOKBACK_DAYS}-day window. {len(to_add)} new publications added.",
    )

    if to_add:
        git_commit_and_push(
            f"[auto] {FIRM}: +{len(to_add)} new publications — {date.today().isoformat()}"
        )


if __name__ == "__main__":
    main()
