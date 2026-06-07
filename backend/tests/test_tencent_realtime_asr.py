import asyncio
from urllib.parse import parse_qs, urlparse

from gamevoice_server.tencent_realtime_asr import (
    PcmChunkBuffer,
    TencentRealtimeAsrConnection,
    TencentRealtimeAsrSession,
    _normalize_realtime_message,
)


def test_pcm_chunk_buffer_emits_target_sized_chunks_and_flushes_tail():
    buffer = PcmChunkBuffer(target_size=4)

    first = buffer.push(b"ab")
    second = buffer.push(b"cdefg")
    tail = buffer.flush()

    assert first == []
    assert second == [b"abcd"]
    assert tail == b"efg"


def test_tencent_realtime_connection_builds_signed_url():
    connection = TencentRealtimeAsrConnection(
        app_id="12345",
        secret_id="secret-id",
        secret_key="secret-key",
        engine_model_type="16k_zh",
        need_vad=1,
        speaker_diarization=1,
        voice_format=1,
        expired_seconds=3600,
        enable_speaker_context=1,
        speaker_context_id="ctx-123",
        timestamp_provider=lambda: 1_700_000_000,
        nonce_provider=lambda: 42,
    )

    url = connection.build_url(voice_id="voice-123")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "wss"
    assert parsed.netloc == "asr.cloud.tencent.com"
    assert parsed.path == "/asr/v2/12345"
    assert params["engine_model_type"] == ["16k_zh"]
    assert params["needvad"] == ["1"]
    assert params["speaker_diarization"] == ["1"]
    assert params["voice_format"] == ["1"]
    assert params["secretid"] == ["secret-id"]
    assert params["timestamp"] == ["1700000000"]
    assert params["expired"] == ["1700003600"]
    assert params["nonce"] == ["42"]
    assert params["voice_id"] == ["voice-123"]
    assert params["enable_speaker_context"] == ["1"]
    assert params["speaker_context_id"] == ["ctx-123"]
    assert "signature" in params


def test_tencent_realtime_session_sends_keepalive_silence_when_idle():
    class DummyWs:
        def __init__(self) -> None:
            self.sent: list[bytes | str] = []

        async def send(self, payload):
            self.sent.append(payload)

    async def scenario():
        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=8,
            keepalive_seconds=0.01,
        )
        session._ws = DummyWs()
        session._last_sent_at -= 1.0
        keepalive_task = asyncio.create_task(session._keepalive_loop())
        await asyncio.sleep(0.03)
        session._closed = True
        await keepalive_task
        return session._ws.sent

    sent = asyncio.run(scenario())
    silence_payloads = [item for item in sent if isinstance(item, bytes)]
    assert silence_payloads
    assert silence_payloads[0] == b"\x00" * 8


def test_tencent_realtime_session_paces_pcm_chunks_at_realtime_rate_by_default():
    class DummyWs:
        def __init__(self) -> None:
            self.sent: list[bytes | str] = []

        async def send(self, payload):
            self.sent.append(payload)

    async def scenario():
        now = 100.0
        sleep_calls: list[float] = []

        def monotonic() -> float:
            return now

        async def sleep(seconds: float) -> None:
            nonlocal now
            sleep_calls.append(seconds)
            now += seconds

        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=6400,
            keepalive_seconds=0,
            audio_bytes_per_second=32000,
            monotonic_provider=monotonic,
            sleep=sleep,
        )
        session._ws = DummyWs()

        await session.send_audio(b"a" * 12800)

        return session._ws.sent, sleep_calls

    sent, sleep_calls = asyncio.run(scenario())

    assert sent == [b"a" * 6400, b"a" * 6400]
    assert sleep_calls == [6400 / 32000]


