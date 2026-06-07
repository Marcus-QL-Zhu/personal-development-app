from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_pending_query(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()


class RuleAnalysisStore:
    def __init__(self, *, max_recent_results_per_table: int = 5) -> None:
        self._records: dict[str, dict] = {}
        self._order_by_table: dict[str, list[str]] = {}
        self._recent_results_by_table: dict[str, list[dict]] = {}
        self._max_recent_results_per_table = max_recent_results_per_table

    def create(self, *, table_id: str, query: str, ack_text: str) -> dict:
        analysis_id = uuid4().hex
        record = {
            "analysis_id": analysis_id,
            "table_id": table_id,
            "query": query,
            "ack_text": ack_text,
            "status": "queued",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "result": None,
            "error": None,
            "materialized_at": None,
        }
        self._records[analysis_id] = record
        self._order_by_table.setdefault(table_id, []).append(analysis_id)
        return deepcopy(record)

    def get(self, analysis_id: str) -> dict | None:
        record = self._records.get(analysis_id)
        return deepcopy(record) if record else None

    def list_for_table(self, table_id: str) -> list[dict]:
        order = self._order_by_table.get(table_id, [])
        return [deepcopy(self._records[analysis_id]) for analysis_id in reversed(order)]

    def find_active_query(self, *, table_id: str, query: str) -> dict | None:
        normalized_query = _normalize_pending_query(query)
        if not normalized_query:
            return None
        for analysis_id in reversed(self._order_by_table.get(table_id, [])):
            record = self._records[analysis_id]
            if record.get("status") not in {"queued", "running"}:
                continue
            if _normalize_pending_query(record.get("query", "")) == normalized_query:
                return deepcopy(record)
        return None

    def has_active_query(self, *, table_id: str, query: str) -> bool:
        return self.find_active_query(table_id=table_id, query=query) is not None

    def mark_running(self, analysis_id: str) -> dict:
        return self._update(analysis_id, status="running")

    def mark_completed(self, analysis_id: str, result: dict) -> dict:
        completed = self._update(
            analysis_id,
            status="completed",
            result=deepcopy(result),
            error=None,
        )
        self._remember_recent_result(completed)
        return completed

    def mark_failed(self, analysis_id: str, error: str) -> dict:
        return self._update(analysis_id, status="failed", error=error)

    def mark_materialized(self, analysis_id: str) -> dict:
        return self._update(analysis_id, materialized_at=_utc_now())

    def list_pending_materializations(self, table_id: str) -> list[dict]:
        order = self._order_by_table.get(table_id, [])
        pending: list[dict] = []
        for analysis_id in order:
            record = self._records[analysis_id]
            if record.get("status") != "completed":
                continue
            if record.get("materialized_at"):
                continue
            pending.append(deepcopy(record))
        return pending

    def list_recent_results(self, table_id: str) -> list[dict]:
        cached = self._recent_results_by_table.get(table_id, [])
        return deepcopy(list(reversed(cached)))

    def _update(self, analysis_id: str, **changes) -> dict:
        record = self._records[analysis_id]
        record.update(changes)
        record["updated_at"] = _utc_now()
        return deepcopy(record)

    def _remember_recent_result(self, record: dict) -> None:
        table_id = record["table_id"]
        cached = self._recent_results_by_table.setdefault(table_id, [])
        cached.append(
            {
                "analysis_id": record["analysis_id"],
                "query": record["query"],
                "result": deepcopy(record.get("result") or {}),
                "updated_at": record["updated_at"],
            }
        )
        if len(cached) > self._max_recent_results_per_table:
            del cached[:-self._max_recent_results_per_table]
