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
    """Return only pubs from new_pubs not already in existing."""
    existing_keys = {_dedup_key(p) for p in existing}
    added = []
    for p in new_pubs:
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


# ── Git commit helper ──────────────────────────────────────────────────────

def git_commit_and_push(message: str) -> None:
    """Stage data/publications.json, commit, and push."""
    subprocess.run(["git", "config", "user.email", "agents@re-apac-research.auto"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "config", "user.name", "APAC Research Agent"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "add", "data/publications.json", "dashboard/index.html"], cwd=REPO_ROOT, check=False)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
    if result.returncode == 0:
        print("No changes to commit.")
        return
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
    print(f"Pushed: {message}")
