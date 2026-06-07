import asyncio
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gamevoice_server.main as main_module
from gamevoice_server.main import _resolve_final_live_transcript_text
from gamevoice_server.main import _resolve_preview_handoff_context
from gamevoice_server.main import _should_run_auto_interrupt_on_final
from gamevoice_server.main import _derive_committed_prefix_for_state
from gamevoice_server.main import _trim_formal_reply_after_preview
from gamevoice_server.main import app
from gamevoice_server.main import session_manager


def _latest_test_event_content(events: list[dict]) -> str:
    for item in reversed(events):
        content = str(item.get("content") or "").strip()
        if content:
            return content
    return ""


def _receive_json_with_timeout(websocket, *, timeout: float = 2.0) -> dict:
    done = threading.Event()
    result: dict[str, object] = {}

    def receive() -> None:
        try:
            result["value"] = websocket.receive_json()
        except BaseException as exc:  # pragma: no cover - surfaced below
            result["error"] = exc
        finally:
            done.set()

    threading.Thread(target=receive, daemon=True).start()
    if not done.wait(timeout):
        raise AssertionError(f"timed out waiting {timeout:.1f}s for websocket JSON event")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result["value"]  # type: ignore[return-value]


def test_preview_lookup_marker_split_strips_internal_marker():
    spoken, should_lookup = main_module._split_preview_lookup_marker("我查一下。<lookup>")

    assert spoken == "我查一下。"
    assert should_lookup is True


def test_preview_lookup_marker_detector_ignores_natural_lookup_words_without_marker():
    spoken, should_lookup = main_module._split_preview_lookup_marker("我去查一下。")

    assert spoken == "我去查一下。"
    assert should_lookup is False


def test_preview_lookup_marker_normalizes_spoken_ack_text():
    spoken, should_lookup = main_module._split_preview_lookup_marker("我听一下。<lookup>")

    assert spoken == "我听一下。"
    assert should_lookup is True


def test_lookup_hook_uses_formal_marker_not_preview_marker(monkeypatch):
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append({"table_id": table_id, "query": query, "events": events})
        return {"analysis_id": "analysis-1", "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)

    table_id = "table-lookup-hook"
    result = {
        "reply_id": "reply-1",
        "turn_id": "turn-1",
        "mode": "conversation",
        "reply": {"content": "你的问题是 XXX 这张牌的效果怎么结算，对吧？我来帮你查一查。<lookup>"},
        "preview_handoff_reply_text": "我查一下。",
    }
    events = [
        {"kind": "voice_transcript", "source": "live_asr", "content": "宝子，帮我查一张牌的效果。"},
        {"kind": "assistant_spoken", "source": "companion", "content": "是哪张牌呀？"},
        {"kind": "assistant_preview", "source": "runtime_preview", "content": "preview noise"},
        {"kind": "audio_debug", "source": "runtime", "content": "debug"},
        {"kind": "voice_transcript", "source": "live_asr", "content": "那张牌中文叫 XXX。"},
    ]

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result=result,
        dialog_events=events,
    )

    assert len(spawned) == 1
    assert spawned[0]["query"] == "那张牌中文叫 XXX。"
    assert [event["content"] for event in spawned[0]["events"]] == [
        "宝子，帮我查一张牌的效果。",
        "是哪张牌呀？",
        "那张牌中文叫 XXX。",
    ]

    spawned.clear()
    result["reply"] = {"content": "你的问题是 XXX 这张牌的效果怎么结算，对吧？我来帮你查一查。"}
    result["preview_handoff_reply_text"] = "我查一下。<lookup>"
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result=result,
        dialog_events=events,
    )
    assert spawned == []


def test_lookup_hook_does_not_spawn_for_weather_words_without_formal_marker(monkeypatch):
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append({"table_id": table_id, "query": query, "events": events})
        return {"analysis_id": "analysis-weather", "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id="table-weather-hook",
        result={
            "reply_id": "reply-weather",
            "turn_id": "turn-weather",
            "mode": "conversation",
            "reply": {"content": "好的，我去查一下明天上海的天气，稍等一下。"},
            "preview_handoff_reply_text": "",
        },
        dialog_events=[
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "宝子，帮我查一下明天上海的天气。",
            }
        ],
    )

    assert spawned == []


def test_preview_lookup_marker_does_not_spawn(monkeypatch):
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append(
            {
                "table_id": table_id,
                "query": query,
                "events": events,
                "inject_only": inject_only,
            }
        )
        return {"analysis_id": "analysis-preview", "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id="table-preview-hook",
        result={
            "reply_id": "preview-reply",
            "turn_id": "preview-turn",
            "mode": "conversation",
            "reply": {"content": ""},
            "preview_handoff_reply_text": "我去查一下。<lookup>",
        },
        dialog_events=[
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "\u5b9d\u5b50\uff0c\u5e2e\u6211\u8054\u7f51\u67e5\u4e00\u4e0b\u7279\u6717\u666e\u65b0\u95fb\u3002",
            }
        ],
    )

    assert spawned == []


def test_formal_lookup_marker_spawns_and_uses_latest_user_query(monkeypatch):
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append(
            {
                "table_id": table_id,
                "query": query,
                "events": events,
                "inject_only": inject_only,
            }
        )
        return {"analysis_id": "analysis-formal", "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id="table-formal-hook",
        result={
            "reply_id": "formal-reply",
            "turn_id": "formal-turn",
            "mode": "conversation",
            "reply": {"content": "我帮你联网查一下最近特朗普的新闻。<lookup>"},
            "preview_handoff_reply_text": "好，我看看。",
        },
        dialog_events=[
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "宝子，帮我联网查一下特朗普新闻。",
            }
        ],
    )

    assert spawned == [
        {
            "table_id": "table-formal-hook",
            "query": "宝子，帮我联网查一下特朗普新闻。",
            "events": [
                {
                    "kind": "voice_transcript",
                    "source": "live_asr",
                    "content": "宝子，帮我联网查一下特朗普新闻。",
                }
            ],
            "inject_only": False,
        }
    ]


def test_lookup_marker_queues_second_lookup_when_table_is_busy(monkeypatch):
    main_module._reset_lookup_runtime_for_tests()
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True, documents=None):
        analysis_id = f"analysis-{len(spawned) + 1}"
        spawned.append(
            {
                "analysis_id": analysis_id,
                "table_id": table_id,
                "query": query,
                "events": events,
                "documents": documents,
            }
        )
        return {"analysis_id": analysis_id, "table_id": table_id, "query": query, "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )
    monkeypatch.setattr(main_module.document_store, "list_documents", lambda table_id: [{"filename": "rules.pdf"}])

    table_id = "table-serial-lookup"
    first_events = [{"kind": "voice_transcript", "source": "live_asr", "content": "先查第一个问题。"}]
    second_events = [{"kind": "voice_transcript", "source": "live_asr", "content": "再查第二个问题。"}]

    first_result = {
        "reply_id": "reply-1",
        "reply": {"content": "我查一下第一个。<lookup>"},
    }
    second_result = {
        "reply_id": "reply-2",
        "reply": {"content": "我查一下第二个。<lookup>"},
    }
    third_result = {
        "reply_id": "reply-3",
        "reply": {"content": "我查一下第三个。<lookup>"},
    }

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result=first_result,
        dialog_events=first_events,
    )
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result=second_result,
        dialog_events=second_events,
    )
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result=third_result,
        dialog_events=[{"kind": "voice_transcript", "source": "live_asr", "content": "再查第三个问题。"}],
    )

    assert [item["query"] for item in spawned] == ["先查第一个问题。"]
    assert second_result["lookup_deferred"] is True
    assert second_result["lookup_busy_reply"] == "我先查完前一个问题再来查这个"
    assert third_result["lookup_deferred"] is False
    assert third_result["lookup_busy_reply"] == "我先查完前一个问题再来查这个"
    pending = main_module._pending_lookup_by_table[table_id]
    assert pending["query"] == "再查第二个问题。"
    assert pending["documents"] == [{"filename": "rules.pdf"}]


def test_lookup_queue_starts_pending_after_oralized_tts_finishes(monkeypatch):
    main_module._reset_lookup_runtime_for_tests()
    spawned: list[dict] = []
    scheduled: list[float] = []

    def fake_start(*, table_id, query, events, inject_only=True, documents=None):
        analysis_id = f"analysis-{len(spawned) + 1}"
        spawned.append({"analysis_id": analysis_id, "query": query})
        return {"analysis_id": analysis_id, "table_id": table_id, "query": query, "status": "running"}

    def fake_schedule(table_id: str, *, delay_seconds: float = main_module.LOOKUP_QUEUE_DELAY_SECONDS) -> None:
        scheduled.append(delay_seconds)
        main_module._start_pending_lookup_if_idle(table_id)

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )
    monkeypatch.setattr(main_module, "_schedule_pending_lookup_start", fake_schedule)

    table_id = "table-serial-played"
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result={"reply_id": "reply-1", "reply": {"content": "我查第一个。<lookup>"}},
        dialog_events=[{"kind": "voice_transcript", "content": "查第一个"}],
    )
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result={"reply_id": "reply-2", "reply": {"content": "我查第二个。<lookup>"}},
        dialog_events=[{"kind": "voice_transcript", "content": "查第二个"}],
    )

    main_module._register_lookup_oralized_job(table_id, analysis_id="analysis-1", job_id="job-1")
    main_module._on_lookup_oralized_tts_finished(table_id, job_id="job-1", completed_normally=True)

    assert scheduled == [3.0]
    assert [item["query"] for item in spawned] == ["查第一个", "查第二个"]


def test_lookup_queue_does_not_start_pending_after_interruption(monkeypatch):
    main_module._reset_lookup_runtime_for_tests()
    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True, documents=None):
        analysis_id = f"analysis-{len(spawned) + 1}"
        spawned.append({"analysis_id": analysis_id, "query": query})
        return {"analysis_id": analysis_id, "table_id": table_id, "query": query, "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    table_id = "table-serial-interrupted"
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result={"reply_id": "reply-1", "reply": {"content": "我查第一个。<lookup>"}},
        dialog_events=[{"kind": "voice_transcript", "content": "查第一个"}],
    )
    main_module._spawn_skillagent_for_lookup_commitment(
        table_id=table_id,
        result={"reply_id": "reply-2", "reply": {"content": "我查第二个。<lookup>"}},
        dialog_events=[{"kind": "voice_transcript", "content": "查第二个"}],
    )

    main_module._register_lookup_oralized_job(table_id, analysis_id="analysis-1", job_id="job-1")
    main_module._on_lookup_oralized_tts_finished(table_id, job_id="job-1", completed_normally=False)

    assert [item["query"] for item in spawned] == ["查第一个"]
    assert table_id not in main_module._pending_lookup_by_table


def test_preview_lookup_hook_does_not_use_user_text_as_trigger(monkeypatch):
    spawned: list[dict] = []

    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "start",
        lambda **kwargs: spawned.append(kwargs) or {"analysis_id": "bad", "status": "running"},
    )
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    main_module._spawn_skillagent_for_lookup_commitment(
        table_id="table-nonlookup-preview",
        result={
            "reply_id": "preview-reply-nonlookup",
            "turn_id": "preview-turn-nonlookup",
            "mode": "conversation",
            "reply": {"content": ""},
            "preview_handoff_reply_text": "我联网查一下。",
        },
        dialog_events=[
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "\u4ed6\u8001\u5b9d\u5b50\u4ecb\u7ecd\u81ea\u5df1\u3002",
            }
        ],
    )

    assert spawned == []


def test_preview_lookup_marker_is_stripped_without_spawning(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Marker Strip Table"})
    table_id = created.json()["id"]
    query = "speaker_0：宝子，帮我查一查最近特朗普的新闻。"

    class PreviewWithMarkerService(FakeAutoInterruptService):
        def preview(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None):
            result = super().preview(events, assistant_name=assistant_name, assistant_voice_id=assistant_voice_id)
            result["reply"]["content"] = "我查一下。<lookup>"
            result["speech_job"]["segments"] = ["我查一下。<lookup>"]
            result["speech_job"]["segment_statuses"][0]["text"] = "我查一下。<lookup>"
            return result

    spawned: list[dict] = []

    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "start",
        lambda **kwargs: spawned.append(kwargs) or {"analysis_id": "analysis-marker", "status": "running"},
    )
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )
    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = PreviewWithMarkerService()
    try:
        result = main_module._run_auto_interrupt_preview_for_table(table_id, transcript=query)
    finally:
        main_module.auto_interrupt_service = original_service

    assert result["reply"]["content"] == "我查一下。"
    assert result["speech_job"]["segments"] == ["我查一下。"]
    runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
    preview_events = [item for item in runtime_events if item.get("kind") == "assistant_preview_ready"]
    assert preview_events[-1]["content"] == "我查一下。"
    assert "<lookup>" not in str(result)
    assert spawned == []


def test_formal_lookup_commitment_repairs_missing_marker_for_explicit_query():
    result = {
        "reply": {"content": "好的好的，马上给你找找。", "lead": "好的好的，马上给你找找。", "tail": ""},
        "speech_job": {
            "text": "好的好的，马上给你找找。",
            "segments": ["好的好的，马上给你找找。"],
            "segment_statuses": [{"text": "好的好的，马上给你找找。"}],
        },
    }
    dialog_events = [
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "speaker_0：宝子，联网搜索一下，昨天有什么新闻",
        }
    ]

    repaired = main_module._strip_formal_lookup_marker_from_result(result, dialog_events=dialog_events)

    assert repaired is True
    assert result["lookup_marker"] is True
    assert result["reply"]["content"] == "好的好的，马上给你找找。"
    assert result["reply"]["lead"] == "好的好的，马上给你找找。"
    assert result["reply"]["tail"] == ""
    assert result["raw_formal_text"].endswith("<lookup>")
    assert "<lookup>" not in str(result["speech_job"])


