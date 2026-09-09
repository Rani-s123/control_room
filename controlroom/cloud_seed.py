"""One-shot bootstrap of a ClickHouse Cloud service, run from inside the app.

The hosted deployment has to prove the ClickHouse track requirement on its own:
a judge opens the URL and `/healthz` must report rows behind the MCP transport.
Requiring a laptop to have pushed the data first makes that fragile, so the
container can do it itself on first boot.

Enabled by BOOTSTRAP_ON_START. It is deliberately opt-in and idempotent - it
applies the schema (every statement is CREATE ... IF NOT EXISTS) and seeds rows
only when the table is empty, so a restart or a second replica does not double
the dataset.

Writes go over clickhouse-connect, never the MCP server, which is read-only by
design. Reads on the critical path are unaffected.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(f"[cloud-seed] {msg}", flush=True)


def enabled() -> bool:
    return os.environ.get("BOOTSTRAP_ON_START", "").lower() in ("1", "true", "yes")


def run() -> None:
    """Create the schema and seed telemetry if the table is empty."""
    if not os.environ.get("CLICKHOUSE_HOST"):
        _log("CLICKHOUSE_HOST not set - skipping (app will run in embedded demo mode)")
        return

    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
    )

    _log("applying schema")
    applied = 0
    for stmt in (ROOT / "sql" / "01_schema.sql").read_text().split(";"):
        body = "\n".join(l for l in stmt.splitlines()
                         if not l.strip().startswith("--")).strip()
        if body:
            client.command(body)
            applied += 1
    _log(f"schema ready ({applied} statements)")

    existing = client.query(
        "SELECT count() FROM control_room.playback_events").result_rows[0][0]
    if existing:
        _log(f"{existing:,} rows already present - not seeding again")
        return

    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timedelta, timezone
    from data.generate_events import COLUMNS, build_batch
    from data.scenarios import DEFAULT, SCENARIOS

    scenario = SCENARIOS[os.environ.get("DEMO_SCENARIO", DEFAULT)]
    rows = int(os.environ.get("BOOTSTRAP_ROWS", 1_000_000))
    batch = int(os.environ.get("BOOTSTRAP_BATCH", 100_000))
    base = int(os.environ.get("DEMO_SEED", 0)) + len(scenario.key)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    window_start = now - timedelta(minutes=180)
    incident_from = now - timedelta(minutes=22)

    _log(f"seeding {rows:,} events for scenario '{scenario.key}'")
    started, done = time.time(), 0
    while done < rows:
        n = min(batch, rows - done)
        client.insert(
            "control_room.playback_events",
            build_batch(n, window_start, now, incident_from, scenario, seed=base + done),
            column_names=COLUMNS,
        )
        done += n
        _log(f"  {done:,}/{rows:,}")
    _log(f"seeded {done:,} rows in {time.time() - started:.1f}s")


def run_safely() -> None:
    """Never let a seeding failure stop the server from binding its port.

    The platform health check has to pass even if ClickHouse is briefly
    unreachable; the error is logged and /healthz will report the real state.
    """
    try:
        run()
    except Exception as exc:
        _log(f"FAILED: {type(exc).__name__}: {exc}")
