from gamevoice_server.dialog_runtime import DialogRuntime
from gamevoice_server.dialog_runtime_store import DialogRuntimeStore


def test_dialog_runtime_interrupts_agent_when_user_starts_talking():
    runtime = DialogRuntime()
    runtime.on_agent_reply_ready()
    runtime.on_agent_speaking_started()

    result = runtime.on_user_audio()

    assert result["interrupted"] is True
    assert result["state"] == "interrupted"
    assert result["is_user_speaking"] is True
    assert result["is_agent_speaking"] is False


def test_dialog_runtime_advances_from_user_turn_to_agent_speaking():
    runtime = DialogRuntime()

    runtime.on_user_audio()
    runtime.on_user_turn_committed()
    runtime.on_agent_reply_ready(job_id="job-1", reply_text="first reply", segment_count=2)
    result = runtime.on_agent_speaking_started(job_id="job-1", segment_index=0)

    assert result["state"] == "agent_speaking"
    assert result["is_user_speaking"] is False
    assert result["is_agent_speaking"] is True
    assert result["current_job_id"] == "job-1"
    assert result["current_segment_index"] == 0


def test_dialog_runtime_priority_reply_overrides_user_turn_and_protects_first_segment():
    runtime = DialogRuntime()

    runtime.on_user_audio()
    ready = runtime.on_priority_agent_reply_ready(
        job_id="job-priority",
        reply_text="looked up answer",
        segment_count=2,
        barge_in_grace_seconds=2.5,
        now_monotonic=100.0,
    )
    started = runtime.on_agent_speaking_started(job_id="job-priority", segment_index=0)

    assert ready["state"] == "assistant_ready"
    assert ready["is_user_speaking"] is False
    assert ready["current_job_id"] == "job-priority"
    assert ready["priority_reply_job_id"] == "job-priority"
    assert started["barge_in_protected"] is True
    assert runtime.is_barge_in_protected(now_monotonic=101.0) is True

    runtime.on_agent_segment_completed(job_id="job-priority", segment_index=0)

    assert runtime.is_barge_in_protected(now_monotonic=103.0) is False


def test_dialog_runtime_returns_to_listening_when_agent_finishes():
    runtime = DialogRuntime()
    runtime.on_agent_reply_ready(job_id="job-1", reply_text="first reply", segment_count=2)
    runtime.on_agent_speaking_started(job_id="job-1", segment_index=0)

    result = runtime.on_agent_speaking_finished(job_id="job-1")

    assert result["state"] == "listening"
    assert result["is_user_speaking"] is False
    assert result["is_agent_speaking"] is False
    assert result["current_job_id"] is None
    assert result["last_completed_job_id"] == "job-1"
    assert result["queue_depth"] == 0


def test_dialog_runtime_returns_to_listening_when_agent_skips_reply():
    runtime = DialogRuntime()
    runtime.on_user_audio()
    runtime.on_user_turn_committed()
    runtime.on_agent_preview_ready(
        reply_text="preview",
        source_text="source",
        stream_id="stream-1",
        job_id="job-preview",
        lookup_marker=True,
    )
    runtime.set_pending_formal_text(
        "formal",
        source_text="source",
        preview_text="preview",
        preview_job_id="job-preview",
    )

    result = runtime.on_agent_reply_skipped()

    assert result["state"] == "listening"
    assert result["last_event"] == "agent_reply_skipped"
    assert result["is_user_speaking"] is False
    assert result["is_agent_speaking"] is False
    assert result["current_job_id"] is None
    assert result["pending_reply_text"] is None
    assert result["pending_source_text"] is None
    assert result["preview_reply_text"] is None
    assert result["preview_source_text"] is None
    assert result["preview_stream_id"] is None
    assert result["preview_job_id"] is None
    assert result["preview_lookup_marker"] is False
    assert result["pending_formal_text"] is None
    assert result["queue_depth"] == 0


def test_dialog_runtime_exposes_assistant_ready_state_before_playback():
    runtime = DialogRuntime()

    result = runtime.on_agent_reply_ready(
        job_id="job-1",
        reply_text="first reply",
        source_text="player_a: first reply request",
        segment_count=2,
    )

    assert result["state"] == "assistant_ready"
    assert result["pending_reply_text"] == "first reply"
    assert result["pending_source_text"] == "player_a: first reply request"
    assert result["current_job_id"] == "job-1"
    assert result["queue_depth"] == 2
    assert result["current_segment_index"] is None


