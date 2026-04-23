"""MCP (Model Context Protocol) client bridge for BestSeller.

BestSeller already ships an MCP **server** at :mod:`bestseller.mcp.server`
which exposes novel-management APIs to external clients.  This module is
the complementary **client** layer: it lets BestSeller's own workers
(Research Agent, Library Curator, Forges) call out to third-party MCP
servers such as ``exa-mcp-server`` for web search, ``mcp-server-wikipedia``
for reference lookup, or a local knowledge-base server for curated notes.

Design goals
------------

* **MCP is enhancement, not hard dependency.**  The upstream ``mcp``
  Python package is an optional dependency; if it cannot be imported the
  bridge returns an empty pool and callers keep working through the
  HTTP + pgvector channels.  Any individual server failing to start is
  logged as a warning and marked unavailable — never propagated.
* **Transport abstraction via :class:`MCPTransport`.**  Production uses
  stdio subprocesses or HTTP Streamable transports; tests plug in an
  in-memory fake without importing the real SDK.
* **ToolRegistry compatibility.**  :meth:`MCPConnectionPool.as_tool_specs`
  emits :class:`ToolSpec` objects with qualified names like
  ``exa_websearch__web_search_exa`` so they drop straight into the
  tool-use loop defined in :mod:`bestseller.services.llm_tool_runtime`.
* **Scoped visibility.**  Each server declares ``enabled_for``
  consumers (``research_agent`` / ``curator`` / ``forge``) so we don't
  leak every tool to every agent.

Config file format (``config/mcp_servers.yaml``)
-------------------------------------------------

.. code-block:: yaml

    servers:
      - name: exa-websearch
        transport: stdio
        command: ["npx", "-y", "exa-mcp-server"]
        env: { EXA_API_KEY: "${EXA_API_KEY}" }
        enabled_for: [research_agent, curator]
        tools_expose: [web_search_exa, research_papers]

      - name: local-knowledge
        transport: http
        url: "http://localhost:3100/mcp"
        headers: { Authorization: "Bearer ${LOCAL_KB_TOKEN}" }
        enabled_for: [research_agent, curator, forge]
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from bestseller.services.llm_tool_runtime import ToolSpec

logger = logging.getLogger(__name__)


# ── Config model ───────────────────────────────────────────────────────


_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


class MCPServerConfig(BaseModel):
    """Validated configuration for one external MCP server."""

    name: str = Field(min_length=1, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    transport: str  # "stdio" | "http"
    command: list[str] | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled_for: list[str] = Field(default_factory=list)
    tools_expose: list[str] = Field(default_factory=list)  # empty = all
    timeout_seconds: float = 60.0
    enabled: bool = True

    @field_validator("transport")
    @classmethod
    def _valid_transport(cls, value: str) -> str:
        if value not in {"stdio", "http"}:
            raise ValueError(f"transport must be 'stdio' or 'http', got {value!r}")
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"Server {self.name!r}: stdio transport requires 'command'")
        if self.transport == "http" and not self.url:
            raise ValueError(f"Server {self.name!r}: http transport requires 'url'")


def _expand_env(value: str, env: Mapping[str, str]) -> str:
    """Replace ``${VAR}`` / ``${VAR:-default}`` placeholders in ``value``."""

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        default = match.group(2) or ""
        return env.get(var, default)

    return _ENV_PATTERN.sub(_sub, value)


def _expand_config_env(
    configs: Sequence[MCPServerConfig],
    env: Mapping[str, str],
) -> list[MCPServerConfig]:
    """Return copies of ``configs`` with env placeholders expanded."""

    expanded: list[MCPServerConfig] = []
    for cfg in configs:
        expanded_env = {k: _expand_env(v, env) for k, v in cfg.env.items()}
        expanded_headers = {k: _expand_env(v, env) for k, v in cfg.headers.items()}
        expanded_url = _expand_env(cfg.url, env) if cfg.url else None
        expanded.append(
            cfg.model_copy(
                update={
                    "env": expanded_env,
                    "headers": expanded_headers,
                    "url": expanded_url,
                }
            )
        )
    return expanded


def load_mcp_config(
    path: Path | str,
    *,
    env: Mapping[str, str] | None = None,
) -> list[MCPServerConfig]:
    """Load and validate ``config/mcp_servers.yaml``.

    Unknown-shape entries raise :class:`ValueError`.  A missing file
    returns an empty list — this is the "no MCP servers configured" path.
    """

    env = dict(env if env is not None else os.environ)
    path = Path(path)
    if not path.exists():
        logger.info("MCP config file not found at %s; no servers will be loaded", path)
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"MCP config at {path} must be a mapping")
    servers = raw.get("servers") or []
    if not isinstance(servers, list):
        raise ValueError(f"MCP config at {path} must have 'servers' list")
    configs: list[MCPServerConfig] = []
    for idx, entry in enumerate(servers):
        if not isinstance(entry, dict):
            raise ValueError(f"MCP servers[{idx}] must be a mapping")
        try:
            configs.append(MCPServerConfig(**entry))
        except ValidationError as exc:
            raise ValueError(f"MCP servers[{idx}] invalid: {exc}") from exc
    return _expand_config_env(configs, env)


# ── Tool + transport abstractions ──────────────────────────────────────


@dataclass(frozen=True)
class MCPToolSchema:
    """Single tool exposed by an MCP server."""

    server_name: str
    tool_name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        """Name used when registering into :class:`ToolRegistry`.

        We join server + tool with ``__`` so it stays inside the
        ``^[a-zA-Z0-9_-]+$`` ToolSpec naming rule even when the server
        name contains hyphens.
        """
        safe_server = self.server_name.replace("-", "_")
        safe_tool = self.tool_name.replace("-", "_")
        return f"{safe_server}__{safe_tool}"


class MCPTransport(Protocol):
    """Abstract transport — production uses stdio/http; tests inject fakes."""

    @property
    def available(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def list_tools(self) -> list[MCPToolSchema]: ...

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...


TransportFactory = Callable[[MCPServerConfig], MCPTransport]


# ── Real transports (lazy-import the ``mcp`` package) ──────────────────


class _MissingMCPTransport:
    """Sentinel used when the ``mcp`` package is not installed.

    Marks the server as unavailable without raising at import-time so the
    rest of the pipeline (HTTP search, pgvector) keeps working.
    """

    def __init__(self, config: MCPServerConfig, reason: str) -> None:
        self._config = config
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    async def start(self) -> None:
        logger.warning(
            "MCP server %s unavailable: %s", self._config.name, self._reason
        )

    async def stop(self) -> None:
        return None

    async def list_tools(self) -> list[MCPToolSchema]:
        return []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return {"error": f"mcp_unavailable:{self._reason}"}


def default_transport_factory(config: MCPServerConfig) -> MCPTransport:
    """Default factory: tries to build a real transport, degrades to sentinel.

    The real stdio/http transports depend on the optional ``mcp`` package.
    If that import fails we return a :class:`_MissingMCPTransport` so the
    rest of the pool still functions.
    """

    try:
        if config.transport == "stdio":
            return _StdioTransport(config)
        return _HttpTransport(config)
    except _MCPImportError as exc:  # pragma: no cover - depends on env
        return _MissingMCPTransport(config, str(exc))


class _MCPImportError(RuntimeError):
    pass


def _import_mcp_client() -> Any:
    """Lazy import of ``mcp`` client SDK; raises :class:`_MCPImportError`."""

    try:
        import mcp  # type: ignore[import-not-found]

        return mcp
    except ImportError as exc:
        raise _MCPImportError(
            "python 'mcp' package not installed; install via pip install mcp"
        ) from exc


class _StdioTransport:
    """Stdio transport backed by the official ``mcp`` Python client.

    Kept intentionally thin: the heavy lifting (JSON-RPC framing, process
    lifecycle) is delegated to the upstream SDK.  If that SDK is
    unavailable the instantiation path via :func:`default_transport_factory`
    falls back to :class:`_MissingMCPTransport`.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        _import_mcp_client()  # raises _MCPImportError if unavailable
        self._config = config
        self._session: Any = None
        self._stack: Any = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession, StdioServerParameters  # type: ignore[import-not-found]
            from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

            params = StdioServerParameters(
                command=self._config.command[0],  # type: ignore[index]
                args=list(self._config.command[1:]),  # type: ignore[index]
                env={**os.environ, **self._config.env},
            )
            self._stack = AsyncExitStack()
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP stdio server %s failed to start: %s", self._config.name, exc
            )
            await self.stop()

    async def stop(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP stdio server %s shutdown error: %s",
                    self._config.name,
                    exc,
                )
            finally:
                self._stack = None
                self._session = None
                self._available = False

    async def list_tools(self) -> list[MCPToolSchema]:
        if not self._available or self._session is None:
            return []
        response = await self._session.list_tools()
        return _convert_tool_list(self._config.name, response)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._available or self._session is None:
            return {"error": "mcp_session_unavailable"}
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return {"error": f"mcp_timeout:{self._config.timeout_seconds}s"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        return _convert_call_result(result)


class _HttpTransport:
    """HTTP Streamable transport — used for hosted MCP servers."""

    def __init__(self, config: MCPServerConfig) -> None:
        _import_mcp_client()
        self._config = config
        self._session: Any = None
        self._stack: Any = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession  # type: ignore[import-not-found]
            from mcp.client.streamable_http import (  # type: ignore[import-not-found]
                streamablehttp_client,
            )

            self._stack = AsyncExitStack()
            streams = await self._stack.enter_async_context(
                streamablehttp_client(
                    self._config.url,
                    headers=self._config.headers,
                )
            )
            # streamable_http returns (read, write, get_session_id) in some versions
            read, write = streams[0], streams[1]
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP http server %s failed to start: %s",
                self._config.name,
                exc,
            )
            await self.stop()

    async def stop(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP http server %s shutdown error: %s",
                    self._config.name,
                    exc,
                )
            finally:
                self._stack = None
                self._session = None
                self._available = False

    async def list_tools(self) -> list[MCPToolSchema]:
        if not self._available or self._session is None:
            return []
        response = await self._session.list_tools()
        return _convert_tool_list(self._config.name, response)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._available or self._session is None:
            return {"error": "mcp_session_unavailable"}
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return {"error": f"mcp_timeout:{self._config.timeout_seconds}s"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        return _convert_call_result(result)


def _convert_tool_list(server_name: str, response: Any) -> list[MCPToolSchema]:
    """Normalise the ``mcp`` SDK's ListToolsResult into our dataclass."""

    tools = getattr(response, "tools", None) or []
    out: list[MCPToolSchema] = []
    for tool in tools:
        name = getattr(tool, "name", None) or (
            tool.get("name") if isinstance(tool, dict) else None
        )
        if not name:
            continue
        description = getattr(tool, "description", None) or (
            tool.get("description") if isinstance(tool, dict) else None
        )
        schema = getattr(tool, "inputSchema", None) or (
            tool.get("inputSchema") if isinstance(tool, dict) else None
        )
        out.append(
            MCPToolSchema(
                server_name=server_name,
                tool_name=str(name),
                description=str(description or ""),
                input_schema=dict(schema) if isinstance(schema, dict) else {},
            )
        )
    return out


def _convert_call_result(result: Any) -> dict[str, Any]:
    """Reduce a ``CallToolResult`` to a JSON-serialisable dict."""

    if isinstance(result, dict):
        return result
    content = getattr(result, "content", None)
    is_error = bool(getattr(result, "isError", False))
    if content is None:
        return {"content": [], "is_error": is_error}
    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict):
            parts.append(item)
            continue
        parts.append(
            {
                "type": getattr(item, "type", "text"),
                "text": getattr(item, "text", None),
                "data": getattr(item, "data", None),
            }
        )
    return {"content": parts, "is_error": is_error}


