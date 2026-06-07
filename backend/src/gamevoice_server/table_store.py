from abc import ABC, abstractmethod

class TableStoreError(Exception):
    pass

class TableStore(ABC):
    @abstractmethod
    def list_tables(self) -> list[dict]: ...

    @abstractmethod
    def create_table(self, metadata: dict) -> dict: ...

    @abstractmethod
    def get_table(self, table_id: str) -> dict | None: ...

    @abstractmethod
    def update_table_metadata(self, table_id: str, metadata: dict) -> None: ...

    @abstractmethod
    def delete_table(self, table_id: str) -> None: ...

    @abstractmethod
    def append_message(self, table_id: str, event: dict) -> int: ...

    @abstractmethod
    def list_messages(self, table_id: str) -> list[dict]: ...

    @abstractmethod
    def append_runtime_event(self, table_id: str, event: dict) -> int: ...

    @abstractmethod
    def list_runtime_events(self, table_id: str) -> list[dict]: ...

    @abstractmethod
    def append_assistant_reply(self, table_id: str, event: dict) -> int: ...

    @abstractmethod
    def list_assistant_replies(self, table_id: str) -> list[dict]: ...

    @abstractmethod
    def get_speaker_identities(self, table_id: str) -> dict | None: ...

    @abstractmethod
    def save_speaker_identities(self, table_id: str, identities: dict) -> None: ...

    @abstractmethod
    def apply_compaction(self, table_id: str, checkpoint: int, summary_event: dict | None, compaction_version: int) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class InMemoryTableStore(TableStore):
    """In-memory store used when no persistence is needed (testing, probes)."""

    def __init__(self) -> None:
        self._tables: dict[str, dict] = {}
        self._messages: dict[tuple[str, int], dict] = {}
        self._runtime_events: dict[tuple[str, int], dict] = {}
        self._assistant_replies: dict[tuple[str, int], dict] = {}
        self._speaker_identities: dict[str, dict] = {}
        self._msg_seq: dict[str, int] = {}
        self._evt_seq: dict[str, int] = {}
        self._rep_seq: dict[str, int] = {}

    def list_tables(self) -> list[dict]:
        return list(self._tables.values())

    def create_table(self, metadata: dict) -> dict:
        self._tables[metadata["id"]] = dict(metadata)
        self._msg_seq.setdefault(metadata["id"], 0)
        self._evt_seq.setdefault(metadata["id"], 0)
        self._rep_seq.setdefault(metadata["id"], 0)
        return self._tables[metadata["id"]]

    def get_table(self, table_id: str) -> dict | None:
        return self._tables.get(table_id)

    def update_table_metadata(self, table_id: str, metadata: dict) -> None:
        if table_id in self._tables:
            self._tables[table_id].update(metadata)

    def delete_table(self, table_id: str) -> None:
        self._tables.pop(table_id, None)
        self._speaker_identities.pop(table_id, None)
        for col in (self._messages, self._runtime_events, self._assistant_replies):
            to_delete = [k for k in col if k[0] == table_id]
            for k in to_delete:
                col.pop(k)

    def append_message(self, table_id: str, event: dict) -> int:
        seq = self._msg_seq.get(table_id, 0)
        self._messages[(table_id, seq)] = {"table_id": table_id, "seq": seq, "data": event}
        self._msg_seq[table_id] = seq + 1
        return seq

    def list_messages(self, table_id: str) -> list[dict]:
        return [v["data"] for _, v in sorted(self._messages.items()) if v["table_id"] == table_id]

    def append_runtime_event(self, table_id: str, event: dict) -> int:
        seq = self._evt_seq.get(table_id, 0)
        self._runtime_events[(table_id, seq)] = {"table_id": table_id, "seq": seq, "data": event}
        self._evt_seq[table_id] = seq + 1
        return seq

    def list_runtime_events(self, table_id: str) -> list[dict]:
        return [v["data"] for _, v in sorted(self._runtime_events.items()) if v["table_id"] == table_id]

    def append_assistant_reply(self, table_id: str, event: dict) -> int:
        seq = self._rep_seq.get(table_id, 0)
        self._assistant_replies[(table_id, seq)] = {"table_id": table_id, "seq": seq, "data": event}
        self._rep_seq[table_id] = seq + 1
        return seq

    def list_assistant_replies(self, table_id: str) -> list[dict]:
        return [v["data"] for _, v in sorted(self._assistant_replies.items()) if v["table_id"] == table_id]

    def get_speaker_identities(self, table_id: str) -> dict | None:
        return self._speaker_identities.get(table_id)

    def save_speaker_identities(self, table_id: str, identities: dict) -> None:
        self._speaker_identities[table_id] = dict(identities)

    def apply_compaction(self, table_id: str, checkpoint: int, summary_event: dict | None, compaction_version: int) -> None:
        if table_id in self._tables:
            self._tables[table_id]["compaction_checkpoint"] = checkpoint
            self._tables[table_id]["compaction_version"] = compaction_version
            self._tables[table_id]["compaction_summary"] = summary_event

    def close(self) -> None:
        pass