def test_dialog_runtime_tracks_segment_progress():
    runtime = DialogRuntime()

    runtime.on_agent_reply_ready(job_id="job-1", reply_text="first reply", segment_count=2)
    started = runtime.on_agent_speaking_started(job_id="job-1", segment_index=0)
    progressed = runtime.on_agent_segment_completed(job_id="job-1", segment_index=0)
    finished = runtime.on_agent_speaking_finished(job_id="job-1")

    assert started["current_segment_index"] == 0
    assert started["queue_depth"] == 2
    assert progressed["completed_segment_count"] == 1
    assert progressed["queue_depth"] == 1
    assert finished["queue_depth"] == 0
    assert finished["current_segment_index"] is None


def test_dialog_runtime_ignores_late_started_update_after_job_finished():
    runtime = DialogRuntime()

    runtime.on_agent_reply_ready(job_id="job-1", reply_text="first reply", segment_count=2)
    runtime.on_agent_speaking_started(job_id="job-1", segment_index=0)
    runtime.on_agent_speaking_finished(job_id="job-1")

    result = runtime.on_agent_speaking_started(job_id="job-1", segment_index=1)

    assert result["state"] == "listening"
    assert result["is_agent_speaking"] is False
    assert result["current_job_id"] is None
    assert result["last_completed_job_id"] == "job-1"
    assert result["last_event"] == "stale_agent_speaking_started"


def test_dialog_runtime_ignores_stale_preview_completion_after_formal_reply_ready():
    runtime = DialogRuntime()

    runtime.on_agent_preview_ready(reply_text="preview", source_text="preview source")
    runtime.on_agent_speaking_started(job_id="job-preview", segment_index=0)
    runtime.on_agent_reply_ready(
        job_id="job-formal",
        reply_text="formal reply",
        source_text="formal source",
        segment_count=2,
    )

    progressed = runtime.on_agent_segment_completed(job_id="job-preview", segment_index=0)
    finished = runtime.on_agent_speaking_finished(job_id="job-preview")

    assert progressed["state"] == "assistant_ready"
    assert progressed["current_job_id"] == "job-formal"
    assert progressed["pending_reply_text"] == "formal reply"
    assert progressed["pending_source_text"] == "formal source"
    assert progressed["queue_depth"] == 2
    assert finished["state"] == "assistant_ready"
    assert finished["current_job_id"] == "job-formal"
    assert finished["pending_reply_text"] == "formal reply"
    assert finished["pending_source_text"] == "formal source"
    assert finished["last_completed_job_id"] == "job-preview"


def test_dialog_runtime_clears_pending_source_when_agent_finishes():
    runtime = DialogRuntime()

    runtime.on_agent_reply_ready(
        job_id="job-1",
        reply_text="first reply",
        source_text="player_a: first reply request",
        segment_count=1,
    )
    runtime.on_agent_speaking_started(job_id="job-1", segment_index=0)

    finished = runtime.on_agent_speaking_finished(job_id="job-1")

    assert finished["pending_source_text"] is None


def test_dialog_runtime_store_accepts_preview_stream_metadata():
    store = DialogRuntimeStore()

    snapshot = store.on_agent_preview_ready(
        "table-1",
        reply_text="preview",
        source_text="source",
        stream_id="stream-1",
        job_id="job-1",
    )

    assert snapshot["preview_reply_text"] == "preview"
    assert snapshot["preview_source_text"] == "source"
    assert snapshot["preview_stream_id"] == "stream-1"
    assert snapshot["preview_job_id"] == "job-1"


def test_dialog_runtime_store_sets_pending_formal_text():
    store = DialogRuntimeStore()

    store.set_pending_formal_text(
        "table-1",
        "formal continuation",
        source_text="what should I do now",
        preview_text="preview",
        preview_job_id="job-preview",
    )

    snapshot = store.snapshot("table-1")
    assert snapshot["pending_formal_text"] == "formal continuation"
    assert snapshot["pending_formal_source_text"] == "what should I do now"
    assert snapshot["pending_formal_preview_text"] == "preview"
    assert snapshot["pending_formal_preview_job_id"] == "job-preview"
