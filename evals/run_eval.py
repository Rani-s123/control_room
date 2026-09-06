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

Each trial draws a fresh seed base. The first three are the seeds the surprise
weight in controlroom/attribution.py was fitted on; everything after that is
held out, and the two are scored separately at the end. Accuracy on the seeds a
weight was fitted on always flatters it, so the held-out column is the result.

    pip install chdb numpy
    python evals/run_eval.py --trials 3      # fitted seeds only
    python evals/run_eval.py --trials 11     # fitted + 8 held-out seeds
    python evals/run_eval.py --sweep-severity --trials 5   # accuracy vs fault size
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

import data.generate_events as generator  # noqa: E402
from data.generate_events import COLUMNS, build_batch  # noqa: E402
from controlroom.attribution import score_candidates  # noqa: E402
from data.scenarios import SCENARIOS  # noqa: E402

# How hard the planted fault bites, as the extra probability that an event
# inside the fault slice becomes a stall. Exposed because a single accuracy
# number is only meaningful next to the severity it was measured at — see
# --sweep-severity, which reports the whole curve down to the noise floor.
DEFAULT_SEVERITY = generator.FAULT_REBUFFER_PROBABILITY
SEVERITY_CURVE = [0.0, 0.005, 0.01, 0.02, 0.04]

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


# Seed bases 0, 1000 and 2000 are the three the surprise weight in
# controlroom/attribution.py was fitted on. Trials past the third draw seeds the
# weight has never seen, and they are scored separately below — a number
# measured on the seeds you tuned on is not a result, it is a memory.
TUNED_TRIALS = 3


def detection_rate(rows: int, trials: int) -> None:
    """Does step 1 fire at all?

    The strategy comparison starts at blame.sql, which quietly assumes the
    Watcher already said yes. It is a gate: when it says no, nothing downstream
    runs and attribution accuracy is irrelevant. So measure it separately —
    the global z-score sweep, and the per-slice sweep the Watcher falls back to
    when the all-up series looks calm.
    """
    from controlroom.pipeline import (DIMENSIONS, SLICE_MIN_SESSIONS,
                                      SLICE_Z_THRESHOLD, Z_THRESHOLD)
    print(f"{'scenario':<22}{'global sweep':>16}{'+ slice sweep':>16}")
    print("-" * 54)
    tot_g = tot_s = tot_n = 0
    for sc in SCENARIOS.values():
        g = s = 0
        for trial in range(trials):
            sess = chs.Session()
            setup(sess, sc, rows, seed=1000 * trial + len(sc.key))
            gz = [float(r[4]) for r in rows_of(sess.query(bind(
                sql("sql/queries/detect.sql"),
                {"lookback_min": WINDOW, "baseline_min": BASELINE,
                 "z_threshold": Z_THRESHOLD}), "CSV")) if len(r) > 4]
            fired = any(z > Z_THRESHOLD for z in gz)
            g += fired
            if fired:
                s += 1
            else:
                for dim in DIMENSIONS:
                    rs = rows_of(sess.query(bind(
                        sql("sql/queries/detect_slice.sql"),
                        {"dim": dim, "lookback_min": WINDOW, "baseline_min": BASELINE,
                         "min_sessions": SLICE_MIN_SESSIONS}), "CSV"))
                    if any(len(r) > 2 and float(r[2]) > SLICE_Z_THRESHOLD for r in rs):
                        s += 1
                        break
            sess.close()
        tot_g += g; tot_s += s; tot_n += trials
        print(f"{sc.key:<22}{g:>10}/{trials}    {s:>10}/{trials}")
    print("-" * 54)
    print(f"{'all':<22}{tot_g:>10}/{tot_n}    {tot_s:>10}/{tot_n}")
    print("\nThe slice sweep exists because a fault contained in one rendition, POP or\n"
          "player version can hurt a large cohort without moving the all-up average.\n"
          "Where the two columns are equal, the global series already saw it.")


