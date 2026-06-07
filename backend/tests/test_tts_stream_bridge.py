import base64
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from gamevoice_server.main import app, session_manager
from gamevoice_server.tts_adapter import TTSAdapter
from gamevoice_server.tts_stream_bridge import TTSStreamBridge


def test_tts_stream_bridge_reads_job_segments_in_order(tmp_path: Path):
    job = TTSAdapter(output_dir=tmp_path).speak("第一句。第二句。")
    bridge = TTSStreamBridge()

    session = bridge.start_stream(job)
    chunks = []
    while True:
        item = bridge.next_chunk(session["stream_id"])
        if item is None:
            break
        chunks.append(item)

    assert session["job_id"] == job["job_id"]
    assert session["state"] == "streaming"
    assert len(chunks) == 2
    assert [chunk["segment_index"] for chunk in chunks] == [0, 1]
    assert [chunk["text"] for chunk in chunks] == job["segments"]
    rebuilt = b"".join(chunk["audio_bytes"] for chunk in chunks)
    assert rebuilt == Path(job["output_path"]).read_bytes()
    assert chunks[-1]["is_final"] is True
    assert bridge.snapshot(session["stream_id"])["state"] == "completed"


def test_tts_stream_bridge_cancel_stops_future_chunks(tmp_path: Path):
    job = TTSAdapter(output_dir=tmp_path).speak("只测试取消。")
    bridge = TTSStreamBridge()

    session = bridge.start_stream(job)
    first = bridge.next_chunk(session["stream_id"])
    bridge.cancel_stream(session["stream_id"])
    second = bridge.next_chunk(session["stream_id"])

    assert first is not None
    assert second is None
    assert bridge.snapshot(session["stream_id"])["state"] == "cancelled"


def test_tts_stream_bridge_can_wait_for_future_chunk():
    bridge = TTSStreamBridge()
    stream = bridge.open_stream(
        job_id="job-live-1",
        turn_id="turn-1",
        reply_id="reply-1",
        segment_count=2,
    )

    def producer() -> None:
        time.sleep(0.05)
        bridge.append_chunk(
            stream["stream_id"],
            segment_index=0,
            text="先接一句。",
            audio_bytes=b"\x01\x02",
        )
        bridge.finish_stream(stream["stream_id"])

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    chunk = bridge.next_chunk(stream["stream_id"], wait_timeout=1.0)
    worker.join(timeout=1.0)

    assert chunk is not None
    assert chunk["job_id"] == "job-live-1"
    assert chunk["segment_index"] == 0
    assert chunk["text"] == "先接一句。"
    assert chunk["audio_bytes"] == b"\x01\x02"
    assert chunk["is_final"] is True


def test_tts_stream_api_can_start_and_read_chunks():
    client = TestClient(app)
    created = client.post("/tables", json={"name": "TTS Stream Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )
    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/stream")

    assert started.status_code == 200
    stream_id = started.json()["stream_id"]
    assert stream_id

    first = client.get(f"/tables/{table_id}/tts-streams/{stream_id}/next")
    assert first.status_code == 200
    payload = first.json()
    assert payload["job_id"] == job_id
    assert payload["stream_id"] == stream_id
    assert payload["segment_index"] == 0
    assert payload["text"]
    assert base64.b64decode(payload["audio_base64"])

    cancelled = client.post(f"/tables/{table_id}/tts-streams/{stream_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"

    after_cancel = client.get(f"/tables/{table_id}/tts-streams/{stream_id}/next")
    assert after_cancel.status_code == 404
