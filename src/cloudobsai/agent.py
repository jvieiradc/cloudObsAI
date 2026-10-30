"""Runs one read-only analysis with the Agent SDK (SPEC section 7)."""
import asyncio
import os
from dataclasses import dataclass, field

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, query

from .agent_options import build_options
from .config import Mode
from .store import Store
from .tools import jaeger_server, memory_server


@dataclass
class AgentResult:
    text: str = ""
    tools: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    outcome: str = "ok"


class Agent:
    def __init__(self, store: Store, system_prompt: str):
        self._store = store
        self._system_prompt = system_prompt

    def _options(self, mode: Mode):
        servers = {
            "grafana": {
                "type": "http",
                "url": os.environ["MCP_GRAFANA_URL"],
                "headers": {"Authorization": f"Bearer {os.environ['MCP_GRAFANA_SERVER_TOKEN']}"},
            },
            "memory": memory_server(self._store),
        }
        if os.environ.get("JAEGER_DATASOURCE_UID"):
            servers["jaeger"] = jaeger_server()
        return build_options(
            system_prompt=self._system_prompt,
            mcp_servers=servers,
            allowed_mcp_prefixes=tuple(servers),
            max_turns=mode.max_turns,
            extra_env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(mode.max_output_tokens)},
        )

    async def run(self, prompt: str, mode: Mode) -> AgentResult:
        result = AgentResult()
        parts: list[str] = []
        async with asyncio.timeout(mode.timeout_seconds):
            async for message in query(prompt=prompt, options=self._options(mode)):
                if isinstance(message, AssistantMessage):
                    # Only the last assistant message is the answer; earlier text is narration.
                    texts = [b.text for b in message.content if isinstance(b, TextBlock)]
                    if texts:
                        parts = texts
                    result.tools += [b.name for b in message.content if isinstance(b, ToolUseBlock)]
                elif isinstance(message, ResultMessage):
                    usage = message.usage or {}
                    result.input_tokens = usage.get("input_tokens")
                    result.output_tokens = usage.get("output_tokens")
                    result.cost_usd = message.total_cost_usd
                    if message.is_error:
                        result.outcome = message.subtype
        result.text = "\n".join(parts).strip()
        return result
