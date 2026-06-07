from __future__ import annotations

import threading


class MemoryCompactionService:
    def __init__(self, store, worker, *, on_completed=None, on_failed=None) -> None:
        self.store = store
        self.worker = worker
        self.on_completed = on_completed
        self.on_failed = on_failed
        self._threads: dict[str, threading.Thread] = {}

    def start(self, *, table_id: str, payload: dict) -> dict:
        record = self.store.create(
            table_id=table_id,
            checkpoint=int(payload.get("checkpoint", 0)),
            payload=payload,
        )
        thread = threading.Thread(
            target=self._run_compaction,
            kwargs={"compaction_id": record["compaction_id"]},
            daemon=True,
        )
        self._threads[record["compaction_id"]] = thread
        thread.start()
        latest = self.store.get(record["compaction_id"])
        return latest if latest is not None else record

    def wait(self, compaction_id: str, timeout: float | None = None) -> None:
        thread = self._threads.get(compaction_id)
        if thread is not None:
            thread.join(timeout=timeout)

    def _run_compaction(self, *, compaction_id: str) -> None:
        self.store.mark_running(compaction_id)
        record = self.store.get(compaction_id)
        if record is None:
            return
        payload = dict(record.get("payload") or {})
        try:
            result = self.worker.compact(payload)
        except Exception as exc:
            failed = self.store.mark_failed(compaction_id, str(exc))
            if self.on_failed is not None:
                self.on_failed(failed)
            return
        completed = self.store.mark_completed(compaction_id, result)
        if self.on_completed is not None:
            self.on_completed(completed)