def test_formal_lookup_commitment_does_not_repair_clarifying_question():
    result = {
        "reply": {"content": "你想查哪方面呀？", "lead": "你想查哪方面呀？", "tail": ""},
        "speech_job": {
            "text": "你想查哪方面呀？",
            "segments": ["你想查哪方面呀？"],
            "segment_statuses": [{"text": "你想查哪方面呀？"}],
        },
    }
    dialog_events = [
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "speaker_0：宝子，帮我查一下",
        }
    ]

    repaired = main_module._strip_formal_lookup_marker_from_result(result, dialog_events=dialog_events)

    assert repaired is False
    assert result.get("lookup_marker") is None
    assert result.get("raw_formal_text") is None


def test_formal_lookup_commitment_repairs_colloquial_commitment_word():
    result = {
        "reply": {"content": "好的，我这就去瞅瞅。", "lead": "好的，我这就去瞅瞅。", "tail": ""},
        "speech_job": {
            "text": "好的，我这就去瞅瞅。",
            "segments": ["好的，我这就去瞅瞅。"],
            "segment_statuses": [{"text": "好的，我这就去瞅瞅。"}],
        },
    }
    dialog_events = [
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "speaker_0：宝子，联网搜索一下，昨天有什么新闻",
        }
    ]

    repaired = main_module._strip_formal_lookup_marker_from_result(result, dialog_events=dialog_events)

    assert repaired is True
    assert result["lookup_marker"] is True
    assert result["raw_formal_text"] == "好的，我这就去瞅瞅。<lookup>"
    assert "<lookup>" not in str(result["speech_job"])


def test_raw_preview_lookup_marker_does_not_spawn_or_set_runtime_marker(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Raw Marker Table"})
    table_id = created.json()["id"]
    query = "宝子，帮我查一下明天上海的天气。"

    class PreviewWithRawMarkerService(FakeAutoInterruptService):
        def preview(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None):
            result = super().preview(events, assistant_name=assistant_name, assistant_voice_id=assistant_voice_id)
            result["reply"]["content"] = "我查一下明天上海的天气。"
            result["reply"]["lead"] = "我查一下明天上海的天气。"
            result["raw_preview_text"] = "我查一下明天上海的天气。<lookup>"
            result["lookup_marker"] = False
            result["speech_job"]["text"] = "我查一下明天上海的天气。"
            result["speech_job"]["segments"] = ["我查一下明天上海的天气。"]
            result["speech_job"]["segment_statuses"][0]["text"] = "我查一下明天上海的天气。"
            return result

    spawned: list[dict] = []

    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "start",
        lambda **kwargs: spawned.append(kwargs) or {"analysis_id": "analysis-raw-marker", "status": "running"},
    )
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )
    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = PreviewWithRawMarkerService()
    try:
        result = main_module._run_auto_interrupt_preview_for_table(table_id, transcript=query)
    finally:
        main_module.auto_interrupt_service = original_service

    assert result["reply"]["content"] == "我查一下明天上海的天气。"
    assert result["speech_job"]["segments"] == ["我查一下明天上海的天气。"]
    runtime_state = client.get(f"/tables/{table_id}/runtime/state").json()
    assert runtime_state["preview_lookup_marker"] is False
    assert spawned == []


def test_final_with_preview_lookup_marker_still_runs_formal_generation(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Lookup Skip Formal"})
    table_id = created.json()["id"]
    query = "\u5b9d\u5b50\uff0c\u5e2e\u6211\u8054\u7f51\u67e5\u4e00\u4e0b\u7279\u6717\u666e\u65b0\u95fb\u3002"

    session_manager.append_context_event(
        table_id,
        {"kind": "voice_transcript", "source": "live_asr", "content": query},
    )
    main_module.dialog_runtime_store.on_agent_preview_ready(
        table_id,
        reply_text="我去查一下。<lookup>",
        source_text=query,
        job_id="preview-lookup-job",
        lookup_marker=True,
    )

    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append({"table_id": table_id, "query": query, "events": events})
        return {"analysis_id": "analysis-preview-final", "status": "running"}

    class FormalRuns:
        def __init__(self):
            self.calls = 0

        def plan(self, *args, **kwargs):
            self.calls += 1
            return {
                "should_interrupt": True,
                "mode": "conversation",
                "decision_reason": "model",
                "transcript": query,
                "reply": {"source": "fake", "content": "我帮你联网查一下。<lookup>"},
            }

        def build_response(self, plan, *, assistant_name="宝子", assistant_voice_id=None):
            return {
                "interrupt": True,
                "mode": "conversation",
                "decision_reason": "model",
                "reply": dict(plan["reply"]),
                "speech_job": {
                    "accepted": False,
                    "job_id": "formal-marker-job",
                    "segment_count": 0,
                    "segment_statuses": [],
                    "segments": ["我帮你联网查一下。<lookup>"],
                    "text": "我帮你联网查一下。<lookup>",
                },
                "assistant_event": {
                    "kind": "assistant_reply",
                    "source": "companion",
                    "mode": "conversation",
                    "content": "我帮你联网查一下。<lookup>",
                    "speech_job": {
                        "accepted": False,
                        "job_id": "formal-marker-job",
                        "segment_count": 0,
                        "segment_statuses": [],
                        "segments": ["我帮你联网查一下。<lookup>"],
                        "text": "我帮你联网查一下。<lookup>",
                    },
                },
            }

        def plan_progressive(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )
    original_service = main_module.auto_interrupt_service
    fake_service = FormalRuns()
    main_module.auto_interrupt_service = fake_service
    try:
        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
    finally:
        main_module.auto_interrupt_service = original_service

    assert result["interrupt"] is True
    assert fake_service.calls == 1
    assert result["reply"]["content"] == "我帮你联网查一下。"
    assert result["assistant_event"]["content"] == "我帮你联网查一下。"
    assert "<lookup>" not in str(result["speech_job"])
    assert len(spawned) == 1
    assert spawned[0]["query"] == query


def test_final_with_nonlookup_text_does_not_skip_formal_for_stale_preview_lookup(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Lookup Nonlookup Final"})
    table_id = created.json()["id"]
    transcript = "\u4ed6\u80fd\u5b50\u4ecb\u7ecd\u3002"

    session_manager.append_context_event(
        table_id,
        {"kind": "voice_transcript", "source": "live_asr", "content": transcript},
    )
    main_module.dialog_runtime_store.on_agent_preview_ready(
        table_id,
        reply_text="我联网查一下。<lookup>",
        source_text="宝子，帮我查一下昨天的天气。",
        job_id="preview-lookup-job-stale",
        lookup_marker=True,
    )

    spawned: list[dict] = []

    class FormalRuns:
        def __init__(self):
            self.calls = 0

        def plan(self, events, assistant_name="宝子", assistant_personality=None):
            self.calls += 1
            return {
                "should_interrupt": True,
                "mode": "conversation",
                "decision_reason": "model",
                "transcript": _latest_test_event_content(events),
                "reply": {"source": "fake", "content": "\u6ca1\u542c\u6e05\uff0c\u4f60\u80fd\u518d\u8bf4\u4e00\u904d\u5417\uff1f"},
            }

        def build_response(self, plan, *, assistant_name="宝子", assistant_voice_id=None):
            return {
                "interrupt": True,
                "mode": "conversation",
                "decision_reason": "model",
                "reply": plan["reply"],
                "speech_job": {"accepted": False, "job_id": "formal-job", "segment_count": 0, "segment_statuses": []},
                "assistant_event": {
                    "kind": "assistant_reply",
                    "source": "companion",
                    "mode": "conversation",
                    "content": plan["reply"]["content"],
                    "speech_job": {"accepted": False, "job_id": "formal-job", "segment_count": 0, "segment_statuses": []},
                },
            }

    fake_service = FormalRuns()
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "start",
        lambda **kwargs: spawned.append(kwargs) or {"analysis_id": "bad", "status": "running"},
    )
    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = fake_service
    try:
        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
    finally:
        main_module.auto_interrupt_service = original_service

    assert result["interrupt"] is True
    assert result["decision_reason"] != "preview_lookup_handoff"
    assert fake_service.calls == 1
    assert spawned == []
    assert result["preview_handoff"] is False
    assert result["preview_reply_text"] is None


def test_streaming_lookup_hook_does_not_spawn_from_natural_words_without_marker(
    tmp_path: Path,
    monkeypatch,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Streaming Lookup Hook Table"})
    table_id = created.json()["id"]

    spawned: list[dict] = []

    def fake_start(*, table_id, query, events, inject_only=True):
        spawned.append({"table_id": table_id, "query": query, "events": events})
        return {"analysis_id": "analysis-streaming", "status": "running"}

    monkeypatch.setattr(main_module.rule_analysis_service, "start", fake_start)
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    original_service = main_module.auto_interrupt_service
    fake_service = FakeBlockingLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "玩家A：宝子，帮我查一张牌的效果。",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="好的，我去查一下。",
            source_text="宝子，帮我查一张牌的效果。",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-lookup",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["decision_reason"] != "preview_lookup_handoff"
        assert spawned == []
    finally:
        fake_service.tts_adapter.release_second_call.set()
        main_module.auto_interrupt_service = original_service


def test_streaming_formal_lookup_marker_is_not_spoken_and_spawns(
    tmp_path: Path,
    monkeypatch,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Streaming Formal Marker Table"})
    table_id = created.json()["id"]
    query = "宝子，帮我查一下明天上海天气。"

    session_manager.append_context_event(
        table_id,
        {"kind": "voice_transcript", "source": "live_asr", "content": query},
    )
    main_module.dialog_runtime_store.on_agent_preview_ready(
        table_id,
        reply_text="好，我看看。",
        source_text=query,
        job_id="job-preview-formal-marker",
    )

    spawned: list[dict] = []

    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "start",
        lambda **kwargs: spawned.append(kwargs) or {"analysis_id": "analysis-streaming-formal", "status": "running"},
    )
    monkeypatch.setattr(
        main_module.rule_analysis_service,
        "try_claim_reply_spawn",
        lambda *, table_id, reply_id: True,
    )

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeLookupMarkerContinuationAutoInterruptService(tmp_path)
    try:
        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
        time.sleep(0.2)
    finally:
        main_module.auto_interrupt_service = original_service

    runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
    stream_id = result["tts_stream"]["stream_id"]
    chunks = []
    while True:
        chunk = main_module.tts_stream_bridge.next_chunk(stream_id, wait_timeout=0.01)
        if chunk is None:
            break
        chunks.append(chunk)

    assert result["interrupt"] is True
    assert spawned
    assert spawned[0]["query"] == query
    assert "<lookup>" not in str(result["speech_job"])
    assert "<lookup>" not in str(runtime_events)
    assert all("<lookup>" not in chunk.get("text", "") for chunk in chunks)
    assert all(chunk.get("text") for chunk in chunks)


class FakeRealtimeSession:
    def __init__(self, *, stable_text: str = "live transcript") -> None:
        self.connected = False
        self.audio_chunks: list[bytes] = []
        self.ended = False
        self.closed = False
        self.events = [
            {"event": "transcript", "slice_type": 1, "index": 0, "text": "live"},
            {"event": "transcript", "slice_type": 2, "index": 0, "text": stable_text},
            {"event": "final"},
        ]

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)

    async def receive_event(self):
        if self.events:
            return self.events.pop(0)
        return None

    async def end(self) -> None:
        self.ended = True

    async def close(self) -> None:
        self.closed = True


class FakeClosedWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.payloads.append(payload)
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class FakeAutoInterruptService:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def plan(
        self,
        events: list[dict],
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        self.calls.append(events)
        return {
            "should_interrupt": True,
            "mode": "conversation",
            "decision_reason": "model",
            "transcript": _latest_test_event_content(events),
            "reply": {"source": "minimax", "content": "then I will jump in"},
        }

    def build_response(
        self,
        plan: dict,
        *,
        assistant_name: str = "宝子",
        assistant_voice_id: str | None = None,
    ) -> dict:
        output_dir = Path(".runtime/tts")
        output_dir.mkdir(parents=True, exist_ok=True)
        full_output_path = output_dir / "job-live-1.mp3"
        segment_output_path = output_dir / "job-live-1-segment-0.mp3"
        segment_bytes = b"\x01\x02\x03"
        full_output_path.write_bytes(segment_bytes)
        segment_output_path.write_bytes(segment_bytes)
        speech_job = {
            "accepted": True,
            "job_id": "job-live-1",
            "status": "ready",
            "format": "mp3",
            "output_path": str(full_output_path),
            "segments": ["then I will jump in"],
            "segment_count": 1,
            "segment_statuses": [
                {
                    "index": 0,
                    "text": "then I will jump in",
                    "status": "queued",
                    "format": "mp3",
                    "bytes": len(segment_bytes),
                    "output_path": str(segment_output_path),
                }
            ],
            "voice_id": assistant_voice_id,
        }
        return {
            "interrupt": True,
            "mode": plan["mode"],
            "decision_reason": plan.get("decision_reason", "model"),
            "reply": plan["reply"],
            "speech_job": speech_job,
            "assistant_event": {
                "kind": "assistant_reply",
                "source": "companion",
                "mode": "conversation",
                "content": "then I will jump in",
                "speech_job": speech_job,
            },
        }

    def run_once(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None) -> dict:
        return self.build_response(self.plan(events, assistant_name=assistant_name), assistant_voice_id=assistant_voice_id)

    def preview(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None) -> dict:
        transcript = _latest_test_event_content(events)
        output_dir = Path(".runtime/tts")
        output_dir.mkdir(parents=True, exist_ok=True)
        full_output_path = output_dir / "job-preview-1.mp3"
        segment_output_path = output_dir / "job-preview-1-segment-0.mp3"
        segment_bytes = b"\x09\x08\x07"
        full_output_path.write_bytes(segment_bytes)
        segment_output_path.write_bytes(segment_bytes)
        return {
            "interrupt": True,
            "mode": "conversation",
            "decision_reason": "preview_model",
            "reply": {"source": "minimax", "content": "let me think"},
            "speech_job": {
                "accepted": True,
                "job_id": "job-preview-1",
                "status": "ready",
                "format": "mp3",
                "output_path": str(full_output_path),
                "segments": ["let me think"],
                "segment_count": 1,
                "segment_statuses": [
                    {
                        "index": 0,
                        "text": "let me think",
                        "status": "queued",
                        "format": "mp3",
                        "bytes": len(segment_bytes),
                        "output_path": str(segment_output_path),
                    }
                ],
                "voice_id": assistant_voice_id,
            },
            "assistant_event": None,
            "transcript": transcript,
        }


class FakeSlowPreviewAutoInterruptService(FakeAutoInterruptService):
    def preview(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None) -> dict:
        time.sleep(0.2)
        return super().preview(events, assistant_name=assistant_name, assistant_voice_id=assistant_voice_id)


class FakeAutoInterruptServiceError:
    def plan(self, events: list[dict], assistant_name: str = "宝子") -> dict:
        raise RuntimeError("MiniMax dialog returned no text")

    def run_once(self, events: list[dict], assistant_name: str = "宝子", assistant_voice_id: str | None = None) -> dict:
        raise RuntimeError("MiniMax dialog returned no text")


class FakeNoReplyAutoInterruptService:
    def plan(
        self,
        events: list[dict],
        assistant_name: str = "瀹濆瓙",
        assistant_personality: str | None = None,
    ) -> dict:
        return {
            "should_interrupt": False,
            "mode": "conversation",
            "decision_reason": "listen_only",
            "transcript": _latest_test_event_content(events),
            "reply": {"source": "minimax", "content": ""},
        }

    def build_response(
        self,
        plan: dict,
        *,
        assistant_name: str = "瀹濆瓙",
        assistant_voice_id: str | None = None,
    ) -> dict:
        return {
            "interrupt": False,
            "mode": plan["mode"],
            "decision_reason": plan["decision_reason"],
            "reply": plan["reply"],
            "speech_job": None,
            "assistant_event": None,
        }


class FakeProgressiveDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        for reply in self.stream_reply_updates(mode=mode, transcript=transcript, events=events):
            yield reply["content"]

    def stream_preview_text(self, *, mode: str, transcript: str, events: list[dict]):
        first = next(self.stream_reply_updates(mode=mode, transcript=transcript, events=events))
        yield first["lead"]

    def stream_reply_updates(self, *, mode: str, transcript: str, events: list[dict]):
        assert mode == "conversation"
        yield {
            "source": "minimax",
            "lead": "三国杀是一款以三国时期为背景的身份对战游戏。",
            "tail": "",
            "content": "三国杀是一款以三国时期为背景的身份对战游戏。核心规则分三块：身份、出牌和胜利条件。",
        }
        yield {
            "source": "minimax",
            "lead": "三国杀是一款以三国时期为背景的身份对战游戏。",
            "tail": "",
            "content": "三国杀是一款以三国时期为背景的身份对战游戏。核心规则分三块：身份、出牌和胜利条件。每回合按摸牌、出牌、弃牌推进。",
        }


class FakeProgressiveTtsAdapter:
    def __init__(self, tmp_dir: Path) -> None:
        self.output_dir = tmp_dir

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        return {
            "audio_bytes": text.encode("utf-8"),
            "format": "mp3",
        }


class VoiceRecordingProgressiveTtsAdapter(FakeProgressiveTtsAdapter):
    def __init__(self, tmp_dir: Path) -> None:
        super().__init__(tmp_dir)
        self.voice_ids: list[str | None] = []

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        self.voice_ids.append(voice_id)
        return super().synthesize_segment(text, voice_id=voice_id)


class FakeContinuationOnlyDialogClient:
    def __init__(self) -> None:
        self.captured_events: list[list[dict]] = []

    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for preview handoff continuation")

    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        yield from self.stream_continuation_text(
            mode=mode,
            transcript=transcript,
            events=events,
            already_spoken_text=kwargs.get("already_spoken_text") or "preview",
        )

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        self.captured_events.append(list(events))
        yield "鏍稿績瑙勫垯鍒嗕笁鍧楋細韬唤銆佸嚭鐗屽拰鑳滃埄鏉′欢銆?"
        yield "鏍稿績瑙勫垯鍒嗕笁鍧楋細韬唤銆佸嚭鐗屽拰鑳滃埄鏉′欢銆傛瘡鍥炲悎鎸夋懜鐗屻€佸嚭鐗屻€佸純鐗屾帹杩涖€?"


class FakePlainOnlyContinuationDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "第一句正式回复。"
        yield "第一句正式回复。第二句继续补充。"


class FakeLookupMarkerContinuationDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "稍等一下，马上给你结果。<lookup>"


class FakePrefixedContinuationDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "宝子：第一句正式回复。"


class FakePartialFirstChunkDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for preview handoff continuation")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "涓夊浗鏉€鏄竴娆句互涓夊浗鍘嗗彶涓鸿儗鏅殑鍗＄墝瀵规垬"
        yield "涓夊浗鏉€鏄竴娆句互涓夊浗鍘嗗彶涓鸿儗鏅殑鍗＄墝瀵规垬娓告垙銆傛瘡浣嶇帺瀹朵細鑾峰緱涓€涓韩浠姐€?"


class FakeRewritingContinuationDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for preview handoff continuation")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "三国杀是一款以"
        yield "三国杀是一款以三国时期为背景的卡牌对战游戏。玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。"
        yield "三国杀是一款以三国时期为背景的卡牌对战游戏。玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。游戏开始前，玩家需要根据身份制定策略。"


class FakeTinyFirstChunkDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for preview handoff continuation")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "涓夊浗"
        yield "涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑鍗＄墝瀵规垬娓告垙銆?"


class FakeProgressiveAutoInterruptService:
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeProgressiveDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)

    def plan_progressive(
        self,
        events: list[dict],
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        return {
            "should_interrupt": True,
            "mode": "conversation",
            "decision_reason": "assistant_name_called",
            "transcript": _latest_test_event_content(events),
            "deferred_generation": True,
        }

    def plan(
        self,
        events: list[dict],
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        raise AssertionError("plan() should not be used for preview handoff formal generation")

    def build_response(self, plan: dict, **kwargs) -> dict:
        raise AssertionError("build_response() should not be used for preview handoff formal generation")


class FakeVoiceRecordingAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeProgressiveDialogClient()},
        )()
        self.tts_adapter = VoiceRecordingProgressiveTtsAdapter(tmp_dir)


class FakePlainFallbackDialogClient(FakeProgressiveDialogClient):
    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for runtime fallback")

    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        assert mode == "conversation"
        yield "???????????????????????????????????????????"
        yield "???????????????????????????????????????????????????????????????????????????????"


class FakePlainFallbackAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakePlainFallbackDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeFormalStreamingDialogClient:
    def __init__(self) -> None:
        self.streamed_transcripts: list[str] = []

    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for user-audible formal replies")

    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        assert mode == "conversation"
        self.streamed_transcripts.append(transcript)
        yield "第一句正式规则说明。"
        yield "第一句正式规则说明。第二句继续解释。"


class FakeFormalStreamingAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.dialog_client = FakeFormalStreamingDialogClient()
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": self.dialog_client},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeDeltaFormalStreamingDialogClient(FakeFormalStreamingDialogClient):
    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        assert mode == "conversation"
        self.streamed_transcripts.append(transcript)
        yield "Hello baoz."
        yield "Hello baoz. I am your tabletop buddy."
        yield "Hello baoz. I am your tabletop buddy. I help with rules."


class FakeDeltaFormalStreamingAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.dialog_client = FakeDeltaFormalStreamingDialogClient()
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": self.dialog_client},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeFailingAfterPendingTextDialogClient(FakeFormalStreamingDialogClient):
    def stream_reply_text(self, *, mode: str, transcript: str, events: list[dict], **kwargs):
        assert mode == "conversation"
        yield "First sentence. Third fragment"
        raise TimeoutError("stream timed out")


class RecordingProgressiveTtsAdapter(FakeProgressiveTtsAdapter):
    def __init__(self, tmp_dir: Path) -> None:
        super().__init__(tmp_dir)
        self.calls: list[str] = []

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        self.calls.append(text)
        return super().synthesize_segment(text, voice_id=voice_id)


class FakeFailingAfterPendingTextAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.dialog_client = FakeFailingAfterPendingTextDialogClient()
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": self.dialog_client},
        )()
        self.tts_adapter = RecordingProgressiveTtsAdapter(tmp_dir)


class FakeContinuationOnlyAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.dialog_client = FakeContinuationOnlyDialogClient()
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": self.dialog_client},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakePlainOnlyContinuationAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakePlainOnlyContinuationDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeLookupMarkerContinuationAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeLookupMarkerContinuationDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakePrefixedContinuationAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakePrefixedContinuationDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakePartialFirstChunkAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakePartialFirstChunkDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeRewritingContinuationAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeRewritingContinuationDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeTinyFirstChunkAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeTinyFirstChunkDialogClient()},
        )()
        self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)


class FakeProgressiveAutoInterruptServiceWithPreview(FakeProgressiveAutoInterruptService):
    def preview(self, events: list[dict], assistant_name: str = "瀹濆瓙", assistant_voice_id: str | None = None) -> dict:
        transcript = _latest_test_event_content(events)
        preview_text = next(
            self.orchestrator.dialog_client.stream_preview_text(
                mode="conversation",
                transcript=transcript,
                events=events,
            )
        )
        output_dir = self.tts_adapter.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        full_output_path = output_dir / "job-preview-progressive.mp3"
        segment_output_path = output_dir / "job-preview-progressive-segment-0.mp3"
        segment_bytes = b"\x04\x05\x06"
        full_output_path.write_bytes(segment_bytes)
        segment_output_path.write_bytes(segment_bytes)
        return {
            "interrupt": True,
            "mode": "conversation",
            "decision_reason": "assistant_name_called",
            "reply": {
                "source": "minimax",
                "content": preview_text,
            },
            "speech_job": {
                "accepted": True,
                "job_id": "job-preview-progressive",
                "status": "ready",
                "format": "mp3",
                "output_path": str(full_output_path),
                "segments": [preview_text],
                "segment_count": 1,
                "segment_statuses": [
                    {
                        "index": 0,
                        "text": preview_text,
                        "status": "queued",
                        "format": "mp3",
                        "bytes": len(segment_bytes),
                        "output_path": str(segment_output_path),
                    }
                ],
                "voice_id": assistant_voice_id,
            },
            "assistant_event": None,
            "transcript": transcript,
        }


class FakeRealtimeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeAudioDrivenRealtimeSession] = []

    def __call__(self) -> "FakeAudioDrivenRealtimeSession":
        session = FakeAudioDrivenRealtimeSession(stable_text=f"turn-{len(self.sessions) + 1}")
        self.sessions.append(session)
        return session


class FakeAudioDrivenRealtimeSession:
    def __init__(self, *, stable_text: str) -> None:
        self.connected = False
        self.audio_chunks: list[bytes] = []
        self.ended = False
        self.closed = False
        self._stable_text = stable_text
        self._events: list[dict] = []

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)
        if not self._events:
            self._events.extend(
                [
                    {"event": "transcript", "slice_type": 2, "index": 0, "text": self._stable_text},
                    {"event": "final"},
                ]
            )

    async def receive_event(self):
        while not self._events and not self.ended:
            await asyncio.sleep(0.005)
        if self._events:
            return self._events.pop(0)
        return None

    async def end(self) -> None:
        self.ended = True

    async def close(self) -> None:
        self.closed = True


