"""P4 validation and read-only check of the Grafana MCP, run inside the stack.

Run: docker compose run --rm --build app python scripts/validate_stack.py
"""
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock, query

from cloudobsai.agent_options import build_options

WRITE_LIKE = re.compile(r"create|update|delete|patch|grafana_api_request", re.I)


def check_jaeger() -> bool:
    uid = os.environ.get("JAEGER_DATASOURCE_UID")
    if not uid:
        print("P4: skipped (JAEGER_DATASOURCE_UID not set)")
        return True
    url = f"{os.environ['GRAFANA_URL'].rstrip('/')}/api/datasources/proxy/uid/{uid}/api/services"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            services = json.load(response).get("data") or []
        print(f"P4: OK, Viewer reached Jaeger through the proxy ({len(services)} services)")
        return True
    except urllib.error.HTTPError as e:
        print(f"P4: FAILED, HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"P4: FAILED, {type(e).__name__}")
    return False


async def check_agent() -> bool:
    options = build_options(
        system_prompt="Be brief. Read-only diagnostics.",
        mcp_servers={
            "grafana": {
                "type": "http",
                "url": os.environ["MCP_GRAFANA_URL"],
                "headers": {"Authorization": f"Bearer {os.environ['MCP_GRAFANA_SERVER_TOKEN']}"},
            }
        },
        allowed_mcp_prefixes=("grafana",),
    )
    ok = True
    prompt = "List the Grafana datasources by name and type only."
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            tools = msg.data.get("tools", [])
            print("mcp servers:", msg.data.get("mcp_servers"))
            print("tools:", len(tools))
            outside = [t for t in tools if not t.startswith("mcp__grafana__")]
            write_like = [t for t in tools if WRITE_LIKE.search(t)]
            print("outside allowlist:", outside or "none")
            print("write-like:", write_like or "none")
            ok = not outside and not write_like and bool(tools)
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    print("tool_use:", block.name)
                elif isinstance(block, TextBlock):
                    print("text:", block.text[:500])
        elif isinstance(msg, ResultMessage):
            print("result:", msg.subtype, "cost:", msg.total_cost_usd)
    return ok


jaeger_ok = check_jaeger()
agent_ok = asyncio.run(check_agent())
sys.exit(0 if jaeger_ok and agent_ok else 1)
