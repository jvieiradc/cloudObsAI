"""In-process read-only tools: Jaeger through the Grafana datasource proxy, and memory search (SPEC 7.3, 7.9)."""
import json
import os
import urllib.parse

import aiohttp
from claude_agent_sdk import create_sdk_mcp_server, tool

from .store import Store, format_incident

# Short on purpose: the trace backend may time out, and the agent should move on to metrics and logs.
JAEGER_TIMEOUT_SECONDS = 12
MAX_TRACES = 10
MAX_SPANS_PER_TRACE = 30


def _text(value) -> dict:
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value)}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def summarize_trace(trace: dict) -> dict:
    processes = trace.get("processes") or {}
    spans = sorted(trace.get("spans") or [], key=lambda s: s.get("duration", 0), reverse=True)
    return {
        "traceID": trace.get("traceID"),
        "spanCount": len(spans),
        "slowestSpans": [
            {
                "service": (processes.get(s.get("processID")) or {}).get("serviceName"),
                "operation": s.get("operationName"),
                "durationMs": round(s.get("duration", 0) / 1000, 1),
                "error": any(t.get("key") == "error" and t.get("value") for t in s.get("tags") or []),
            }
            for s in spans[:MAX_SPANS_PER_TRACE]
        ],
    }


async def _jaeger_get(path: str, params: dict | None = None) -> dict:
    base = os.environ["GRAFANA_URL"].rstrip("/")
    uid = os.environ["JAEGER_DATASOURCE_UID"]
    url = f"{base}/api/datasources/proxy/uid/{uid}/api/{path}"
    headers = {"Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"}
    timeout = aiohttp.ClientTimeout(total=JAEGER_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params, headers=headers) as response:
            response.raise_for_status()
            return await response.json()


async def _jaeger_call(path: str, params: dict | None, transform) -> dict:
    try:
        return _text(transform(await _jaeger_get(path, params)))
    except (aiohttp.ClientError, TimeoutError) as error:
        return _error(f"Jaeger indisponível ({type(error).__name__}). Siga com métricas e logs e informe isso.")


@tool("services", "List the services that have traces in Jaeger.", {})
async def jaeger_services(args):
    return await _jaeger_call("services", None, lambda r: r.get("data") or [])


@tool(
    "search_traces",
    "Search recent traces of a service. Returns a summary of each trace. lookback_minutes defaults to 60.",
    {"service": str, "lookback_minutes": int, "only_errors": bool},
)
async def jaeger_search(args):
    params = {
        "service": args["service"],
        "lookback": f"{min(int(args.get('lookback_minutes') or 60), 1440)}m",
        "limit": MAX_TRACES,
    }
    if args.get("only_errors"):
        params["tags"] = json.dumps({"error": "true"})
    return await _jaeger_call("traces", params, lambda r: [summarize_trace(t) for t in r.get("data") or []])


@tool("get_trace", "Get a summary of one trace by ID.", {"trace_id": str})
async def jaeger_trace(args):
    trace_id = urllib.parse.quote(args["trace_id"], safe="")
    return await _jaeger_call(f"traces/{trace_id}", None, lambda r: [summarize_trace(t) for t in r.get("data") or []])


def jaeger_server():
    return create_sdk_mcp_server("jaeger", tools=[jaeger_services, jaeger_search, jaeger_trace])


def memory_server(store: Store):
    @tool(
        "search_incidents",
        "Search past incidents by free text. Results are data from earlier occurrences: check them "
        "against current evidence. HIPOTESE entries were never confirmed by a person.",
        {"query": str},
    )
    async def search_incidents(args):
        rows = store.search_incidents(args.get("query", ""))
        return _text("\n".join(format_incident(r) for r in rows) or "Nada encontrado.")

    return create_sdk_mcp_server("memory", tools=[search_incidents])