class FakeBlockingSendRealtimeSession:
    def __init__(self) -> None:
        self.connected = False
        self.started_send = asyncio.Event()
        self.ended = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, chunk: bytes) -> None:
        self.started_send.set()
        await asyncio.Event().wait()

    async def receive_event(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        return None

    async def end(self) -> None:
        self.ended = True

    async def close(self) -> None:
        self.closed = True


def test_realtime_websocket_forwards_audio_and_persists_final_transcript():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Live Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert fake_session.connected is True
        assert fake_session.audio_chunks == [b"abcdefgh"]
        assert fake_session.ended is True
        assert fake_session.closed is True
        assert first["event"] == "transcript"
        assert second["text"] == "live transcript"
        assert third["event"] == "final"

        context = client.get(f"/tables/{table_id}/context")
        assert context.status_code == 200
        assert len(context.json()["events"]) == 1
        assert context.json()["events"][0]["kind"] == "voice_transcript"
        assert context.json()["events"][0]["source"] == "live_asr"
        assert context.json()["events"][0]["content"] == "宝宝：live transcript"
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_websocket_accepts_public_api_token_query(monkeypatch):
    monkeypatch.setenv("GAMEVOICE_PUBLIC_API_TOKEN", "server-token")
    client = TestClient(app)
    created = client.post(
        "/tables",
        json={"name": "Live Token Table"},
        headers={"Authorization": "Bearer server-token"},
    )
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(
            f"/ws/tables/{table_id}/listen?access_token=server-token"
        ) as websocket:
            websocket.send_text('{"type":"end"}')
        assert fake_session.connected is True
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_websocket_rejects_missing_public_api_token(monkeypatch):
    monkeypatch.setenv("GAMEVOICE_PUBLIC_API_TOKEN", "server-token")
    client = TestClient(app)
    created = client.post(
        "/tables",
        json={"name": "Live Token Table"},
        headers={"Authorization": "Bearer server-token"},
    )
    table_id = created.json()["id"]

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen"):
            raise AssertionError("websocket should require public api token")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_realtime_websocket_control_end_is_not_blocked_by_slow_audio_send():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Slow Audio Bridge Table"})
    table_id = created.json()["id"]

    fake_session = FakeBlockingSendRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            websocket.send_text('{"type":"end"}')

        assert fake_session.connected is True
        assert fake_session.ended is True
        assert fake_session.closed is True
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_websocket_emits_speaker_identity_batch_and_snapshots():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Live Identity Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            live_session_id = next(iter(main_module.speaker_live_connector._live_sessions[table_id].keys()))
            main_module.speaker_live_connector.ingest_live_pipeline_batch(
                table_id,
                live_session_id,
                source="pyannote_wespeaker",
                diarization_segments=[
                    {
                        "segment_id": "seg-identity-1",
                        "speaker": "SPEAKER_00",
                        "speaker_profile": "profile-0",
                        "start": 0.0,
                        "end": 1.2,
                        "text": "I am Nova",
                        "confidence": 0.98,
                    }
                ],
                speaker_embeddings=[
                    {
                        "speaker_profile": "profile-0",
                        "vector": [1.0, 0.0, 0.0],
                        "sample_count": 1,
                    }
                ],
            )

            websocket.send_bytes(b"abcdefgh")
            events = []
            for _ in range(6):
                events.append(websocket.receive_json())
                if any(item.get("event") == "transcript" for item in events) and any(
                    item.get("event") == "speaker_identity_batch" for item in events
                ):
                    break
            websocket.send_text('{"type":"end"}')

        transcript_events = [item for item in events if item.get("event") == "transcript"]
        identity_events = [item for item in events if item.get("event") == "speaker_identity_batch"]

        assert transcript_events
        assert identity_events
        assert "speaker_identities" in transcript_events[0]
        assert "speaker_identity_review_candidates" in transcript_events[0]
        assert identity_events[0]["speaker_identity_batch"]["speaker_identities"][0]["speaker_id"] == "player_a"
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_speaker_id_transcript_does_not_run_alias_rewrite_before_poll():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Native Speaker Alias Rewrite Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="Alice says hello")
    fake_session.events = [
        {
            "event": "transcript",
            "slice_type": 2,
            "index": 0,
            "text": "Alice says hello",
            "speaker_id": "0",
            "speaker_label": "speaker_0",
        },
        {
            "event": "final",
            "stream_final": True,
            "index": 0,
            "text": "Alice says hello",
            "speaker_id": "0",
            "speaker_label": "speaker_0",
        },
    ]
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_text('{"type":"end"}')
    finally:
        app.state.realtime_session_factory = original_factory

    alias_state = client.get(f"/tables/{table_id}/speaker-identities/alias-map").json()["alias_rewrite_state"]
    assert alias_state is None


def test_realtime_records_completed_sentence_alias_evidence_from_draft_events():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Native Speaker Alias Evidence Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="placeholder")
    fake_session.events = [
        {
            "event": "transcript",
            "slice_type": 1,
            "index": 3,
            "text": "孙哥说今晚",
            "speaker_id": -1,
            "sentences": {
                "sentence_list": [
                    {
                        "sentence": "孙哥说今晚看星星。",
                        "sentence_type": 1,
                        "sentence_id": 3,
                        "speaker_id": 0,
                        "start_time": 1000,
                        "end_time": 2400,
                    }
                ]
            },
        },
        {"event": "final", "stream_final": True},
    ]
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_text('{"type":"end"}')
    finally:
        app.state.realtime_session_factory = original_factory

    runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
    evidence = [item for item in runtime_events if item.get("kind") == "speaker_alias_evidence"]
    assert evidence
    assert evidence[-1]["speaker_id"] == "speaker_0"
    assert evidence[-1]["content"] == "speaker_0：孙哥说今晚看星星。"
    alias_map = client.get(f"/tables/{table_id}/speaker-identities/alias-map").json()["speaker_alias_map"]
    assert "speaker_0" in alias_map
    assert not [
        item for item in runtime_events if item.get("kind") == "speaker_alias_rewrite_completed"
    ]


def test_safe_send_json_swallows_closed_websocket_runtime_error():
    websocket = FakeClosedWebSocket()

    sent = asyncio.run(main_module._safe_send_ws_json(websocket, {"event": "noop"}))

    assert sent is False
    assert websocket.payloads == [{"event": "noop"}]


def test_resolve_preview_handoff_context_falls_back_to_preview_spoken_event():
    runtime = {
        "preview_reply_text": None,
        "preview_source_text": None,
    }
    dialog_events = [
        {
            "kind": "assistant_spoken",
            "source": "runtime_preview",
            "content": "宝子：三国杀是一款以三国时期为背景的身份对战游戏。",
        }
    ]

    preview_text, preview_source = _resolve_preview_handoff_context(dialog_events, runtime)

    assert preview_text == "三国杀是一款以三国时期为背景的身份对战游戏。"
    assert preview_source is None


def test_resolve_preview_handoff_context_rejects_history_preview_for_new_transcript():
    runtime = {
        "preview_reply_text": None,
        "preview_source_text": None,
    }
    dialog_events = [
        {
            "kind": "assistant_spoken",
            "source": "runtime_preview",
            "content": "old self intro preview",
        }
    ]

    preview_text, preview_source = _resolve_preview_handoff_context(
        dialog_events,
        runtime,
        current_source_text="player_a: explain landlord rules",
    )

    assert preview_text is None
    assert preview_source is None


def test_trim_formal_reply_after_preview_removes_overlap_and_lead_tail():
    reply = {
        "source": "companion",
        "lead": "来说说三国杀核心规则",
        "tail": "听我细细讲",
        "content": "三国杀是一款以三国时期为背景的身份对战游戏。核心规则分三块：身份、基本牌、回合流程。",
    }

    trimmed = _trim_formal_reply_after_preview(
        reply,
        "三国杀是一款以三国时期为背景的身份对战游戏。",
    )

    assert trimmed["lead"] == "核心规则分三块：身份、基本牌、回合流程。"
    assert trimmed["tail"] == ""
    assert trimmed["content"] == "核心规则分三块：身份、基本牌、回合流程。"


def test_run_auto_interrupt_registers_tts_job_before_assistant_ready(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Ready Order Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeAutoInterruptService()
    original_append_runtime_event = session_manager.append_runtime_event
    ready_checked = False

    def append_runtime_event(table: str, event: dict) -> dict:
        nonlocal ready_checked
        if table == table_id and event.get("kind") == "assistant_ready":
            ready_checked = True
            assert session_manager.find_assistant_reply_by_job(table_id, event["job_id"]) is not None
        return original_append_runtime_event(table, event)

    monkeypatch.setattr(session_manager, "append_runtime_event", append_runtime_event)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain landlord rules",
            },
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert ready_checked is True
    finally:
        main_module.auto_interrupt_service = original_service


def test_realtime_diagnostics_endpoint_tracks_audio_and_transcript_flow():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Diagnostics Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            websocket.receive_json()
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        diagnostics = client.get(f"/tables/{table_id}/live-diagnostics")
        assert diagnostics.status_code == 200
        payload = diagnostics.json()
        assert payload["audio_chunks_received"] == 1
        assert payload["audio_bytes_received"] == 8
        assert "last_audio_chunk_bytes" not in payload
        assert "recent_audio_chunk_bytes" not in payload
        assert payload["audio_receive_monotonic_ms"] is not None
        assert "audio_inter_arrival_ms" in payload
        assert "receive_burst_count" in payload
        assert "max_receive_burst_chunks_per_second" in payload
        assert "audio_queue_depth_on_enqueue" in payload
        assert "max_audio_queue_depth_on_enqueue" in payload
        assert "audio_queue_depth_on_dequeue" in payload
        assert "max_audio_queue_depth_on_dequeue" in payload
        assert "send_worker_lag_ms" in payload
        assert "send_audio_elapsed_ms" in payload
        assert "tencent_payload_send_elapsed_ms" in payload
        assert "send_audio_pacing_requested_ms" in payload
        assert "send_audio_pacing_actual_ms" in payload
        assert "event_loop_lag_ms" in payload
        assert "silence_gate_state" in payload
        assert "silence_gate_passed_chunks" in payload
        assert "silence_gate_suppressed_chunks" in payload
        assert "silence_gate_preroll_flushes" in payload
        assert "silence_gate_last_decision" in payload
        assert payload["draft_transcripts_forwarded"] == 1
        assert payload["stable_transcripts_forwarded"] == 1
        assert payload["final_transcripts_forwarded"] == 1
        assert payload["websocket_connects"] == 1
        assert payload["websocket_disconnects"] >= 1
        assert payload["last_audio_chunk_at"]
        assert payload["last_draft_transcript_at"]
        assert payload["last_stable_transcript_at"]
        assert payload["last_final_transcript_at"]
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_bridge_suppresses_audio_when_silence_gate_filters_chunk(monkeypatch):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Silence Gate Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    class SuppressAllGate:
        def process_chunk(self, chunk: bytes):
            from gamevoice_server.live_silence_gate import SilenceGateDecision

            return SilenceGateDecision(
                forward_chunks=[],
                state="idle",
                input_bytes=len(chunk),
                forwarded_bytes=0,
                suppressed_bytes=len(chunk),
                voiced_frames=0,
                total_frames=1,
            )

    monkeypatch.setattr(main_module, "_build_live_silence_gate", lambda: SuppressAllGate())

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            websocket.receive_json()
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert fake_session.audio_chunks == []
        payload = client.get(f"/tables/{table_id}/live-diagnostics").json()
        assert payload["audio_chunks_received"] == 1
        assert payload["silence_gate_suppressed_chunks"] == 1
        assert payload["silence_gate_suppressed_bytes"] == 8
    finally:
        app.state.realtime_session_factory = original_factory


def test_final_auto_interrupt_does_not_block_live_audio_receive(monkeypatch):
    class FinalOnlyRealtimeSession(FakeRealtimeSession):
        def __init__(self) -> None:
            super().__init__()
            self.events = [{"event": "final", "text": "baozi answer me", "index": 0}]
            self.audio_chunks: list[bytes] = []
            self._ended = False

        async def send_audio(self, chunk: bytes) -> None:
            self.audio_chunks.append(chunk)

        async def receive_event(self):
            if self.events:
                return self.events.pop(0)
            while not self._ended:
                await asyncio.sleep(0.01)
            return None

        async def end(self) -> None:
            self._ended = True
            self.ended = True

    client = TestClient(app)
    created = client.post("/tables", json={"name": "Nonblocking Final Table"})
    table_id = created.json()["id"]

    fake_session = FinalOnlyRealtimeSession()
    auto_interrupt_started = threading.Event()
    allow_auto_interrupt_finish = threading.Event()
    auto_interrupt_had_running_loop: list[bool] = []

    def slow_auto_interrupt(*args, **kwargs):
        auto_interrupt_started.set()
        try:
            asyncio.get_running_loop()
            auto_interrupt_had_running_loop.append(True)
        except RuntimeError:
            auto_interrupt_had_running_loop.append(False)
        allow_auto_interrupt_finish.wait(timeout=1.0)
        return {
            "interrupt": False,
            "mode": "conversation",
            "decision_reason": "test",
            "reply": {"source": "test", "content": ""},
            "speech_job": None,
            "assistant_event": None,
        }

    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session
    monkeypatch.setattr(main_module, "_should_run_auto_interrupt_on_final", lambda *args, **kwargs: True)
    monkeypatch.setattr(main_module, "_run_auto_interrupt_for_table_safely", slow_auto_interrupt)

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"first")
            assert auto_interrupt_started.wait(timeout=1.0)
            websocket.send_bytes(b"second")
            deadline = time.time() + 0.5
            while time.time() < deadline and len(fake_session.audio_chunks) < 2:
                time.sleep(0.01)
            assert fake_session.audio_chunks[:2] == [b"first", b"second"]
            allow_auto_interrupt_finish.set()
            websocket.send_text('{"type":"end"}')

        assert auto_interrupt_had_running_loop == [False]
    finally:
        allow_auto_interrupt_finish.set()
        app.state.realtime_session_factory = original_factory