def test_tencent_realtime_session_records_pacing_requested_and_actual_ms():
    class DummyWs:
        def __init__(self) -> None:
            self.sent: list[bytes | str] = []

        async def send(self, payload):
            self.sent.append(payload)

    async def scenario():
        now = 100.0

        def monotonic() -> float:
            return now

        async def sleep(seconds: float) -> None:
            nonlocal now
            now += seconds + 4.5

        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=6400,
            keepalive_seconds=0,
            audio_bytes_per_second=32000,
            monotonic_provider=monotonic,
            sleep=sleep,
        )
        session._ws = DummyWs()

        await session.send_audio(b"a" * 12800)

        return (
            session.last_pacing_requested_ms,
            session.last_pacing_actual_ms,
            session.max_pacing_actual_ms,
        )

    requested_ms, actual_ms, max_actual_ms = asyncio.run(scenario())

    assert requested_ms == 200.0
    assert actual_ms == 4700.0
    assert max_actual_ms == 4700.0


def test_tencent_realtime_session_records_payload_send_elapsed_ms():
    class DummyWs:
        def __init__(self, advance_time) -> None:
            self.sent: list[bytes | str] = []
            self._advance_time = advance_time

        async def send(self, payload):
            self.sent.append(payload)
            self._advance_time(0.012)

    async def scenario():
        now = 100.0

        def monotonic() -> float:
            return now

        def advance_time(seconds: float) -> None:
            nonlocal now
            now += seconds

        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=4,
            keepalive_seconds=0,
            audio_bytes_per_second=0,
            monotonic_provider=monotonic,
        )
        session._ws = DummyWs(advance_time)

        await session.send_audio(b"abcd")

        return session.last_payload_send_elapsed_ms, session.max_payload_send_elapsed_ms

    elapsed_ms, max_elapsed_ms = asyncio.run(scenario())

    assert elapsed_ms == 12.0
    assert max_elapsed_ms == 12.0


def test_tencent_realtime_message_normalizer_extracts_speaker_sentence():
    message = {
        "result": {
            "speaker_context_id": "ctx-abc",
            "sentences": [
                {
                    "sentence": "大家好",
                    "sentence_type": 0,
                    "sentence_id": 2,
                    "speaker_id": 3,
                    "start_time": 1200,
                    "end_time": 2450,
                },
                {
                    "sentence": "我先来",
                    "sentence_type": 1,
                    "sentence_id": 3,
                    "speaker_id": 3,
                    "start_time": 2450,
                    "end_time": 3610,
                },
            ]
        }
    }

    event = _normalize_realtime_message(message)

    assert event is not None
    assert event["event"] == "final"
    assert event["slice_type"] == 2
    assert event["index"] == 3
    assert event["text"] == "我先来"
    assert event["speaker_id"] == 3
    assert event["speaker_label"] == "speaker_3"
    assert event["speaker_context_id"] == "ctx-abc"


def test_tencent_realtime_message_normalizer_keeps_final_sentence_payload():
    message = {
        "final": 1,
        "result": {
            "speaker_context_id": "ctx-final",
            "sentences": [
                {
                    "sentence": "最后一句话",
                    "sentence_type": 1,
                    "sentence_id": 7,
                    "speaker_id": 2,
                    "start_time": 5000,
                    "end_time": 6123,
                }
            ],
        },
    }

    event = _normalize_realtime_message(message)

    assert event is not None
    assert event["event"] == "final"
    assert event["text"] == "最后一句话"
    assert event["speaker_id"] == 2
    assert event["speaker_label"] == "speaker_2"
    assert event["speaker_context_id"] == "ctx-final"


def test_tencent_realtime_message_normalizer_supports_top_level_sentences():
    message = {
        "final": 1,
        "speaker_context_id": "ctx-top",
        "sentences": [
            {
                "sentence": "hello world",
                "sentence_type": 1,
                "sentence_id": 9,
                "speaker_id": 4,
                "start_time": 7000,
                "end_time": 8123,
            }
        ],
    }

    event = _normalize_realtime_message(message)

    assert event is not None
    assert event["event"] == "final"
    assert event["text"] == "hello world"
    assert event["speaker_id"] == 4
    assert event["speaker_label"] == "speaker_4"
    assert event["speaker_context_id"] == "ctx-top"


