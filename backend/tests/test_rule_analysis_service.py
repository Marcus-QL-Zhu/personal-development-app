import threading

from gamevoice_server.rule_analysis_service import RuleAnalysisService
from gamevoice_server.rule_analysis_store import RuleAnalysisStore


class StubWorker:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []
        self.started = threading.Event()

    def analyze(self, *, query: str, events: list[dict], recent_results: list[dict] | None = None) -> dict:
        self.calls.append(
            {
                "query": query,
                "events": events,
                "recent_results": recent_results or [],
            }
        )
        self.started.set()
        return dict(self.result)


def test_rule_analysis_service_runs_worker_and_persists_completion():
    store = RuleAnalysisStore()
    worker = StubWorker(
        {
            "source": "local",
            "lead": "我查到了。",
            "tail": "Final explanation.",
            "content": "我查到了。Final explanation.",
        }
    )
    completed_records: list[dict] = []
    service = RuleAnalysisService(
        store,
        worker,
        on_completed=lambda record: completed_records.append(record),
    )

    queued = service.start(table_id="table-1", query="resolve this", events=[{"kind": "voice_transcript"}])
    assert queued["status"] in {"queued", "running", "completed"}
    assert worker.started.wait(timeout=1)
    service.wait(queued["analysis_id"], timeout=1)

    final = store.get(queued["analysis_id"])
    assert final is not None
    assert final["status"] == "completed"
    assert final["result"]["content"] == "我查到了。Final explanation."
    assert completed_records
    assert worker.calls == [
        {
            "query": "resolve this",
            "events": [{"kind": "voice_transcript"}],
            "recent_results": [],
        }
    ]


def test_rule_analysis_service_passes_recent_results_into_worker():
    store = RuleAnalysisStore()
    previous = store.create(table_id="table-1", query="old", ack_text="ack")
    store.mark_completed(previous["analysis_id"], {"source": "local", "content": "old answer"})
    worker = StubWorker({"source": "local", "content": "new answer"})
    service = RuleAnalysisService(store, worker)

    queued = service.start(table_id="table-1", query="new", events=[])
    assert worker.started.wait(timeout=1)
    service.wait(queued["analysis_id"], timeout=1)

    assert worker.calls[-1]["recent_results"] == [
        {
            "analysis_id": previous["analysis_id"],
            "query": "old",
            "result": {"source": "local", "content": "old answer"},
            "updated_at": store.get(previous["analysis_id"])["updated_at"],
        }
    ]


def test_rule_analysis_service_claims_reply_id_once():
    service = RuleAnalysisService(
        RuleAnalysisStore(),
        StubWorker({"source": "local", "content": "answer"}),
    )

    assert service.try_claim_reply_spawn(table_id="table-1", reply_id="reply-1") is True
    assert service.try_claim_reply_spawn(table_id="table-1", reply_id="reply-1") is False
    assert service.try_claim_reply_spawn(table_id="table-1", reply_id="reply-2") is True
    assert service.try_claim_reply_spawn(table_id="table-2", reply_id="reply-1") is True
    assert service.try_claim_reply_spawn(table_id="table-1", reply_id="") is False
