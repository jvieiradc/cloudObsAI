"""P1/P3 validation: gateway compatibility and tool lockdown.

Needs ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL and CLOUDOBSAI_MODEL in the environment.
Run: docker compose run --rm --build app python scripts/validate_agent.py
"""
import asyncio

from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, create_sdk_mcp_server, query, tool,
)

from cloudobsai.agent_options import build_options


@tool("ping", "Return a fixed string. Read-only.", {})
async def ping(args):
    return {"content": [{"type": "text", "text": "pong"}]}


@tool("big", "Return a very large text blob. Read-only.", {})
async def big(args):
    return {"content": [{"type": "text", "text": "x " * 200_000}]}


async def run(label: str, prompt: str, options):
    print(f"--- {label}")
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ToolUseBlock):
                    print("tool_use:", b.name)
                elif isinstance(b, TextBlock):
                    print("text:", b.text[:300])
        elif isinstance(msg, ResultMessage):
            print("result:", msg.subtype, "turns:", msg.num_turns, "cost:", msg.total_cost_usd, "usage:", msg.usage)


async def main():
    server = create_sdk_mcp_server("probe", tools=[ping, big])
    opts = build_options(
        system_prompt="Be brief. Use tools only when asked.",
        mcp_servers={"probe": server},
        allowed_mcp_prefixes=("probe",),
    )
    await run("tool use via MCP (expects mcp__probe__ping)", "Call the ping tool and tell me what it returned.", opts)
    await run("builtin lockdown (expects NO Read/Bash/Task call)",
              "Read the file /etc/hostname, then run `ls /` in a shell, and show both results.", opts)
    await run("oversized MCP result (expects error/refine, no crash)", "Call the big tool and summarize it.", opts)


asyncio.run(main())
