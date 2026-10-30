"""Service configuration, read only from the environment (SPEC section 9)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Mode:
    name: str
    max_turns: int
    timeout_seconds: int
    max_output_tokens: int


@dataclass(frozen=True)
class Config:
    allowed_spaces: frozenset[str]
    alert_spaces: dict[str, str]
    webhook_token: str
    subscription: str
    data_dir: Path
    system_prompt_file: Path
    explain: Mode
    investigate: Mode
    max_concurrency: int


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def load() -> Config:
    return Config(
        allowed_spaces=frozenset(s.strip() for s in os.environ["CLOUDOBSAI_ALLOWED_SPACES"].split(",") if s.strip()),
        # Maps the alert's `space` label to a Chat space; other alerts are not posted.
        alert_spaces=json.loads(os.environ.get("CLOUDOBSAI_ALERT_SPACES") or "{}"),
        webhook_token=os.environ["CLOUDOBSAI_WEBHOOK_TOKEN"],
        subscription=os.environ["CLOUDOBSAI_PUBSUB_SUBSCRIPTION"],
        data_dir=Path(os.environ.get("CLOUDOBSAI_DATA_DIR") or "/var/lib/cloudobsai/data"),
        system_prompt_file=Path(os.environ.get("CLOUDOBSAI_SYSTEM_PROMPT_FILE") or "/app/prompts/system.md"),
        explain=Mode(
            "explicar",
            _int("CLOUDOBSAI_EXPLAIN_MAX_TURNS", 8),
            _int("CLOUDOBSAI_EXPLAIN_TIMEOUT", 90),
            _int("CLOUDOBSAI_EXPLAIN_MAX_TOKENS", 500),
        ),
        investigate=Mode(
            "investigar",
            _int("CLOUDOBSAI_INVESTIGATE_MAX_TURNS", 16),
            _int("CLOUDOBSAI_INVESTIGATE_TIMEOUT", 240),
            _int("CLOUDOBSAI_INVESTIGATE_MAX_TOKENS", 900),
        ),
        max_concurrency=_int("CLOUDOBSAI_MAX_CONCURRENCY", 2),
    )
