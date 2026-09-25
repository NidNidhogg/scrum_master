"""
evals/run_evals.py — live extraction-accuracy eval runner.

Feeds each golden transcript through the REAL Gemini parser and scores:
  - captured: expected (alias, description) pairs found in the output
  - exact:    carry-over wording preserved verbatim (normalized compare)
  - forbidden: bare/ambiguous aliases that must not appear

Usage:  PYTHONPATH=. .venv/bin/python evals/run_evals.py
Requires GEMINI_API_KEY in .env — exits early with a note otherwise.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "evals" / "cases"


def _norm(text: str) -> str:
    stripped = re.sub(r"\[.*?\]|\(.*?\)", "", text)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _alias_norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\s:.,]+$", "", text.strip().lower())).strip()


async def run() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set — skipping live evals.")
        return 0

    from agent.core import agent  # noqa: E402  (post-env import)

    cases = sorted(CASES_DIR.glob("*.json"))
    if not cases:
        print(f"No eval cases in {CASES_DIR}")
        return 1

    total_checks = total_passed = 0
    failed_cases = []

    for case_path in cases:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        print(f"\n=== {case['name']} ===")
        try:
            parsed = await agent.parse_transcript(
                case["transcript"],
                existing_backlog=case.get("existing_backlog", ""),
            )
        except Exception as exc:
            print(f"  PARSE FAILED: {exc}")
            failed_cases.append(case["name"])
            continue

        got = [
            (_alias_norm(t.developer_alias), _norm(t.description), t.description)
            for t in parsed.tasks
        ]

        for exp in case.get("expected_tasks", []):
            total_checks += 1
            want_alias = _alias_norm(exp["developer_alias"])
            hits = [g for g in got if g[0] == want_alias]
            if "description_exact" in exp:
                ok = any(g[1] == _norm(exp["description_exact"]) for g in hits)
                label = f"exact '{exp['description_exact'][:50]}'"
            else:
                ok = any(
                    _norm(exp["description_contains"]) in g[1] for g in hits
                )
                label = f"contains '{exp['description_contains']}'"
            total_passed += ok
            print(f"  {'PASS' if ok else 'FAIL'} {want_alias}: {label}")

        for bad in case.get("forbidden_aliases", []):
            total_checks += 1
            ok = not any(g[0] == _alias_norm(bad) for g in got)
            total_passed += ok
            print(f"  {'PASS' if ok else 'FAIL'} forbidden alias '{bad}'")

        if total_passed < total_checks:
            pass  # tracked below

    print(f"\n{'=' * 40}\nScore: {total_passed}/{total_checks}")
    if failed_cases:
        print(f"Cases with parse errors: {', '.join(failed_cases)}")
    return 0 if total_passed == total_checks else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
