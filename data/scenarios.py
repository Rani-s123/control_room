"""
Fault scenarios, plus the confounder that makes this problem hard.

THE CONFOUNDER
--------------
Every one of these datasets contains a permanent, innocent slice that looks
terrible in absolute terms: every `BSNL` viewer, pinned to the bottom of the
ABR ladder. They stall constantly. They always have. Nobody should be paged
about them, and no amount of CDN failover helps them.

A naive detector — "rank slices by total rebuffering, blame the top one" —
picks that slice on every single scenario and is wrong every single time. It is
the exact mistake a real on-call engineer makes at 2am, and the exact mistake an
LLM makes if you hand it a `SELECT ... ORDER BY rebuffer_ms DESC`.

Beating it requires comparing each slice against *its own* baseline. That is
what `sql/queries/blame.sql` does, and `evals/run_eval.py` measures the gap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    fault_dim: str            # ground truth: the dimension that explains it
    fault_value: str          # ground truth: the slice
    amplifier_dim: str        # secondary cohort that suffers most
    amplifier_value: str
    artifact: str             # what the picture looks like: clean | corrupt | frozen
    fault_domain: str         # who should be paged: delivery | encoder | packager | client
    error_code: str
    ladder_collapse: bool     # does the ABR ladder fall during the fault?


SCENARIOS: dict[str, Scenario] = {
    "shield_eviction": Scenario(
        "shield_eviction", "Origin shield evicting segments in one POP",
        "cdn", "edgecast", "device_type", "smart_tv",
        "clean", "delivery", "SEGMENT_TIMEOUT_504", True),

    "isp_peering": Scenario(
        "isp_peering", "ISP peering congestion during the second half",
        "isp", "Airtel", "region", "IN-MH",
        "clean", "delivery", "MEDIA_ERR_NETWORK", True),

    "encoder_corruption": Scenario(
        "encoder_corruption", "Encoder emitting corrupt slices on the top rendition",
        "rendition", "1080p60", "device_type", "console",
        "corrupt", "encoder", "DECODE_FAIL", False),

    "packager_manifest": Scenario(
        "packager_manifest", "Packager publishing a stale manifest",
        "cdn", "fastly", "region", "US-CA",
        "frozen", "packager", "MANIFEST_404", False),

    "player_regression": Scenario(
        "player_regression", "Player release regressing buffer management",
        "player_version", "4.12.0", "device_type", "mobile_ios",
        "clean", "client", "MEDIA_ERR_NETWORK", True),

    "regional_pop": Scenario(
        "regional_pop", "Single edge POP degraded, all CDNs unaffected",
        "region", "DE-BE", "isp", "Telekom",
        "clean", "delivery", "SEGMENT_TIMEOUT_504", True),
}

DEFAULT = "shield_eviction"


# The always-bad, always-innocent slice. Present in every dataset.
CONFOUNDER = {
    "isp": "BSNL",                       # every BSNL viewer, the whole window
    "rendition": "360p",                 # pinned to the bottom of the ladder
    "extra_rebuffer_ms": (3500, 11000),  # chronic, severe, and completely flat
    "rebuffer_probability": 0.30,
}
