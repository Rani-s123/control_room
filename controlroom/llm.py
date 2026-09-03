"""
The model layer.

With Vertex credentials configured, every reasoning step goes to Gemini.

Without them, the pipeline still runs end to end using the rule-based fallbacks
below, and every step is labelled `offline-stub` in the run log, in the API
response and in the UI banner. This exists so a reviewer can clone the repo and
see the whole product work in one command — not to pass rules off as reasoning.
The fallbacks are deliberately shallow: they take the top-ranked candidate and
apply a lookup table. The measurable work happens in SQL either way, which is
why `evals/run_eval.py` scores the SQL and not the prose.
"""

from __future__ import annotations

import json
import os

OFFLINE_MODEL = "offline-stub"


def credentials_present() -> bool:
    if os.environ.get("GOOGLE_API_KEY"):
        return True
    return (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"
            and bool(os.environ.get("GOOGLE_CLOUD_PROJECT")))


def client():
    from google import genai
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
        return genai.Client(vertexai=True,
                            project=os.environ["GOOGLE_CLOUD_PROJECT"],
                            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def ask(role: str, system: str, payload: dict, model: str) -> dict:
    """One reasoning turn. JSON in, JSON out. Returns (result, _model, _tokens)."""
    if not credentials_present():
        out = FALLBACKS[role](payload)
        out["_model"], out["_tokens"] = OFFLINE_MODEL, (0, 0)
        return out

    from google.genai import types
    resp = client().models.generate_content(
        model=model,
        contents=json.dumps(payload, default=str),
        config=types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json", temperature=0.1),
    )
    out = json.loads(resp.text)
    usage = resp.usage_metadata
    out["_model"] = model
    out["_tokens"] = (usage.prompt_token_count or 0, usage.candidates_token_count or 0)
    return out


# --- fallbacks --------------------------------------------------------------

def _watcher(p: dict) -> dict:
    flagged = p.get("flagged", 0)
    worst = max((m.get("z_score", 0) for m in p.get("minutes", [])), default=0)
    return {
        "is_incident": flagged > 0,
        "severity": "sev1" if flagged >= 5 else "sev2" if flagged >= 2 else "sev3",
        "headline": (f"Rebuffering above baseline for {flagged} of the last minutes, "
                     f"peak z-score {worst}") if flagged else "Within normal variance",
        "confidence": min(0.5 + 0.1 * flagged, 0.95),
    }


def _diagnostician(p: dict) -> dict:
    ranked = p.get("ranked_candidates") or []
    if not ranked:
        return {"culprit_dim": "cdn", "culprit_value": "unknown", "reasoning": "no candidates",
                "confidence": 0.0, "secondary_dim": "", "secondary_value": ""}
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else {})
    return {
        "culprit_dim": top["dim"], "culprit_value": top["slice"],
        "reasoning": (f"explains {float(top.get('explanatory_power', 0)):.0%} of unforecast "
                      f"stall time across {int(top.get('sessions', 0)):,} sessions"),
        "confidence": min(float(top.get("explanatory_power", 0)), 0.99),
        "secondary_dim": second.get("dim", ""), "secondary_value": second.get("slice", ""),
    }


def _eyewitness(p: dict) -> dict:
    dropped = sum(int(f.get("dropped_frames", 0)) for f in p.get("forensics", []))
    codes = {c for f in p.get("forensics", []) for c in (f.get("error_codes") or [])}
    if dropped > 400 or "DECODE_FAIL" in codes:
        return {"visual_verdict": "corrupt", "fault_domain": "encoder",
                "evidence": f"{dropped:,} dropped frames in the sampled sessions", "confidence": 0.7}
    if "MANIFEST_404" in codes:
        return {"visual_verdict": "frozen", "fault_domain": "packager",
                "evidence": "manifest errors in the sampled sessions", "confidence": 0.7}
    return {"visual_verdict": "clean", "fault_domain": "delivery",
            "evidence": "picture intact in sampled sessions; viewers are stalling, not seeing damage",
            "confidence": 0.65}


REMEDIATION = {
    "delivery": ("Shift traffic off the affected edge and cap the ladder while it drains.",
                 ["Steer the affected slice to the next-healthiest CDN at the traffic manager",
                  "Cap the ABR ladder at 720p for that slice for 10 minutes",
                  "Purge the origin shield for the affected content",
                  "Watch stall-per-session for 5 minutes before restoring"],
                 "Restore the original steering weights once stall-per-session is within baseline.",
                 "delivery / CDN on-call"),
    "encoder": ("Drop the damaged rendition from the manifest and restart the affected encoder.",
                ["Remove the damaged rendition from the published manifest",
                 "Fail over to the standby encoder for that channel",
                 "Confirm the ladder republished without the bad variant",
                 "Keep the rendition out until a clean segment sequence is verified"],
                "Re-add the rendition after two minutes of clean output.",
                "media / encoding on-call"),
    "packager": ("Force a manifest republish and invalidate the stale playlist at the edge.",
                 ["Trigger a packager republish for the affected channel",
                  "Invalidate the playlist at every edge POP",
                  "Verify segment sequence numbers are advancing",
                  "Hold the incident open until manifest age is under 10 seconds"],
                 "None needed; republishing is idempotent.",
                 "packaging on-call"),
    "client": ("Halt the player rollout and pin affected devices to the previous release.",
               ["Stop the staged rollout of the affected player version",
                "Pin the affected device class to the previous release",
                "Confirm new sessions are picking up the pinned version",
                "Keep the rollout frozen until buffer health recovers"],
               "Resume the rollout once buffer health matches the previous release.",
               "client platform on-call"),
}


def _action(p: dict) -> dict:
    domain = p.get("fault_domain", "delivery")
    remediation, steps, rollback, team = REMEDIATION.get(domain, REMEDIATION["delivery"])
    culprit = f"{p.get('culprit_dim')}={p.get('culprit_value')}"
    impact = p.get("impact", {})
    return {
        "remediation": remediation, "runbook_steps": steps, "rollback": rollback,
        "page_team": team,
        "status_page_post": ("Some viewers are seeing playback interruptions. We have identified "
                             "the cause and are rerouting traffic. Playback should recover shortly."),
        "exec_summary": (f"{culprit} is responsible for the current quality drop. "
                         f"{int(impact.get('sessions_hit', 0)):,} sessions affected, "
                         f"${p.get('revenue_at_risk', 0):,.2f} at risk. {remediation}"),
    }


FALLBACKS = {"watcher": _watcher, "diagnostician": _diagnostician,
             "eyewitness": _eyewitness, "action": _action}
