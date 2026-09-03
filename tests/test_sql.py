"""
Runs the real schema, the materialized view and every agent query against an
embedded ClickHouse (chdb). If this passes, nothing in the demo path is SQL
that has never executed.

    python tests/test_sql.py
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import chdb.session as chs  # noqa: E402
except ImportError:
    print("ERROR: `chdb` is not installed in this environment. Run `pip install chdb` to run test_sql.py.")
    sys.exit(1)

from data.generate_events import COLUMNS, build_batch  # noqa: E402
from data.scenarios import SCENARIOS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sess = chs.Session()


def run(sql: str, fmt: str = "CSV") -> str:
    return str(sess.query(sql, fmt))


def bind(sql: str, params: dict) -> str:
    """chdb has no server-side params; substitute the same names the agents use."""
    for key, val in params.items():
        sql = re.sub(r"\{%s:[^}]+\}" % re.escape(key),
                     val if isinstance(val, str) and key == "dim" else
                     (f"'{val}'" if isinstance(val, str) else str(val)),
                     sql)
    return sql


def load_sql(path: str) -> str:
    with open(os.path.join(ROOT, path)) as fh:
        return fh.read()


def main() -> None:
    print("-> creating schema")
    for stmt in load_sql("sql/01_schema.sql").split(";"):
        body = "\n".join(ln for ln in stmt.splitlines() if not ln.strip().startswith("--")).strip()
        if body:
            run(body)

    print("-> inserting 300k synthetic events")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = build_batch(300_000, now - timedelta(minutes=180), now,
                       now - timedelta(minutes=22), SCENARIOS["shield_eviction"])
    def _fmt(rows):
        return ",".join(
        "(" + ",".join(
            f"'{r[i]}'" if isinstance(r[i], str) else
            (f"'{r[i]:%Y-%m-%d %H:%M:%S.%f}'"[:-4] + "'" if hasattr(r[i], "strftime") else str(r[i]))
            for i in range(len(COLUMNS))
        ) + ")"
        for r in rows)

    values_chunks = [_fmt(rows[i:i + 100_000]) for i in range(0, len(rows), 100_000)]
    for i in range(0, len(values_chunks), 1):
        run(f"INSERT INTO control_room.playback_events ({','.join(COLUMNS)}) VALUES {values_chunks[i]}")

    total = run("SELECT count() FROM control_room.playback_events").strip()
    rolled = run("SELECT count() FROM control_room.qoe_1m").strip()
    print(f"  raw rows: {total}   rollup rows: {rolled}")
    assert int(total) == 300_000, "raw insert failed"
    assert int(rolled) > 0, "materialized view did not fire"

    print("-> WATCHER: detect.sql")
    detect = bind(load_sql("sql/queries/detect.sql"),
                  {"lookback_min": 20, "baseline_min": 180, "z_threshold": 2.5})
    out = run(detect)
    anomalous = [ln for ln in out.strip().splitlines() if ln.endswith(",1")]
    print(f"  {len(anomalous)} anomalous minutes flagged")
    assert anomalous, "watcher found no anomaly in data that contains one"

    print("-> DIAGNOSTICIAN: blame.sql, ranked by explanatory power")
    best, best_score = None, -1.0
    for dim in ("cdn", "region", "device_type", "isp", "player_version", "rendition"):
        blame = bind(load_sql("sql/queries/blame.sql"),
                     {"dim": dim, "window_min": 20, "baseline_min": 180})
        top = run(blame).strip().splitlines()[0].split(",")
        slice_, ep, surprise = top[0].strip('"'), float(top[7]), float(top[8])
        score = ep + 25 * surprise
        print(f"  {dim:16s} -> {slice_:16s} EP={ep:.3f} surprise={surprise:.5f}")
        if score > best_score:
            best, best_score = (dim, slice_), score

    print(f"  culprit: {best[0]}={best[1]}")
    assert best == ("cdn", "edgecast"), f"blamed {best}, expected cdn=edgecast"

    print("-> forensics + impact")
    forensics, impact = load_sql("sql/queries/forensics.sql").split(";")[:2]
    p = {"dim": "cdn", "value": "edgecast", "window_min": 20}
    assert run(bind(forensics, p)).strip(), "forensics returned nothing"
    imp = run(bind(impact, p)).strip().split(",")
    print(f"  sessions_hit={imp[0]} viewers_hit={imp[1]} stall_minutes={imp[2]} rage_quits={imp[3]}")
    assert int(imp[0]) > 0

    print("\nALL SQL VERIFIED — watcher found the fault, blame analysis named the right slice.")


if __name__ == "__main__":
    main()