def test_realtime_websocket_emits_assistant_ready_when_auto_interrupt_triggers():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Auto Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="what should I do this round")
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            events = []
            for _ in range(8):
                events.append(_receive_json_with_timeout(websocket))
                if any(item.get("event") == "assistant_ready" for item in events):
                    break
            websocket.send_text('{"type":"end"}')

        assert events[0]["event"] == "transcript"
        assert events[1]["event"] == "transcript"
        assert any(item["event"] == "assistant_preview" for item in events[2:])
        ready_event = next(item for item in events if item["event"] == "assistant_ready")
        assert ready_event["content"] == "then I will jump in"
        assert ready_event["speech_job"]["job_id"] == "job-live-1"
        assert ready_event["speech_job"]["turn_id"]
        assert ready_event["speech_job"]["reply_id"]
        assert ready_event["speech_job"]["turn_id"] == ready_event["turn_id"]
        assert ready_event["speech_job"]["reply_id"] == ready_event["reply_id"]
        assert ready_event["tts_stream"]["job_id"] == "job-live-1"
        assert ready_event["tts_stream"]["stream_id"]

        context = client.get(f"/tables/{table_id}/context")
        assert context.status_code == 200
        assert [item for item in context.json()["events"] if item.get("kind") == "assistant_reply"] == []
        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert tts_jobs.json()["jobs"][-1]["job_id"] == "job-live-1"
        assert tts_jobs.json()["jobs"][-1]["turn_id"]
        assert tts_jobs.json()["jobs"][-1]["reply_id"]
        runtime_events_response = client.get(f"/tables/{table_id}/runtime/events")
        assert runtime_events_response.status_code == 200
        decision_events = [
            item for item in runtime_events_response.json()["events"] if item.get("kind") == "assistant_turn_decision"
        ]
        ready_events = [
            item for item in runtime_events_response.json()["events"] if item.get("kind") == "assistant_ready"
        ]
        assert decision_events
        assert decision_events[-1]["interrupt"] is True
        assert decision_events[-1]["reason"] == "model"
        assert ready_events
        assert ready_events[-1]["source_transcript"] == "宝宝：what should I do this round"
        assert ready_events[-1]["preview_handoff"] is True
        assert ready_events[-1]["preview_reply_text"] == "let me think"
        assert ready_events[-1]["preview_source_text"] == "what should I do this round"

        assert fake_auto_interrupt.calls
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_final_waits_for_inflight_stable_preview_before_formal():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Final Wait Preview Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="what should I do this round")
    fake_auto_interrupt = FakeSlowPreviewAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            events = []
            deadline = time.time() + 2.0
            while time.time() < deadline:
                events.append(websocket.receive_json())
                if any(item.get("event") == "assistant_ready" for item in events):
                    break
            websocket.send_text('{"type":"end"}')

        assert any(item.get("event") == "assistant_preview" for item in events)
        ready_event = next(item for item in events if item.get("event") == "assistant_ready")
        assert ready_event["preview_handoff"] is True
        assert ready_event["preview_reply_text"] == "let me think"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_preview_handoff_does_not_start_duplicate_progressive_formal(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "No Duplicate Formal Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="what should I do this round")
    fake_auto_interrupt = FakeProgressiveAutoInterruptServiceWithPreview(tmp_path)
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            events = []
            deadline = time.time() + 2.0
            while time.time() < deadline:
                events.append(websocket.receive_json())
                if any(item.get("event") == "assistant_ready" for item in events):
                    break
            websocket.send_text('{"type":"end"}')

        time.sleep(0.2)
        runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
        formal_started = [
            item
            for item in runtime_events
            if item.get("kind") == "assistant_formal_generation_started"
        ]
        ready_events = [
            item for item in runtime_events if item.get("kind") == "assistant_ready"
        ]
        assert len(formal_started) == 1
        assert len(ready_events) == 1
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_final_only_still_uses_preview_before_formal():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Final Only Preview Table"})
    table_id = created.json()["id"]

    fake_session = FakeTextFinalThenEmptyFinalRealtimeSession()
    fake_auto_interrupt = FakeSlowPreviewAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            events = []
            deadline = time.time() + 2.0
            while time.time() < deadline:
                events.append(websocket.receive_json())
                if any(item.get("event") == "assistant_ready" for item in events):
                    break
            websocket.send_text('{"type":"end"}')

        assert any(item.get("event") == "assistant_preview" for item in events)
        ready_event = next(item for item in events if item.get("event") == "assistant_ready")
        assert ready_event["preview_handoff"] is True
        assert ready_event["preview_reply_text"] == "let me think"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_websocket_emits_assistant_preview_from_stable_transcript():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Table"})
    table_id = created.json()["id"]

    fake_session = FakeSingleStableRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "assistant_preview"
        assert second["content"] == "let me think"
        assert second["mode"] == "conversation"
        assert second["speech_job"]["job_id"] == "job-preview-1"
        assert second["tts_stream"]["job_id"] == "job-preview-1"
        assert second["tts_stream"]["stream_id"]

        context = client.get(f"/tables/{table_id}/context")
        assert context.status_code == 200
        assert context.json()["events"] == []

        runtime = client.get(f"/tables/{table_id}/runtime/events")
        assert runtime.status_code == 200
        preview_events = [
            item for item in runtime.json()["events"] if item.get("kind") == "assistant_preview_ready"
        ]
        assert preview_events
        assert preview_events[-1]["content"] == "let me think"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_websocket_does_not_start_formal_generation_from_stable_before_final(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Stable Formal Table"})
    table_id = created.json()["id"]

    fake_session = FakeSingleStableRealtimeSession()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = FakeProgressiveAutoInterruptServiceWithPreview(tmp_path)

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "assistant_preview"

        time.sleep(0.2)
        runtime_events = client.get(f"/tables/{table_id}/runtime/events")
        events = runtime_events.json()["events"]
        ready_events = [item for item in events if item.get("kind") == "assistant_ready"]
        formal_started = [
            item for item in events if item.get("kind") == "assistant_formal_generation_started"
        ]
        assert ready_events == []
        assert formal_started == []

        runtime_state = client.get(f"/tables/{table_id}/runtime/state")
        assert runtime_state.status_code == 200
        assert runtime_state.json()["preview_reply_text"] == second["content"]
        assert runtime_state.json()["preview_source_text"] == "what should I do now"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_preview_pregeneration_waits_until_preview_starts_playing(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Pregeneration Table"})
    table_id = created.json()["id"]

    class PregenerationDialogClient:
        def stream_continuation_text(self, *, mode: str, transcript: str, events: list[dict], already_spoken_text: str):
            yield "formal continuation"

    class PregenerationService:
        def __init__(self) -> None:
            self.orchestrator = type(
                "FakeOrchestrator",
                (),
                {"dialog_client": PregenerationDialogClient()},
            )()

        def preview(
            self,
            events: list[dict],
            assistant_name: str = "宝子",
            assistant_voice_id: str | None = None,
        ) -> dict:
            full_output_path = tmp_path / "preview.mp3"
            segment_output_path = tmp_path / "preview-segment-0.mp3"
            segment_bytes = b"preview"
            full_output_path.write_bytes(segment_bytes)
            segment_output_path.write_bytes(segment_bytes)
            speech_job = {
                "accepted": True,
                "job_id": "job-preview-pregen",
                "status": "ready",
                "format": "mp3",
                "output_path": str(full_output_path),
                "segments": ["preview"],
                "segment_count": 1,
                "segment_statuses": [
                    {
                        "index": 0,
                        "text": "preview",
                        "status": "queued",
                        "format": "mp3",
                        "bytes": len(segment_bytes),
                        "output_path": str(segment_output_path),
                    }
                ],
            }
            return {
                "interrupt": True,
                "mode": "conversation",
                "decision_reason": "assistant_name_called",
                "reply": {"source": "test", "content": "preview"},
                "speech_job": speech_job,
                "assistant_event": None,
                "transcript": _latest_test_event_content(events),
            }

        def plan_progressive(self, events: list[dict], assistant_name: str = "宝子") -> dict:
            return {
                "should_interrupt": True,
                "mode": "conversation",
                "decision_reason": "assistant_name_called",
                "transcript": _latest_test_event_content(events),
                "reply": None,
                "deferred_generation": True,
            }

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = PregenerationService()

    try:
        result = main_module._run_auto_interrupt_preview_for_table(
            table_id,
            transcript="what should I do now",
        )

        assert result["interrupt"] is True
        assert main_module.dialog_runtime_store.snapshot(table_id)["pending_formal_text"] is None
        started = client.post(
            f"/tables/{table_id}/tts-jobs/{result['speech_job']['job_id']}/segments/0/started"
        )
        assert started.status_code == 200
        deadline = time.time() + 1.0
        pending_formal = None
        while time.time() < deadline:
            pending_formal = main_module.dialog_runtime_store.snapshot(table_id)["pending_formal_text"]
            if pending_formal:
                break
            time.sleep(0.01)

        assert pending_formal == "formal continuation"
        context = session_manager.list_dialog_context(table_id)
        assert any(
            item.get("kind") == "assistant_spoken"
            and item.get("source") == "runtime_preview"
            and item.get("job_id") == result["speech_job"]["job_id"]
            for item in context
        )
        expected_legacy = (
            "涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑韬唤瀵规垬娓告垙銆傛牳蹇冭鍒欏垎涓夊潡锛氳韩浠姐€佸嚭鐗屽拰鑳滃埄鏉′欢銆傛瘡鍥炲悎鎸夋懜鐗屻€佸嚭鐗屻€佸純鐗屾帹杩涖€?"
        )
    finally:
        main_module.auto_interrupt_service = original_service


def test_final_without_preview_handoff_uses_formal_streaming(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "No Preview Formal Table"})
    table_id = created.json()["id"]
    session_manager.commit_live_transcript(
        table_id,
        source="live_asr",
        text="宝子，介绍三国杀规则",
    )

    original_service = main_module.auto_interrupt_service
    fake_service = FakeFormalStreamingAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        result = main_module._run_auto_interrupt_for_table_safely(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["reply"]["content"] == "第一句正式规则说明。"
        assert result["tts_stream"]["job_id"] == result["speech_job"]["job_id"]
        assert fake_service.dialog_client.streamed_transcripts
        assert "宝子" in fake_service.dialog_client.streamed_transcripts[-1]
    finally:
        main_module.auto_interrupt_service = original_service


def test_automatic_no_reply_resets_runtime_to_listening():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "No Reply Runtime Table"})
    table_id = created.json()["id"]
    session_manager.commit_live_transcript(
        table_id,
        source="live_asr",
        text="speaker_0: still chatting at the table",
    )
    main_module.dialog_runtime_store.on_user_audio(table_id)
    main_module.dialog_runtime_store.on_user_turn_committed(table_id)

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeNoReplyAutoInterruptService()

    try:
        result = main_module._run_auto_interrupt_for_table_safely(table_id, automatic=True)
        runtime = main_module.dialog_runtime_store.snapshot(table_id)

        assert result["interrupt"] is False
        assert result["assistant_event"] is None
        assert runtime["state"] == "listening"
        assert runtime["last_event"] == "agent_reply_skipped"
        assert runtime["is_user_speaking"] is False
        assert runtime["is_agent_speaking"] is False
    finally:
        main_module.auto_interrupt_service = original_service


def test_final_without_preview_handoff_does_not_duplicate_formal_opening(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "No Preview Delta Formal Table"})
    table_id = created.json()["id"]
    session_manager.commit_live_transcript(
        table_id,
        source="live_asr",
        text="player_a: baoz, introduce yourself",
    )

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeDeltaFormalStreamingAutoInterruptService(tmp_path)

    try:
        result = main_module._run_auto_interrupt_for_table_safely(table_id, automatic=True)
        emitted: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline:
            chunk = main_module.tts_stream_bridge.next_chunk(
                result["tts_stream"]["stream_id"],
                wait_timeout=0.2,
            )
            if chunk is None:
                if len(emitted) >= 3:
                    break
                continue
            emitted.append(chunk["text"])
            main_module.dialog_runtime_store.on_agent_speaking_started(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )
            main_module.dialog_runtime_store.on_agent_segment_completed(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )

        assert emitted == [
            "Hello baoz.",
            "I am your tabletop buddy.",
            "I help with rules.",
        ]
    finally:
        main_module.auto_interrupt_service = original_service


