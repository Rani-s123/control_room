"""
The Eyewitness.

Metrics tell you viewers are stalling. They cannot tell you whether the picture
those viewers *did* receive was intact. That distinction decides who gets paged:
a clean picture with stalls is a delivery problem (CDN team); macroblocked or
frozen frames are an encoder/packager problem (media team). Telemetry alone
routes this wrong all the time.

So the Eyewitness fetches real frames from the affected renditions and has
Gemini look at them.

Frame source, in order of preference:
  1. THUMBNAIL_BASE_URL — your packager's sprite/thumbnail endpoint.
  2. A local fixture set under data/frames/.
  3. In demo mode only, frames rendered by the simulated origin below.

On the third path the frames are drawn to match the fault the telemetry
generator planted, because the same simulated world produces both — exactly as
a real origin serves pictures that match what its encoder actually did. Every
frame carries a SIMULATED ORIGIN slate so nobody mistakes one for a capture,
and the run log records the source. The agent is never told which scenario is
loaded; it only ever sees the pixels.

Outside demo mode nothing is synthesised. With no origin and no fixtures the
Eyewitness reports that it had no frames, rather than inventing a picture and
letting a verdict rest on it.
"""

from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "frames"
THUMBNAIL_BASE_URL = os.environ.get("THUMBNAIL_BASE_URL", "")


def _from_origin(value: str, n: int) -> list[bytes]:
    import urllib.request
    out = []
    for i in range(n):
        url = f"{THUMBNAIL_BASE_URL.rstrip('/')}/{value}/frame_{i}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                out.append(resp.read())
        except Exception:
            break
    return out


def _from_fixtures(n: int) -> list[bytes]:
    if not FIXTURES.exists():
        return []
    files = sorted(FIXTURES.glob("*.jpg")) + sorted(FIXTURES.glob("*.png"))
    return [f.read_bytes() for f in files[:n]]


def _planted_artifact() -> str:
    """What the simulated origin is actually emitting right now.

    Demo mode plants a fault in the telemetry; the picture has to agree with it.
    Reading the loaded scenario here — inside the fake origin, not inside the
    agent — is the same move the generator makes when it plants error codes and
    dropped frames. Returns clean | corrupt | frozen | black.
    """
    try:
        from data.scenarios import DEFAULT, SCENARIOS
        return SCENARIOS[os.environ.get("DEMO_SCENARIO", DEFAULT)].artifact
    except Exception:
        return "clean"


def _synthesize(n: int) -> list[bytes]:
    """Render frames from the simulated origin, showing whatever artefact the
    loaded scenario planted. A delivery fault produces an intact picture that
    simply stops arriving; an encoder fault produces visible damage. Getting
    this wrong is not cosmetic — it is the evidence the Eyewitness pages a team
    on, and frames that always showed macroblocking sent every scenario to the
    encoder team."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []
    import random

    artifact = _planted_artifact()
    rnd = random.Random(7)
    out = []
    for k in range(n):
        img = Image.new("RGB", (640, 360), (14, 18, 24))
        d = ImageDraw.Draw(img)
        for y in range(0, 360, 24):
            d.line([(0, y), (640, y)], fill=(24, 32, 44))
        for x in range(0, 640, 40):
            d.line([(x, 0), (x, 360)], fill=(24, 32, 44))

        d.rectangle([0, 0, 640, 28], fill=(30, 38, 52))
        d.text((12, 6), f"SIMULATED ORIGIN | FEED SAMPLE 0{k + 1} | T+{k * 2}s",
               fill=(180, 195, 215))

        if artifact == "corrupt" and k % 2 == 1:
            # Corrupt slices: blocky 16x16 damage across the lower two thirds.
            for _ in range(60):
                x, y = rnd.randrange(0, 624, 16), rnd.randrange(60, 344, 16)
                d.rectangle([x, y, x + 16, y + 16],
                            fill=(rnd.randrange(255), rnd.randrange(255), rnd.randrange(255)))
        elif artifact == "frozen" and k > 0:
            # Stale manifest: every later frame is a duplicate of the first,
            # with the clock stuck where playback stopped advancing.
            d.rectangle([0, 0, 640, 28], fill=(30, 38, 52))
            d.text((12, 6), "SIMULATED ORIGIN | FEED SAMPLE 01 | T+0s", fill=(180, 195, 215))
            for i in range(6):
                d.rectangle([80 + i * 80, 150, 140 + i * 80, 210], fill=(46, 58, 78))
        elif artifact == "black":
            d.rectangle([0, 28, 640, 360], fill=(0, 0, 0))
        else:
            # Intact picture: a stable test pattern. Viewers stalling on this
            # are waiting for segments, not looking at damage.
            for i in range(6):
                d.rectangle([80 + i * 80, 150, 140 + i * 80, 210],
                            fill=(46 + i * 22, 58 + i * 18, 78 + i * 14))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        out.append(buf.getvalue())
    return out


def inspect_frames(dim: str, value: str, forensics: list[dict], n: int = 4) -> dict:
    started = time.perf_counter()

    from . import ch, llm

    frames, source = [], "none"
    if THUMBNAIL_BASE_URL:
        frames = _from_origin(value, n)
        if frames:
            source = "origin"
    if not frames:
        frames = _from_fixtures(n)
        if frames:
            source = "fixtures"
    if not frames and ch.demo_mode():
        # Only the demo world invents pictures, and it labels every one.
        frames = _synthesize(n)
        if frames:
            source = "simulated-origin"

    notes = []
    if frames and llm.credentials_present():
        from google.genai import types
        client = llm.client()
        parts = [types.Part.from_bytes(data=f, mime_type="image/jpeg") for f in frames]
        parts.append(types.Part.from_text(text=(
            f"These frames were captured from streams where {dim}={value} during a "
            f"quality incident. For each frame, describe only what you can actually see: "
            f"macroblocking, frozen or duplicated content, black frame, colour-space error, "
            f"or a normal intact picture. One short line per frame.")))

        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_REASONING_MODEL", "gemini-2.5-pro"),
            contents=[types.Content(role="user", parts=parts)],
        )
        notes = [ln.strip() for ln in (resp.text or "").splitlines() if ln.strip()]
    elif frames:
        notes = [f"{len(frames)} frames captured from {source}; no vision model "
                 f"configured, visual verdict inferred from decode telemetry instead"]
    else:
        notes = ["no frames available — set THUMBNAIL_BASE_URL or drop samples in "
                 "data/frames/; visual verdict inferred from decode telemetry only"]

    return {
        "count": len(frames),
        "notes": notes,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "thumbnails": [f"data:image/jpeg;base64,{base64.b64encode(f).decode()}" for f in frames],
        "source": source,
    }
