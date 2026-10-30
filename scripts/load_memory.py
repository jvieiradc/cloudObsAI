"""Loads curated operator knowledge into the incident memory (SPEC 7.9).

Input: a JSON Lines file with `summary` and optional `alertname`, `date`. Never commit that file.
Run: docker compose run --rm -v /path/seed.jsonl:/tmp/seed.jsonl:ro app python scripts/load_memory.py /tmp/seed.jsonl
"""
import json
import sys

from cloudobsai.config import load
from cloudobsai.store import Store

store = Store(load().data_dir / "cloudobsai.db")
count = 0
with open(sys.argv[1]) as file:
    for line in file:
        if line.strip():
            item = json.loads(line)
            store.add_incident(
                item["summary"],
                "confirmado",
                alertname=item.get("alertname", ""),
                author="carga inicial",
                created_at=item.get("date"),
            )
            count += 1
print(f"loaded {count}")