def test_rule_analysis_return_reply_injects_reference_before_oralized_tts(tmp_path: Path):
    client = TestClient(app)
    created = client.post(
        "/tables",
        json={
            "name": "Rule Return Streaming Table",
            "assistant_name": "宝子",
            "assistant_personality": "活泼但说话像真人",
        },
    )
    table_id = created.json()["id"]
    session_manager.commit_live_transcript(
        table_id,
        source="live_asr",
        text="宝子，帮我查一下 Tony Morgan 的卡牌效果。",
    )
    raw_lookup_result = (
        "Tony Morgan（托尼·摩根）调查员卡牌效果 --- 调查员信息 | 属性 | 详情 | "
        "|------|------| | 编号 | 06003 | | 职业 | 浪客（Rogue） | "
        "核心玩法总结：通过悬赏敌人获得资源。"
    )
    oralized_reply = "查到了，Tony Morgan 的重点就是给敌人挂悬赏，打掉以后把悬赏变成资源。"

    class OralizingDialogClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_context_reply(self, **kwargs):
            raise AssertionError("rule analysis reintegration must not use context reply JSON path")

        def generate_reply(self, **kwargs):
            self.calls.append(kwargs)
            return {"source": "companion", "content": f"{oralized_reply}<lookup>"}

    class OralizingAutoInterruptService:
        def __init__(self, tmp_dir: Path) -> None:
            self.dialog_client = OralizingDialogClient()
            self.orchestrator = type(
                "FakeOrchestrator",
                (),
                {"dialog_client": self.dialog_client},
            )()
            self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)

    record = main_module.rule_analysis_store.create(
        table_id=table_id,
        query="宝子，帮我查一下 Tony Morgan 的卡牌效果。",
        ack_text="我去查一下",
    )
    record = main_module.rule_analysis_store.mark_completed(
        record["analysis_id"],
        {
            "source": "skill_agent",
            "content": raw_lookup_result,
        },
    )

    original_service = main_module.auto_interrupt_service
    fake_service = OralizingAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        main_module._materialize_rule_analysis_reply(record)

        context = client.get(f"/tables/{table_id}/context").json()["events"]
        references = [item for item in context if item.get("kind") == "rule_reference"]
        assert references
        assert references[-1]["content"] == f"你刚刚查询得到的结果是：{raw_lookup_result}"
        assert references[-1]["analysis_id"] == record["analysis_id"]

        assert len(fake_service.dialog_client.calls) == 1
        call = fake_service.dialog_client.calls[0]
        assert call["mode"] == "conversation"
        assert call["strict"] is True
        assert call["assistant_name"] == "宝子"
        assert call["assistant_personality"] == "活泼但说话像真人"
        assert any(
            item.get("kind") == "rule_reference"
            and item.get("content") == f"你刚刚查询得到的结果是：{raw_lookup_result}"
            for item in call["events"]
        )
        assert raw_lookup_result not in call["transcript"]
        assert "不要追加 <lookup>" not in call["transcript"]
        assert "不要照搬资料原文" not in call["transcript"]
        assert "默认 1 到 3 句" not in call["transcript"]

        runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
        ready_events = [item for item in runtime_events if item.get("kind") == "assistant_ready"]
        assert ready_events
        assert ready_events[-1]["content"] == oralized_reply
        assert "<lookup>" not in ready_events[-1]["content"]
        assert ready_events[-1].get("lead", "") == ""
        assert ready_events[-1].get("tail", "") == ""
        assert raw_lookup_result not in ready_events[-1]["content"]
        stream_events = [item for item in runtime_events if item.get("kind") == "assistant_stream_ready"]
        assert stream_events
        assert stream_events[-1]["stream_id"]
        assert ready_events[-1]["stream_id"] == stream_events[-1]["stream_id"]
        jobs = client.get(f"/tables/{table_id}/tts-jobs").json()["jobs"]
        assert jobs[-1]["stream_id"] == stream_events[-1]["stream_id"]
        assert jobs[-1]["segment_statuses"][0]["text"] == oralized_reply
        assert "<lookup>" not in jobs[-1]["segment_statuses"][0]["text"]
        assert raw_lookup_result not in jobs[-1]["segment_statuses"][0]["text"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_rule_analysis_return_reply_preempts_user_turn_runtime(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Rule Return Priority Table"})
    table_id = created.json()["id"]
    session_manager.commit_live_transcript(
        table_id,
        source="live_asr",
        text="baoz check a card",
    )
    record = main_module.rule_analysis_store.create(
        table_id=table_id,
        query="check the short handgun card",
        ack_text="I will check it",
    )
    record = main_module.rule_analysis_store.mark_completed(
        record["analysis_id"],
        {
            "source": "skill_agent",
            "content": "The lookup result says the card was not found.",
        },
    )

    main_module.dialog_runtime_store.on_agent_reply_ready(
        table_id,
        job_id="old-job",
        reply_text="old interrupted lookup commitment",
        segment_count=1,
    )
    main_module.dialog_runtime_store.on_user_audio(table_id)

    class OralizingDialogClient:
        def generate_context_reply(self, **kwargs):
            raise AssertionError("rule analysis reintegration must not use context reply JSON path")

        def generate_reply(self, **kwargs):
            return {"source": "companion", "content": "I checked it: that card was not found."}

    class OralizingAutoInterruptService:
        def __init__(self, tmp_dir: Path) -> None:
            self.orchestrator = type(
                "FakeOrchestrator",
                (),
                {"dialog_client": OralizingDialogClient()},
            )()
            self.tts_adapter = FakeProgressiveTtsAdapter(tmp_dir)

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = OralizingAutoInterruptService(tmp_path)

    try:
        main_module._materialize_rule_analysis_reply(record)

        runtime = client.get(f"/tables/{table_id}/runtime/state").json()
        assert runtime["state"] == "assistant_ready"
        assert runtime["current_job_id"] != "old-job"
        assert runtime["priority_reply_job_id"] == runtime["current_job_id"]

        runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
        assert [item["kind"] for item in runtime_events if item.get("kind") == "assistant_priority_reply_ready"]

        context = client.get(f"/tables/{table_id}/context").json()["events"]
        assert not any(
            item.get("kind") == "assistant_unspoken"
            and item.get("job_id") == runtime["current_job_id"]
            for item in context
        )
    finally:
        main_module.auto_interrupt_service = original_service


def test_preview_handoff_formal_generation_uses_independent_tts_stream(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Independent Formal Stream Table"})
    table_id = created.json()["id"]
    preview_stream = main_module.tts_stream_bridge.open_stream(
        job_id="job-preview-existing",
        turn_id="turn-preview",
        reply_id="reply-preview",
        segment_count=1,
    )
    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeProgressiveAutoInterruptService(tmp_path)

    try:
        result = main_module._build_incremental_auto_interrupt_response(
            table_id,
            plan={
                "mode": "conversation",
                "transcript": "what should I do now",
                "decision_reason": "assistant_name_called",
            },
            dialog_events=[
                {
                    "kind": "voice_transcript",
                    "source": "live_asr",
                    "content": "what should I do now",
                }
            ],
            latest_transcript="what should I do now",
            preview_handoff_reply_text="preview already spoken",
            preview_handoff_source_text="what should I do now",
            preview_stream_id=preview_stream["stream_id"],
            preview_job_id=preview_stream["job_id"],
        )

        formal_stream = result["tts_stream"]
        assert formal_stream["stream_id"] != preview_stream["stream_id"]
        assert formal_stream["job_id"] == result["speech_job"]["job_id"]
        chunk = main_module.tts_stream_bridge.next_chunk(
            formal_stream["stream_id"],
            wait_timeout=1.0,
        )
        assert chunk is not None
        assert chunk["job_id"] == result["speech_job"]["job_id"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_realtime_websocket_does_not_start_formal_generation_from_stable_even_if_preview_starts_speaking(
    tmp_path: Path,
    monkeypatch,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Stable Formal Preview Race Table"})
    table_id = created.json()["id"]

    fake_session = FakeSingleStableRealtimeSession()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    original_safe_send = main_module._safe_send_ws_json
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = FakeProgressiveAutoInterruptServiceWithPreview(tmp_path)

    async def fake_safe_send(websocket, payload):
        if payload.get("event") == "assistant_preview":
            job_id = (payload.get("speech_job") or {}).get("job_id")
            if job_id:
                main_module.dialog_runtime_store.on_agent_speaking_started(
                    table_id,
                    job_id=job_id,
                    segment_index=0,
                )
        return await original_safe_send(websocket, payload)

    monkeypatch.setattr(main_module, "_safe_send_ws_json", fake_safe_send)

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "assistant_preview"

        time.sleep(0.2)
        runtime_events = client.get(f"/tables/{table_id}/runtime/events")
        events = runtime_events.json()["events"]
        ready_events = [item for item in events if item.get("kind") == "assistant_ready"]
        formal_started = [
            item for item in events if item.get("kind") == "assistant_formal_generation_started"
        ]
        assert ready_events == []
        assert formal_started == []
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service
        main_module._safe_send_ws_json = original_safe_send


def test_realtime_websocket_keeps_stream_alive_when_auto_interrupt_fails():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Auto Error Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="hello from player")
    fake_auto_interrupt = FakeAutoInterruptServiceError()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "transcript"
        assert third["event"] == "final"

        runtime_events = client.get(f"/tables/{table_id}/runtime/events")
        assert runtime_events.status_code == 200
        failure_events = [
            item
            for item in runtime_events.json()["events"]
            if item.get("kind") == "assistant_auto_reply_failed"
        ]
        assert failure_events
        assert "no text" in failure_events[-1]["content"]
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


class FakeDuplicateStableRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
            {"event": "transcript", "slice_type": 2, "index": 1, "text": "what should I do now"},
            {"event": "final"},
        ]


class FakeTextFinalThenEmptyFinalRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="hello assistant")
        self.events = [
            {
                "event": "final",
                "slice_type": 2,
                "index": 0,
                "text": "hello assistant",
                "speaker_id": "0",
                "speaker_label": "speaker_0",
            },
            {"event": "final", "stream_final": True},
        ]


class FakeTwoStableRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
            {"event": "transcript", "slice_type": 2, "index": 1, "text": "should I attack first"},
            {"event": "final"},
        ]


class FakeSingleStableRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
        ]
        self._ended = False

    async def receive_event(self):
        while not self.events and not self._ended:
            await asyncio.sleep(0.01)
        if self.events:
            return self.events.pop(0)
        return None

    async def end(self) -> None:
        self._ended = True
        self.ended = True


class FakeBargeInRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
        ]
        self._audio_count = 0
        self._ended = False

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)
        self._audio_count += 1
        if self._audio_count == 2:
            self.events.append(
                {"event": "transcript", "slice_type": 2, "index": 1, "text": "wait wait"}
            )

    async def receive_event(self):
        while not self.events and not self._ended:
            await asyncio.sleep(0.01)
        if self.events:
            return self.events.pop(0)
        return None

    async def end(self) -> None:
        self._ended = True
        self.ended = True


class FakePartialBargeInRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
        ]
        self._audio_count = 0
        self._ended = False

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)
        self._audio_count += 1
        if self._audio_count == 2:
            self.events.extend(
                [
                    {"event": "transcript", "slice_type": 1, "index": 1, "text": "宝子等一下"},
                    {"event": "final"},
                ]
            )

    async def receive_event(self):
        while not self.events and not self._ended:
            await asyncio.sleep(0.01)
        if self.events:
            return self.events.pop(0)
        return None

    async def end(self) -> None:
        self._ended = True
        self.ended = True


class FakeNonInterruptingPartialRealtimeSession(FakeRealtimeSession):
    def __init__(self) -> None:
        super().__init__(stable_text="what should I do now")
        self.events = [
            {"event": "transcript", "slice_type": 2, "index": 0, "text": "what should I do now"},
        ]
        self._audio_count = 0
        self._ended = False

    async def send_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)
        self._audio_count += 1
        if self._audio_count == 2:
            self.events.extend(
                [
                    {"event": "transcript", "slice_type": 1, "index": 1, "text": "我觉得也还行"},
                    {"event": "final"},
                ]
            )

    async def receive_event(self):
        while not self.events and not self._ended:
            await asyncio.sleep(0.01)
        if self.events:
            return self.events.pop(0)
        return None

    async def end(self) -> None:
        self._ended = True
        self.ended = True


def test_realtime_websocket_deduplicates_same_stable_transcript_auto_interrupt():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Dedup Table"})
    table_id = created.json()["id"]

    fake_session = FakeDuplicateStableRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            raw_events = [websocket.receive_json() for _ in range(5)]
            websocket.send_text('{"type":"end"}')

        events = [item for item in raw_events if item["event"] != "realtime_reconnected"]
        assert events[0]["event"] == "transcript"
        assert len([item for item in events if item["event"] == "assistant_preview"]) == 1
        assert len([item for item in events if item["event"] == "assistant_ready"]) == 1
        assert any(item["event"] == "transcript" and item.get("index") == 1 for item in events)
        assert any(item["event"] == "final" for item in events)

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert len(tts_jobs.json()["jobs"]) == 1
        runtime_events = client.get(f"/tables/{table_id}/runtime/events")
        blocked_events = [
            item
            for item in runtime_events.json()["events"]
            if item.get("kind") == "assistant_auto_reply_blocked"
        ]
        assert blocked_events == []
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_resolve_final_live_transcript_text_ignores_empty_stream_final_after_commit():
    assert (
        _resolve_final_live_transcript_text(
            {
                "pending_source_text": "speaker_0：hello assistant",
                "preview_source_text": None,
                "preview_reply_text": None,
            },
            None,
        )
        is None
    )


def test_realtime_final_only_normalizes_tencent_speaker_id_to_speaker_bucket():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Final Speaker Bucket Table"})
    table_id = created.json()["id"]

    fake_session = FakeTextFinalThenEmptyFinalRealtimeSession()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = FakeAutoInterruptService()

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            for _ in range(4):
                event = websocket.receive_json()
                if event.get("event") == "final":
                    break
            websocket.send_text('{"type":"end"}')

        context = client.get(f"/tables/{table_id}/context").json()["events"]
        transcript = next(item for item in context if item["kind"] == "voice_transcript")
        identities = client.get(f"/tables/{table_id}/speaker-identities").json()["speaker_identities"]

        assert transcript["speaker_id"] == "speaker_0"
        assert transcript["speaker_label"] == "speaker_0"
        assert transcript["content"] == "speaker_0：hello assistant"
        assert any(item["speaker_id"] == "speaker_0" for item in identities)
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_websocket_blocks_second_auto_interrupt_while_agent_busy():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Busy Table"})
    table_id = created.json()["id"]

    fake_session = FakeTwoStableRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            raw_events = [websocket.receive_json() for _ in range(5)]
            websocket.send_text('{"type":"end"}')

        events = [item for item in raw_events if item["event"] != "realtime_reconnected"]
        assert events[0]["event"] == "transcript"
        assert len([item for item in events if item["event"] == "assistant_preview"]) == 1
        assert len([item for item in events if item["event"] == "assistant_ready"]) == 1
        assert any(item["event"] == "transcript" and item.get("index") == 1 for item in events)
        assert any(item["event"] == "final" for item in events)

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert len(tts_jobs.json()["jobs"]) == 1

        runtime_events = client.get(f"/tables/{table_id}/runtime/events")
        blocked_events = [
            item
            for item in runtime_events.json()["events"]
            if item.get("kind") == "assistant_auto_reply_blocked"
        ]
        assert blocked_events == []
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_audio_chunk_does_not_interrupt_before_segment_playback_starts():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Barge In Runtime Table"})
    table_id = created.json()["id"]

    fake_session = FakeSingleStableRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "what should I do now",
            },
        )
        interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
        assert interrupt.status_code == 200
        job_id = interrupt.json()["speech_job"]["job_id"]

        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            websocket.send_bytes(b"ijklmnop")
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert first["slice_type"] == 2

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert tts_jobs.json()["jobs"][0]["job_id"] == job_id
        assert tts_jobs.json()["jobs"][0]["status"] == "ready"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_barge_in_marks_active_job_interrupted_after_segment_playback_starts():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Barge In Runtime Table"})
    table_id = created.json()["id"]

    fake_session = FakeBargeInRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "what should I do now",
            },
        )
        interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
        assert interrupt.status_code == 200
        job_id = interrupt.json()["speech_job"]["job_id"]
        started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
        assert started.status_code == 200

        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            websocket.send_bytes(b"ijklmnop")
            barge_in = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert barge_in["event"] == "barge_in"

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert tts_jobs.json()["jobs"][0]["job_id"] == job_id
        assert tts_jobs.json()["jobs"][0]["status"] == "interrupted"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_realtime_partial_transcript_barges_in_while_agent_speaks():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Partial Barge In Runtime Table"})
    table_id = created.json()["id"]

    fake_session = FakePartialBargeInRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            started = client.post(
                f"/tables/{table_id}/tts-jobs/job-live-1/segments/0/started"
            )
            assert started.status_code == 200
            websocket.send_bytes(b"ijklmnop")
            barge_in = websocket.receive_json()
            transcript = websocket.receive_json()
            final = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "assistant_ready"
        assert barge_in["event"] == "barge_in"
        assert transcript["event"] == "transcript"
        assert transcript["slice_type"] == 1
        assert transcript["text"] == "宝子等一下"
        assert final["event"] == "final"

        context = client.get(f"/tables/{table_id}/context")
        events = context.json()["events"]
        assert [
            item
            for item in events
            if item.get("kind") == "voice_transcript" and item.get("content") == "宝宝（打断）：宝子等一下"
        ]
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_resolve_final_live_transcript_text_uses_new_final_when_pending_source_is_different_turn():
    assert (
        _resolve_final_live_transcript_text(
            {
                "pending_source_text": "Baozi, please check tomorrow's Shanghai weather.",
                "preview_source_text": None,
                "preview_reply_text": None,
            },
            "Hello Baozi, introduce yourself.",
        )
        == "Hello Baozi, introduce yourself."
    )


