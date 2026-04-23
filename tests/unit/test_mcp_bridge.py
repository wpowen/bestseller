"""Unit tests for ``bestseller.services.mcp_bridge``.

The real ``mcp`` Python SDK is treated as an optional dependency; these
tests never import it.  Instead they inject :class:`FakeTransport`
implementations through the ``transport_factory`` hook so behaviour can
be asserted deterministically — stdout framing, subprocess lifecycle,
and JSON-RPC are out of scope here (they belong to the SDK).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from bestseller.services.mcp_bridge import (
    MCPConnectionPool,
    MCPServerConfig,
    MCPToolSchema,
    MCPTransport,
    _expand_env,
    build_mcp_pool,
    load_mcp_config,
)

pytestmark = pytest.mark.unit


# ── Fake transport used by every test ──────────────────────────────────


class FakeTransport:
    """In-memory transport — behaves like :class:`MCPTransport`."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        tools: list[MCPToolSchema] | None = None,
        responses: dict[str, dict[str, Any]] | None = None,
        start_raises: Exception | None = None,
        call_raises: dict[str, Exception] | None = None,
        never_available: bool = False,
    ) -> None:
        self.config = config
        self._tools = tools or []
        self._responses = responses or {}
        self._start_raises = start_raises
        self._call_raises = call_raises or {}
        self._never_available = never_available
        self._available = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        if self._start_raises is not None:
            raise self._start_raises
        if self._never_available:
            return
        self._available = True

    async def stop(self) -> None:
        self._available = False

    async def list_tools(self) -> list[MCPToolSchema]:
        return list(self._tools)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name in self._call_raises:
            raise self._call_raises[tool_name]
        return self._responses.get(tool_name, {"content": [], "is_error": False})


def _cfg(
    name: str = "fake-server",
    *,
    transport: str = "stdio",
    enabled_for: list[str] | None = None,
    tools_expose: list[str] | None = None,
    enabled: bool = True,
) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport=transport,
        command=["echo", "hi"] if transport == "stdio" else None,
        url="http://example.test/mcp" if transport == "http" else None,
        enabled_for=enabled_for or ["research_agent"],
        tools_expose=tools_expose or [],
        enabled=enabled,
    )


def _tool(server: str, name: str, *, description: str = "doc") -> MCPToolSchema:
    return MCPToolSchema(
        server_name=server,
        tool_name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )


# ── Config loading + env expansion ─────────────────────────────────────


