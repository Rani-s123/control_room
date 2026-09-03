"""
Candidate scoring — the one place root-cause ranking is defined.

This module is imported by both `controlroom/pipeline.py` and
`evals/run_eval.py` on purpose. If the eval scored one formula and production
ran another, the accuracy number in the README would be fiction.

The score has two terms:

  explanatory power   share of all positive excess stall time the slice owns.
                      Identifies containment: a root cause holds the whole
                      fault, a cohort that merely suffers holds part of it.

  normalised surprise Jensen-Shannon divergence between the slice's baseline
                      and incident share of stall time, divided by the largest
                      surprise among the candidates being compared. This is a
                      tiebreak for the case EP cannot settle: a fault and the
                      cohort it hits hardest can both be near-fully contained.
                      Normalising keeps it scale-free, so the weight below means
                      the same thing whatever the units of the metric are.
"""

from __future__ import annotations

import os

SURPRISE_WEIGHT = float(os.environ.get("SURPRISE_WEIGHT", 0.6))


def score_candidates(candidates: list[dict]) -> list[dict]:
    """Attach a `score` to each candidate and return them best-first.

    Each candidate needs `explanatory_power` and `surprise`; `dim` and `slice`
    are carried through untouched.
    """
    if not candidates:
        return []

    max_surprise = max((abs(float(c.get("surprise") or 0)) for c in candidates), default=0.0)

    scored = []
    for c in candidates:
        ep = float(c.get("explanatory_power") or 0)
        surprise = float(c.get("surprise") or 0)
        norm = (surprise / max_surprise) if max_surprise > 0 else 0.0
        scored.append({**c, "score": round(ep + SURPRISE_WEIGHT * norm, 4),
                       "surprise_normalised": round(norm, 4)})

    return sorted(scored, key=lambda c: -c["score"])
