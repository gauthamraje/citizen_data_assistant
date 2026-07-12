"""Claude + MCP tool-use loop for SamaajData insights."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from anthropic import Anthropic

from knowledge_base import build_messages_for_claude
from samaajdata_mcp import SamaajDataMCPClient

MAX_TOOL_ROUNDS = 12
FIRST_TURN_MAX_TOOL_ROUNDS = 10

INSIGHTS_PRODUCT_RULES = """
LOCAL DATA INSIGHTS — PRODUCT RULES (Option 2 only):
- The SAMAADATA REFERENCE block is already loaded. Do NOT re-fetch category/partner lists unless essential.
- Call multiple tools in ONE turn when possible (e.g. location check + count together).
- If a city has no data, say so honestly — do not keep retrying different tool combinations.
- DEFAULT TO TEXT. Respond with a text-only insight unless the user EXPLICITLY asks for a chart, graph, plot, or visualization in their message.
- RESPONSE FORMAT (default text response, always use this structure):
  1. **Headline** — one sentence with the key number or finding.
  2. **What the data shows** — 2–3 short bullets with concrete figures.
  3. **One follow-up** — offer to dig deeper (ward breakdown, different category, another city) AND mention they can ask for a chart to visualize this.
- CHARTS ARE OPT-IN: Only create a chart when the user's current message explicitly requests a visual (e.g. "show me a chart", "graph this", "can I see a plot"). Do NOT create a chart just because you have breakdown data.
- When the user does ask for a chart, call a chart-creating tool, then embed ONLY the real image URL the tool returns, like ![Data chart](https://...actual-url...). Keep the surrounding text brief.
- NEVER output a placeholder image such as ![Data chart](IMAGE_URL) or an empty ![Data chart](). If you did NOT call a chart tool and get back a real URL, include NO image markdown at all — respond with text only.
- If city OR topic is missing, ask ONE short question — do not run tools yet.
- Never use the "---" delimiter in insights replies. No action blueprints, scripts, or mission steps.
- Never mention MCP, tools, or APIs — say you pulled this from **SamaajData Collective**.
- Keep tone warm and citizen-friendly; lead with the insight, not the process.
""".strip()

ProgressCallback = Optional[Callable[[str], None]]


def _extract_text(content: List[Any]) -> str:
    return "".join(block.text for block in content if block.type == "text")


def _serialize_tool_result(result: str) -> str:
    if len(result) > 12000:
        return result[:12000] + "\n...[truncated]"
    return result


def _is_first_insights_turn(history: List[Dict[str, Any]]) -> bool:
    return sum(1 for message in history if message.get("role") == "user") <= 1


def _build_insights_system(base_system: str, prefetch_context: str) -> str:
    parts = [base_system, INSIGHTS_PRODUCT_RULES]
    if prefetch_context:
        parts.append(prefetch_context)
    return "\n\n".join(parts)


async def generate_with_mcp_tools(
    client: Anthropic,
    *,
    model: str,
    system: str,
    history: List[Dict[str, Any]],
    user_text: str,
    mcp_url: str,
    on_progress: ProgressCallback = None,
    search_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """Return (assistant_text, response_id) using live SamaajData MCP tools."""
    search_blocks = search_blocks or []
    first_turn = _is_first_insights_turn(history)
    max_rounds = FIRST_TURN_MAX_TOOL_ROUNDS if first_turn else MAX_TOOL_ROUNDS

    async with SamaajDataMCPClient(url=mcp_url, on_progress=on_progress) as mcp:
        if on_progress:
            on_progress("Analysing your question...")

        tools = mcp.anthropic_tools()
        messages = build_messages_for_claude(history, user_text, search_blocks)
        insights_system = _build_insights_system(system, mcp.prefetch_context)

        for round_num in range(max_rounds):
            if on_progress:
                on_progress("Preparing your insight...")

            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=insights_system,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                if on_progress:
                    on_progress("Almost done...")
                return _extract_text(response.content), response.id

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    raw = await mcp.call_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": _serialize_tool_result(raw),
                        }
                    )
                except Exception as exc:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Tool error: {exc}",
                            "is_error": True,
                        }
                    )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        if on_progress:
            on_progress("Wrapping up your insight...")
        print(f"WARNING: MCP tool loop hit {max_rounds} rounds — synthesizing final answer")
        final = client.messages.create(
            model=model,
            max_tokens=4096,
            system=(
                insights_system
                + "\n\nYou have enough tool data. Respond NOW with your best insight "
                "using only what you already retrieved. Do not request more tools."
            ),
            messages=messages,
        )
        return _extract_text(final.content), final.id


async def generate_standard(
    client: Anthropic,
    *,
    model: str,
    system: str,
    history: List[Dict[str, Any]],
    user_text: str,
    search_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    search_blocks = search_blocks or []
    messages = build_messages_for_claude(history, user_text, search_blocks)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=messages,
    )
    return _extract_text(response.content), response.id
