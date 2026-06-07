from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MobileDiagnosticsStore:
    def __init__(self, *, max_entries_per_table: int = 500) -> None:
        self._max_entries_per_table = max_entries_per_table
        self._by_table: dict[str, list[dict]] = {}

    def append(self, table_id: str, entries: list[dict]) -> int:
        if not entries:
            return 0
        table_entries = self._by_table.setdefault(table_id, [])
        received_at = _utc_now()
        for entry in entries:
            normalized = deepcopy(entry)
            normalized["received_at"] = received_at
            table_entries.append(normalized)
        overflow = len(table_entries) - self._max_entries_per_table
        if overflow > 0:
            del table_entries[:overflow]
        return len(entries)

    def snapshot(self, table_id: str) -> dict:
        entries = deepcopy(self._by_table.get(table_id, []))
        return {
            "count": len(entries),
            "entries": entries,
        }
