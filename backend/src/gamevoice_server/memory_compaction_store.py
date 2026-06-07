from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryCompactionStore:
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._table_index: dict[str, list[str]] = {}
        self._daily_snapshot_sequence: dict[str, int] = {}

    def _next_snapshot_name(self) -> str:
        date_key = datetime.now().strftime("%Y%m%d")
        next_value = self._daily_snapshot_sequence.get(date_key, 0) + 1
        self._daily_snapshot_sequence[date_key] = next_value
        return f"{date_key}_dialog_{next_value}"

    def create(self, *, table_id: str, checkpoint: int, payload: dict) -> dict:
        compaction_id = uuid4().hex
        snapshot_name = self._next_snapshot_name()
        record = {
            "compaction_id": compaction_id,
            "table_id": table_id,
            "status": "queued",
            "checkpoint": checkpoint,
            "snapshot_name": snapshot_name,
            "summary_text": "",
            "metadata": {},
            "error": None,
            "created_at": _utc_now(),
            "started_at": None,
            "completed_at": None,
            "payload": dict(payload),
        }
        self._records[compaction_id] = record
        self._table_index.setdefault(table_id, []).append(compaction_id)
        return dict(record)

    def mark_running(self, compaction_id: str) -> dict:
        record = self._records[compaction_id]
        record["status"] = "running"
        record["started_at"] = _utc_now()
        return dict(record)

    def mark_completed(self, compaction_id: str, result: dict) -> dict:
        record = self._records[compaction_id]
        record["status"] = "completed"
        record["summary_text"] = str(result.get("summary_text") or "")
        record["metadata"] = dict(result.get("metadata") or {})
        record["completed_at"] = _utc_now()
        return dict(record)

    def mark_failed(self, compaction_id: str, error: str) -> dict:
        record = self._records[compaction_id]
        record["status"] = "failed"
        record["error"] = error
        record["completed_at"] = _utc_now()
        return dict(record)

    def get(self, compaction_id: str) -> dict | None:
        record = self._records.get(compaction_id)
        return dict(record) if record is not None else None

    def list_for_table(self, table_id: str) -> list[dict]:
        ids = self._table_index.get(table_id, [])
        return [dict(self._records[item]) for item in ids]

    def has_active_for_table(self, table_id: str) -> bool:
        ids = self._table_index.get(table_id, [])
        for item in ids:
            status = self._records[item]["status"]
            if status in {"queued", "running"}:
                return True
        return False
