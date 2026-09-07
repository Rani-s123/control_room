"""Vercel ASGI entrypoint for The Control Room.

The application remains a FastAPI app; this thin adapter only gives Vercel
an importable function entrypoint. Runtime behavior is defined in
controlroom.server.
"""

from controlroom.server import app

__all__ = ["app"]