# ── Connection pool ────────────────────────────────────────────────────


@dataclass
class MCPConnectionPool:
    """Holds live transports for the whole worker process.

    Usage::

        pool = MCPConnectionPool(configs)
        await pool.start()
        ...
        await pool.stop()

    Thread-unsafe by design — worker processes create one pool per
    process and reuse it across tasks via dependency injection.
    """

    configs: Sequence[MCPServerConfig]
    transport_factory: TransportFactory | None = None
    _transports: dict[str, MCPTransport] = field(default_factory=dict, init=False)
    _tools: dict[str, MCPToolSchema] = field(default_factory=dict, init=False)
    _started: bool = field(default=False, init=False)

    async def start(self) -> None:
        """Spin up every enabled transport.  Never raises."""
        if self._started:
            return
        factory = self.transport_factory or default_transport_factory
        for cfg in self.configs:
            if not cfg.enabled:
                continue
            transport = factory(cfg)
            try:
                await transport.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP pool: transport %s start raised: %s", cfg.name, exc
                )
                continue
            if not transport.available:
                continue
            try:
                tools = await transport.list_tools()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP pool: transport %s list_tools raised: %s", cfg.name, exc
                )
                tools = []
            allow = set(cfg.tools_expose)
            for tool in tools:
                if allow and tool.tool_name not in allow:
                    continue
                self._tools[tool.qualified_name] = tool
            self._transports[cfg.name] = transport
        self._started = True

    async def stop(self) -> None:
        for name, transport in list(self._transports.items()):
            try:
                await transport.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP pool: transport %s stop raised: %s", name, exc)
        self._transports.clear()
        self._tools.clear()
        self._started = False

    def is_empty(self) -> bool:
        return not self._tools

    def server_names(self) -> list[str]:
        return list(self._transports.keys())

    def qualified_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def tools_for(self, consumer: str) -> list[MCPToolSchema]:
        """Return tools whose server config lists ``consumer`` in enabled_for."""
        consumers_by_server = {cfg.name: cfg.enabled_for for cfg in self.configs}
        return [
            tool
            for tool in self._tools.values()
            if not consumers_by_server.get(tool.server_name)
            or consumer in consumers_by_server[tool.server_name]
        ]

    async def call(
        self, qualified_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(qualified_name)
        if tool is None:
            return {"error": f"unknown_mcp_tool:{qualified_name}"}
        transport = self._transports.get(tool.server_name)
        if transport is None or not transport.available:
            return {"error": f"mcp_server_unavailable:{tool.server_name}"}
        return await transport.call_tool(tool.tool_name, arguments)

    def as_tool_specs(
        self,
        consumer: str | None = None,
    ) -> list[ToolSpec]:
        """Bridge MCP tools into :class:`ToolSpec` for ``ToolRegistry``.

        Each MCP tool's handler routes through :meth:`call`.  Pass
        ``consumer="research_agent"`` to filter by ``enabled_for``.
        """

        if consumer is None:
            tools = list(self._tools.values())
        else:
            tools = self.tools_for(consumer)

        specs: list[ToolSpec] = []
        for tool in tools:
            handler = _make_handler(self, tool.qualified_name)
            specs.append(
                ToolSpec(
                    name=tool.qualified_name,
                    description=tool.description
                    or f"MCP tool {tool.tool_name} from {tool.server_name}",
                    parameters=tool.input_schema
                    or {"type": "object", "properties": {}},
                    handler=handler,
                )
            )
        return specs


def _make_handler(pool: MCPConnectionPool, qualified_name: str) -> Callable[
    [dict[str, Any]], Awaitable[dict[str, Any]]
]:
    async def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
        return await pool.call(qualified_name, arguments)

    return _handler


# ── Convenience bootstrap ──────────────────────────────────────────────


async def build_mcp_pool(
    config_path: Path | str,
    *,
    env: Mapping[str, str] | None = None,
    transport_factory: TransportFactory | None = None,
) -> MCPConnectionPool:
    """Load config + spin up the pool in one call.

    The returned pool is always safe to use; a malformed config raises,
    but individual server start-failures are swallowed.
    """
    configs = load_mcp_config(config_path, env=env)
    pool = MCPConnectionPool(configs=configs, transport_factory=transport_factory)
    await pool.start()
    return pool


__all__ = [
    "MCPServerConfig",
    "MCPToolSchema",
    "MCPTransport",
    "MCPConnectionPool",
    "TransportFactory",
    "load_mcp_config",
    "default_transport_factory",
    "build_mcp_pool",
]