class SQLiteTableStore(TableStore):
    """SQLite-backed persistent store. DB file is created at the given path."""

    def __init__(self, *, db_path: str | Path = ".runtime/gamevoice.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS tables (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            assistant_name TEXT NOT NULL DEFAULT '宝子',
            assistant_personality TEXT NOT NULL DEFAULT '',
            assistant_voice_id TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_active_at TEXT NOT NULL,
            compaction_checkpoint INTEGER NOT NULL DEFAULT 0,
            compaction_version INTEGER NOT NULL DEFAULT 0,
            compaction_summary TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (table_id) REFERENCES tables(id) ON DELETE CASCADE,
            UNIQUE(table_id, seq)
        );
        CREATE TABLE IF NOT EXISTS runtime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (table_id) REFERENCES tables(id) ON DELETE CASCADE,
            UNIQUE(table_id, seq)
        );
        CREATE TABLE IF NOT EXISTS assistant_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (table_id) REFERENCES tables(id) ON DELETE CASCADE,
            UNIQUE(table_id, seq)
        );
        CREATE TABLE IF NOT EXISTS speaker_identities (
            table_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            FOREIGN KEY (table_id) REFERENCES tables(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_table_id ON messages(table_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_events_table_id ON runtime_events(table_id);
        CREATE INDEX IF NOT EXISTS idx_assistant_replies_table_id ON assistant_replies(table_id);
        """)
        self._ensure_column("tables", "origin", "TEXT NOT NULL DEFAULT 'manual'")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {
            row[1]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _row_to_table_metadata(self, row: tuple[Any, ...]) -> dict:
        (id_, name, assistant_name, personality, voice_id, origin, status, created_at,
         last_active_at, checkpoint, version, summary_str) = row
        return {
            "id": id_,
            "name": name,
            "assistant_name": assistant_name,
            "assistant_personality": personality,
            "assistant_voice_id": voice_id,
            "origin": origin or "manual",
            "status": status,
            "created_at": created_at,
            "last_active_at": last_active_at,
            "compaction_checkpoint": checkpoint,
            "compaction_version": version,
            "compaction_summary": json.loads(summary_str) if summary_str else None,
        }

    def list_tables(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, assistant_name, assistant_personality, assistant_voice_id, "
                "origin, status, created_at, last_active_at, compaction_checkpoint, compaction_version, "
                "compaction_summary FROM tables ORDER BY last_active_at DESC"
            )
            return [self._row_to_table_metadata(row) for row in cur.fetchall()]

    def create_table(self, metadata: dict) -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tables (id, name, assistant_name, assistant_personality, assistant_voice_id, "
                "origin, status, created_at, last_active_at, compaction_checkpoint, compaction_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (metadata["id"], metadata["name"], metadata["assistant_name"],
                 metadata.get("assistant_personality", ""), metadata.get("assistant_voice_id", ""),
                 metadata.get("origin", "manual"), metadata.get("status", "active"),
                 metadata["created_at"], metadata["last_active_at"])
            )
            self._conn.commit()
        return metadata

    def get_table(self, table_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, assistant_name, assistant_personality, assistant_voice_id, "
                "origin, status, created_at, last_active_at, compaction_checkpoint, compaction_version, "
                "compaction_summary FROM tables WHERE id = ?", (table_id,)
            )
            row = cur.fetchone()
            return self._row_to_table_metadata(row) if row else None

    def update_table_metadata(self, table_id: str, metadata: dict) -> None:
        setters, args = [], []
        for key in ("name", "assistant_name", "assistant_personality", "assistant_voice_id",
                    "origin", "status", "last_active_at", "compaction_checkpoint", "compaction_version",
                    "compaction_summary"):
            if key in metadata:
                setters.append(f"{key} = ?")
                val = metadata[key]
                if val is not None and key == "compaction_summary":
                    val = json.dumps(val)
                args.append(val)
        if setters:
            args.append(table_id)
            with self._lock:
                self._conn.execute(f"UPDATE tables SET {', '.join(setters)} WHERE id = ?", args)
                self._conn.commit()

    def delete_table(self, table_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tables WHERE id = ?", (table_id,))
            self._conn.commit()

    def append_message(self, table_id: str, event: dict) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE table_id = ?", (table_id,)
                )
                seq = cur.fetchone()[0]
                self._conn.execute(
                    "INSERT INTO messages (table_id, seq, data) VALUES (?, ?, ?)",
                    (table_id, seq, json.dumps(event, ensure_ascii=False))
                )
                self._conn.commit()
                return seq
            except Exception:
                self._conn.rollback()
                raise

    def list_messages(self, table_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM messages WHERE table_id = ? ORDER BY seq", (table_id,)
            )
            return [json.loads(row[0]) for row in cur.fetchall()]

    def append_runtime_event(self, table_id: str, event: dict) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM runtime_events WHERE table_id = ?", (table_id,)
                )
                seq = cur.fetchone()[0]
                self._conn.execute(
                    "INSERT INTO runtime_events (table_id, seq, data) VALUES (?, ?, ?)",
                    (table_id, seq, json.dumps(event, ensure_ascii=False))
                )
                self._conn.commit()
                return seq
            except Exception:
                self._conn.rollback()
                raise

    def list_runtime_events(self, table_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM runtime_events WHERE table_id = ? ORDER BY seq", (table_id,)
            )
            return [json.loads(row[0]) for row in cur.fetchall()]

    def append_assistant_reply(self, table_id: str, event: dict) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) + 1 FROM assistant_replies WHERE table_id = ?", (table_id,)
                )
                seq = cur.fetchone()[0]
                self._conn.execute(
                    "INSERT INTO assistant_replies (table_id, seq, data) VALUES (?, ?, ?)",
                    (table_id, seq, json.dumps(event, ensure_ascii=False))
                )
                self._conn.commit()
                return seq
            except Exception:
                self._conn.rollback()
                raise

    def list_assistant_replies(self, table_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM assistant_replies WHERE table_id = ? ORDER BY seq", (table_id,)
            )
            return [json.loads(row[0]) for row in cur.fetchall()]

    def get_speaker_identities(self, table_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM speaker_identities WHERE table_id = ?", (table_id,)
            )
            row = cur.fetchone()
            return json.loads(row[0]) if row else None

    def save_speaker_identities(self, table_id: str, identities: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO speaker_identities (table_id, data) VALUES (?, ?)",
                (table_id, json.dumps(identities, ensure_ascii=False))
            )
            self._conn.commit()

    def apply_compaction(
        self, table_id: str, checkpoint: int, summary_event: dict | None, compaction_version: int
    ) -> None:
        summary_json = json.dumps(summary_event, ensure_ascii=False) if summary_event else None
        with self._lock:
            self._conn.execute(
                "UPDATE tables SET compaction_checkpoint = ?, compaction_version = ?, "
                "compaction_summary = ? WHERE id = ?",
                (checkpoint, compaction_version, summary_json, table_id)
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