def test_realtime_partial_transcript_does_not_barge_in_without_interrupt_phrase():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Non Interrupting Partial Table"})
    table_id = created.json()["id"]

    fake_session = FakeNonInterruptingPartialRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            started = client.post(
                f"/tables/{table_id}/tts-jobs/job-live-1/segments/0/started"
            )
            assert started.status_code == 200
            websocket.send_bytes(b"ijklmnop")
            transcript = websocket.receive_json()
            final = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "assistant_ready"
        assert transcript["event"] == "transcript"
        assert transcript["slice_type"] == 1
        assert transcript["text"] == "我觉得也还行"
        assert final["event"] == "final"

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert tts_jobs.json()["jobs"][0]["status"] == "ready"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_derive_committed_prefix_for_state_waits_for_clause_boundary():
    assert _derive_committed_prefix_for_state("This is a") == ""
    assert _derive_committed_prefix_for_state("Great!", min_content_chars=12) == ""
    assert _derive_committed_prefix_for_state(
        "Nice, let me explain the rules",
        min_content_chars=12,
    ) == ""
    assert _derive_committed_prefix_for_state(
        "This is a card game, played by 4 to 8 people",
        min_content_chars=12,
    ) == "This is a card game,"
    assert _derive_committed_prefix_for_state(
        "This is a card game, played by 4 to 8 people."
    ) == "This is a card game, played by 4 to 8 people."
    assert _derive_committed_prefix_for_state(
        "First choose a role, lord, loyalist, rebel, or renegade.",
        min_content_chars=12,
    ) == "First choose a role, lord, loyalist, rebel, or renegade."


def test_realtime_websocket_ignores_malformed_control_message():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Malformed Control Table"})
    table_id = created.json()["id"]

    fake_session = FakeRealtimeSession(stable_text="hello from live stream")
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = lambda: fake_session

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_text("ping")
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert second["event"] == "transcript"
        assert second["text"] == "hello from live stream"
        assert third["event"] == "final"
        assert fake_session.connected is True
        assert fake_session.closed is True
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_websocket_reconnects_after_stream_final_before_next_audio():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Reconnect Table"})
    table_id = created.json()["id"]

    fake_factory = FakeRealtimeSessionFactory()
    original_factory = app.state.realtime_session_factory
    app.state.realtime_session_factory = fake_factory

    try:
        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            second = websocket.receive_json()
            websocket.send_bytes(b"ijklmnop")
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert first["text"] == "turn-1"
        assert second["event"] == "final"
        assert len(fake_factory.sessions) == 2
        assert fake_factory.sessions[0].audio_chunks == [b"abcdefgh"]
        assert fake_factory.sessions[1].audio_chunks == [b"ijklmnop"]
        assert fake_factory.sessions[1].connected is True
        assert fake_factory.sessions[1].closed is True
    finally:
        app.state.realtime_session_factory = original_factory


def test_realtime_partial_transcript_barges_in_while_agent_speaks():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Partial Barge In Runtime Table"})
    table_id = created.json()["id"]

    fake_session = FakePartialBargeInRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "what should I do now",
            },
        )
        interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
        assert interrupt.status_code == 200
        job_id = interrupt.json()["speech_job"]["job_id"]
        started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
        assert started.status_code == 200

        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            websocket.send_bytes(b"ijklmnop")
            barge_in = websocket.receive_json()
            transcript = websocket.receive_json()
            final = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert barge_in["event"] == "barge_in"
        assert transcript["event"] == "transcript"
        assert transcript["slice_type"] == 1
        assert transcript["text"] == "宝子等一下"
        assert final["event"] == "final"

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert tts_jobs.json()["jobs"][0]["job_id"] == job_id
        assert tts_jobs.json()["jobs"][0]["status"] == "interrupted"

        context = client.get(f"/tables/{table_id}/context")
        events = context.json()["events"]
        assert [
            item
            for item in events
            if item.get("kind") == "voice_transcript" and item.get("content") == "宝宝（打断）：宝子等一下"
        ]
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service


def test_should_run_auto_interrupt_on_final_allows_preview_runtime():
    assert (
        _should_run_auto_interrupt_on_final(
            {
                "state": "agent_speaking",
                "preview_reply_text": "好的，给你讲讲三国杀规则。",
            }
        )
        is True
    )


def test_should_run_auto_interrupt_on_final_blocks_formal_agent_reply_state():
    assert (
        _should_run_auto_interrupt_on_final(
            {
                "state": "agent_speaking",
                "preview_reply_text": None,
            }
        )
        is False
    )


def test_should_run_auto_interrupt_on_final_allows_explicit_request_while_agent_speaking():
    assert (
        _should_run_auto_interrupt_on_final(
            {
                "state": "agent_speaking",
                "preview_reply_text": None,
            },
            "宝子，给我查一下 Tony Morgan 的卡牌效果。",
            events=[],
            assistant_name="宝子",
        )
        is True
    )


def test_resolve_final_live_transcript_text_prefers_preview_source_when_more_complete():
    assert (
        _resolve_final_live_transcript_text(
            {
                "preview_source_text": "嘿，宝子，介绍一下三国杀规则。",
                "preview_reply_text": "来了，三国杀基础规则了解一下！",
            },
            "嗯。",
        )
        == "嘿，宝子，介绍一下三国杀规则。"
    )


def test_resolve_final_live_transcript_text_prefers_pending_source_when_speculative_formal_started():
    assert (
        _resolve_final_live_transcript_text(
            {
                "pending_source_text": "宝子，给我讲解三国杀规则。",
                "preview_source_text": None,
                "preview_reply_text": None,
            },
            "嗯。",
        )
        == "宝子，给我讲解三国杀规则。"
    )


def test_resolve_final_live_transcript_text_falls_back_to_latest_stable_when_no_preview_source():
    assert (
        _resolve_final_live_transcript_text(
            {
                "pending_source_text": None,
                "preview_source_text": None,
                "preview_reply_text": None,
            },
            "正常 final 文本",
        )
        == "正常 final 文本"
    )


def test_realtime_partial_transcript_does_not_barge_in_without_interrupt_phrase():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Non Interrupting Partial Table"})
    table_id = created.json()["id"]

    fake_session = FakeNonInterruptingPartialRealtimeSession()
    fake_auto_interrupt = FakeAutoInterruptService()
    original_factory = app.state.realtime_session_factory
    original_service = main_module.auto_interrupt_service
    app.state.realtime_session_factory = lambda: fake_session
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "what should I do now",
            },
        )
        interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
        assert interrupt.status_code == 200
        job_id = interrupt.json()["speech_job"]["job_id"]
        started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
        assert started.status_code == 200

        with client.websocket_connect(f"/ws/tables/{table_id}/listen") as websocket:
            websocket.send_bytes(b"abcdefgh")
            first = websocket.receive_json()
            websocket.send_bytes(b"ijklmnop")
            transcript = websocket.receive_json()
            final = websocket.receive_json()
            websocket.send_text('{"type":"end"}')

        assert first["event"] == "transcript"
        assert transcript["event"] == "transcript"
        assert transcript["slice_type"] == 1
        assert transcript["text"] == "我觉得也还行"
        assert final["event"] == "final"

        tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
        assert tts_jobs.status_code == 200
        assert len(tts_jobs.json()["jobs"]) == 1
        assert tts_jobs.json()["jobs"][0]["job_id"] == job_id
        assert tts_jobs.json()["jobs"][0]["status"] == "ready"
        assert tts_jobs.json()["jobs"][0]["segment_statuses"][0]["status"] == "playing"
    finally:
        app.state.realtime_session_factory = original_factory
        main_module.auto_interrupt_service = original_service

def test_run_auto_interrupt_allows_formal_reply_while_preview_is_speaking():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Handoff Table"})
    table_id = created.json()["id"]

    fake_auto_interrupt = FakeAutoInterruptService()
    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = fake_auto_interrupt

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "鐜╁A锛氫粙缁嶄竴涓嬩笁鍥芥潃瑙勫垯",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="鏉ヤ簡锛岀粰浣犺璁蹭笁鍥芥潃瑙勫垯銆?",
            source_text="鍢匡紝瀹濆瓙锛屼粙缁嶄竴涓嬩笁鍥芥潃瑙勫垯銆?",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["decision_reason"] == "model"
        assert result["reply"]["content"] == "then I will jump in"
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_streams_formal_content_incrementally_after_preview_handoff(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Progressive Formal Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeProgressiveAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "玩家A：宝子，给我解释三国杀规则",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="三国杀是一款以三国时期为背景的身份对战游戏。",
            source_text="宝子，给我解释三国杀规则",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["mode"] == "conversation"
        assert result["reply"]["tail"] == ""
        assert result["reply"]["content"] == "核心规则分三块：身份、出牌和胜利条件。"
        assert result["tts_stream"]["stream_id"]
        assert result["speech_job"]["segment_count"] >= 1
        assert result["speech_job"]["segments"][0] == "核心规则分三块：身份、出牌和胜利条件。"

        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        second = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )

        assert first is not None
        assert first["text"] == "核心规则分三块：身份、出牌和胜利条件。"
        assert second is not None
        assert second["text"] == "每回合按摸牌、出牌、弃牌推进。"
    finally:
        main_module.auto_interrupt_service = original_service


