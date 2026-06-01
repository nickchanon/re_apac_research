"""
agents/run_all.py
-----------------
Runs all four agents sequentially.
Used by GitHub Actions and for local full refreshes.

Usage:  python agents/run_all.py
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

AGENTS = ["cbre_agent", "jll_agent", "savills_agent", "knightfrank_agent"]


def main():
    for agent_name in AGENTS:
        print(f"\n{'#'*60}")
        print(f"  Running {agent_name}")
        print(f"{'#'*60}")
        try:
            module = importlib.import_module(agent_name)
            module.main()
        except Exception as e:
            print(f"[ERROR] {agent_name} failed: {e}")
            # Continue with remaining agents even if one fails
            continue

    print("\nAll agents complete.")


if __name__ == "__main__":
    main()
