import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from uuid import uuid4

from .config import Settings

logger = logging.getLogger(__name__)


def _describe_connection_closed(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None)
    return f"{exc.__class__.__name__}(code={code!r}, reason={reason!r})"


def _is_connection_closed_error(exc: Exception) -> bool:
    return exc.__class__.__name__.startswith("ConnectionClosed")


class RealtimeAsrSession(Protocol):
    async def connect(self) -> None:
        ...

    async def send_audio(self, chunk: bytes) -> None:
        ...

    async def receive_event(self):
        ...

    async def end(self) -> None:
        ...

    async def close(self) -> None:
        ...


class MissingRealtimeAsrSession:
    async def connect(self) -> None:
        logger.error("MissingRealtimeAsrSession connect called without Tencent realtime config")
        raise RuntimeError("Tencent realtime ASR is not configured")

    async def send_audio(self, chunk: bytes) -> None:
        raise RuntimeError("Tencent realtime ASR is not configured")

    async def receive_event(self):
        return {"event": "error", "message": "Tencent realtime ASR is not configured"}

    async def end(self) -> None:
        return None

    async def close(self) -> None:
        return None


@dataclass
class PcmChunkBuffer:
    target_size: int

    def __post_init__(self) -> None:
        self._buffer = bytearray()

    def push(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        ready: list[bytes] = []
        while len(self._buffer) >= self.target_size:
            ready.append(bytes(self._buffer[: self.target_size]))
            del self._buffer[: self.target_size]
        return ready

    def flush(self) -> bytes:
        tail = bytes(self._buffer)
        self._buffer.clear()
        return tail


class TencentRealtimeAsrConnection:
    endpoint = "wss://asr.cloud.tencent.com/asr/v2"
    host = "asr.cloud.tencent.com"

    def __init__(
        self,
        *,
        app_id: str,
        secret_id: str,
        secret_key: str,
        engine_model_type: str,
        need_vad: int,
        speaker_diarization: int,
        voice_format: int,
        expired_seconds: int,
        enable_speaker_context: int = 0,
        speaker_context_id: str | None = None,
        timestamp_provider=None,
        nonce_provider=None,
    ) -> None:
        self.app_id = app_id
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.engine_model_type = engine_model_type
        self.need_vad = need_vad
        self.speaker_diarization = speaker_diarization
        self.voice_format = voice_format
        self.expired_seconds = expired_seconds
        self.enable_speaker_context = enable_speaker_context
        self.speaker_context_id = speaker_context_id
        self._timestamp_provider = timestamp_provider or (lambda: int(time.time()))
        self._nonce_provider = nonce_provider or (lambda: int(time.time() * 1000) % 10_000_000_000)

    def build_url(self, *, voice_id: str, speaker_context_id: str | None = None) -> str:
        timestamp = self._timestamp_provider()
        params = {
            "engine_model_type": self.engine_model_type,
            "expired": timestamp + self.expired_seconds,
            "needvad": self.need_vad,
            "nonce": self._nonce_provider(),
            "enable_speaker_context": self.enable_speaker_context,
            "speaker_diarization": self.speaker_diarization,
            "secretid": self.secret_id,
            "timestamp": timestamp,
            "voice_format": self.voice_format,
            "voice_id": voice_id,
        }
        resolved_speaker_context_id = speaker_context_id or self.speaker_context_id
        if resolved_speaker_context_id:
            params["speaker_context_id"] = resolved_speaker_context_id
        sorted_items = sorted((key, str(value)) for key, value in params.items())
        query = urlencode(sorted_items)
        signature_source = f"{self.host}/asr/v2/{self.app_id}?{query}"
        digest = hmac.new(
            self.secret_key.encode("utf-8"),
            signature_source.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(digest).decode("utf-8")
        return f"{self.endpoint}/{self.app_id}?{query}&signature={urlencode({'signature': signature})[10:]}"


class TencentRealtimeAsrSession:
    def __init__(
        self,
        *,
        connection: TencentRealtimeAsrConnection,
        chunk_bytes: int,
        speaker_context_id: str | None = None,
        keepalive_seconds: float = 4.0,
        audio_bytes_per_second: int = 32000,
        pacing_headroom: float = 1.0,
        monotonic_provider=None,
        sleep=None,
    ) -> None:
        self.connection = connection
        self.chunk_buffer = PcmChunkBuffer(target_size=chunk_bytes)
        self.chunk_bytes = chunk_bytes
        self.speaker_context_id = speaker_context_id
        self.keepalive_seconds = keepalive_seconds
        self.audio_bytes_per_second = audio_bytes_per_second
        self.pacing_headroom = max(1.0, pacing_headroom)
        self._monotonic = monotonic_provider or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self.voice_id = str(uuid4())
        self._ws = None
        self._receiver_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._events: asyncio.Queue[dict | None] = asyncio.Queue()
        self._closed = False
        self._ending = False
        self._last_sent_at = 0.0
        self.last_payload_send_elapsed_ms: float | None = None
        self.max_payload_send_elapsed_ms: float | None = None
        self.last_pacing_requested_ms: float | None = None
        self.last_pacing_actual_ms: float | None = None
        self.max_pacing_actual_ms: float | None = None

    async def connect(self) -> None:
        import websockets

        logger.warning("TencentRealtimeAsrSession connecting voice_id=%s", self.voice_id)
        self._ws = await websockets.connect(
            self.connection.build_url(
                voice_id=self.voice_id,
                speaker_context_id=self.speaker_context_id,
            )
        )
        handshake = await self._ws.recv()
        logger.warning("TencentRealtimeAsrSession connected voice_id=%s handshake=%s", self.voice_id, handshake)
        self._last_sent_at = self._monotonic()
        self._receiver_task = asyncio.create_task(self._pump_events())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("Realtime ASR session is not connected")
        for ready in self.chunk_buffer.push(chunk):
            logger.debug("TencentRealtimeAsrSession sending chunk voice_id=%s bytes=%s", self.voice_id, len(ready))
            try:
                await self._send_binary_payload(ready)
            except Exception as exc:
                logger.exception(
                    "TencentRealtimeAsrSession send chunk failed voice_id=%s bytes=%s ws_state=%s close=%s",
                    self.voice_id,
                    len(ready),
                    getattr(self._ws, "state", None),
                    _describe_connection_closed(exc),
                )
                raise

    async def receive_event(self):
        return await self._events.get()

    async def end(self) -> None:
        if self._ws is None:
            return
        self._ending = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
        tail = self.chunk_buffer.flush()
        if tail:
            logger.warning("TencentRealtimeAsrSession sending tail voice_id=%s bytes=%s", self.voice_id, len(tail))
            try:
                await self._send_binary_payload(tail)
            except Exception:
                logger.exception(
                    "TencentRealtimeAsrSession send tail failed voice_id=%s bytes=%s ws_state=%s",
                    self.voice_id,
                    len(tail),
                    getattr(self._ws, "state", None),
                )
                raise
        logger.warning("TencentRealtimeAsrSession sending end voice_id=%s", self.voice_id)
        try:
            await self._ws.send(json.dumps({"type": "end"}))
        except Exception:
            logger.exception(
                "TencentRealtimeAsrSession send end failed voice_id=%s ws_state=%s",
                self.voice_id,
                getattr(self._ws, "state", None),
            )
            raise
        self._last_sent_at = self._monotonic()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
        if self._receiver_task is not None:
            with contextlib.suppress(Exception):
                await self._receiver_task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        logger.info("TencentRealtimeAsrSession closed voice_id=%s", self.voice_id)

    async def _pump_events(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                logger.warning("TencentRealtimeAsrSession received raw event voice_id=%s raw=%s", self.voice_id, raw)
                message = json.loads(raw)
                normalized = _normalize_realtime_message(message)
                if normalized is None:
                    continue
                if normalized.get("event") == "error":
                    await self._events.put(normalized)
                    break
                await self._events.put(normalized)
                if normalized.get("event") == "final" and normalized.get("stream_final", True):
                    break
        except Exception as exc:
            if _is_connection_closed_error(exc):
                logger.warning(
                    "TencentRealtimeAsrSession pump events closed voice_id=%s ws_state=%s close=%s",
                    self.voice_id,
                    getattr(self._ws, "state", None),
                    _describe_connection_closed(exc),
                )
            else:
                logger.exception(
                    "TencentRealtimeAsrSession pump events failed voice_id=%s ws_state=%s",
                    self.voice_id,
                    getattr(self._ws, "state", None),
                )
                raise
        finally:
            await self._events.put(None)

    async def _keepalive_loop(self) -> None:
        if self.keepalive_seconds <= 0:
            return
        while not self._closed and not self._ending:
            await asyncio.sleep(self.keepalive_seconds)
            if self._ws is None or self._ending:
                continue
            idle_seconds = self._monotonic() - self._last_sent_at
            if idle_seconds < self.keepalive_seconds:
                continue
            silence = b"\x00" * self.chunk_bytes
            logger.warning(
                "TencentRealtimeAsrSession sending keepalive silence voice_id=%s bytes=%s idle=%.2fs",
                self.voice_id,
                len(silence),
                idle_seconds,
            )
            try:
                await self._send_binary_payload(silence)
            except Exception:
                logger.exception(
                    "TencentRealtimeAsrSession keepalive send failed voice_id=%s ws_state=%s",
                    self.voice_id,
                    getattr(self._ws, "state", None),
                )
                break

    async def _send_binary_payload(self, payload: bytes) -> None:
        assert self._ws is not None
        if self.audio_bytes_per_second > 0 and self._last_sent_at > 0:
            elapsed = self._monotonic() - self._last_sent_at
            min_interval = len(payload) / (self.audio_bytes_per_second * self.pacing_headroom)
            wait_seconds = min_interval - elapsed
            if wait_seconds > 0:
                self.last_pacing_requested_ms = round(wait_seconds * 1000, 3)
                sleep_started = self._monotonic()
                await self._sleep(wait_seconds)
                actual_ms = round((self._monotonic() - sleep_started) * 1000, 3)
                self.last_pacing_actual_ms = actual_ms
                self.max_pacing_actual_ms = (
                    actual_ms
                    if self.max_pacing_actual_ms is None
                    else max(self.max_pacing_actual_ms, actual_ms)
                )
            else:
                self.last_pacing_requested_ms = 0.0
                self.last_pacing_actual_ms = 0.0
        send_started = self._monotonic()
        await self._ws.send(payload)
        send_elapsed_ms = round((self._monotonic() - send_started) * 1000, 3)
        self.last_payload_send_elapsed_ms = send_elapsed_ms
        self.max_payload_send_elapsed_ms = (
            send_elapsed_ms
            if self.max_payload_send_elapsed_ms is None
            else max(self.max_payload_send_elapsed_ms, send_elapsed_ms)
        )
        self._last_sent_at = self._monotonic()


def build_realtime_session_factory(settings: Settings):
    if not (settings.tencent_app_id and settings.tencent_secret_id and settings.tencent_secret_key):
        return lambda: MissingRealtimeAsrSession()

    connection = TencentRealtimeAsrConnection(
        app_id=settings.tencent_app_id,
        secret_id=settings.tencent_secret_id,
        secret_key=settings.tencent_secret_key,
        engine_model_type=settings.tencent_realtime_engine,
        need_vad=settings.tencent_realtime_need_vad,
        speaker_diarization=settings.tencent_realtime_speaker_diarization,
        voice_format=settings.tencent_realtime_voice_format,
        expired_seconds=settings.tencent_realtime_expired_seconds,
        enable_speaker_context=settings.tencent_realtime_enable_speaker_context,
        speaker_context_id=settings.tencent_realtime_speaker_context_id,
    )

    return lambda speaker_context_id=None: TencentRealtimeAsrSession(
        connection=connection,
        chunk_bytes=settings.tencent_realtime_chunk_bytes,
        speaker_context_id=speaker_context_id,
        keepalive_seconds=settings.tencent_realtime_keepalive_seconds,
        audio_bytes_per_second=(
            settings.speaker_live_sample_rate
            * settings.speaker_live_channels
            * settings.speaker_live_sample_width_bytes
        ),
    )


def _normalize_realtime_message(message: dict) -> dict | None:
    code = message.get("code", 0)
    if code != 0:
        return {
            "event": "error",
            "code": code,
            "message": message.get("message", "unknown error"),
        }

    result = message.get("result")
    top_level_sentences = message.get("sentences")
    payload = None
    if isinstance(top_level_sentences, (list, dict)) or "voice_text_str" in message or "text" in message:
        payload = message
    elif isinstance(result, dict):
        payload = result
    elif message.get("final") == 1:
        return {"event": "final", "stream_final": True}
    else:
        return None

    text = str(payload.get("voice_text_str") or payload.get("text") or "").strip()
    slice_type = payload.get("slice_type")
    index = payload.get("index")
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    speaker_id = payload.get("speaker_id")
    speaker_label = None
    speaker_context_id = payload.get("speaker_context_id")
    sentences = payload.get("sentences")
    normalized_sentences: list[dict] = []
    is_stream_final = message.get("final") == 1
    is_utterance_final = is_stream_final
    if isinstance(sentences, list):
        normalized_sentences = [item for item in sentences if isinstance(item, dict)]
    elif isinstance(sentences, dict):
        sentence_list = sentences.get("sentence_list")
        if isinstance(sentence_list, list):
            normalized_sentences = [item for item in sentence_list if isinstance(item, dict)]

    if normalized_sentences:
        latest = normalized_sentences[-1]
        text = str(latest.get("sentence") or text).strip()
        speaker_id = latest.get("speaker_id", speaker_id)
        index = latest.get("sentence_id", index)
        sentence_type = latest.get("sentence_type")
        if sentence_type is not None:
            normalized_sentence_type = int(sentence_type)
            is_utterance_final = normalized_sentence_type == 1
            slice_type = 2 if is_utterance_final else 1
        start_time = latest.get("start_time", start_time)
        end_time = latest.get("end_time", end_time)
        speaker_context_id = latest.get("speaker_context_id", speaker_context_id)
        if speaker_id not in (None, "", -1, "-1"):
            speaker_label = f"speaker_{speaker_id}"

    if speaker_id not in (None, "", -1, "-1") and speaker_label is None:
        speaker_label = f"speaker_{speaker_id}"

    normalized = {
        "event": "transcript",
        "slice_type": slice_type,
        "index": index,
        "text": text,
        "start_time": start_time,
        "end_time": end_time,
        "speaker_id": speaker_id,
        "speaker_label": speaker_label,
        "speaker_context_id": speaker_context_id,
        "sentences": sentences,
        "stream_final": is_stream_final,
    }
    if is_utterance_final:
        normalized["event"] = "final"
    return normalized
