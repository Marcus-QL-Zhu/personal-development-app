class ArchiveStore:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._compaction_snapshots: dict[str, list[dict]] = {}

    def save(self, table_id: str, payload: dict) -> None:
        self._store[table_id] = payload

    def load(self, table_id: str) -> dict | None:
        return self._store.get(table_id)

    def save_compaction_snapshot(self, table_id: str, payload: dict) -> None:
        snapshots = self._compaction_snapshots.setdefault(table_id, [])
        snapshots.append(dict(payload))

    def list_compaction_snapshots(self, table_id: str) -> list[dict]:
        return [dict(item) for item in self._compaction_snapshots.get(table_id, [])]

    def get_compaction_snapshot(self, table_id: str, compaction_id: str) -> dict | None:
        for item in self._compaction_snapshots.get(table_id, []):
            if item.get("compaction_id") == compaction_id:
                return dict(item)
        return None
