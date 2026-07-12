"""Client for the SamaajData MCP server (SSE transport)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from httpx_sse import aconnect_sse

DEFAULT_MCP_URL = "https://mcp.samaajdata.org/sse"
CACHE_TTL_SECONDS = 3600
CACHEABLE_TOOLS = {
    "get_valid_categories",
    "get_valid_subcategories",
    "get_valid_event_types",
    "get_data_partners_list",
}

_TOOL_CACHE: Dict[str, tuple[float, str]] = {}
ProgressCallback = Optional[Callable[[str], None]]


def _cache_get(key: str) -> Optional[str]:
    entry = _TOOL_CACHE.get(key)
    if not entry:
        return None
    cached_at, value = entry
    if time.time() - cached_at > CACHE_TTL_SECONDS:
        _TOOL_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: str) -> None:
    _TOOL_CACHE[key] = (time.time(), value)


class SamaajDataMCPClient:
    """MCP session with optional prefetch and tool-result caching."""

    def __init__(
        self,
        url: str = DEFAULT_MCP_URL,
        timeout: float = 30.0,
        sse_read_timeout: float = 120.0,
        on_progress: ProgressCallback = None,
    ):
        self.url = url
        self.timeout = timeout
        self.sse_read_timeout = sse_read_timeout
        self.on_progress = on_progress
        self._client: Optional[httpx.AsyncClient] = None
        self._post_url: Optional[str] = None
        self._responses: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._request_id = 0
        self._tools: List[Dict[str, Any]] = []
        self.prefetch_context: str = ""

    def _emit(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools

    async def __aenter__(self) -> "SamaajDataMCPClient":
        self._emit("Connecting to SamaajData...")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, read=self.sse_read_timeout)
        )
        sse_ctx = aconnect_sse(self._client, "GET", self.url)
        self._events = await sse_ctx.__aenter__()
        self._sse_ctx = sse_ctx
        self._events.response.raise_for_status()
        self._reader_task = asyncio.create_task(self._read_sse())

        kind, post_url = await asyncio.wait_for(self._responses.get(), timeout=self.timeout)
        if kind != "endpoint":
            raise RuntimeError("MCP SSE did not return an endpoint event.")
        self._post_url = post_url

        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "citizen-data-assistant", "version": "1.0.0"},
            },
        )
        assert self._client and self._post_url
        await self._client.post(
            self._post_url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        tools_response = await self._rpc("tools/list", {})
        self._tools = tools_response.get("result", {}).get("tools", [])
        await self.prefetch_reference_data()
        return self

    async def prefetch_reference_data(self) -> None:
        """Warm cache for stable lookup tools used on most insight queries."""
        self._emit("Loading data categories...")
        categories = await self.call_tool("get_valid_categories", {})
        self._emit("Loading data partners...")
        partners = await self.call_tool("get_data_partners_list", {})
        self.prefetch_context = (
            "SAMAADATA REFERENCE (already loaded — do NOT re-fetch these lists):\n"
            f"Valid categories:\n{categories[:4000]}\n\n"
            f"Data partners:\n{partners[:4000]}"
        )

    async def __aexit__(self, *args: Any) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "_sse_ctx"):
            await self._sse_ctx.__aexit__(*args)
        if self._client:
            await self._client.aclose()

    async def _read_sse(self) -> None:
        assert self._events is not None
        try:
            async for sse in self._events.aiter_sse():
                if sse.event == "endpoint":
                    await self._responses.put(("endpoint", urljoin(self.url, sse.data)))
                elif sse.event == "message" and sse.data:
                    await self._responses.put(("message", json.loads(sse.data)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._responses.put(("error", exc))

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert self._client and self._post_url

        self._request_id += 1
        current_id = self._request_id
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": current_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        await self._client.post(self._post_url, json=payload)

        while True:
            kind, data = await asyncio.wait_for(
                self._responses.get(),
                timeout=self.sse_read_timeout,
            )
            if kind == "error":
                raise data
            if kind != "message":
                continue
            if data.get("id") == current_id:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data

    _PROGRESS_LABELS = {
        "get_valid_categories": "Checking data categories",
        "get_valid_subcategories": "Checking subcategories",
        "get_valid_event_types": "Checking event types",
        "get_data_partners_list": "Loading data partners",
        "get_available_locations_for_category": "Finding locations with data",
        "get_location_hierarchy": "Mapping local areas",
        "get_data_count_from_samaajdata": "Counting civic records",
        "get_data_metadata_on_samaajdata": "Reading data details",
        "get_data_field_values_on_samaajdata": "Pulling field values",
        "get_event_points_for_area_from_samaajdata": "Fetching map points",
        "create_bar_chart": "Building chart",
        "create_pie_chart": "Building chart",
        "create_line_plot": "Building chart",
        "create_histogram": "Building chart",
        "create_scatter_plot": "Building chart",
        "create_donut_chart": "Building chart",
        "create_heatmap": "Building chart",
        "create_area_chart": "Building chart",
    }

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        cache_key = name if name in CACHEABLE_TOOLS and not arguments else ""
        if cache_key:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached

        label = self._PROGRESS_LABELS.get(name, "Querying SamaajData")
        self._emit(f"{label}...")

        response = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        result = response.get("result", {})
        content = result.get("content", [])
        if content:
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block))
            text = "\n".join(parts).strip()
        else:
            text = json.dumps(result)

        if cache_key:
            _cache_set(cache_key, text)
        return text

    def anthropic_tools(self) -> List[Dict[str, Any]]:
        anthropic: List[Dict[str, Any]] = []
        for tool in self._tools:
            schema = dict(tool.get("inputSchema") or {"type": "object", "properties": {}})
            schema.pop("title", None)
            anthropic.append(
                {
                    "name": tool["name"],
                    "description": (tool.get("description") or tool["name"]).strip(),
                    "input_schema": schema,
                }
            )
        return anthropic