def severity_sweep(rows: int, trials: int) -> None:
    """Accuracy as a function of how hard the planted fault bites.

    A single accuracy number says nothing without this curve. At 0.0 the fault
    slice never stalls more often than usual — it only stalls *longer* when it
    was going to stall anyway — which for a small slice is indistinguishable
    from noise, and no attribution method can recover a cause the data does not
    contain. The curve shows where that floor is and how each strategy behaves
    approaching it.
    """
    strategies = ["worst_qoe", "largest_total", "biggest_delta", "control_room"]
    print(f"{'fault severity':<16}" + "".join(f"{s:>18}" for s in strategies))
    print("-" * (16 + 18 * len(strategies)))
    for severity in SEVERITY_CURVE:
        generator.FAULT_REBUFFER_PROBABILITY = severity
        hits = {s: 0 for s in strategies}
        n = 0
        for trial in range(trials):
            for sc in SCENARIOS.values():
                sess = chs.Session()
                setup(sess, sc, rows, seed=1000 * trial + len(sc.key))
                truth_pair = (sc.fault_dim, sc.fault_value)
                for s in strategies:
                    top = pick(sess, s)
                    hits[s] += bool(top) and top[0] == truth_pair
                n += 1
                sess.close()
        label = f"{severity:.3f}" + ("  (default)" if severity == DEFAULT_SEVERITY else "")
        print(f"{label:<16}" + "".join(f"{hits[s]:>10}/{n} {100 * hits[s] / n:4.0f}%"
                                       for s in strategies))
    generator.FAULT_REBUFFER_PROBABILITY = DEFAULT_SEVERITY
    print("\nSeverity is the extra probability that an event inside the fault slice\n"
          "becomes a stall. At 0.000 the fault is not present in the aggregate at all,\n"
          "so nothing can find it; that row is the noise floor, not a score.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=250_000)
    ap.add_argument("--trials", type=int, default=1,
                    help=f"seed bases to run; the first {TUNED_TRIALS} are the fitted "
                         f"seeds, the rest are held out")
    ap.add_argument("--fault-severity", type=float, default=None,
                    help="extra stall probability inside the fault slice "
                         f"(default {DEFAULT_SEVERITY})")
    ap.add_argument("--sweep-severity", action="store_true",
                    help="report accuracy across the whole severity curve and exit")
    ap.add_argument("--detection", action="store_true",
                    help="report how often the Watcher fires at all, and exit")
    args = ap.parse_args()

    if args.fault_severity is not None:
        generator.FAULT_REBUFFER_PROBABILITY = args.fault_severity
    if args.sweep_severity:
        severity_sweep(args.rows, args.trials)
        return
    if args.detection:
        detection_rate(args.rows, args.trials)
        return

    print(f"fault severity {generator.FAULT_REBUFFER_PROBABILITY} "
          f"(extra stall probability inside the fault slice)\n")

    strategies = ["worst_qoe", "largest_total", "biggest_delta", "control_room"]
    score = {s: 0 for s in strategies}
    score2 = {s: 0 for s in strategies}
    tuned = {s: [0, 0, 0] for s in strategies}    # hits, top-2 hits, n
    held = {s: [0, 0, 0] for s in strategies}
    per_scenario = {sc.key: [0, 0, 0] for sc in SCENARIOS.values()}
    total = 0
    latencies: list[float] = []

    head = f"{'scenario':<20}{'truth':<24}" + "".join(f"{s:<26}" for s in strategies)
    print(head)
    print("-" * len(head))

    for trial in range(args.trials):
        bucket = tuned if trial < TUNED_TRIALS else held
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
                bucket[s][0] += ok
                bucket[s][1] += in_top2
                bucket[s][2] += 1
                if s == "control_room":
                    rec = per_scenario[sc.key]
                    rec[0] += ok
                    rec[1] += in_top2
                    rec[2] += 1
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

    if held["control_room"][2]:
        print(f"\n{'':<20}{'fitted seeds':>28}{'held-out seeds':>28}")
        for s in strategies:
            th, th2, tn = tuned[s]
            hh, hh2, hn = held[s]
            t = f"{th}/{tn} {100 * th / tn:5.1f}%  top-2 {100 * th2 / tn:5.1f}%" if tn else "-"
            h = f"{hh}/{hn} {100 * hh / hn:5.1f}%  top-2 {100 * hh2 / hn:5.1f}%" if hn else "-"
            print(f"{s:<20}{t:>28}{h:>28}")
        print("\nThe held-out column is the honest one. The fitted column is only\n"
              "here so the gap between them stays visible.")
    else:
        print(f"\nEvery seed here is one the surprise weight was fitted on. Run\n"
              f"--trials {TUNED_TRIALS + 3} or more to score it on seeds it has never seen.")

    print("\nTop-2 is reported beside top-1 because both the culprit and the cohort it\n"
          "hurts most go into the same page. When this ranks them the wrong way round,\n"
          "the on-call engineer still receives the right slice. Top-1 is the headline.")

    print("\nper scenario (control_room, all seeds):")
    for k, (a, b, n) in per_scenario.items():
        if n:
            print(f"  {k:<20} top-1 {a}/{n}   top-2 {b}/{n}")

    if latencies:
        latencies.sort()
        print(f"\ndiagnosis latency: median {latencies[len(latencies)//2]:.0f} ms "
              f"over {len(DIMS)} dimensions, {args.rows:,} events "
              f"(embedded engine; ClickHouse Cloud is faster at 1000× the rows)")
    print("\nHuman baseline for the same task, from published incident retrospectives: 20-40 minutes.")


if __name__ == "__main__":
    main()
