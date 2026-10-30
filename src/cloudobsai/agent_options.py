"""Hardened Agent SDK options: read-only, isolated from host configuration (SPEC section 7)."""
import os
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

# Isolated state dirs for the child process (SPEC 7.1).
STATE_HOME = Path(os.environ.get("CLOUDOBSAI_STATE_DIR", Path.cwd() / ".agent-home"))
WORK_DIR = Path(os.environ.get("CLOUDOBSAI_WORK_DIR", Path.cwd() / ".agent-cwd"))


async def _deny_outside_allowlist(allowed_prefixes: tuple[str, ...], input_data, tool_use_id, context):
    name = input_data.get("tool_name", "")
    if name.startswith(allowed_prefixes):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "tool not allowed",
        }
    }


def build_options(
    *,
    system_prompt: str,
    mcp_servers: dict,
    allowed_mcp_prefixes: tuple[str, ...],
    model: str | None = None,
    max_turns: int = 6,
    extra_env: dict[str, str] | None = None,
) -> ClaudeAgentOptions:
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    env = {
        # Config dir and home relocated so ~/.claude.json and user settings are never read.
        "CLAUDE_CONFIG_DIR": str(STATE_HOME),
        "HOME": str(STATE_HOME),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "MAX_MCP_OUTPUT_TOKENS": os.environ.get("CLOUDOBSAI_MAX_MCP_OUTPUT_TOKENS", "8000"),
        # Credentials come only from the service environment (API key, never a claude.ai login).
        "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
    }
    if base_url := os.environ.get("ANTHROPIC_BASE_URL"):
        env["ANTHROPIC_BASE_URL"] = base_url
    env.update(extra_env or {})

    async def hook(input_data, tool_use_id, context):
        return await _deny_outside_allowlist(
            tuple(f"mcp__{p}" for p in allowed_mcp_prefixes), input_data, tool_use_id, context
        )

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools=[],  # removes every built-in tool from the request
        allowed_tools=[f"mcp__{p}__*" for p in allowed_mcp_prefixes],
        permission_mode="dontAsk",
        setting_sources=[],
        mcp_servers=mcp_servers,
        strict_mcp_config=True,
        hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
        cwd=str(WORK_DIR),
        env=env,
        model=model or os.environ.get("CLOUDOBSAI_MODEL") or None,
        max_turns=max_turns,
    )
