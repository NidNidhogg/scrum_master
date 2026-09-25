"""
evals/test_name_resolution.py — offline evals for scored name resolution.

These are the production error cases: names picked up wrong (substring
collisions like "Иван" -> "Иванов", bare first names shared by several
people). Run: PYTHONPATH=. pytest evals/test_name_resolution.py
"""
from __future__ import annotations

import pytest

from bot.handlers.admin import score_name_match, _normalize_name


# ── scoring ladder ──────────────────────────────────────────

def test_alias_exact_beats_everything():
    s = score_name_match("дима", aliases=["дима"], full_name="Дмитрий С.", username="ds")
    assert s == 100


def test_full_name_exact():
    s = score_name_match("Дмитрий С.", full_name="Дмитрий С.")
    assert s == 95


def test_username_exact():
    s = score_name_match("dsmirnov", username="dsmirnov")
    assert s == 90


def test_first_token_of_full_name():
    s = score_name_match("дмитрий", full_name="Дмитрий С.")
    assert s == 70


# ── the prod bug: substring must NOT match ─────────────────

def test_ivan_does_not_match_ivanov():
    # "Иван" was silently resolving to "Иванов" via substring match
    assert score_name_match("иван", full_name="Иванов") == 0
    assert score_name_match("иван", username="ivanov") == 0


def test_partial_token_no_match():
    # "дим" is a prefix of "дмитрий" but not a full token -> no match
    assert score_name_match("дим", full_name="Дмитрий С.") == 0


def test_unknown_name_scores_zero():
    assert score_name_match("владимир", full_name="Иван Петров", username="ipetrov") == 0


def test_empty_query_scores_zero():
    assert score_name_match("") == 0
    assert score_name_match("   .,") == 0


# ── token overlap with alias ────────────────────────────────

def test_alias_token_overlap():
    # alias "дима с" contains query token "дима" -> token overlap
    s = score_name_match("дима", aliases=["дима с"])
    assert s == 60


# ── normalization ───────────────────────────────────────────

def test_normalize_strips_trailing_punct():
    assert _normalize_name("Дмитрий: ") == "дмитрий"
    assert _normalize_name("  Иван  Петров.") == "иван петров"