class TestConfigLoading:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_mcp_config(tmp_path / "absent.yaml") == []

    def test_expand_env_plain_and_default(self) -> None:
        env = {"FOO": "bar"}
        assert _expand_env("${FOO}", env) == "bar"
        assert _expand_env("${MISSING:-fallback}", env) == "fallback"
        assert _expand_env("prefix-${FOO}-suffix", env) == "prefix-bar-suffix"

    def test_load_parses_servers_and_expands_env(self, tmp_path: Path) -> None:
        path = tmp_path / "mcp_servers.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "servers": [
                        {
                            "name": "exa",
                            "transport": "stdio",
                            "command": ["npx", "exa-mcp-server"],
                            "env": {"EXA_API_KEY": "${MY_KEY}"},
                            "enabled_for": ["research_agent"],
                            "tools_expose": ["web_search_exa"],
                        },
                        {
                            "name": "local-http",
                            "transport": "http",
                            "url": "http://127.0.0.1:${PORT:-3100}/mcp",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        configs = load_mcp_config(path, env={"MY_KEY": "abc123"})
        assert [c.name for c in configs] == ["exa", "local-http"]
        assert configs[0].env["EXA_API_KEY"] == "abc123"
        assert configs[1].url == "http://127.0.0.1:3100/mcp"

    def test_invalid_transport_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.safe_dump({"servers": [{"name": "x", "transport": "ftp"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_mcp_config(path, env={})

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValueError):
            MCPServerConfig(name="x", transport="stdio")

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValueError):
            MCPServerConfig(name="x", transport="http")


# ── Pool lifecycle ─────────────────────────────────────────────────────


class TestPoolLifecycle:
    async def test_start_collects_tools_from_each_server(self) -> None:
        cfg_a = _cfg("srv-a", enabled_for=["research_agent"])
        cfg_b = _cfg("srv-b", enabled_for=["curator", "forge"])
        transports = {
            "srv-a": FakeTransport(cfg_a, tools=[_tool("srv-a", "search")]),
            "srv-b": FakeTransport(cfg_b, tools=[_tool("srv-b", "lookup")]),
        }

        def factory(cfg: MCPServerConfig) -> MCPTransport:
            return transports[cfg.name]

        pool = MCPConnectionPool([cfg_a, cfg_b], transport_factory=factory)
        await pool.start()
        assert pool.qualified_tool_names() == ["srv_a__search", "srv_b__lookup"]
        assert pool.server_names() == ["srv-a", "srv-b"]

    async def test_start_tolerates_individual_server_failure(self) -> None:
        cfg_ok = _cfg("srv-ok")
        cfg_bad = _cfg("srv-bad")
        transports = {
            "srv-ok": FakeTransport(cfg_ok, tools=[_tool("srv-ok", "search")]),
            "srv-bad": FakeTransport(
                cfg_bad, start_raises=RuntimeError("stdio died")
            ),
        }

        def factory(cfg: MCPServerConfig) -> MCPTransport:
            return transports[cfg.name]

        pool = MCPConnectionPool([cfg_ok, cfg_bad], transport_factory=factory)
        await pool.start()
        # Bad server is skipped; good server still exposes its tool.
        assert pool.server_names() == ["srv-ok"]
        assert pool.qualified_tool_names() == ["srv_ok__search"]

    async def test_disabled_server_is_skipped(self) -> None:
        cfg_on = _cfg("on")
        cfg_off = _cfg("off", enabled=False)
        transports = {
            "on": FakeTransport(cfg_on, tools=[_tool("on", "t1")]),
            "off": FakeTransport(cfg_off, tools=[_tool("off", "t2")]),
        }
        pool = MCPConnectionPool(
            [cfg_on, cfg_off],
            transport_factory=lambda c: transports[c.name],
        )
        await pool.start()
        assert pool.server_names() == ["on"]

    async def test_start_unavailable_transport_is_skipped(self) -> None:
        cfg = _cfg("srv")
        transport = FakeTransport(cfg, never_available=True)
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        assert pool.is_empty()
        assert pool.server_names() == []

    async def test_tools_expose_whitelist_applies(self) -> None:
        cfg = _cfg("srv", tools_expose=["keep_me"])
        transport = FakeTransport(
            cfg,
            tools=[
                _tool("srv", "keep_me"),
                _tool("srv", "skip_me"),
            ],
        )
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        assert pool.qualified_tool_names() == ["srv__keep_me"]

    async def test_stop_closes_every_transport(self) -> None:
        cfg_a = _cfg("a")
        cfg_b = _cfg("b")
        transports = {
            "a": FakeTransport(cfg_a, tools=[_tool("a", "t")]),
            "b": FakeTransport(cfg_b, tools=[_tool("b", "t")]),
        }
        pool = MCPConnectionPool(
            [cfg_a, cfg_b], transport_factory=lambda c: transports[c.name]
        )
        await pool.start()
        assert all(t.available for t in transports.values())
        await pool.stop()
        assert not any(t.available for t in transports.values())
        assert pool.is_empty()

    async def test_start_is_idempotent(self) -> None:
        cfg = _cfg("srv")
        transport = FakeTransport(cfg, tools=[_tool("srv", "t")])
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        await pool.start()  # should be a no-op, not re-enter
        assert pool.qualified_tool_names() == ["srv__t"]


# ── Dispatch ───────────────────────────────────────────────────────────


class TestPoolDispatch:
    async def test_call_returns_transport_response(self) -> None:
        cfg = _cfg("srv")
        transport = FakeTransport(
            cfg,
            tools=[_tool("srv", "search")],
            responses={"search": {"content": [{"type": "text", "text": "hi"}], "is_error": False}},
        )
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        result = await pool.call("srv__search", {"q": "cultivation"})
        assert result == {"content": [{"type": "text", "text": "hi"}], "is_error": False}
        assert transport.calls == [("search", {"q": "cultivation"})]

    async def test_call_unknown_qualified_name(self) -> None:
        cfg = _cfg("srv")
        transport = FakeTransport(cfg, tools=[_tool("srv", "search")])
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        result = await pool.call("nonexistent", {})
        assert result == {"error": "unknown_mcp_tool:nonexistent"}

    async def test_tools_for_filters_by_consumer(self) -> None:
        cfg_research = _cfg("r", enabled_for=["research_agent"])
        cfg_forge = _cfg("f", enabled_for=["forge"])
        transports = {
            "r": FakeTransport(cfg_research, tools=[_tool("r", "search")]),
            "f": FakeTransport(cfg_forge, tools=[_tool("f", "lookup")]),
        }
        pool = MCPConnectionPool(
            [cfg_research, cfg_forge],
            transport_factory=lambda c: transports[c.name],
        )
        await pool.start()
        research_tools = pool.tools_for("research_agent")
        forge_tools = pool.tools_for("forge")
        assert [t.qualified_name for t in research_tools] == ["r__search"]
        assert [t.qualified_name for t in forge_tools] == ["f__lookup"]

    async def test_as_tool_specs_bridges_to_tool_registry(self) -> None:
        cfg = _cfg("srv", enabled_for=["research_agent"])
        transport = FakeTransport(
            cfg,
            tools=[_tool("srv", "search", description="the search tool")],
            responses={"search": {"hits": 3}},
        )
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        specs = pool.as_tool_specs(consumer="research_agent")
        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "srv__search"
        assert spec.description == "the search tool"
        assert spec.parameters["type"] == "object"
        # The handler closes over the pool — invoking it dispatches to call().
        result = await spec.handler({"q": "pulse"})
        assert result == {"hits": 3}
        assert transport.calls == [("search", {"q": "pulse"})]

    async def test_as_tool_specs_consumer_none_returns_everything(self) -> None:
        cfg = _cfg("srv", enabled_for=["curator"])
        transport = FakeTransport(cfg, tools=[_tool("srv", "t")])
        pool = MCPConnectionPool([cfg], transport_factory=lambda c: transport)
        await pool.start()
        specs = pool.as_tool_specs()
        assert [s.name for s in specs] == ["srv__t"]


# ── build_mcp_pool convenience ─────────────────────────────────────────


class TestBuildMcpPool:
    async def test_build_pool_from_config_file(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "servers": [
                        {
                            "name": "x",
                            "transport": "stdio",
                            "command": ["echo", "hi"],
                            "enabled_for": ["research_agent"],
                            "tools_expose": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        captured: dict[str, Any] = {}

        def factory(cfg: MCPServerConfig) -> MCPTransport:
            transport = FakeTransport(cfg, tools=[_tool(cfg.name, "ping")])
            captured["transport"] = transport
            return transport

        pool = await build_mcp_pool(path, env={}, transport_factory=factory)
        try:
            assert pool.qualified_tool_names() == ["x__ping"]
        finally:
            await pool.stop()

    async def test_missing_file_yields_empty_pool(self, tmp_path: Path) -> None:
        pool = await build_mcp_pool(tmp_path / "missing.yaml", env={})
        assert pool.is_empty()
        await pool.stop()
