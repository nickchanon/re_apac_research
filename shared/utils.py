"""
shared/utils.py
---------------
Shared utilities for all APAC Research agents.
Handles: loading/saving publications.json, deduplication, normalisation.
"""

import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "publications.json"

# ── Official domain allowlist (only publications from these domains are accepted) ──
OFFICIAL_DOMAINS: dict[str, list[str]] = {
    "CBRE": [
        "cbre.com", "cbre.com.au", "cbre.com.hk", "cbre.com.sg",
        "cbre.co.jp", "cbrekorea.com", "cbre.co.kr", "cbrevietnam.com",
        "cbre.com.my", "cbre.co.id", "cbre.co.th",
    ],
    "JLL": [
        "jll.com", "research.jllapsites.com", "jll.com.au", "jll.com.hk",
        "jll.com.sg", "jll.co.jp", "jll.co.kr", "ap.jll.com", "co.jll",
    ],
    "Savills": [
        "savills.com", "savills.com.au", "savills.com.hk", "savills.com.sg",
        "savills.co.jp", "savills.co.kr", "savills.com.cn", "savills.co.uk",
        "savillsim.com", "impacts.savills.com", "prospects.savills.com",
        "savills.com.my", "savills.co.th", "savills.co.id",
    ],
    "Knight Frank": [
        "knightfrank.com", "knightfrank.com.au", "knightfrank.com.sg",
        "knightfrank.com.hk", "knightfrank.co.uk", "apac.knightfrank.com",
        "international-residential.knightfrank.com.sg", "content.knightfrank.com",
        "kfmap.asia", "knightfrank.co.jp", "knightfrank.co.kr",
        "knightfrank.com.my", "knightfrank.co.th",
    ],
}


