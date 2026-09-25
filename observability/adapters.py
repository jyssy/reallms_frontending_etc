import asyncio
import json
import time
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

from django.conf import settings
from mcp import Client, StdioServerParameters

from .activity import record_event
from .contract import (
    MAX_PROGRESS_MESSAGE_BYTES,
    ContractError,
    parse_observed_result,
    parse_trace_event,
)
from .traces import trace_store


class MCPUnavailableError(RuntimeError):
    """Raised when the configured local MCP server cannot be discovered safely."""


class ObservedRunUnavailableError(RuntimeError):
    """Raised with a stable code when an observed MCP call cannot complete."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def configuration_status() -> dict[str, Any]:
    command, server = _mcp_paths()
    command_ready = command.is_file()
    server_ready = server.is_file()
    return {
        "configured": command_ready and server_ready,
        "transport": "stdio",
        "command_ready": command_ready,
        "server_ready": server_ready,
    }


def _mcp_paths() -> tuple[Path, Path]:
    root = Path(settings.ORCHESTRATOR_ROOT).resolve()
    return root / ".venv" / "bin" / "python", root / "mcp_server.py"


def discovery_context(server_name: str, server_version: str | None) -> dict[str, Any]:
    return {
        "model_calls": 0,
        "steps": [
            {
                "order": 1,
                "component": "Django",
                "version": version("Django"),
                "action": "Receives the local catalog request and applies the response boundary.",
            },
            {
                "order": 2,
                "component": "MCP Python client",
                "version": version("mcp"),
                "action": "Starts stdio, negotiates the classic protocol, and sends tools/list.",
            },
            {
                "order": 3,
                "component": server_name,
                "version": server_version,
                "action": "Returns its registered tool names, descriptions, and input schemas.",
            },
            {
                "order": 4,
                "component": "Model providers",
                "version": None,
                "action": "Not called during discovery.",
            },
        ],
        "model_roles": [
            {
                "role": "Router",
                "model": None,
                "status": "not_observed",
                "purpose": "Classifies model-backed requests; not used by tools/list.",
            },
            {
                "role": "Specialist / reviewer",
                "model": None,
                "status": "not_observed",
                "purpose": "Produces or reviews model-backed work; not used by tools/list.",
            },
            {
                "role": "Judge",
                "model": None,
                "status": "not_observed",
                "purpose": "Evaluates a reviewed result when requested; not used by tools/list.",
            },
        ],
        "attribution_note": (
            "This MCP call does not expose configured model names. Actual model attribution "
            "will be shown only after a model-backed result reports it."
        ),
    }


async def discover_tools() -> dict[str, Any]:
    config = configuration_status()
    if not config["configured"]:
        record_event("mcp.tools.discover", "unavailable", reason="not_configured")
        raise MCPUnavailableError("The local MCP server is not configured.")

    started = time.perf_counter()
    record_event("mcp.tools.discover", "started")
    command, server_path = _mcp_paths()
    server = StdioServerParameters(
        command=str(command),
        args=[str(server_path)],
        cwd=str(server_path.parent),
    )

    try:
        async with asyncio.timeout(settings.ORCHESTRATOR_MCP_TIMEOUT_SECONDS):
            async with Client(
                server,
                read_timeout_seconds=settings.ORCHESTRATOR_MCP_TIMEOUT_SECONDS,
                mode="legacy",
            ) as client:
                result = await client.list_tools()
                tools = [
                    {
                        "name": tool.name,
                        "title": tool.title or tool.name.replace("_", " ").title(),
                        "description": tool.description or "No description supplied.",
                        "input_schema": tool.input_schema,
                    }
                    for tool in result.tools
                ]
                server_info = client.server_info
                protocol_version = str(client.protocol_version)
    except TimeoutError as error:
        record_event("mcp.tools.discover", "error", reason="timeout")
        raise MCPUnavailableError("The local MCP server timed out.") from error
    except Exception as error:
        record_event("mcp.tools.discover", "error", reason="connection_failed")
        raise MCPUnavailableError("The local MCP server could not be reached.") from error

    latency_ms = round((time.perf_counter() - started) * 1000)
    record_event("mcp.tools.discover", "completed", tool_count=len(tools), latency_ms=latency_ms)
    return {
        "source": "live_mcp",
        "observed": True,
        "server": {
            "name": server_info.name if server_info else "unknown",
            "version": server_info.version if server_info else None,
            "protocol_version": protocol_version,
        },
        "tool_count": len(tools),
        "latency_ms": latency_ms,
        "discovery": discovery_context(
            server_info.name if server_info else "MCP server",
            server_info.version if server_info else None,
        ),
        "tools": tools,
    }


async def observe_orchestrator(
    prompt: str,
    *,
    repo_root: str = "",
    use_judge: bool = True,
    effective_constraints: str = "",
) -> dict[str, Any]:
    """Call the observed MCP tool and retain only validated metadata.

    This is a server-side integration seam. It is intentionally not exposed as
    a browser endpoint, and the returned projection excludes answer content.
    """
    config = configuration_status()
    if not config["configured"]:
        record_event("mcp.run.observe", "unavailable", reason="not_configured")
        raise ObservedRunUnavailableError("not_configured")

    command, server_path = _mcp_paths()
    server = StdioServerParameters(
        command=str(command),
        args=[str(server_path)],
        cwd=str(server_path.parent),
    )
    accepted_events = 0
    run_id: str | None = None

    async def receive_progress(
        progress: float,
        total: float | None,
        message: str | None,
    ) -> None:
        del progress, total
        nonlocal accepted_events, run_id
        if (
            not isinstance(message, str)
            or len(message.encode("utf-8")) > MAX_PROGRESS_MESSAGE_BYTES
        ):
            return
        try:
            event = parse_trace_event(json.loads(message))
        except (json.JSONDecodeError, ContractError):
            return
        if run_id is None:
            run_id = event["run_id"]
        if event["run_id"] != run_id:
            return
        if trace_store.add_event(event):
            accepted_events += 1

    started = time.perf_counter()
    try:
        async with asyncio.timeout(settings.ORCHESTRATOR_OBSERVED_TIMEOUT_SECONDS):
            async with Client(
                server,
                read_timeout_seconds=settings.ORCHESTRATOR_OBSERVED_TIMEOUT_SECONDS,
                mode="legacy",
            ) as client:
                result = await client.call_tool(
                    "ask_orchestrator_observed",
                    {
                        "prompt": prompt,
                        "repo_root": repo_root,
                        "use_judge": use_judge,
                        "effective_constraints": effective_constraints,
                    },
                    progress_callback=receive_progress,
                )
    except TimeoutError as error:
        record_event("mcp.run.observe", "error", reason="timeout")
        raise ObservedRunUnavailableError("timeout") from error
    except Exception as error:
        record_event("mcp.run.observe", "error", reason="connection_failed")
        raise ObservedRunUnavailableError("connection_failed") from error

    structured = getattr(result, "structured_content", None)
    if not isinstance(structured, Mapping) or getattr(result, "is_error", False):
        record_event("mcp.run.observe", "error", reason="tool_error")
        raise ObservedRunUnavailableError("tool_error")
    safe_result = parse_observed_result(structured)
    if run_id is not None:
        trace_store.finish_run(run_id, safe_result)
    latency_ms = round((time.perf_counter() - started) * 1000)
    record_event(
        "mcp.run.observe",
        "completed",
        accepted_events=accepted_events,
        latency_ms=latency_ms,
    )
    return {"run_id": run_id, "result": safe_result, "accepted_events": accepted_events}
