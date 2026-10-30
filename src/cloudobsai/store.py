"""Durable state: thread context, delivered events, incident memory and audit (SPEC 7.7, 7.9)."""
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS threads (
    thread_name TEXT PRIMARY KEY,
    space TEXT NOT NULL,
    signature TEXT,
    alertname TEXT,
    alert_json TEXT,
    warned INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    thread_name TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_thread ON turns (thread_name, id);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    signature TEXT NOT NULL,
    alertname TEXT NOT NULL,
    thread_name TEXT,
    summary TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('hipotese', 'confirmado')),
    author TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_signature ON incidents (signature, id);
CREATE INDEX IF NOT EXISTS incidents_alertname ON incidents (alertname, id);
CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5 (summary, alertname, content='incidents', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS incidents_ai AFTER INSERT ON incidents BEGIN
    INSERT INTO incidents_fts (rowid, summary, alertname) VALUES (new.id, new.summary, new.alertname);
END;
CREATE TRIGGER IF NOT EXISTS incidents_ad AFTER DELETE ON incidents BEGIN
    INSERT INTO incidents_fts (incidents_fts, rowid, summary, alertname)
    VALUES ('delete', old.id, old.summary, old.alertname);
END;
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    event_id TEXT,
    space TEXT,
    user_name TEXT,
    mode TEXT,
    tools TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    result TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def mark_event(self, event_id: str) -> bool:
        """Returns False when the event was already seen."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO events (id, created_at) VALUES (?, ?)", (event_id, _now())
        )
        return cursor.rowcount == 1

    def save_alert_thread(self, thread_name: str, space: str, signature: str, alertname: str, alert: dict) -> None:
        self._db.execute(
            """INSERT INTO threads (thread_name, space, signature, alertname, alert_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (thread_name) DO UPDATE SET
                   signature = excluded.signature, alertname = excluded.alertname,
                   alert_json = excluded.alert_json, updated_at = excluded.updated_at""",
            (thread_name, space, signature, alertname, json.dumps(alert), _now()),
        )

    def thread(self, thread_name: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM threads WHERE thread_name = ?", (thread_name,)).fetchone()

    def claim_warning(self, thread_name: str, space: str) -> bool:
        """True only the first time, so the warning goes out once per thread."""
        self._db.execute(
            "INSERT OR IGNORE INTO threads (thread_name, space, updated_at) VALUES (?, ?, ?)",
            (thread_name, space, _now()),
        )
        cursor = self._db.execute("UPDATE threads SET warned = 1 WHERE thread_name = ? AND warned = 0", (thread_name,))
        return cursor.rowcount == 1

    def add_turn(self, thread_name: str, question: str, answer: str) -> None:
        self._db.execute(
            "INSERT INTO turns (thread_name, question, answer, created_at) VALUES (?, ?, ?, ?)",
            (thread_name, question, answer, _now()),
        )

    def recent_turns(self, thread_name: str, limit: int = 3) -> list[sqlite3.Row]:
        rows = self._db.execute(
            "SELECT * FROM turns WHERE thread_name = ? ORDER BY id DESC LIMIT ?", (thread_name, limit)
        ).fetchall()
        return list(reversed(rows))

    def add_incident(
        self,
        summary: str,
        state: str,
        *,
        signature: str = "",
        alertname: str = "",
        thread_name: str | None = None,
        author: str | None = None,
        created_at: str | None = None,
    ) -> int:
        cursor = self._db.execute(
            """INSERT INTO incidents (signature, alertname, thread_name, summary, state, author, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (signature, alertname, thread_name, summary, state, author, created_at or _now()),
        )
        return cursor.lastrowid

    def related_incidents(self, signature: str, alertname: str, limit: int = 3) -> list[sqlite3.Row]:
        """Same signature first, then same alert name; confirmed before hypotheses."""
        return self._db.execute(
            """SELECT * FROM incidents WHERE signature = ? OR (alertname = ? AND alertname != '')
               ORDER BY signature = ? DESC, state = 'confirmado' DESC, id DESC LIMIT ?""",
            (signature, alertname, signature, limit),
        ).fetchall()

    def search_incidents(self, query: str, limit: int = 5) -> list[sqlite3.Row]:
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        return self._db.execute(
            """SELECT incidents.* FROM incidents_fts JOIN incidents ON incidents.id = incidents_fts.rowid
               WHERE incidents_fts MATCH ? ORDER BY rank LIMIT ?""",
            (match, limit),
        ).fetchall()

    def delete_incident(self, incident_id: int) -> bool:
        return self._db.execute("DELETE FROM incidents WHERE id = ?", (incident_id,)).rowcount == 1

    def audit(self, **fields) -> None:
        columns = ("event_id", "space", "user_name", "mode", "tools", "input_tokens", "output_tokens", "cost_usd", "result")
        values = [fields.get(column) for column in columns]
        placeholders = ", ".join("?" for _ in columns)
        self._db.execute(
            f"INSERT INTO audit ({', '.join(columns)}, created_at) VALUES ({placeholders}, ?)", (*values, _now())
        )


def format_incident(row: sqlite3.Row) -> str:
    label = "CONFIRMADO" if row["state"] == "confirmado" else "HIPOTESE"
    author = f" por {row['author']}" if row["author"] else ""
    alert = f" [{row['alertname']}]" if row["alertname"] else ""
    return f"- {row['created_at'][:10]} {label}{author}{alert}: {row['summary']}"