def is_official_source(url: str | None, firm: str) -> bool:
    """Return True only if url belongs to an official domain for the given firm."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return False
    allowed = OFFICIAL_DOMAINS.get(firm, [])
    return any(domain == a or domain.endswith("." + a) for a in allowed)


# ── Valid field values ─────────────────────────────────────────────────────
VALID_MARKETS = {"Tokyo", "Seoul", "Singapore", "Australia", "Hong Kong", "APAC"}
VALID_SECTORS = {
    "Office",
    "Industrial & Logistics",
    "Retail",
    "Residential",
    "Investment",
    "Data Centre",
    "Mixed/Cross-Sector",
}
VALID_FIRMS = {"CBRE", "JLL", "Savills", "Knight Frank"}

# ── Market aliases (normalise variations from agents) ─────────────────────
MARKET_ALIASES = {
    "sydney": "Australia",
    "melbourne": "Australia",
    "brisbane": "Australia",
    "perth": "Australia",
    "australia": "Australia",
    "tokyo": "Tokyo",
    "japan": "Tokyo",
    "seoul": "Seoul",
    "korea": "Seoul",
    "south korea": "Seoul",
    "singapore": "Singapore",
    "hong kong": "Hong Kong",
    "hk": "Hong Kong",
    "apac": "APAC",
    "asia pacific": "APAC",
    "asia-pacific": "APAC",
}

SECTOR_ALIASES = {
    "industrial": "Industrial & Logistics",
    "industrial/logistics": "Industrial & Logistics",
    "logistics": "Industrial & Logistics",
    "industrial & logistics": "Industrial & Logistics",
    "capital markets": "Investment",
    "investment markets": "Investment",
    "investment": "Investment",
    "office": "Office",
    "retail": "Retail",
    "residential": "Residential",
    "data center": "Data Centre",
    "data centres": "Data Centre",
    "data center": "Data Centre",
    "multi-sector": "Mixed/Cross-Sector",
    "mixed": "Mixed/Cross-Sector",
    "cross-sector": "Mixed/Cross-Sector",
}


def normalise_market(raw: str) -> str:
    return MARKET_ALIASES.get(raw.strip().lower(), raw.strip())


def normalise_sector(raw: str) -> str:
    return SECTOR_ALIASES.get(raw.strip().lower(), raw.strip())


# ── Load & Save ────────────────────────────────────────────────────────────

def load_publications() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_publications(pubs: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Re-assign sequential IDs
    for i, p in enumerate(pubs, 1):
        p["id"] = i
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(pubs, f, indent=2, ensure_ascii=False)


# ── Deduplication ──────────────────────────────────────────────────────────

def _dedup_key(pub: dict) -> str:
    """Primary key: URL if present, otherwise normalised title."""
    if pub.get("url"):
        return pub["url"].strip().lower()
    title = pub.get("title", "").strip().lower()
    # Remove common suffixes/punctuation for fuzzy match
    title = re.sub(r"[^a-z0-9 ]", "", title)
    return title


def deduplicate(existing: list[dict], new_pubs: list[dict]) -> list[dict]:
    """Return only pubs from new_pubs not already in existing AND from official sources."""
    existing_keys = {_dedup_key(p) for p in existing}
    added = []
    for p in new_pubs:
        url = p.get("url") or p.get("source") or p.get("link", "")
        firm = p.get("firm", "")
        if not is_official_source(url, firm):
            print(f"[{firm}] Skipped non-official source: {url}")
            continue
        key = _dedup_key(p)
        if key not in existing_keys:
            existing_keys.add(key)
            added.append(p)
    return added


# ── Publication schema ─────────────────────────────────────────────────────

SCHEMA_FIELDS = [
    "id", "publishDate", "firm", "market", "sector",
    "title", "summary", "primeYield", "primeRent",
    "savills_yield", "savills_rent", "url", "notes",
]


def make_publication(
    firm: str,
    title: str,
    publish_date: str,          # YYYY-MM-DD
    market: str,
    sector: str,
    summary: str = "",
    prime_yield: str | None = None,
    prime_rent: str | None = None,
    url: str | None = None,
    notes: str | None = None,
) -> dict:
    """Create a publication dict with all required fields."""
    return {
        "id": None,              # assigned on save
        "publishDate": publish_date or date.today().isoformat(),
        "firm": firm,
        "market": normalise_market(market),
        "sector": normalise_sector(sector),
        "title": title.strip(),
        "summary": summary.strip(),
        "primeYield": prime_yield,
        "primeRent": prime_rent,
        "savills_yield": None,
        "savills_rent": None,
        "url": url,
        "notes": notes,
    }


# ── Agent run logging ────────────────────────────────────────────────────

RUN_LOG_FILE = REPO_ROOT / "data" / "agent_runs.json"


def load_run_log() -> list[dict]:
    if not RUN_LOG_FILE.exists():
        return []
    with open(RUN_LOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def append_agent_result(
    run_id: str,
    firm: str,
    status: str,
    publications_found: int,
    new_publications: int,
    new_yield_datapoints: int,
    new_rent_datapoints: int,
    markets: list[str],
    sectors: list[str],
    duration_seconds: float,
    notes: str = "",
) -> None:
    """
    Append or update an agent result for today's run in agent_runs.json.
    Creates a new run entry if one doesn't exist for today.
    """
    import datetime
    today = date.today().isoformat()
    run_id_full = run_id or f"{today}T06:00:00Z"

    log = load_run_log()

    # Find today's run entry or create it
    run_entry = next((r for r in log if r["date"] == today), None)
    if run_entry is None:
        run_entry = {
            "runId": run_id_full,
            "date": today,
            "triggeredBy": "github-actions",
            "agents": [],
            "totals": {
                "publicationsFound": 0,
                "newPublications": 0,
                "newYieldDatapoints": 0,
                "newRentDatapoints": 0,
            },
        }
        log.insert(0, run_entry)  # newest first

    # Remove existing entry for this firm (in case of re-run)
    run_entry["agents"] = [a for a in run_entry["agents"] if a["firm"] != firm]

    agent_result = {
        "firm": firm,
        "status": status,
        "publicationsFound": publications_found,
        "newPublications": new_publications,
        "newYieldDatapoints": new_yield_datapoints,
        "newRentDatapoints": new_rent_datapoints,
        "markets": sorted(set(markets)),
        "sectors": sorted(set(sectors)),
        "durationSeconds": round(duration_seconds, 1),
        "notes": notes,
    }
    run_entry["agents"].append(agent_result)

    # Recompute totals
    run_entry["totals"] = {
        "publicationsFound": sum(a["publicationsFound"] for a in run_entry["agents"]),
        "newPublications": sum(a["newPublications"] for a in run_entry["agents"]),
        "newYieldDatapoints": sum(a["newYieldDatapoints"] for a in run_entry["agents"]),
        "newRentDatapoints": sum(a["newRentDatapoints"] for a in run_entry["agents"]),
    }

    RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"[{firm}] Run logged: {new_publications} new pubs, {new_yield_datapoints} yield pts, {new_rent_datapoints} rent pts")


# ── Git commit helper ──────────────────────────────────────────────────────

def git_commit_and_push(message: str) -> None:
    """Stage data/publications.json, commit, and push."""
    subprocess.run(["git", "config", "user.email", "agents@re-apac-research.auto"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "config", "user.name", "APAC Research Agent"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "add", "data/publications.json", "data/agent_runs.json", "docs/index.html"], cwd=REPO_ROOT, check=False)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
    if result.returncode == 0:
        print("No changes to commit.")
        return
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
    print(f"Pushed: {message}")
