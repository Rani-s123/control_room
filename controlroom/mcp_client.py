"""
Client for the official ClickHouse MCP server (`mcp-clickhouse`).

Every read on the critical path goes through here. The SQL still ships in this
repo and is still chosen by the pipeline, not by a model — what changes is who
executes it: the official MCP server rather than a direct driver connection.
That keeps the determinism argument intact (re-run a run_id, get the same
queries against the same window) while the transport is the one ClickHouse
publishes and maintains.

Two details worth knowing before editing this file.

The MCP protocol is asyncio and the pipeline is synchronous, so the session
lives on a dedicated event-loop thread. The stdio transport and the session are
async context managers whose cancel scopes must be entered and exited from the
same task, so one long-lived `_serve` coroutine owns both and pulls requests off
a queue. Entering the contexts in one coroutine and leaving them in another
looks like it works and then fails under cancellation.

The query tool has been renamed across releases: `mcp-clickhouse` 0.6 exposes
`run_query`, earlier versions `run_select_query`. The tool list is read at
startup and the name resolved from it rather than hard-coded, so an upgrade
does not silently leave the pipeline with no way to query.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import threading

# In preference order. The first one the server actually advertises wins.
QUERY_TOOLS = ("run_query", "run_select_query", "run_chdb_select_query")

_PASSTHROUGH_ENV = (
    "CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_SECURE", "CLICKHOUSE_VERIFY", "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_CONNECT_TIMEOUT", "CLICKHOUSE_SEND_RECEIVE_TIMEOUT",
    "CLICKHOUSE_MCP_SERVER_TRANSPORT", "CLICKHOUSE_ENABLED",
    "CHDB_ENABLED", "CHDB_DATA_PATH",
)


def server_command() -> list[str]:
    """How to launch the MCP server.

    `uvx` matches what the ADK agent uses and needs nothing pre-installed, but
    it is not always on PATH — in a slim container image it usually is not — so
    fall back to the console script and then to the module.
    """
    override = os.environ.get("CLICKHOUSE_MCP_COMMAND")
    if override:
        return override.split()
    if shutil.which("uvx"):
        return ["uvx", "--from", "mcp-clickhouse", "mcp-clickhouse"]
    if shutil.which("mcp-clickhouse"):
        return ["mcp-clickhouse"]
    return ["python", "-m", "mcp_clickhouse.main"]


def server_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLICKHOUSE_")}
    for key in _PASSTHROUGH_ENV:
        if os.environ.get(key) is not None:
            env[key] = os.environ[key]
    env.setdefault("CLICKHOUSE_DATABASE", "control_room")
    env.setdefault("CLICKHOUSE_MCP_SERVER_TRANSPORT", "stdio")
    return env


class _Result:
    """Shaped like a clickhouse_connect QueryResult, so callers cannot tell
    which transport produced it."""

    def __init__(self, column_names, result_rows, summary=None):
        self.column_names = column_names
        self.result_rows = result_rows
        self.summary = summary or {}


def _parse(payload: str) -> _Result:
    """Normalise the two shapes the server returns.

    `run_query` answers {"columns": [...], "rows": [[...]]}; the chDB tool
    answers a bare list of row objects. Errors come back as {"status": "error"}
    or {"error": ...} with a 200-shaped envelope, so they have to be checked
    explicitly rather than assumed away.
    """
    if not payload or not payload.strip():
        return _Result([], [])
    try:
        doc = json.loads(payload)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"ClickHouse MCP server returned non-JSON: {payload[:200]}") from err

    if isinstance(doc, dict):
        if doc.get("status") == "error" or "error" in doc:
            raise RuntimeError(f"ClickHouse MCP query failed: "
                               f"{doc.get('message') or doc.get('error')}")
        if "columns" in doc and "rows" in doc:
            return _Result(list(doc["columns"]), [list(r) for r in doc["rows"]])
        doc = [doc]

    if isinstance(doc, list):
        if not doc:
            return _Result([], [])
        columns = list(doc[0].keys())
        return _Result(columns, [[row.get(c) for c in columns] for row in doc])

    raise RuntimeError(f"Unexpected payload from ClickHouse MCP server: {payload[:200]}")


class ClickHouseMCPClient:
    """Synchronous facade over the stdio MCP session."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout
        self._requests: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self.tool_name: str = ""
        self.server_tools: list[str] = []
        self._thread = threading.Thread(target=self._run, name="clickhouse-mcp", daemon=True)
        self._thread.start()
        self._ready.result(timeout=self._timeout)   # re-raises startup failures here

    # -- event-loop thread --------------------------------------------------

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as err:                      # startup failure
            if not self._ready.done():
                self._ready.set_exception(err)

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._loop = asyncio.get_running_loop()
        self._requests = asyncio.Queue()

        command, *args = server_command()
        params = StdioServerParameters(command=command, args=args, env=server_env())

        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.server_tools = [t.name for t in listed.tools]
                    self.tool_name = next(
                        (t for t in QUERY_TOOLS if t in self.server_tools), "")
                    if not self.tool_name:
                        raise RuntimeError(
                            "the ClickHouse MCP server exposes no query tool; saw "
                            f"{self.server_tools}")
                    self._ready.set_result(None)

                    while True:
                        item = await self._requests.get()
                        if item is None:
                            break
                        sql, fut = item
                        try:
                            res = await session.call_tool(self.tool_name, {"query": sql})
                            text = "".join(getattr(c, "text", "") for c in res.content)
                            if getattr(res, "isError", False):
                                raise RuntimeError(f"ClickHouse MCP query failed: {text[:300]}")
                            fut.set_result(text)
                        except Exception as err:
                            if not fut.done():
                                fut.set_exception(err)
        except Exception as err:
            if not self._ready.done():
                self._ready.set_exception(err)
            raise

    # -- caller thread ------------------------------------------------------

    def query(self, sql: str, parameters: dict | None = None) -> _Result:
        """`parameters` are already bound into `sql` by the caller — the MCP
        tool takes a finished query string, so binding happens in ch.py against
        the dimension whitelist before anything reaches here."""
        if self._loop is None or self._requests is None:
            raise RuntimeError("ClickHouse MCP client is not running")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._requests.put_nowait, (sql, fut))
        return _parse(fut.result(timeout=self._timeout))

    def command(self, sql: str) -> None:
        raise RuntimeError(
            "the ClickHouse MCP server is read-only; schema changes and run logging "
            "use the direct connection (see controlroom/ch.py)")

    def close(self) -> None:
        if self._loop is not None and self._requests is not None:
            try:
                self._loop.call_soon_threadsafe(self._requests.put_nowait, None)
            except RuntimeError:
                pass
        self._thread.join(timeout=5)
