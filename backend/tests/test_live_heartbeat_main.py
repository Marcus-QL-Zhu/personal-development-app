from __future__ import annotations

import gamevoice_server.main as main_module
from gamevoice_server.live_heartbeat import LiveHeartbeatScheduler


class SequenceRandom:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def uniform(self, minimum: float, maximum: float) -> float:
        return self.values.pop(0)


def test_run_live_heartbeat_uses_reliable_player_names_and_prepares_tts_stream():
    table = main_module.session_manager.start_table("Heartbeat Probe", assistant_name="宝子")
    table_id = table.id
    main_module.dialog_runtime_store.ensure_table(table_id)
    main_module.session_manager.link_speaker_identity(table_id, "speaker_0", "蛙爷")
    main_module.session_manager.link_speaker_identity(table_id, "speaker_1", "教主")

    result = main_module._run_live_heartbeat_for_table(table_id)

    assert result["interrupt"] is True
    assert result["decision_reason"] == "heartbeat"
    assert result["assistant_event"]["kind"] == "assistant_heartbeat"
    assert result["assistant_event"]["source"] == "companion_heartbeat"
    assert result["speech_job"]["accepted"] is True
    assert result["tts_stream"]["stream_id"]
    assert main_module.dialog_runtime_store.snapshot(table_id)["state"] == "assistant_ready"
    assert main_module.session_manager.list_assistant_replies(table_id)[-1]["kind"] == "assistant_heartbeat"


def test_run_live_heartbeat_marks_reply_barge_in_protected():
    table = main_module.session_manager.start_table("Heartbeat Barge In Grace", assistant_name="瀹濆瓙")
    table_id = table.id
    main_module.dialog_runtime_store.ensure_table(table_id)

    result = main_module._run_live_heartbeat_for_table(table_id)
    runtime = main_module.dialog_runtime_store.snapshot(table_id)

    assert runtime["priority_reply_job_id"] == result["speech_job"]["job_id"]
    assert runtime["barge_in_protected"] is True


def test_run_live_heartbeat_can_call_group_without_reliable_player_names():
    table = main_module.session_manager.start_table("Heartbeat Fallback", assistant_name="宝子")
    table_id = table.id
    main_module.dialog_runtime_store.ensure_table(table_id)

    result = main_module._run_live_heartbeat_for_table(table_id)

    assert result["interrupt"] is True
    assert "宝宝们" in result["reply"]["content"]


def test_tts_segment_started_resets_live_heartbeat_scheduler(monkeypatch):
    scheduler = LiveHeartbeatScheduler(
        min_seconds=180,
        max_seconds=300,
        rng=SequenceRandom([240, 210]),
    )
    monkeypatch.setattr(main_module, "live_heartbeat_scheduler", scheduler)
    table = main_module.session_manager.start_table("Heartbeat Reset", assistant_name="宝子")
    table_id = table.id
    main_module.dialog_runtime_store.ensure_table(table_id)
    scheduler.on_listening_started(table_id, now_monotonic=1000)
    scheduler.mark_inflight(table_id)
    result = main_module._run_live_heartbeat_for_table(table_id)
    job_id = result["speech_job"]["job_id"]

    response = main_module.mark_tts_segment_started(table_id, job_id, 0)

    assert response["segment"]["status"] == "playing"
    snapshot = scheduler.snapshot(table_id)
    assert snapshot["inflight"] is False
    assert snapshot["deadline_monotonic"] is not None
