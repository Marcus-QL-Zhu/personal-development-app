from __future__ import annotations

import threading


class RuleAnalysisService:
    def __init__(
        self,
        store,
        worker,
        *,
        ack_text: str = "这个我去查一下，等我一会儿。",
        on_enqueued=None,
        on_completed=None,
        on_failed=None,
    ) -> None:
        self.store = store
        self.worker = worker
        self.ack_text = ack_text
        self.on_enqueued = on_enqueued
        self.on_completed = on_completed
        self.on_failed = on_failed
        self._threads: dict[str, threading.Thread] = {}
        self._spawned_reply_ids: set[tuple[str, str]] = set()
        self._spawn_lock = threading.Lock()

    def try_claim_reply_spawn(self, *, table_id: str, reply_id: str) -> bool:
        if not reply_id:
            return False
        key = (table_id, reply_id)
        with self._spawn_lock:
            if key in self._spawned_reply_ids:
                return False
            self._spawned_reply_ids.add(key)
            return True

    def start(self, *, table_id: str, query: str, events: list[dict],
              inject_only: bool = False, documents: list[dict] | None = None) -> dict:
        from .main import _inject_analysis_result_to_stream as _inject_cb

        active = self.store.find_active_query(table_id=table_id, query=query)
        if active is not None:
            return active

        record = self.store.create(table_id=table_id, query=query, ack_text=self.ack_text)
        if self.on_enqueued is not None:
            self.on_enqueued(record)

        captured_callback = _inject_cb if inject_only else None

        thread = threading.Thread(
            target=self._run_analysis,
            kwargs={
                "analysis_id": record["analysis_id"],
                "table_id": table_id,
                "query": query,
                "events": list(events),
                "documents": list(documents or []),
                "_inject_only_callback": captured_callback,
            },
            daemon=True,
        )
        self._threads[record["analysis_id"]] = thread
        thread.start()
        latest = self.store.get(record["analysis_id"])
        return latest if latest is not None else record

    def wait(self, analysis_id: str, timeout: float | None = None) -> None:
        thread = self._threads.get(analysis_id)
        if thread is not None:
            thread.join(timeout=timeout)

    def _run_analysis(self, *, analysis_id: str, table_id: str, query: str, events: list[dict],
                      documents: list[dict] | None = None, _inject_only_callback=None) -> None:
        self.store.mark_running(analysis_id)
        try:
            recent_results = self.store.list_recent_results(table_id)
            try:
                result = self.worker.analyze(
                    query=query,
                    events=events,
                    recent_results=recent_results,
                    table_id=table_id,
                    documents=documents,
                )
            except TypeError as exc:
                if not any(name in str(exc) for name in ("recent_results", "table_id", "documents")):
                    raise
                try:
                    result = self.worker.analyze(
                        query=query,
                        events=events,
                        recent_results=recent_results,
                    )
                except TypeError as fallback_exc:
                    if "recent_results" not in str(fallback_exc):
                        raise
                    result = self.worker.analyze(query=query, events=events)
            if not self._has_usable_result(result):
                raise RuntimeError("no usable conversation reply")
        except Exception as exc:
            failed = self.store.mark_failed(analysis_id, str(exc))
            if self.on_failed is not None:
                self.on_failed(failed)
            return

        completed = self.store.mark_completed(analysis_id, result)
        cb = _inject_only_callback if _inject_only_callback is not None else self.on_completed
        if cb is not None:
            cb(completed)

    @staticmethod
    def _has_usable_result(result: dict | None) -> bool:
        if not result:
            return False
        if result.get("source") == "minimax_fallback":
            return False
        return bool(str(result.get("content", "")).strip())
