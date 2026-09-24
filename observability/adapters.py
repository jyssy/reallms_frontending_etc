import asyncio
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from django.conf import settings
from mcp import Client, StdioServerParameters

from .activity import record_event


class MCPUnavailableError(RuntimeError):
    """Raised when the configured local MCP server cannot be discovered safely."""


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
