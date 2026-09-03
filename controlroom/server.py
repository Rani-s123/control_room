"""HTTP surface. Serves the control room and streams a live run over SSE."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from . import ch, llm, pipeline

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="The Control Room", version="1.0.0")


@app.get("/api/mode")
def mode() -> dict:
    """What is actually powering this instance right now."""
    return {"database": "embedded (chdb)" if ch.demo_mode() else "ClickHouse Cloud",
            "reasoning": "offline stub" if not llm.credentials_present() else "Gemini on Vertex AI",
            "degraded": ch.demo_mode() or not llm.credentials_present()}


@app.get("/healthz")
def healthz() -> dict:
    try:
        n = ch.client().query("SELECT count() FROM control_room.playback_events").result_rows[0][0]
        return {"status": "ok", "events_loaded": n}
    except Exception as exc:
        raise HTTPException(503, f"ClickHouse unreachable: {exc}")


@app.get("/api/scenarios")
def list_scenarios() -> dict:
    """Return all telemetry fault scenarios with ground truth labels."""
    from data.scenarios import SCENARIOS
    return {
        "scenarios": [
            {
                "key": s.key,
                "label": s.label,
                "fault_dim": s.fault_dim,
                "fault_value": s.fault_value,
                "fault_domain": s.fault_domain,
                "artifact": s.artifact,
            }
            for s in SCENARIOS.values()
        ]
    }


@app.get("/api/banner.png")
def get_banner() -> FileResponse:
    banner_path = WEB / "banner.png"
    if banner_path.exists():
        return FileResponse(banner_path, media_type="image/png")
    raise HTTPException(404, "Banner not found")


@app.post("/api/run")
def start_run(scenario: str | None = None) -> StreamingResponse:
    def events():
        for step in pipeline.stream(scenario_key=scenario):
            yield f"data: {json.dumps(step, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    steps = ch.replay(run_id)
    if not steps:
        raise HTTPException(404, f"no run {run_id}")
    return {"run_id": run_id, "steps": steps}


@app.get("/api/incidents")
def incidents() -> dict:
    res = ch.client().query(
        "SELECT run_id, opened_at, severity, culprit_dim, culprit_value, root_cause, "
        "sessions_hit, revenue_at_risk, status FROM control_room.incidents "
        "ORDER BY opened_at DESC LIMIT 25")
    return {"incidents": [dict(zip(res.column_names, r)) for r in res.result_rows]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")
