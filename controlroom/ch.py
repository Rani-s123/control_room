"""ClickHouse access layer.

Every read on the critical path is executed by the official ClickHouse MCP
server (`mcp-clickhouse`), and every one of those queries ships in this repo.
Those two facts are not in tension, and the distinction is the whole design:

  * The MCP server is the *transport*. It is what connects to ClickHouse Cloud,
    runs the statement and returns the rows — the integration ClickHouse
    publishes and maintains, rather than a bespoke driver call.
  * `sql/queries/` is the *decision*. Which query runs, against which window,
    for which dimension, is fixed by `pipeline.py` before any model is asked
    anything. Re-run a run_id and you get the same queries and the same
    culprit.

What a model must never do is choose the evidence on the critical path, and it
never does. It is handed rows it did not select. The same MCP session is also
exposed to the ADK agent (see `agents.py`) for open-ended follow-up questions,
where a wrong query costs nothing and everything it runs is logged.

Three transports, selected by `transport()`:

  mcp        reads through the official MCP server against ClickHouse Cloud or
             a self-hosted cluster. The default whenever CLICKHOUSE_HOST is set.
  direct     reads through clickhouse-connect. An escape hatch for debugging
             the MCP layer, and the path writes always take, because the MCP
             server is read-only by design.
  embedded   chdb in-process, for the zero-credential demo. Labelled everywhere
             it appears.
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

# The only identifiers that may ever be substituted into `{dim:Identifier}`.
# The culprit dimension arrives from a model response, so it is validated
# against this set before it reaches SQL.
ALLOWED_DIMENSIONS = frozenset(
    {"cdn", "region", "device_type", "isp", "player_version", "rendition"})


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


def transport() -> str:
    """Which of the three read paths this instance is using: mcp | direct | embedded."""
    if demo_mode():
        return "embedded"
    return "direct" if os.environ.get("CLICKHOUSE_TRANSPORT", "mcp").lower() == "direct" else "mcp"


def bind_params(sql: str, parameters: dict | None) -> str:
    """Bind parameters into a repo-owned statement, client-side.

    Both engines that take a finished string need this: chdb has no parameter
    protocol, and the MCP server's query tool accepts one SQL string. Only
    `run_template` calls it, and only with parameters the pipeline chose, but
    `dim` and `value` can carry a model's answer — so identifiers are checked
    against the dimension whitelist and every literal is escaped. A hallucinated
    culprit gets a ValueError, not a rewritten statement.
    """
    for key, val in (parameters or {}).items():
        if key == "dim":
            if val not in ALLOWED_DIMENSIONS:
                raise ValueError(f"unknown dimension {val!r}; "
                                 f"expected one of {sorted(ALLOWED_DIMENSIONS)}")
            repl = str(val)
        elif isinstance(val, str):
            repl = "'" + val.replace("\\", "\\\\").replace("'", "\\'") + "'"
        else:
            repl = str(val)
        sql = re.sub(r"\{%s:[^}]+\}" % re.escape(key), lambda _m, r=repl: r, sql)
    return sql


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

    def query(self, sql: str, parameters: dict | None = None):
        raw = str(self._sess.query(bind_params(sql, parameters), "JSONCompact"))
        if not raw.strip():
            return _Result([], [])
        doc = json.loads(raw)
        # chdb reports scan volume under `statistics`, where clickhouse-connect
        # puts it in `summary`. Normalising here is what keeps `rows_scanned`
        # truthful in the run log whichever engine is behind it.
        stats = doc.get("statistics") or {}
        summary = {"read_rows": stats.get("rows_read", 0),
                   "read_bytes": stats.get("bytes_read", 0)}
        return _Result([c["name"] for c in doc.get("meta", [])], doc.get("data", []), summary)

    def command(self, sql: str) -> None:
        self._sess.query(sql)

    def close(self) -> None:
        sess, self._sess = getattr(self, "_sess", None), None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

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
    def __init__(self, column_names, result_rows, summary=None):
        self.column_names = column_names
        self.result_rows = result_rows
        self.summary = summary or {}


def reset_client() -> None:
    """Drop the cached clients, closing the engines first.

    Switching scenarios rebuilds the whole dataset, which means a new chdb
    session. Clearing the cache without closing the old one leaks a full
    in-process ClickHouse per switch — enough to exhaust a small machine after
    a few clicks in the UI. The MCP session owns a subprocess and a thread, so
    it needs the same treatment.
    """
    for cached in (client, reader):
        if getattr(cached, "cache_info", lambda: None)() and cached.cache_info().currsize:
            try:
                current = cached()
                if hasattr(current, "close"):
                    current.close()
            except Exception:
                pass
        cached.cache_clear()


@functools.lru_cache(maxsize=1)
def reader():
    """The client that executes SELECTs on the critical path.

    On a credentialed instance this is the official ClickHouse MCP server. It
    is separate from `client()` because the MCP server is read-only: schema
    creation and the run log have to go through the direct connection, so both
    exist at once and each does the half it can.
    """
    if transport() != "mcp":
        return client()
    from .mcp_client import ClickHouseMCPClient
    return ClickHouseMCPClient()


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
    """Create the schema and load one scenario into the in-process engine.

    The seed is derived the same way `evals/run_eval.py` derives its trial
    seeds, so the dataset a reviewer sees in the UI is drawn from the same
    distribution the published accuracy is measured over. It used to be a fixed
    `seed=chunk_offset` for every scenario, which pinned the demo to one
    arbitrary draw — and that particular draw was well below average.

    Set DEMO_SEED to reproduce a specific dataset exactly.
    """
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
    base = int(os.environ.get("DEMO_SEED", 0)) + len(sc.key)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    done = 0
    while done < rows:
        n = min(100_000, rows - done)
        c.insert("playback_events",
                 build_batch(n, now - timedelta(minutes=180), now,
                             now - timedelta(minutes=22), sc, seed=base + done),
                 column_names=COLUMNS)
        done += n


@functools.lru_cache(maxsize=32)
def load_template(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text()


def run_template(name: str, params: dict, statement: int = 0) -> tuple[list[dict], int, int]:
    """Execute a repo-owned query. Returns (rows, elapsed_ms, rows_read).

    This is the only function on the critical path that touches ClickHouse, and
    the only SQL it will run is a statement from `sql/queries/`. On a
    credentialed instance the official MCP server executes it.
    """
    if "dim" in params and params["dim"] not in ALLOWED_DIMENSIONS:
        raise ValueError(f"unknown dimension {params['dim']!r}; "
                         f"expected one of {sorted(ALLOWED_DIMENSIONS)}")
    sql = load_template(name).split(";")[statement]
    conn = reader()
    started = time.perf_counter()
    if transport() == "mcp":
        # The MCP query tool takes one finished string, so bind before sending.
        res = conn.query(bind_params(sql, params))
    else:
        # clickhouse-connect binds server-side; the embedded client binds itself.
        res = conn.query(sql, parameters=params)
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
    """Read a past run back out of ClickHouse, step by step, including the SQL.

    A read, so it goes through the same transport as the critical path.
    """
    sql = ("SELECT step_no, ts, agent, action, sql_executed, rows_scanned, latency_ms, "
           "model, finding, confidence FROM control_room.agent_runs "
           "WHERE run_id = {r:String} ORDER BY step_no")
    if transport() == "mcp":
        res = reader().query(bind_params(sql, {"r": run_id}))
    else:
        res = reader().query(sql, parameters={"r": run_id})
    return [dict(zip(res.column_names, r)) for r in res.result_rows]
