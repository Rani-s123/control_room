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

# Fitted on seed bases 0/1000/2000 only — the three datasets per archetype that
# `evals/run_eval.py --trials 3` produces. Every later trial draws seeds this
# weight has never seen, and the eval scores those separately, because a number
# measured on the seeds a weight was fitted on is not a result.
#
# The choice is not delicate. Accuracy is flat from about 0.4 to 0.7 on both the
# fitted and the held-out seeds, and only falls away below 0.3, where
# explanatory power carries the ranking with no tiebreak at all, and above 1.0,
# where the tiebreak starts outvoting containment. Sitting mid-plateau is what
# makes it safe to leave this alone.
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