def test_incremental_formal_tts_uses_table_voice_id(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post(
        "/tables",
        json={
            "name": "Custom Voice Formal Table",
            "assistant_voice_id": "custom-voice-1",
        },
    )
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeVoiceRecordingAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: custom voice formal request",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="preview text",
            source_text="custom voice formal request",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-custom-voice",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
        chunk = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )

        assert chunk is not None
        assert fake_service.tts_adapter.voice_ids
        assert set(fake_service.tts_adapter.voice_ids) == {"custom-voice-1"}
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_uses_plain_continuation_stream(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Continuation Handoff Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeContinuationOnlyAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "鐜╁A锛氬疂瀛愶紝缁欐垜瑙ｉ噴涓夊浗鏉€瑙勫垯",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑韬唤瀵规垬娓告垙銆?",
            source_text="瀹濆瓙锛岀粰鎴戣В閲婁笁鍥芥潃瑙勫垯",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["reply"]["content"] == "鏍稿績瑙勫垯鍒嗕笁鍧楋細韬唤銆佸嚭鐗屽拰鑳滃埄鏉′欢銆?"
        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        assert first is not None
        assert first["text"] == "鏍稿績瑙勫垯鍒嗕笁鍧楋細韬唤銆佸嚭鐗屽拰鑳滃埄鏉′欢銆?"
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_formal_context_includes_preview_handoff_event(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Preview Context Handoff Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeContinuationOnlyAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    transcript = "player_a: explain catan rules"
    preview_text = "Sure, I can start with the Catan basics."

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": transcript,
            },
        )
        preview_job = {
            "accepted": True,
            "job_id": "job-preview-1",
            "status": "ready",
            "format": "mp3",
            "segments": [preview_text],
            "segment_count": 1,
            "segment_statuses": [
                {
                    "index": 0,
                    "text": preview_text,
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 1,
                }
            ],
        }
        session_manager.append_assistant_reply(
            table_id,
            {
                "kind": "assistant_preview",
                "source": "runtime_preview",
                "mode": "conversation",
                "content": preview_text,
                "speech_job": preview_job,
                "turn_id": "turn-preview",
                "reply_id": "reply-preview",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text=preview_text,
            source_text=transcript,
            job_id="job-preview-1",
        )
        started = client.post(f"/tables/{table_id}/tts-jobs/job-preview-1/segments/0/started")
        assert started.status_code == 200

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert fake_service.dialog_client.captured_events
        events = fake_service.dialog_client.captured_events[-1]
        assert any(
            item.get("kind") == "voice_transcript" and item.get("content") == transcript
            for item in events
        )
        preview_events = [
            item
            for item in events
            if item.get("kind") == "assistant_spoken"
            and item.get("source") == "runtime_preview"
        ]
        assert preview_events
        assert preview_events[-1]["job_id"] == "job-preview-1"
        assert preview_text in preview_events[-1]["content"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_fallback_uses_plain_reply_text_stream(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Plain Fallback Handoff Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakePlainFallbackAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "鐜╁A锛氬疂瀛愶紝缁欐垜瑙ｉ噴涓夊浗鏉€瑙勫垯",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑韬唤瀵规垬娓告垙銆?",
            source_text="瀹濆瓙锛岀粰鎴戣В閲婁笁鍥芥潃瑙勫垯",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["reply"]["content"]
        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        assert first is not None
        assert result["speech_job"]["segments"]
        assert first["text"] == result["speech_job"]["segments"][0]
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_emits_partial_first_tts_chunk(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Partial First Chunk Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakePartialFirstChunkAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "鐜╁A锛氬疂瀛愶紝缁欐垜瑙ｉ噴涓夊浗鏉€瑙勫垯",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑韬唤瀵规垬娓告垙銆?",
            source_text="瀹濆瓙锛岀粰鎴戣В閲婁笁鍥芥潃瑙勫垯",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        second = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )

        assert first is not None
        assert second is not None
        assert first["text"] == "涓夊浗鏉€鏄竴娆句互涓夊浗鍘嗗彶涓鸿儗鏅殑鍗＄墝瀵规垬"
        assert second["text"] == "娓告垙銆傛瘡浣嶇帺瀹朵細鑾峰緱涓€涓韩浠姐€?"
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_handles_rewritten_cumulative_text_without_duplicates(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Rewriting Continuation Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeRewritingContinuationAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "玩家A：宝子，给我讲解三国杀规则",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="三国杀规则我来给你顺一遍。",
            source_text="宝子，给我讲解三国杀规则",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        emitted: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline:
            chunk = main_module.tts_stream_bridge.next_chunk(
                result["tts_stream"]["stream_id"],
                wait_timeout=0.2,
            )
            if chunk is None:
                if len(emitted) >= 3:
                    break
                continue
            emitted.append(chunk["text"])
            main_module.dialog_runtime_store.on_agent_speaking_started(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )
            main_module.dialog_runtime_store.on_agent_segment_completed(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )

        assert emitted == [
            "三国杀是一款以",
            "三国时期为背景的卡牌对战游戏。",
            "玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。",
            "游戏开始前，玩家需要根据身份制定策略。",
        ]
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_handles_rewritten_cumulative_text_without_duplicates(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Rewriting Continuation Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeRewritingContinuationAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Sanguosha rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        emitted: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline:
            chunk = main_module.tts_stream_bridge.next_chunk(
                result["tts_stream"]["stream_id"],
                wait_timeout=0.2,
            )
            if chunk is None:
                if len(emitted) >= 3:
                    break
                continue
            emitted.append(chunk["text"])
            main_module.dialog_runtime_store.on_agent_speaking_started(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )
            main_module.dialog_runtime_store.on_agent_segment_completed(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )

        assert emitted == [
            "三国杀是一款以三国时期为背景的卡牌对战游戏。",
            "玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。",
            "游戏开始前，玩家需要根据身份制定策略。",
        ]
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_skips_too_short_first_provisional_chunk(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Tiny First Chunk Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeTinyFirstChunkAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Sanguosha rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        emitted: list[str] = []
        while True:
            chunk = main_module.tts_stream_bridge.next_chunk(
                result["tts_stream"]["stream_id"],
                wait_timeout=0.2,
            )
            if chunk is None:
                break
            emitted.append(chunk["text"])

        assert emitted == [
            "涓夊浗鏉€鏄竴娆句互涓夊浗鏃舵湡涓鸿儗鏅殑鍗＄墝瀵规垬娓告垙銆?"
        ]
    finally:
        main_module.auto_interrupt_service = original_service


def test_run_auto_interrupt_preview_handoff_emits_partial_first_tts_chunk(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Partial First Chunk Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakePartialFirstChunkAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Sanguosha rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        second = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=0.2,
        )

        assert first is not None
        assert first["text"]
        assert second is None
    finally:
        main_module.auto_interrupt_service = original_service


def test_progressive_formal_handoff_emits_timing_runtime_events(
    tmp_path: Path,
):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Progressive Formal Timing Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakeProgressiveAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "玩家A：宝子，给我解释三国杀规则",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="三国杀是一款以三国时期为背景的身份对战游戏。",
            source_text="宝子，给我解释三国杀规则",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True

        deadline = time.time() + 1.0
        timing_kinds = {
            "assistant_formal_generation_started",
            "assistant_formal_first_reply_update",
            "assistant_formal_first_tts_started",
            "assistant_formal_first_tts_completed",
            "assistant_formal_generation_finished",
        }
        while time.time() < deadline:
            runtime_events = session_manager.list_runtime_events(table_id)
            seen = {item.get("kind") for item in runtime_events}
            if timing_kinds.issubset(seen):
                break
            time.sleep(0.01)

        runtime_events = session_manager.list_runtime_events(table_id)
        timing_events = [item for item in runtime_events if item.get("kind") in timing_kinds]
        timing_map = {item["kind"]: item for item in timing_events}

        assert timing_kinds.issubset(timing_map.keys())
        elapsed_values = [
            timing_map["assistant_formal_generation_started"]["elapsed_ms"],
            timing_map["assistant_formal_first_reply_update"]["elapsed_ms"],
            timing_map["assistant_formal_first_tts_started"]["elapsed_ms"],
            timing_map["assistant_formal_first_tts_completed"]["elapsed_ms"],
            timing_map["assistant_formal_generation_finished"]["elapsed_ms"],
        ]
        assert elapsed_values[0] == 0
        assert elapsed_values == sorted(elapsed_values)
        assert all(item.get("at") for item in timing_events)
    finally:
        main_module.auto_interrupt_service = original_service


def test_preview_finish_does_not_erase_preview_source_before_final_commit():
    runtime = main_module.dialog_runtime_store.ensure_table("preview-final-table")
    runtime.on_agent_preview_ready(
        reply_text="鏉ヤ簡锛屼笁鍥芥潃鍩虹瑙勫垯浜嗚В涓€涓嬶紒",
        source_text="鍢匡紝瀹濆瓙锛屼粙缁嶄竴涓嬩笁鍥芥潃瑙勫垯銆?",
    )
    runtime.on_agent_speaking_started(job_id="job-preview", segment_index=0)
    runtime.on_agent_segment_completed(job_id="job-preview", segment_index=0)
    runtime.on_agent_speaking_finished(job_id="job-preview")

    assert (
        _resolve_final_live_transcript_text(
            runtime.snapshot(),
            "鍡€?",
        )
        == "鍢匡紝瀹濆瓙锛屼粙缁嶄竴涓嬩笁鍥芥潃瑙勫垯銆?"
    )
class CountingLookaheadTtsAdapter(FakeProgressiveTtsAdapter):
    def __init__(self, tmp_dir: Path) -> None:
        super().__init__(tmp_dir)
        self.calls: list[str] = []

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        self.calls.append(text)
        return super().synthesize_segment(text, voice_id=voice_id)


class BlockingSecondChunkTtsAdapter(CountingLookaheadTtsAdapter):
    def __init__(self, tmp_dir: Path) -> None:
        super().__init__(tmp_dir)
        self.second_call_started = threading.Event()
        self.release_second_call = threading.Event()

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        call_index = len(self.calls)
        result = super().synthesize_segment(text, voice_id=voice_id)
        if call_index == 1:
            self.second_call_started.set()
            assert self.release_second_call.wait(timeout=1.0)
        return result


class FakeLookaheadContinuationDialogClient:
    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used for preview handoff")

    def stream_reply_updates(self, *args, **kwargs):
        raise AssertionError("structured stream should not be used for preview handoff continuation")

    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
    ):
        assert mode == "conversation"
        assert already_spoken_text
        yield "第一句。第二句。第三句。"


class FakeLookaheadAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeLookaheadContinuationDialogClient()},
        )()
        self.tts_adapter = CountingLookaheadTtsAdapter(tmp_dir)


class FakeBlockingLookaheadAutoInterruptService(FakeProgressiveAutoInterruptService):
    def __init__(self, tmp_dir: Path) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {"dialog_client": FakeLookaheadContinuationDialogClient()},
        )()
        self.tts_adapter = BlockingSecondChunkTtsAdapter(tmp_dir)


def test_formal_lazy_tts_keeps_only_one_chunk_of_lookahead(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Lazy Lookahead Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        deadline = time.time() + 0.3
        while time.time() < deadline and len(fake_service.tts_adapter.calls) < 2:
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]

        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=0,
        )
        main_module.dialog_runtime_store.on_agent_segment_completed(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=0,
        )

        time.sleep(0.1)
        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]

        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=1,
        )

        deadline = time.time() + 0.3
        while time.time() < deadline and len(fake_service.tts_adapter.calls) < 3:
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。", "第三句。"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_lazy_tts_waits_for_next_chunk_to_start_before_third_synth(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Lazy Lookahead Started Gate Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        deadline = time.time() + 0.3
        while time.time() < deadline and len(fake_service.tts_adapter.calls) < 2:
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]

        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=0,
        )
        main_module.dialog_runtime_store.on_agent_segment_completed(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=0,
        )

        time.sleep(0.1)
        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]

        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=1,
        )

        deadline = time.time() + 0.3
        while time.time() < deadline and len(fake_service.tts_adapter.calls) < 3:
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。", "第三句。"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_generation_accepts_plain_only_continuation_client(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Plain Only Continuation Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakePlainOnlyContinuationAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain this rule",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="我先接一句。",
            source_text="player_a: explain this rule",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["tts_stream"]["stream_id"]
        chunk = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=0.5,
        )
        assert chunk is not None
        assert chunk["audio_bytes"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_generation_strips_assistant_name_prefix_from_tts_segments(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Prefixed Continuation Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = FakePrefixedContinuationAutoInterruptService(tmp_path)

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "玩家A：宝子，继续",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="来，接着说。",
            source_text="宝子，继续",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-prefixed-continuation",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert result["interrupt"] is True
        assert result["reply"]["content"] == "第一句正式回复。"
        assert result["speech_job"]["segments"] == ["第一句正式回复。"]
        chunk = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=0.5,
        )
        assert chunk is not None
        assert chunk["text"] == "第一句正式回复。"
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_lazy_tts_drops_deferred_chunks_after_interrupt(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Lazy Interrupt Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        deadline = time.time() + 0.3
        while time.time() < deadline and len(fake_service.tts_adapter.calls) < 2:
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]

        main_module.dialog_runtime_store.on_agent_reply_interrupted(
            table_id,
            job_id=result["speech_job"]["job_id"],
        )
        main_module.tts_stream_bridge.cancel_stream(result["tts_stream"]["stream_id"])

        time.sleep(0.1)

        assert fake_service.tts_adapter.calls == ["第一句。", "第二句。"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_stream_failure_records_only_synthesized_speech_job_text(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Formal Stream Failure Text Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeFailingAfterPendingTextAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: tell me something",
            },
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        first = main_module.tts_stream_bridge.next_chunk(
            result["tts_stream"]["stream_id"],
            wait_timeout=1.0,
        )
        assert first is not None
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=first["segment_index"],
        )
        main_module.dialog_runtime_store.on_agent_segment_completed(
            table_id,
            job_id=result["speech_job"]["job_id"],
            segment_index=first["segment_index"],
        )

        deadline = time.time() + 1.0
        while time.time() < deadline and result["speech_job"]["status"] != "failed":
            time.sleep(0.01)

        assert fake_service.tts_adapter.calls == ["First sentence."]
        assert result["speech_job"]["text"] == "First sentence."
        assert "Third fragment" not in result["speech_job"]["text"]
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_lazy_tts_interrupt_drops_inflight_chunk_before_append(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Lazy Inflight Interrupt Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeBlockingLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)

        assert fake_service.tts_adapter.second_call_started.wait(timeout=1.0)
        main_module._interrupt_active_runtime_job(
            table_id,
            {"current_job_id": result["speech_job"]["job_id"]},
        )
        fake_service.tts_adapter.release_second_call.set()
        time.sleep(0.1)

        assert [segment["text"] for segment in result["speech_job"]["segment_statuses"]] == ["第一句。"]
        assert result["speech_job"]["tts_input_chunk_count"] == 1
    finally:
        main_module.auto_interrupt_service = original_service


def test_formal_generation_finished_reports_tts_input_counts(tmp_path: Path):
    client = TestClient(app)
    created = client.post("/tables", json={"name": "Formal Usage Metrics Table"})
    table_id = created.json()["id"]

    original_service = main_module.auto_interrupt_service
    fake_service = FakeLookaheadAutoInterruptService(tmp_path)
    main_module.auto_interrupt_service = fake_service

    try:
        session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "player_a: explain sanguosha rules",
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text="Rules quick guide.",
            source_text="player_a: explain sanguosha rules",
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="job-preview-1",
            segment_index=0,
        )

        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
        assert result["speech_job"]["tts_input_chars_total"] > 0
        assert result["speech_job"]["tts_input_chunk_count"] == len(result["speech_job"]["segments"])

        deadline = time.time() + 1.0
        while time.time() < deadline:
            chunk = main_module.tts_stream_bridge.next_chunk(
                result["tts_stream"]["stream_id"],
                wait_timeout=0.2,
            )
            if chunk is None:
                if result["speech_job"]["status"] == "ready":
                    break
                continue
            main_module.dialog_runtime_store.on_agent_speaking_started(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )
            main_module.dialog_runtime_store.on_agent_segment_completed(
                table_id,
                job_id=result["speech_job"]["job_id"],
                segment_index=chunk["segment_index"],
            )

        deadline = time.time() + 1.0
        finished_events = []
        while time.time() < deadline:
            runtime_events = session_manager.list_runtime_events(table_id)
            finished_events = [
                item for item in runtime_events if item.get("kind") == "assistant_formal_generation_finished"
            ]
            if finished_events:
                break
            time.sleep(0.01)
        assert finished_events
        assert finished_events[-1]["tts_input_chars_total"] == result["speech_job"]["tts_input_chars_total"]
        assert finished_events[-1]["tts_input_chunk_count"] == result["speech_job"]["tts_input_chunk_count"]
    finally:
        main_module.auto_interrupt_service = original_service
