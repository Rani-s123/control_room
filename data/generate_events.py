"""
Generate realistic OTT playback telemetry with one planted fault.

Every dataset also contains a chronic-but-innocent slice (see scenarios.py) that
a naive detector will always blame. That is deliberate.

    python data/generate_events.py --rows 5_000_000 --scenario shield_eviction
    python data/generate_events.py --list
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.scenarios import CONFOUNDER, DEFAULT, SCENARIOS, Scenario  # noqa: E402

CDNS = np.array(["akamai", "cloudfront", "fastly", "edgecast"])
CDN_W = np.array([0.34, 0.30, 0.22, 0.14])
REGIONS = np.array(["IN-MH", "IN-KA", "IN-DL", "US-CA", "US-NY", "GB-LND", "DE-BE", "BR-SP"])
REGION_W = np.array([0.18, 0.12, 0.12, 0.16, 0.12, 0.10, 0.06, 0.14])
POPS = {"IN-MH": "BOM2", "IN-KA": "BLR1", "IN-DL": "DEL3", "US-CA": "SJC1",
        "US-NY": "EWR4", "GB-LND": "LHR2", "DE-BE": "FRA1", "BR-SP": "GRU1"}
ISPS = np.array(["Jio", "Airtel", "BSNL", "Comcast", "Verizon", "BT", "Telekom", "Vivo"])
ISP_W = np.array([0.20, 0.18, 0.14, 0.12, 0.10, 0.08, 0.08, 0.10])
DEVICES = np.array(["smart_tv", "mobile_android", "mobile_ios", "web", "console"])
DEVICE_W = np.array([0.31, 0.27, 0.19, 0.18, 0.05])
RENDITIONS = np.array(["1080p60", "1080p", "720p", "480p", "360p"])
RENDITION_W = np.array([0.22, 0.30, 0.28, 0.14, 0.06])
RENDITION_KBPS = {"1080p60": 8500, "1080p": 6000, "720p": 3200, "480p": 1600, "360p": 800}
CONTENT = [("live-final-2026", "The Championship Final (Live)", 1),
           ("live-studio-pre", "Pre-Match Studio (Live)", 1),
           ("vod-s04e07", "Nightfall S04E07", 0),
           ("vod-doc-icarus", "Icarus: The Long Fall", 0)]
CONTENT_W = np.array([0.62, 0.14, 0.14, 0.10])
EVENTS = np.array(["heartbeat", "start", "rebuffer", "bitrate_switch", "error", "ad_start", "ad_complete", "exit"])
EVENT_W = np.array([0.70, 0.06, 0.05, 0.06, 0.01, 0.05, 0.04, 0.03])
PLAYER_VERSIONS = np.array(["4.12.0", "4.11.3", "4.10.9", "3.98.2"])
PLAYER_W = np.array([0.55, 0.28, 0.12, 0.05])

# How hard a planted fault bites. One pair of constants for all six archetypes —
# no per-scenario knobs, so no scenario can be quietly tuned until it passes.
# The fault slice stalls more often than it should; the cohort it hits hardest
# stalls more often still, which is what keeps "the cause" and "who suffers
# most" genuinely different slices.
#
# 0.01 is the smallest value at which the planted fault is reliably present in
# the per-minute aggregate for all six archetypes. Below it, the smaller slices
# (region=DE-BE is 6% of traffic) sit inside noise: the "incident" window can
# show the fault slice stalling *less* than its own baseline, and then there is
# no cause in the data for anything to find. `--sweep-severity` in the eval
# reports accuracy across this whole range, down to that floor.
#
# Still deliberately subtle — the confounder below stalls 30% of the time and
# always has, and it dwarfs every planted fault in absolute terms.
FAULT_REBUFFER_PROBABILITY = 0.01
AMPLIFIER_REBUFFER_PROBABILITY = 0.12

COLUMNS = ["ts", "session_id", "user_id", "event_type", "content_id", "content_title", "is_live",
           "cdn", "pop", "isp", "asn", "country", "region", "device_type", "os", "player_version",
           "rendition", "bitrate_kbps", "startup_ms", "rebuffer_ms", "dropped_frames",
           "buffer_health_ms", "error_code", "subscription", "ad_revenue_usd"]


def build_batch(n, t_start, t_end, incident_from, scenario: Scenario, seed=None):
    rng = np.random.default_rng(seed if seed is not None else 20260909)
    span = (t_end - t_start).total_seconds()
    ts = np.array([t_start + timedelta(seconds=float(o)) for o in rng.random(n) * span])

    cols = {
        "cdn": rng.choice(CDNS, n, p=CDN_W),
        "region": rng.choice(REGIONS, n, p=REGION_W),
        "device_type": rng.choice(DEVICES, n, p=DEVICE_W),
        "isp": rng.choice(ISPS, n, p=ISP_W),
        "player_version": rng.choice(PLAYER_VERSIONS, n, p=PLAYER_W),
    }
    event = rng.choice(EVENTS, n, p=EVENT_W)
    rend_idx = rng.choice(len(RENDITIONS), n, p=RENDITION_W)
    content_idx = rng.choice(len(CONTENT), n, p=CONTENT_W)
    sub = rng.choice(np.array(["free_ad", "premium"]), n, p=[0.58, 0.42])

    # ---- the chronic, innocent slice: bad the whole window, never worsens ----
    confounded = cols["isp"] == CONFOUNDER["isp"]
    rend_idx = np.where(confounded, len(RENDITIONS) - 1, rend_idx)

    # ---- the planted fault: only after incident_from -----------------------
    in_window = np.array([t >= incident_from for t in ts])
    fault_col = cols[scenario.fault_dim] if scenario.fault_dim in cols else RENDITIONS[rend_idx]
    hit = in_window & (fault_col == scenario.fault_value)

    amp_col = cols[scenario.amplifier_dim] if scenario.amplifier_dim in cols else RENDITIONS[rend_idx]
    hit_hard = hit & (amp_col == scenario.amplifier_value)

    # Events are decided first, including forced stalls, so that stall duration
    # is only ever attached to rows that are actually rebuffer events.
    #
    # A fault raises the stall RATE across the whole slice it hits, and the
    # cohort it hits hardest stalls harder still. Both draws matter: without the
    # first one the fault slice only ever got *longer* stalls on the stalls it
    # would have had anyway, so its excess scaled with its own baseline rate.
    # For a small slice — region=DE-BE is 6% of traffic — that landed inside
    # noise, and the only slice actually carrying the fault was the amplifier
    # subset. The attribution was then being asked to find a cause the data had
    # never contained.
    event = np.where(confounded & (rng.random(n) < CONFOUNDER["rebuffer_probability"]), "rebuffer", event)
    event = np.where(hit & (rng.random(n) < FAULT_REBUFFER_PROBABILITY), "rebuffer", event)
    event = np.where(hit_hard & (rng.random(n) < AMPLIFIER_REBUFFER_PROBABILITY), "rebuffer", event)

    is_stall = event == "rebuffer"
    lo, hi = CONFOUNDER["extra_rebuffer_ms"]
    rebuffer_ms = np.where(is_stall, rng.integers(400, 2600, n), 0)
    rebuffer_ms = np.where(is_stall & confounded, rng.integers(lo, hi, n), rebuffer_ms)
    rebuffer_ms = np.where(is_stall & hit, rebuffer_ms + rng.integers(500, 1600, n), rebuffer_ms)
    rebuffer_ms = np.where(is_stall & hit_hard, rebuffer_ms + rng.integers(700, 2200, n), rebuffer_ms)

    startup_ms = np.where(event == "start", rng.integers(600, 2400, n), 0)
    startup_ms = np.where(hit & (event == "start"), startup_ms + rng.integers(700, 2400, n), startup_ms)

    if scenario.ladder_collapse:
        rend_idx = np.where(hit, np.minimum(rend_idx + rng.integers(1, 4, n), len(RENDITIONS) - 1), rend_idx)
    rendition = RENDITIONS[rend_idx]
    bitrate = (np.array([RENDITION_KBPS[r] for r in rendition]) * rng.uniform(0.88, 1.08, n)).astype(np.uint32)

    err_pool = np.array(["MEDIA_ERR_NETWORK", "MANIFEST_404", "DECODE_FAIL"])
    error_code = np.where(event == "error", rng.choice(err_pool, n), "")
    error_code = np.where(hit & (event == "error"), scenario.error_code, error_code)

    dropped = np.where(hit_hard & (scenario.artifact == "corrupt"), rng.integers(80, 400, n),
                       np.where(hit_hard, rng.integers(0, 60, n), rng.integers(0, 12, n)))
    buffer_health = np.where(hit, rng.integers(0, 1800, n), rng.integers(4000, 30000, n))
    ad_rev = np.where(event == "ad_complete", rng.uniform(0.004, 0.031, n), 0.0)

    sess = rng.integers(0, max(n // 6, 1), n)
    users = rng.integers(0, max(n // 9, 1), n)

    rows = []
    for i in range(n):
        c_id, c_title, live = CONTENT[content_idx[i]]
        r = cols["region"][i]
        rows.append((ts[i], f"s-{sess[i]:09d}", f"u-{users[i]:08d}", str(event[i]),
                     c_id, c_title, int(live), str(cols["cdn"][i]), POPS[r], str(cols["isp"][i]),
                     int(rng.integers(4000, 65000)), r.split("-")[0], r, str(cols["device_type"][i]),
                     "tizen" if cols["device_type"][i] == "smart_tv" else "generic",
                     str(cols["player_version"][i]), str(rendition[i]), int(bitrate[i]),
                     int(startup_ms[i]), int(rebuffer_ms[i]), int(dropped[i]), int(buffer_health[i]),
                     str(error_code[i]), str(sub[i]), float(ad_rev[i])))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5_000_000)
    ap.add_argument("--window-min", type=int, default=180)
    ap.add_argument("--incident-start-min-ago", type=int, default=22)
    ap.add_argument("--scenario", default=DEFAULT, choices=list(SCENARIOS))
    global FAULT_REBUFFER_PROBABILITY

    ap.add_argument("--batch", type=int, default=250_000)
    ap.add_argument("--fault-severity", type=float, default=FAULT_REBUFFER_PROBABILITY,
                    help="extra probability that an event in the fault slice stalls")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    FAULT_REBUFFER_PROBABILITY = args.fault_severity

    if args.list:
        for s in SCENARIOS.values():
            print(f"  {s.key:22s} {s.label}\n{'':24s} truth: {s.fault_dim}={s.fault_value} -> page {s.fault_domain}")
        return

    sc = SCENARIOS[args.scenario]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    t_start = now - timedelta(minutes=args.window_min)
    incident_from = now - timedelta(minutes=args.incident_start_min_ago)

    print(f"scenario  : {sc.label}")
    print(f"ground truth: {sc.fault_dim}={sc.fault_value}, worst on {sc.amplifier_dim}={sc.amplifier_value}")
    print(f"decoy     : isp={CONFOUNDER['isp']} - chronic, severe, innocent")
    print(f"window    : {t_start:%H:%M} -> {now:%H:%M} UTC, fault opens {incident_from:%H:%M}")

    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"], port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true")

    written, t0 = 0, time.time()
    while written < args.rows:
        n = min(args.batch, args.rows - written)
        client.insert("control_room.playback_events",
                      build_batch(n, t_start, now, incident_from, sc, seed=written),
                      column_names=COLUMNS)
        written += n
        print(f"  {written:,}/{args.rows:,} ({written / max(time.time() - t0, .001):,.0f} rows/s)", end="\r")
    print(f"\nloaded {written:,} rows in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
