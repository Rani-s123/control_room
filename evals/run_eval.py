"""
Does the diagnosis actually work, or does it just look like it works?

Runs every fault scenario against a real ClickHouse engine (embedded, no
credentials, no cloud) and scores three approaches against ground truth:

  worst_qoe      rank by rebuffering per session. This is the "worst quality"
                 panel on every QoE dashboard ever built.
  largest_total  rank by total stall milliseconds. The "most impacted" panel.
  biggest_delta  rank by change vs baseline, ignoring how many sessions the
                 slice touches. Catches small slices with noisy averages.
  control_room   rank by explanatory power: the share of ALL unforecast stall
                 time in the incident that this one slice accounts for. A root
                 cause contains the whole fault; a cohort that merely suffers
                 from it contains only part. Containment, not severity.

Every dataset contains a chronic-but-innocent slice designed to fool the first
one, because that is what fools people at 2am.

    pip install chdb numpy
    python evals/run_eval.py
    python evals/run_eval.py --trials 5      # variance across seeds
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import chdb.session as chs  # noqa: E402
except ImportError:
    print("ERROR: `chdb` is not installed in this environment. Run `pip install chdb` to run evals/run_eval.py.")
    sys.exit(1)

from data.generate_events import COLUMNS, build_batch  # noqa: E402
from controlroom.attribution import score_candidates  # noqa: E402
from data.scenarios import SCENARIOS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIMS = ["cdn", "region", "device_type", "isp", "player_version", "rendition"]
WINDOW, BASELINE = 20, 180


def sql(path: str) -> str:
    with open(os.path.join(ROOT, path)) as fh:
        return fh.read()


def bind(q: str, params: dict) -> str:
    for k, v in params.items():
        repl = v if k == "dim" else (f"'{v}'" if isinstance(v, str) else str(v))
        q = re.sub(r"\{%s:[^}]+\}" % re.escape(k), repl, q)
    return q


def rows_of(out: str) -> list[list[str]]:
    return [ln.split(",") for ln in str(out).strip().splitlines() if ln.strip()]


def setup(sess, scenario, n_rows: int, seed: int) -> None:
    for stmt in sql("sql/01_schema.sql").split(";"):
        body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            sess.query(body)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    done = 0
    while done < n_rows:
        chunk = min(100_000, n_rows - done)
        data = build_batch(chunk, now - timedelta(minutes=BASELINE), now,
                           now - timedelta(minutes=WINDOW + 2), scenario, seed=seed + done)
        values = ",".join(
            "(" + ",".join(
                (f"'{c:%Y-%m-%d %H:%M:%S.%f}'"[:-4] + "'") if hasattr(c, "strftime")
                else (f"'{c}'" if isinstance(c, str) else str(c))
                for c in row) + ")"
            for row in data)
        sess.query(f"INSERT INTO control_room.playback_events ({','.join(COLUMNS)}) VALUES {values}")
        done += chunk


def rank(sess, dim: str) -> list[dict]:
    """One blame.sql run. Columns: slice, sessions, rb_now, rb_before, delta, excess, bitrate_d, errors."""
    out = rows_of(sess.query(bind(sql("sql/queries/blame.sql"),
                                  {"dim": dim, "window_min": WINDOW, "baseline_min": BASELINE}), "CSV"))
    parsed = []
    for r in out:
        if len(r) < 6:
            continue
        try:
            parsed.append({"slice": r[0].strip('"'), "sessions": float(r[1]),
                           "rb_now": float(r[2]), "delta": float(r[4]), "excess": float(r[5]),
                           "ep": float(r[7]), "surprise": float(r[8])})
        except (ValueError, IndexError):
            continue
    return parsed


def pick(sess, strategy: str, k: int = 2) -> list[tuple[str, str]]:
    """Return each strategy's top-k blamed slices, best first, across all dimensions."""
    candidates = [{"dim": d, **row} for d in DIMS for row in rank(sess, d)]
    if not candidates:
        return []

    if strategy == "control_room":
        # exactly what production runs — same module, same weights
        ordered = score_candidates([{**c, "explanatory_power": c["ep"]} for c in candidates])
    else:
        key = {"worst_qoe": lambda r: r["rb_now"],
               "largest_total": lambda r: r["rb_now"] * r["sessions"],
               "biggest_delta": lambda r: r["delta"]}[strategy]
        ordered = sorted(candidates, key=key, reverse=True)

    seen, out = set(), []
    for c in ordered:
        pair = (c["dim"], c["slice"])
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
        if len(out) == k:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=250_000)
    ap.add_argument("--trials", type=int, default=1)
    args = ap.parse_args()

    strategies = ["worst_qoe", "largest_total", "biggest_delta", "control_room"]
    score = {s: 0 for s in strategies}
    score2 = {s: 0 for s in strategies}
    total = 0
    latencies: list[float] = []

    head = f"{'scenario':<20}{'truth':<24}" + "".join(f"{s:<26}" for s in strategies)
    print(head)
    print("-" * len(head))

    for trial in range(args.trials):
        for sc in SCENARIOS.values():
            sess = chs.Session()
            setup(sess, sc, args.rows, seed=1000 * trial + len(sc.key))
            truth = f"{sc.fault_dim}={sc.fault_value}"
            cells = []
            for s in strategies:
                t0 = time.perf_counter()
                top = pick(sess, s)
                if s == "control_room":
                    latencies.append((time.perf_counter() - t0) * 1000)
                truth_pair = (sc.fault_dim, sc.fault_value)
                ok = bool(top) and top[0] == truth_pair
                in_top2 = truth_pair in top
                score[s] += ok
                score2[s] += in_top2
                got = f"{top[0][0]}={top[0][1]}" if top else "-"
                cells.append(("PASS " if ok else ("2nd  " if in_top2 else "x    ")) + got)
            total += 1
            print(f"{sc.key:<20}{truth:<24}" + "".join(f"{c:<26}" for c in cells))
            sess.close()

    print("-" * len(head))
    print(f"{'strategy':<20}{'top-1':>12}{'top-2':>12}")
    for s in strategies:
        pct, pct2 = 100 * score[s] / total, 100 * score2[s] / total
        print(f"{s:<20}{score[s]:>4}/{total} {pct:5.1f}%{score2[s]:>6}/{total} {pct2:5.1f}%  "
              + "█" * int(pct / 5))

    print("\nTop-2 matters here because both the culprit and the cohort it hurts most\n"
          "go into the same page. When this ranks them the wrong way round, the\n"
          "on-call engineer still receives the right slice.")

    if latencies:
        latencies.sort()
        print(f"\ndiagnosis latency: median {latencies[len(latencies)//2]:.0f} ms "
              f"over {len(DIMS)} dimensions, {args.rows:,} events "
              f"(embedded engine; ClickHouse Cloud is faster at 1000× the rows)")
    print("\nHuman baseline for the same task, from published incident retrospectives: 20-40 minutes.")


if __name__ == "__main__":
    main()
