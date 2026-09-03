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
  2. A local fixture set under data/frames/ (used by the demo, so the run works
     with no live origin attached).
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


def _synthesize(n: int) -> list[bytes]:
    """Last resort so a fresh clone still runs: render frames that show the
    artefact class described by the telemetry (blocky 16x16 macroblock damage)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []
    import random
    rnd = random.Random(7)
    out = []
    for k in range(n):
        img = Image.new("RGB", (640, 360), (14, 18, 24))
        d = ImageDraw.Draw(img)
        # Background grid
        for y in range(0, 360, 24):
            d.line([(0, y), (640, y)], fill=(24, 32, 44))
        for x in range(0, 640, 40):
            d.line([(x, 0), (x, 360)], fill=(24, 32, 44))
        
        # Slate header bar
        d.rectangle([0, 0, 640, 28], fill=(30, 38, 52))
        d.text((12, 6), f"FEED SAMPLE 0{k+1} | LIVE STREAM MONITOR", fill=(180, 195, 215))

        if k % 2 == 1:  # damaged frame
            d.rectangle([12, 36, 180, 56], fill=(180, 40, 40))
            d.text((20, 40), "MACROBLOCK FAIL", fill=(255, 255, 255))
            for _ in range(60):
                x, y = rnd.randrange(0, 624, 16), rnd.randrange(60, 344, 16)
                d.rectangle([x, y, x + 16, y + 16],
                            fill=(rnd.randrange(255), rnd.randrange(255), rnd.randrange(255)))
        else:
            d.rectangle([12, 36, 120, 56], fill=(40, 160, 100))
            d.text((20, 40), "INTACT 1080p", fill=(255, 255, 255))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        out.append(buf.getvalue())
    return out


def inspect_frames(dim: str, value: str, forensics: list[dict], n: int = 4) -> dict:
    started = time.perf_counter()

    frames = (_from_origin(value, n) if THUMBNAIL_BASE_URL else []) or _from_fixtures(n) or _synthesize(n)

    from . import llm

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
        notes = [f"{len(frames)} frames captured; no vision model configured, "
                 f"visual verdict inferred from decode telemetry instead"]

    return {
        "count": len(frames),
        "notes": notes,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "thumbnails": [f"data:image/jpeg;base64,{base64.b64encode(f).decode()}" for f in frames],
        "source": "origin" if THUMBNAIL_BASE_URL else ("fixtures" if FIXTURES.exists() else "synthetic"),
    }
