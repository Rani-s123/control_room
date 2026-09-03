"""
The Control Room pipeline.

Five agents, always in this order, always the same five steps. Gemini decides
*what the evidence means*; it never decides *which evidence to collect* on the
critical path. That is what makes a run reproducible — re-run a run_id and you
get the same queries against the same window.

    WATCHER        fixed z-score sweep over the 1-minute rollup
    DIAGNOSTICIAN  explanatory-power attribution across every dimension
    EYEWITNESS     Gemini looks at frames from the affected renditions
    IMPACT         sessions, watch-time and revenue arithmetic
    ACTION         remediation + incident comms
    CONTINUITY     re-measures the slice and decides whether to actually close
"""

from __future__ import annotations

import json
import os
from typing import Callable, Iterator

from . import ch, llm
from .attribution import score_candidates
from .eyewitness import inspect_frames

MODEL_REASONING = os.environ.get("GEMINI_REASONING_MODEL", "gemini-2.5-pro")
MODEL_FAST = os.environ.get("GEMINI_FAST_MODEL", "gemini-2.5-flash")

DIMENSIONS = ["cdn", "region", "device_type", "isp", "player_version", "rendition"]
WINDOW_MIN = int(os.environ.get("INCIDENT_WINDOW_MIN", 20))
BASELINE_MIN = int(os.environ.get("BASELINE_MIN", 180))
RECHECK_MIN = int(os.environ.get("RECHECK_MIN", 5))
Z_THRESHOLD = float(os.environ.get("Z_THRESHOLD", 2.0))


def _ask(role: str, system: str, payload: dict, model: str = MODEL_REASONING) -> dict:
    """One reasoning turn, JSON in, JSON out. Falls back to a labelled offline
    stub when no credentials are configured — see controlroom/llm.py."""
    return llm.ask(role, system, payload, model)


# ---------------------------------------------------------------------------


WATCHER_BRIEF = """You are the Watcher in a broadcast control room for a live OTT service.
You receive per-minute quality-of-experience rows that a fixed z-score rule has already
scored. You do not re-do the maths. Decide only: is this a real incident worth waking
someone for, or normal variance? Return JSON:
{"is_incident": bool, "severity": "sev1"|"sev2"|"sev3", "headline": str, "confidence": 0-1}
Severity: sev1 = live event at risk, sev2 = degraded for a large cohort, sev3 = localised."""

DIAGNOSTICIAN_BRIEF = """You are the Diagnostician. For every dimension you receive each
slice's stall time now, its forecast from its own baseline, and two scores:

  explanatory_power  the share of ALL unforecast stall time this slice accounts for
  surprise           how much the slice's share of stall time shifted (JS divergence)

Name the dimension and value that best explains the incident. Rules, in order:

1. A root cause CONTAINS the fault. Prefer high explanatory power over high severity.
   A slice that stalls badly but stalled just as badly yesterday is not a cause.
2. When two slices are both largely contained — typically a fault and the cohort it
   hurts most — the one with higher surprise is the cause; the other is the amplifier.
3. Report the amplifier as secondary. Both go in the page.

Return JSON:
{"culprit_dim": str, "culprit_value": str, "reasoning": str, "confidence": 0-1,
 "secondary_dim": str, "secondary_value": str}"""

EYEWITNESS_BRIEF = """You are the Eyewitness. You are shown sample frames captured from the
affected stream renditions, plus the session forensics. Decide whether the picture itself is
damaged (macroblocking, frozen frame, black frame, corrupt slice — an encoder or packaging
fault) or whether the picture is clean and viewers are simply stalling (a delivery fault).
This distinction changes who gets paged. Return JSON:
{"visual_verdict": "clean"|"corrupt"|"frozen"|"black", "fault_domain": "encoder"|"packager"|"delivery",
 "evidence": str, "confidence": 0-1}"""

ACTION_BRIEF = """You are the Action agent. Given a confirmed root cause and its measured
business impact, produce a remediation the on-call engineer can execute in under two minutes,
plus viewer-facing comms. Be specific about the lever (CDN failover, rendition cap, shield
purge, player-version rollback). Return JSON:
{"remediation": str, "runbook_steps": [str], "rollback": str, "status_page_post": str,
 "exec_summary": str, "page_team": str}"""


