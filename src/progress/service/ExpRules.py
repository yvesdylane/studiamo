"""Exp, Xexp, level and rank rules in one place. Migration 0006 has the same
formulas in SQL (level_exp_total, rank_xexp_total, the player_levels view):
change both together.

exp   (drives the LEVEL)
  notes      full max_exp, once, when completed
  exercises  attempt n awards floor(max_exp * score_pct/100 * retake_decay^(n-1))
  exams      floor(max_exp * best_score_pct/100); only the difference from what
             was already earned (if positive)
  level      L -> L+1 costs 1.5^L * 10 + 10 exp, starting at level 0; the total
             to reach L is 20 * (1.5^L - 1) + 10 * L, rounded up to whole exp

Xexp  (drives the RANK; it measures the quality of the work)
  exercises  max_exp * 0.85 * best_score% when the best score is >= 75%, else 0;
  and exams  only the best attempt counts: a better retry adds the difference
  notes      2.5% of max_exp, once
  rank       rank level r -> r+1 costs RANK_GROWTH^r * 5 + 5 Xexp (RANK_GROWTH = 1.18);
             the total to reach r is 5 * (g^r - 1) / (g - 1) + 5 * r.
             5 levels per letter, F1 ... S5.
"""

from __future__ import annotations

import math
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction

from src.player.PlayerStats import PlayerRank

XEXP_FACTOR = Decimal("0.85")          # Xexp = exp * this * score%, for good work
XEXP_MIN_SCORE = Decimal("75")         # best score needed for any Xexp
NOTE_XEXP_FACTOR = Decimal("0.025")    # notes: 2.5% of their exp
LEVELS_PER_LETTER = 5
LETTERS = [PlayerRank.F, PlayerRank.E, PlayerRank.D, PlayerRank.C, PlayerRank.B, PlayerRank.A, PlayerRank.S]
MAX_RANK_LEVEL = len(LETTERS) * LEVELS_PER_LETTER - 1   # 34 = S5
# exp given to new content; only admins change it (lecturers cannot)
DEFAULT_EXP = {"note": 20, "exercise": 50, "system_exam": 50, "scheduled_exam": 100}

RANK_GROWTH = Fraction(118, 100)       # migration 0007; change it there too
CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_DOWN)


# ------------------------------------------------------------------ exp
def exercise_exp(max_exp: int, score_pct: float | Decimal, retake_decay: float | Decimal, attempt_no: int) -> int:
    value = Decimal(max_exp) * Decimal(score_pct) / 100 * Decimal(retake_decay) ** (attempt_no - 1)
    return max(0, math.floor(value))


def exam_exp_due(max_exp: int, best_score_pct: float | Decimal, already_earned: int) -> int:
    target = math.floor(Decimal(max_exp) * Decimal(best_score_pct) / 100)
    return max(0, target - already_earned)


def level_exp_total(level: int) -> int:
    """Total exp needed to reach level (level 0 = 0 exp)."""
    return math.ceil(20 * (Fraction(3, 2) ** level - 1) + 10 * level)


def level_from_exp(total_exp: int) -> int:
    level = 0
    while level < 200 and level_exp_total(level + 1) <= total_exp:
        level += 1
    return level


# ----------------------------------------------------------------- Xexp
def task_xexp(max_exp: int, best_score_pct: float | Decimal) -> Decimal:
    """Xexp an exercise or exam is worth at this best score."""
    score = Decimal(best_score_pct)
    if score < XEXP_MIN_SCORE:
        return Decimal("0.00")
    return _cents(Decimal(max_exp) * XEXP_FACTOR * score / 100)


def xexp_due(max_exp: int, best_score_pct: float | Decimal, already_earned: Decimal) -> Decimal:
    """What to add now: the task's Xexp at the best score minus what was given before."""
    return max(Decimal("0.00"), task_xexp(max_exp, best_score_pct) - Decimal(already_earned))


def note_xexp(max_exp: int) -> Decimal:
    return _cents(Decimal(max_exp) * NOTE_XEXP_FACTOR)


# ----------------------------------------------------------------- rank
def rank_xexp_total(rank_level: int) -> Decimal:
    """Total Xexp needed to reach rank_level (0 = F1)."""
    g = RANK_GROWTH
    exact = 5 * (g ** rank_level - 1) / (g - 1) + 5 * rank_level
    return Decimal(exact.numerator) / Decimal(exact.denominator)


def rank_level_from_xexp(total_xexp: Decimal | float) -> int:
    total = Decimal(total_xexp)
    r = 0
    while r < MAX_RANK_LEVEL and rank_xexp_total(r + 1) <= total:
        r += 1
    return r


def rank_letter(rank_level: int) -> PlayerRank:
    return LETTERS[min(rank_level, MAX_RANK_LEVEL) // LEVELS_PER_LETTER]


def rank_label(rank_level: int) -> str:
    """'F1' ... 'S5'."""
    r = min(rank_level, MAX_RANK_LEVEL)
    return f"{rank_letter(r).value}{r % LEVELS_PER_LETTER + 1}"
