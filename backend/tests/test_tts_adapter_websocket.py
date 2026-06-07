import asyncio
import json
from pathlib import Path

from gamevoice_server.config import Settings
from gamevoice_server.tts_adapter import MiniMaxWebSocketTTSAdapter, build_tts_adapter


class FakeMiniMaxWebSocketSession:
    def __init__(self, *, responses: list[dict]):
        self._responses = list(responses)
        self.sent_messages: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent_messages.append(json.loads(payload))

    async def recv(self) -> str:
        if not self._responses:
            raise AssertionError("unexpected recv with no queued response")
        return json.dumps(self._responses.pop(0), ensure_ascii=False)

    async def close(self) -> None:
        self.closed = True


def test_minimax_websocket_tts_adapter_streams_segments_in_protocol_order(tmp_path: Path):
    sentence_break = "\u3002"
    session = FakeMiniMaxWebSocketSession(
        responses=[
            {"event": "connected_success"},
            {"event": "task_started"},
            {
                "event": "task_continued",
                "data": {"audio": "0102"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {
                "event": "task_continued",
                "data": {"audio": "0304"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {"event": "task_finished"},
        ]
    )

    adapter = MiniMaxWebSocketTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        voice_id="voice-1",
        model="speech-2.8-hd",
        base_url="wss://example.test/ws",
        connect_timeout_seconds=4.0,
        connect_ws=lambda url, headers, timeout: session,
        job_id_provider=lambda: "ws-job-1",
        async_runner=lambda coro: asyncio.run(coro),
    )

    job = adapter.speak(
        f"Lead{sentence_break}Tail{sentence_break}",
        reply={
            "source": "minimax",
            "lead": f"Lead{sentence_break}",
            "tail": f"Tail{sentence_break}",
            "content": f"Lead{sentence_break}Tail{sentence_break}",
        },
    )

    assert [message["event"] for message in session.sent_messages] == [
        "task_start",
        "task_continue",
        "task_continue",
        "task_finish",
    ]
    assert session.sent_messages[0]["model"] == "speech-2.8-hd"
    assert session.sent_messages[0]["voice_setting"]["voice_id"] == "voice-1"
    assert [message["text"] for message in session.sent_messages[1:3]] == [
        f"Lead{sentence_break}",
        f"Tail{sentence_break}",
    ]
    assert job["job_id"] == "ws-job-1"
    assert job["segments"] == [f"Lead{sentence_break}", f"Tail{sentence_break}"]
    assert Path(job["output_path"]).read_bytes() == b"\x01\x02\x03\x04"
    assert [Path(item["output_path"]).read_bytes() for item in job["segment_statuses"]] == [
        b"\x01\x02",
        b"\x03\x04",
    ]
    assert session.closed is True


def test_minimax_websocket_tts_adapter_aggregates_multiple_chunks_until_final(tmp_path: Path):
    sentence_break = "\u3002"
    session = FakeMiniMaxWebSocketSession(
        responses=[
            {"event": "connected_success"},
            {"event": "task_started"},
            {
                "event": "task_continued",
                "data": {"audio": "0102"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": False,
            },
            {
                "event": "task_continued",
                "data": {"audio": "0304"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {
                "event": "task_continued",
                "data": {"audio": "0506"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": False,
            },
            {
                "event": "task_continued",
                "data": {"audio": "0708"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {"event": "task_finished"},
        ]
    )

    adapter = MiniMaxWebSocketTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        voice_id="voice-1",
        model="speech-2.8-hd",
        base_url="wss://example.test/ws",
        connect_timeout_seconds=4.0,
        connect_ws=lambda url, headers, timeout: session,
        job_id_provider=lambda: "ws-job-2",
        async_runner=lambda coro: asyncio.run(coro),
    )

    job = adapter.speak(
        f"Lead{sentence_break}Tail{sentence_break}",
        reply={
            "source": "minimax",
            "lead": f"Lead{sentence_break}",
            "tail": f"Tail{sentence_break}",
            "content": f"Lead{sentence_break}Tail{sentence_break}",
        },
    )

    assert [message["event"] for message in session.sent_messages] == [
        "task_start",
        "task_continue",
        "task_continue",
        "task_finish",
    ]
    assert Path(job["output_path"]).read_bytes() == b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert [Path(item["output_path"]).read_bytes() for item in job["segment_statuses"]] == [
        b"\x01\x02\x03\x04",
        b"\x05\x06\x07\x08",
    ]
    assert session.closed is True


def test_minimax_websocket_tts_adapter_allows_empty_final_chunk_after_audio(tmp_path: Path):
    sentence_break = "\u3002"
    session = FakeMiniMaxWebSocketSession(
        responses=[
            {"event": "connected_success"},
            {"event": "task_started"},
            {
                "event": "task_continued",
                "data": {"audio": "0102"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": False,
            },
            {
                "event": "task_continued",
                "data": {"audio": ""},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {"event": "task_finished"},
        ]
    )

    adapter = MiniMaxWebSocketTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        voice_id="voice-1",
        model="speech-2.8-hd",
        base_url="wss://example.test/ws",
        connect_timeout_seconds=4.0,
        connect_ws=lambda url, headers, timeout: session,
        job_id_provider=lambda: "ws-job-3",
        async_runner=lambda coro: asyncio.run(coro),
    )

    job = adapter.speak(
        f"Lead{sentence_break}",
        reply={
            "source": "minimax",
            "lead": f"Lead{sentence_break}",
            "tail": "",
            "content": f"Lead{sentence_break}",
        },
    )

    assert Path(job["output_path"]).read_bytes() == b"\x01\x02"
    assert [Path(item["output_path"]).read_bytes() for item in job["segment_statuses"]] == [b"\x01\x02"]
    assert session.closed is True


def test_minimax_websocket_synthesize_segment_uses_override_voice_id(tmp_path: Path):
    session = FakeMiniMaxWebSocketSession(
        responses=[
            {"event": "connected_success"},
            {"event": "task_started"},
            {
                "event": "task_continued",
                "data": {"audio": "0102"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {"event": "task_finished"},
        ]
    )

    adapter = MiniMaxWebSocketTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        voice_id="default-voice",
        model="speech-2.8-hd",
        base_url="wss://example.test/ws",
        connect_timeout_seconds=4.0,
        connect_ws=lambda url, headers, timeout: session,
        job_id_provider=lambda: "ws-job-override",
        async_runner=lambda coro: asyncio.run(coro),
    )

    result = adapter.synthesize_segment("hello", voice_id="custom-voice")

    assert result["audio_bytes"] == b"\x01\x02"
    assert [message["event"] for message in session.sent_messages] == [
        "task_start",
        "task_continue",
        "task_finish",
    ]
    assert session.sent_messages[0]["voice_setting"]["voice_id"] == "custom-voice"
    assert session.closed is True


def test_minimax_websocket_synthesize_segment_uses_default_voice_for_blank_override(tmp_path: Path):
    session = FakeMiniMaxWebSocketSession(
        responses=[
            {"event": "connected_success"},
            {"event": "task_started"},
            {
                "event": "task_continued",
                "data": {"audio": "0102"},
                "extra_info": {"audio_format": "mp3"},
                "is_final": True,
            },
            {"event": "task_finished"},
        ]
    )

    adapter = MiniMaxWebSocketTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        voice_id="default-voice",
        model="speech-2.8-hd",
        base_url="wss://example.test/ws",
        connect_timeout_seconds=4.0,
        connect_ws=lambda url, headers, timeout: session,
        job_id_provider=lambda: "ws-job-default-voice",
        async_runner=lambda coro: asyncio.run(coro),
    )

    result = adapter.synthesize_segment("hello", voice_id="")

    assert result["audio_bytes"] == b"\x01\x02"
    assert session.sent_messages[0]["voice_setting"]["voice_id"] == "default-voice"
    assert session.closed is True


def test_build_tts_adapter_returns_websocket_adapter_when_api_key_present(tmp_path: Path):
    settings = Settings(
        minimax_api_key="secret",
        minimax_tts_output_dir=str(tmp_path),
    )

    adapter = build_tts_adapter(settings)

    assert isinstance(adapter, MiniMaxWebSocketTTSAdapter)