def run(emit: Callable[[dict], None] | None = None) -> dict:
    """Execute one full incident response. Yields/emits each step as it completes."""
    run_id = ch.new_run_id()
    steps: list[ch.StepLog] = []

    def record(step: ch.StepLog) -> None:
        steps.append(step)
        ch.log_step(step)
        if emit:
            emit({"run_id": run_id, "step_no": step.step_no, "agent": step.agent,
                  "action": step.action, "finding": step.finding,
                  "latency_ms": step.latency_ms, "rows_scanned": step.rows_scanned,
                  "confidence": step.confidence, "result": step.result})

    # -- 1. WATCHER ---------------------------------------------------------
    rows, ms, read = ch.run_template("detect", {
        "lookback_min": WINDOW_MIN, "baseline_min": BASELINE_MIN, "z_threshold": Z_THRESHOLD})
    flagged = [r for r in rows if r.get("is_anomalous")]
    verdict = _ask("watcher", WATCHER_BRIEF, {"minutes": rows[:30], "flagged": len(flagged)}, MODEL_FAST)
    record(ch.StepLog(run_id, 1, "watcher", "z-score sweep over qoe_1m",
                      sql_executed=ch.load_template("detect"), rows_scanned=read, latency_ms=ms,
                      model=verdict["_model"], tokens_in=verdict["_tokens"][0], tokens_out=verdict["_tokens"][1],
                      finding=verdict["headline"], confidence=verdict["confidence"],
                      result={"minutes": rows[:30], "severity": verdict["severity"]}))

    if not verdict["is_incident"]:
        return {"run_id": run_id, "status": "all_clear", "offline": not llm.credentials_present(), "steps": [s.__dict__ for s in steps]}

    # -- 2. DIAGNOSTICIAN ---------------------------------------------------
    blame, total_ms, total_read = {}, 0, 0
    for dim in DIMENSIONS:
        r, ms, read = ch.run_template("blame", {
            "dim": dim, "window_min": WINDOW_MIN, "baseline_min": BASELINE_MIN})
        blame[dim] = r
        total_ms += ms
        total_read += read

    # The shortlist is computed here, not by the model: explanatory power with
    # surprise as the tiebreak. evals/run_eval.py scores exactly this ranking.
    ranked = score_candidates([{"dim": d, **r} for d, rows in blame.items() for r in rows])[:6]

    diag = _ask("diagnostician", DIAGNOSTICIAN_BRIEF, {"ranked_candidates": ranked,
                                                       "contribution_by_dimension": blame})
    dim, value = diag["culprit_dim"], diag["culprit_value"]
    ep_at_detection = next((float(r.get("explanatory_power", 0)) for r in ranked
                            if r["dim"] == dim and r["slice"] == value), 0.0)

    forensics, f_ms, f_read = ch.run_template(
        "forensics", {"dim": dim, "value": value, "window_min": WINDOW_MIN}, statement=0)

    record(ch.StepLog(run_id, 2, "diagnostician",
                      f"contribution analysis × {len(DIMENSIONS)} dims, then session drill-down",
                      sql_executed=ch.load_template("blame"),
                      rows_scanned=total_read + f_read, latency_ms=total_ms + f_ms,
                      model=diag["_model"], tokens_in=diag["_tokens"][0], tokens_out=diag["_tokens"][1],
                      finding=f"{dim}={value} — {diag['reasoning']}", confidence=diag["confidence"],
                      result={"blame": blame, "ranked": ranked, "forensics": forensics[:10],
                              "culprit_dim": dim, "culprit_value": value}))

    # -- 3. EYEWITNESS ------------------------------------------------------
    frames = inspect_frames(dim, value, forensics[:5])
    eye = _ask("eyewitness", EYEWITNESS_BRIEF, {"forensics": forensics[:5], "frame_notes": frames["notes"]})
    record(ch.StepLog(run_id, 3, "eyewitness", f"visual inspection of {frames['count']} frames",
                      latency_ms=frames["latency_ms"], model=eye["_model"],
                      tokens_in=eye["_tokens"][0], tokens_out=eye["_tokens"][1],
                      finding=f"{eye['visual_verdict']} picture → {eye['fault_domain']} fault. {eye['evidence']}",
                      confidence=eye["confidence"],
                      result={"frames": frames["thumbnails"], "verdict": eye["visual_verdict"],
                              "fault_domain": eye["fault_domain"]}))

    # -- 4. IMPACT ----------------------------------------------------------
    impact_rows, i_ms, i_read = ch.run_template(
        "forensics", {"dim": dim, "value": value, "window_min": WINDOW_MIN}, statement=1)
    impact = impact_rows[0] if impact_rows else {}
    arpu_hour = float(os.environ.get("ARPU_PER_VIEWER_HOUR_USD", 0.42))
    at_risk = round(impact.get("stall_minutes", 0) / 60 * arpu_hour
                    + impact.get("premium_viewers_hit", 0) * 0.03, 2)
    record(ch.StepLog(run_id, 4, "impact", "blast-radius and revenue arithmetic",
                      sql_executed=ch.load_template("forensics").split(";")[1],
                      rows_scanned=i_read, latency_ms=i_ms,
                      finding=f"{impact.get('sessions_hit', 0):,} sessions, "
                              f"{impact.get('rage_quits', 0):,} abandons, ${at_risk:,.2f} at risk",
                      confidence=1.0,
                      result={**impact, "revenue_at_risk": at_risk}))

    # -- 5. ACTION ----------------------------------------------------------
    act = _ask("action", ACTION_BRIEF, {
        "severity": verdict["severity"], "culprit_dim": dim, "culprit_value": value,
        "secondary": f"{diag.get('secondary_dim')}={diag.get('secondary_value')}",
        "fault_domain": eye["fault_domain"], "visual_verdict": eye["visual_verdict"],
        "impact": impact, "revenue_at_risk": at_risk})
    record(ch.StepLog(run_id, 5, "action", "remediation and comms",
                      model=act["_model"], tokens_in=act["_tokens"][0], tokens_out=act["_tokens"][1],
                      finding=act["remediation"], confidence=0.9,
                      result=act))

    # -- 6. CONTINUITY ------------------------------------------------------
    # An incident is not closed because an agent wrote a remediation. It is
    # closed because the numbers came back. This step re-measures the culprit
    # slice over the freshest minutes and reports the direction of travel.
    recheck, r_ms, r_read = ch.run_template("blame", {
        "dim": dim, "window_min": RECHECK_MIN, "baseline_min": BASELINE_MIN})
    still = next((r for r in recheck if r["slice"] == value), None)
    ep_now = float(still.get("explanatory_power", 0)) if still else 0.0
    recovering = ep_now < ep_at_detection * 0.5
    status = "mitigated" if recovering else "open"
    record(ch.StepLog(run_id, 6, "continuity",
                      f"re-measured {dim}={value} over the last {RECHECK_MIN} minutes",
                      sql_executed=ch.load_template("blame"), rows_scanned=r_read, latency_ms=r_ms,
                      finding=(f"explanatory power {ep_at_detection:.0%} → {ep_now:.0%} — "
                               + ("excess is falling, holding as mitigated"
                                  if recovering else
                                  "excess still concentrated here, incident stays open")),
                      confidence=1.0,
                      result={"explanatory_power_now": ep_now, "status": status,
                              "recheck_window_min": RECHECK_MIN}))

    ch.open_incident(run_id, severity=verdict["severity"], culprit_dim=dim, culprit_value=value,
                     root_cause=f"{eye['fault_domain']}: {diag['reasoning']}",
                     sessions_hit=int(impact.get("sessions_hit", 0)), revenue_at_risk=at_risk,
                     remediation=act["remediation"], status=status)

    return {"run_id": run_id, "status": "incident", "offline": not llm.credentials_present(),
            "embedded_db": ch.demo_mode(), "severity": verdict["severity"],
            "culprit": f"{dim}={value}", "revenue_at_risk": at_risk, "final_status": status,
            "steps": [s.__dict__ for s in steps]}


def stream() -> Iterator[dict]:
    """Generator form for the SSE endpoint."""
    import queue
    import threading

    q: queue.Queue = queue.Queue()
    result: dict = {}

    def worker():
        try:
            result.update(run(emit=q.put))
        except Exception as exc:  # surfaced in the UI, not swallowed
            q.put({"agent": "system", "error": str(exc)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while (item := q.get()) is not None:
        yield item
    yield {"done": True, **result}
