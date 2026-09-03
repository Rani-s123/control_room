"""ClickHouse access layer.

Two separate paths, deliberately:

  * `run_template()` executes SQL that ships in this repo, with server-side
    bound parameters. The critical path never runs model-authored SQL.
  * The ClickHouse MCP server (see agents.py) is exposed to the Diagnostician
    for open-ended follow-up questions only, and everything it does is logged.
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "queries"


@dataclass
class StepLog:
    run_id: str
    step_no: int
    agent: str
    action: str
    sql_executed: str = ""
    rows_scanned: int = 0
    latency_ms: int = 0
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    finding: str = ""
    confidence: float = 0.0
    result: Any = field(default=None, repr=False)


def demo_mode() -> bool:
    """True when no ClickHouse Cloud credentials are configured."""
    return os.environ.get("DEMO_MODE", "").lower() in ("1", "true") or not os.environ.get("CLICKHOUSE_HOST")


class EmbeddedClient:
    """Minimal stand-in for clickhouse_connect backed by chdb, the same
    ClickHouse engine compiled in-process. Lets the whole product run from a
    fresh clone with no cloud account. Same SQL, same semantics, one machine."""

    def __init__(self) -> None:
        try:
            import chdb.session as chs
            self._sess = chs.Session()
        except ImportError as err:
            raise RuntimeError(
                "chdb is required for embedded mode on this machine. "
                "Install it using `pip install chdb` or configure CLICKHOUSE_HOST for cloud mode."
            ) from err

    @staticmethod
    def _bind(sql: str, parameters: dict | None) -> str:
        for key, val in (parameters or {}).items():
            repl = val if key == "dim" else (f"'{val}'" if isinstance(val, str) else str(val))
            sql = re.sub(r"\{%s:[^}]+\}" % re.escape(key), str(repl), sql)
        return sql

    def query(self, sql: str, parameters: dict | None = None):
        raw = str(self._sess.query(self._bind(sql, parameters), "JSONCompact"))
        if not raw.strip():
            return _Result([], [])
        doc = json.loads(raw)
        return _Result([c["name"] for c in doc.get("meta", [])], doc.get("data", []))

    def command(self, sql: str) -> None:
        self._sess.query(sql)

    def insert(self, table: str, rows: list, column_names: list[str]) -> None:
        def lit(v):
            if hasattr(v, "strftime"):
                return "'" + v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "'"
            if v is None:
                return "NULL"
            if isinstance(v, str):
                return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
            return str(v)
        for i in range(0, len(rows), 100_000):
            values = ",".join("(" + ",".join(lit(c) for c in r) + ")" for r in rows[i:i + 100_000])
            self._sess.query(f"INSERT INTO control_room.{table} ({','.join(column_names)}) VALUES {values}")


class _Result:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows
        self.summary = {}


@functools.lru_cache(maxsize=1)
def client():
    if demo_mode():
        c = EmbeddedClient()
        bootstrap_embedded(c)
        return c
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
        database="control_room",
    )


def bootstrap_embedded(c) -> None:
    """Create the schema and load one scenario into the in-process engine."""
    import sys
    from datetime import datetime, timedelta, timezone
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.generate_events import COLUMNS, build_batch
    from data.scenarios import DEFAULT, SCENARIOS

    schema = (Path(__file__).resolve().parent.parent / "sql" / "01_schema.sql").read_text()
    for stmt in schema.split(";"):
        body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            c.command(body)

    sc = SCENARIOS[os.environ.get("DEMO_SCENARIO", DEFAULT)]
    rows = int(os.environ.get("DEMO_ROWS", 250_000))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    done = 0
    while done < rows:
        n = min(100_000, rows - done)
        c.insert("playback_events",
                 build_batch(n, now - timedelta(minutes=180), now,
                             now - timedelta(minutes=22), sc, seed=done),
                 column_names=COLUMNS)
        done += n


@functools.lru_cache(maxsize=32)
def load_template(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text()


def run_template(name: str, params: dict, statement: int = 0) -> tuple[list[dict], int, int]:
    """Execute a repo-owned query. Returns (rows, elapsed_ms, rows_read)."""
    sql = load_template(name).split(";")[statement]
    started = time.perf_counter()
    res = client().query(sql, parameters=params)
    elapsed = int((time.perf_counter() - started) * 1000)
    rows = [dict(zip(res.column_names, r)) for r in res.result_rows]
    rows_read = int(res.summary.get("read_rows", 0)) if res.summary else 0
    return rows, elapsed, rows_read


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


def log_step(step: StepLog) -> None:
    from datetime import datetime, timezone
    client().insert(
        "agent_runs",
        [(step.run_id, step.step_no, datetime.now(timezone.utc), step.agent,
          step.action, step.sql_executed[:4000], step.rows_scanned, step.latency_ms,
          step.model, step.tokens_in, step.tokens_out, step.finding[:4000], step.confidence)],
        column_names=["run_id", "step_no", "ts", "agent", "action", "sql_executed",
                      "rows_scanned", "latency_ms", "model", "tokens_in", "tokens_out",
                      "finding", "confidence"],
    )


def open_incident(run_id: str, **kw) -> None:
    from datetime import datetime, timezone
    client().insert(
        "incidents",
        [(run_id, datetime.now(timezone.utc), None, kw["severity"], kw["culprit_dim"],
          kw["culprit_value"], kw["root_cause"], kw["sessions_hit"],
          kw["revenue_at_risk"], kw["remediation"], kw.get("status", "open"))],
        column_names=["run_id", "opened_at", "closed_at", "severity", "culprit_dim",
                      "culprit_value", "root_cause", "sessions_hit", "revenue_at_risk",
                      "remediation", "status"],
    )


def replay(run_id: str) -> list[dict]:
    res = client().query(
        "SELECT step_no, ts, agent, action, sql_executed, rows_scanned, latency_ms, "
        "model, finding, confidence FROM control_room.agent_runs "
        "WHERE run_id = {r:String} ORDER BY step_no",
        parameters={"r": run_id},
    )
    return [dict(zip(res.column_names, r)) for r in res.result_rows]