def test_tencent_realtime_message_normalizer_supports_sentence_list_container():
    message = {
        "code": 0,
        "message": "success",
        "voice_id": "voice-1",
        "message_id": "voice-1_11_0",
        "sentences": {
            "sentence_list": [
                {
                    "sentence": "拿走拿走",
                    "sentence_type": 1,
                    "sentence_id": 11,
                    "speaker_id": 2,
                    "start_time": 39050,
                    "end_time": 40800,
                }
            ]
        },
    }

    event = _normalize_realtime_message(message)

    assert event is not None
    assert event["event"] == "final"
    assert event["text"] == "拿走拿走"
    assert event["speaker_id"] == 2
    assert event["speaker_label"] == "speaker_2"


def test_tencent_realtime_message_normalizer_treats_sentence_type_one_as_utterance_final():
    message = {
        "code": 0,
        "message": "success",
        "voice_id": "voice-1",
        "message_id": "voice-1_27_0",
        "sentences": {
            "sentence_list": [
                {
                    "sentence": "宝子介绍三国杀的规则。",
                    "sentence_type": 1,
                    "sentence_id": 27,
                    "speaker_id": 0,
                    "start_time": 210900,
                    "end_time": 213050,
                }
            ]
        },
    }

    event = _normalize_realtime_message(message)

    assert event is not None
    assert event["event"] == "final"
    assert event["slice_type"] == 2
    assert event["index"] == 27
    assert event["text"] == "宝子介绍三国杀的规则。"


def test_tencent_realtime_session_forwards_final_payload_with_text():
    class DummyWs:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)
            self.sent: list[bytes | str] = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._payloads)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            self.sent.append(payload)

        async def close(self):
            return None

    async def scenario():
        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=8,
            keepalive_seconds=0,
        )
        session._ws = DummyWs(
            [
                '{"final": 1, "speaker_context_id": "ctx-stream", "sentences": [{"sentence": "stream final", "sentence_type": 1, "sentence_id": 12, "speaker_id": 8, "start_time": 1200, "end_time": 2450}]}'
            ]
        )
        pump_task = asyncio.create_task(session._pump_events())
        event = await session.receive_event()
        await pump_task
        return event

    event = asyncio.run(scenario())

    assert event["event"] == "final"
    assert event["text"] == "stream final"
    assert event["speaker_id"] == 8
    assert event["speaker_context_id"] == "ctx-stream"


def test_tencent_realtime_session_keeps_stream_open_after_utterance_final():
    class DummyWs:
        def __init__(self, payloads: list[str]) -> None:
            self._payloads = iter(payloads)
            self.sent: list[bytes | str] = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._payloads)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            self.sent.append(payload)

        async def close(self):
            return None

    async def scenario():
        connection = TencentRealtimeAsrConnection(
            app_id="12345",
            secret_id="secret-id",
            secret_key="secret-key",
            engine_model_type="16k_zh",
            need_vad=1,
            speaker_diarization=1,
            voice_format=1,
            expired_seconds=3600,
            timestamp_provider=lambda: 1_700_000_000,
            nonce_provider=lambda: 42,
        )
        session = TencentRealtimeAsrSession(
            connection=connection,
            chunk_bytes=8,
            keepalive_seconds=0,
        )
        session._ws = DummyWs(
            [
                '{"sentences": {"sentence_list": [{"sentence": "first done", "sentence_type": 1, "sentence_id": 1, "speaker_id": 0}]}}',
                '{"sentences": {"sentence_list": [{"sentence": "next draft", "sentence_type": 0, "sentence_id": 2, "speaker_id": -1}]}}',
                '{"final": 1}',
            ]
        )
        pump_task = asyncio.create_task(session._pump_events())
        first = await asyncio.wait_for(session.receive_event(), timeout=0.5)
        second = await asyncio.wait_for(session.receive_event(), timeout=0.5)
        third = await asyncio.wait_for(session.receive_event(), timeout=0.5)
        await pump_task
        return first, second, third

    first, second, third = asyncio.run(scenario())

    assert first["event"] == "final"
    assert first["text"] == "first done"
    assert not first["stream_final"]
    assert second["event"] == "transcript"
    assert second["text"] == "next draft"
    assert third["event"] == "final"
    assert third["stream_final"]
