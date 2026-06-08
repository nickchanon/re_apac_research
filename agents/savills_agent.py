"""
agents/savills_agent.py
--------------------
Daily agent: fetches new Savills APAC research publications.

Strategy:
  - Fan-out across multiple targeted queries, one per market × domain combination
  - Each query targets a specific Savills regional domain to maximise coverage
  - Deduplicates results across queries before saving
  - Domain allowlist enforced in shared/utils.py deduplicate()

Run manually:  python agents/savills_agent.py
Run via CI:    triggered by .github/workflows/daily.yml
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.utils import (
    load_publications,
    save_publications,
    deduplicate,
    make_publication,
    git_commit_and_push,
    append_agent_result,
)

try:
    from openai import OpenAI
except ImportError:
    print("openai package not found. Run: pip install openai")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────
FIRM          = "Savills"
LOOKBACK_DAYS = 35

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

# ── Fan-out queries — one per market/domain combination ─────────────────────
# Each query is tightly focused so Perplexity surfaces the right indexed content
QUERIES = [
    # Singapore — savills.com.sg
    "Savills Singapore real estate research report site:savills.com.sg 2026",
    "savills.com.sg insight-and-opinion research Singapore office retail residential 2026",
    "Savills Singapore office market briefing 2026 primeYield rent",
    "Savills Singapore industrial logistics residential investment 2026 research",

    # Hong Kong — savills.com.hk
    "Savills Hong Kong real estate research report site:savills.com.hk 2026",
    "savills.com.hk research Hong Kong office retail residential investment 2026",
    "Savills Hong Kong market briefing office industrial 2026",

    # Japan / Tokyo — savills.co.jp
    "Savills Japan Tokyo real estate research site:savills.co.jp 2026",
    "savills.co.jp research_articles Tokyo office residential logistics 2026",
    "Savills Japan office leasing market report Q1 Q2 2026",
    "Savills Tokyo office rents vacancy 2026 research",

    # Korea / Seoul — savills.co.kr
    "Savills Korea Seoul real estate research site:savills.co.kr 2026",
    "Savills Seoul office industrial investment market report 2026",

    # Australia — savills.com.au
    "Savills Australia real estate research report site:savills.com.au 2026",
    "savills.com.au research Sydney Melbourne office industrial retail 2026",
    "Savills Australia market spotlight industrial logistics office 2026",

    # APAC regional — savills.com main site
    "Savills Asia Pacific APAC real estate research report site:savills.com 2026",
    "savills.com research Asia-Pacific capital markets investment 2026",
    "Savills APAC regional market outlook research Q1 Q2 2026",
    "impacts.savills.com Asia Pacific real estate 2026",
]

EXTRACTION_PROMPT = """
You are extracting structured data from Savills real estate research publications for an APAC market intelligence database.

Search for and return ALL Savills research publications published in the last {lookback_days} days that are relevant to this query:
"{query}"

Focus on these APAC markets:
- Japan / Tokyo
- South Korea / Seoul
- Singapore
- Australia (Sydney, Melbourne, Brisbane)
- Hong Kong
- APAC / Asia Pacific (regional)

For EACH publication found, return a JSON array with objects containing EXACTLY these fields:
{{
  "publishDate": "YYYY-MM-DD",
  "firm": "Savills",
  "title": "exact report title",
  "market": "one of: Tokyo | Seoul | Singapore | Australia | Hong Kong | APAC",
  "sector": "one of: Office | Industrial & Logistics | Retail | Residential | Investment | Data Centre | Mixed/Cross-Sector",
  "summary": "2-3 sentence summary including any specific yield/rent data points",
  "primeYield": "e.g. 3.50% or null",
  "primeRent": "e.g. SGD 10.50 psf/month or null",
  "url": "direct URL on a Savills official domain or null"
}}

CRITICAL RULES:
1. Only include publications whose URL is on an official Savills domain:
   savills.com, savills.com.sg, savills.com.hk, savills.co.jp, savills.co.kr,
   savills.com.au, savills.co.uk, savillsim.com, impacts.savills.com, prospects.savills.com
2. If a report only exists on LinkedIn, Facebook, YouTube, news aggregators
   (realestateasia.com, itiger.com, businesstimes.com.sg, etc.) — OMIT IT.
3. If you cannot find a direct Savills-domain URL for a publication, set url to null
   but still include the publication if you are confident it exists on a Savills domain.
4. Return an empty array [] if no qualifying publications are found for this query.

Today's date: {today}
Return ONLY the JSON array, no other text.
"""


def run_query(client: object, query: str) -> list[dict]:
    """Run a single targeted search query and return extracted publications."""
    prompt = EXTRACTION_PROMPT.format(
        lookback_days=LOOKBACK_DAYS,
        query=query,
        today=date.today().isoformat(),
    )
    try:
        response = client.chat.completions.create(
            model="sonar",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        pubs = json.loads(raw)
        if isinstance(pubs, list):
            return pubs
    except (json.JSONDecodeError, Exception) as e:
        print(f"  [warn] Query failed: {e}")
    return []


def fetch_new_publications() -> list[dict]:
    if not PERPLEXITY_API_KEY:
        print("ERROR: PERPLEXITY_API_KEY not set.")
        return []

    client = OpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai")

    all_raw: list[dict] = []
    seen_titles: set[str] = set()

    for i, query in enumerate(QUERIES, 1):
        print(f"  [{i}/{len(QUERIES)}] {query[:70]}...")
        results = run_query(client, query)
        for p in results:
            title_key = p.get("title", "").strip().lower()[:80]
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                all_raw.append(p)
        # Small delay to avoid rate limiting
        if i < len(QUERIES):
            time.sleep(1.5)

    print(f"[{FIRM}] Raw results across all queries: {len(all_raw)}")

    # Normalise into publication dicts
    cleaned = []
    for p in all_raw:
        if not p.get("title"):
            continue
        cleaned.append(make_publication(
            firm=FIRM,
            title=p.get("title", ""),
            publish_date=p.get("publishDate", date.today().isoformat()),
            market=p.get("market", "APAC"),
            sector=p.get("sector", "Mixed/Cross-Sector"),
            summary=p.get("summary", ""),
            prime_yield=p.get("primeYield"),
            prime_rent=p.get("primeRent"),
            url=p.get("url"),
        ))

    return cleaned


def main():
    start = time.time()

    print("\n" + "=" * 55)
    print(f" Savills Agent — {date.today().isoformat()} ({len(QUERIES)} queries)")
    print("=" * 55)

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
        save_publications(existing + to_add)

    new_yield_pts = sum(1 for p in to_add if p.get("primeYield"))
    new_rent_pts  = sum(1 for p in to_add if p.get("primeRent"))

    append_agent_result(
        run_id=f"{date.today().isoformat()}T06:00:00Z",
        firm=FIRM,
        status="success",
        publications_found=len(new_pubs),
        new_publications=len(to_add),
        new_yield_datapoints=new_yield_pts,
        new_rent_datapoints=new_rent_pts,
        markets=sorted({p["market"] for p in to_add}),
        sectors=sorted({p["sector"] for p in to_add}),
        duration_seconds=time.time() - start,
        notes=f"{len(QUERIES)}-query fan-out, {LOOKBACK_DAYS}-day window. {len(to_add)} new pubs added.",
    )

    git_commit_and_push(
        f"[auto] {FIRM}: +{len(to_add)} new pubs, run logged — {date.today().isoformat()}"
    )


if __name__ == "__main__":
    main()
