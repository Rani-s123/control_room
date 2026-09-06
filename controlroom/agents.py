"""
The ADK surface, deployable to Vertex AI Agent Engine.

`root_agent` is what a human talks to. It has two kinds of tools:

  * `open_control_room` — runs the fixed six-step pipeline. Every read in it is
    executed by the official ClickHouse MCP server, and every one of those
    queries ships in `sql/queries/`. The transport is ClickHouse's; the
    decisions are the pipeline's.
  * The same ClickHouse MCP toolset, handed to the agent directly for
    *follow-up* questions ("was BLR1 affected too?", "show me player 3.98.2 on
    Fire TV"). Open-ended exploration belongs here, where a wrong query costs
    nothing and the pipeline's verdict is already recorded.

Deploy:
    adk deploy agent_engine --project $GOOGLE_CLOUD_PROJECT \
        --region $GOOGLE_CLOUD_LOCATION controlroom
"""

from __future__ import annotations

import os

from . import ch, mcp_client, pipeline

MODEL = os.environ.get("GEMINI_REASONING_MODEL", "gemini-2.5-pro")

try:
    from google.adk.agents import Agent
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams
    from mcp import StdioServerParameters
    HAS_ADK = True
except ImportError:
    HAS_ADK = False
    Agent = None
    McpToolset = None


# --- tools ------------------------------------------------------------------

def open_control_room() -> dict:
    """Run a full incident sweep: detect, diagnose, visually verify, size the
    impact, and produce a remediation. Returns the run summary including run_id."""
    return pipeline.run()


def replay_run(run_id: str) -> dict:
    """Replay a previous incident response step by step, including every SQL
    statement that was executed and how many rows it scanned.

    Args:
        run_id: identifier returned by open_control_room, e.g. 'run-a1b2c3d4e5'.
    """
    return {"run_id": run_id, "steps": ch.replay(run_id)}


def check_slice(dimension: str, value: str, window_min: int = 20) -> dict:
    """Check whether one specific slice is currently degraded.

    Args:
        dimension: one of cdn, region, device_type, isp, player_version, rendition.
        value: the slice to check, e.g. 'akamai' or 'IN-KA'.
        window_min: minutes of history to consider.
    """
    allowed = {"cdn", "region", "device_type", "isp", "player_version", "rendition"}
    if dimension not in allowed:
        return {"error": f"dimension must be one of {sorted(allowed)}"}
    rows, ms, read = ch.run_template(
        "forensics", {"dim": dimension, "value": value, "window_min": window_min}, statement=1)
    return {"slice": f"{dimension}={value}", "metrics": rows[0] if rows else {},
            "latency_ms": ms, "rows_scanned": read}


# --- ClickHouse MCP ---------------------------------------------------------

# The query tool was renamed between releases: mcp-clickhouse 0.6 exposes
# `run_query`, earlier versions `run_select_query`. Filtering on one name alone
# silently leaves the agent able to list tables and unable to query anything,
# which looks like a quiet agent rather than a broken one — so allow both and
# let the server decide which it serves.
MCP_TOOLS = ["list_databases", "list_tables", "run_query", "run_select_query"]


def clickhouse_mcp():
    """Official ClickHouse MCP server, read-only, scoped to the control_room db.

    The same server the pipeline reads through (see controlroom/mcp_client.py).
    Here it is handed to the agent for open-ended follow-up questions, where a
    wrong query costs nothing.
    """
    if not HAS_ADK:
        return None
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=mcp_client.server_command()[0],
                args=mcp_client.server_command()[1:],
                env=mcp_client.server_env(),
            ),
            timeout=30,
        ),
        tool_filter=MCP_TOOLS,
    )


INSTRUCTION = """You run the control room for a live OTT service during a broadcast.

When someone reports trouble, or asks what is happening, call `open_control_room`
first. It runs the same six deterministic steps every time and returns a run_id.
Report what it found in the order the evidence arrived — detection, culprit slice,
visual verdict, impact, remediation, and whether the numbers have actually come
back yet — and always give the numbers, never adjectives
in place of numbers.

Use the ClickHouse MCP tools only for follow-up questions the pipeline did not
already answer. Never use them to second-guess the pipeline's culprit: if you
disagree with it, say so and show the query you ran.

If someone asks what happened earlier, call `replay_run` with the run_id. Every
step is stored, including the SQL, so you can always show your work.

You are talking to an on-call engineer during a live event. Lead with the action
they should take. Keep it short enough to read on a phone."""


root_agent = Agent(
    name="control_room",
    model=MODEL,
    description="Diagnoses live-streaming quality incidents against ClickHouse telemetry.",
    instruction=INSTRUCTION,
    tools=[t for t in [open_control_room, replay_run, check_slice, clickhouse_mcp()] if t is not None],
) if HAS_ADK else None
