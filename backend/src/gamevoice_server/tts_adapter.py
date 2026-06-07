from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib import request
from uuid import uuid4

import websockets

from .config import Settings

WsConnect = Callable[[str, dict[str, str], float], Awaitable[Any]]
AsyncRunner = Callable[[Awaitable[Any]], Any]


def split_tts_segments(text: str) -> list[str]:
    parts = [item.strip() for item in re.split(r"(?<=[\u3002\uff01\uff1f])", text) if item.strip()]
    return parts or [text.strip()]


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _effective_voice_id(override: str | None, default: str) -> str:
    cleaned = _clean_text(override)
    return cleaned or default


def _normalize_segment_signature(text: str) -> str:
    return re.sub(r"\s+", "", _clean_text(text))


def _append_segment_if_new(segments: list[str], segment: str) -> None:
    cleaned = _clean_text(segment)
    if not cleaned:
        return
    if segments and _normalize_segment_signature(segments[-1]) == _normalize_segment_signature(cleaned):
        return
    segments.append(cleaned)


def _trim_prefixed_text(text: str, prefix: str) -> str:
    cleaned_text = _clean_text(text)
    cleaned_prefix = _clean_text(prefix)
    if not cleaned_text or not cleaned_prefix:
        return cleaned_text
    if not cleaned_text.startswith(cleaned_prefix):
        return cleaned_text
    remainder = cleaned_text[len(cleaned_prefix) :].lstrip(" ，。！？、:：；)]}\"'")
    return remainder.strip()


def _build_tts_segments(text: str, reply: dict | None = None) -> list[str]:
    if reply:
        lead = _clean_text(reply.get("lead"))
        tail = _clean_text(reply.get("tail"))
        content = _clean_text(reply.get("content")) or _clean_text(text)
        if lead and tail:
            combined = " ".join(item for item in [lead, tail] if item).strip()
            if not content or _normalize_segment_signature(combined) == _normalize_segment_signature(content):
                return [lead, tail]
        body = content
        if lead:
            body = _trim_prefixed_text(body, lead)
        if tail and body:
            if tail.startswith(body):
                body = ""
            elif body.startswith(tail):
                tail = ""
        content_segments = split_tts_segments(body) if body else []
        content_signatures = {
            _normalize_segment_signature(segment)
            for segment in content_segments
            if _clean_text(segment)
        }
        segments: list[str] = []
        if lead and _normalize_segment_signature(lead) not in content_signatures:
            _append_segment_if_new(segments, lead)
        for segment in content_segments:
            _append_segment_if_new(segments, segment)
        if tail and _normalize_segment_signature(tail) not in content_signatures:
            _append_segment_if_new(segments, tail)
        if segments:
            return segments
    return split_tts_segments(text)


def build_segment_statuses(
    *,
    job_id: str,
    segments: list[str],
    output_dir: Path,
    format_name: str,
    segment_bytes: list[bytes],
) -> list[dict]:
    statuses: list[dict] = []
    for index, segment in enumerate(segments):
        output_path = output_dir / f"{job_id}-segment-{index}.{format_name}"
        output_path.write_bytes(segment_bytes[index])
        statuses.append(
            {
                "index": index,
                "text": segment,
                "status": "queued",
                "format": format_name,
                "bytes": len(segment_bytes[index]),
                "output_path": str(output_path),
            }
        )
    return statuses


def _decode_audio_blob(value: str) -> bytes:
    cleaned = _clean_text(value)
    if not cleaned:
        return b""
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return base64.b64decode(cleaned, validate=True)


def _run_coro_in_worker_thread(coro: Awaitable[Any]) -> Any:
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["result"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised synchronously
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


@dataclass
class TTSAdapter:
    output_dir: str | Path = ".runtime/tts"

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        cleaned = _clean_text(text)
        return {
            "audio_bytes": cleaned.encode("utf-8"),
            "format": "mp3",
        }

    def speak(
        self,
        text: str,
        *,
        reply: dict | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        voice_id: str | None = None,
    ) -> dict:
        job_id = uuid4().hex
        segments = _build_tts_segments(text, reply=reply)
        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        full_text = "".join(segment for segment in segments if _clean_text(segment)).strip()
        if not full_text:
            full_text = _clean_text(reply.get("content")) if reply else text
        if not full_text:
            full_text = text
        full_audio_bytes = full_text.encode("utf-8")
        segment_bytes = [
            self.synthesize_segment(segment, voice_id=voice_id)["audio_bytes"]
            for segment in segments
        ]
        segment_statuses = build_segment_statuses(
            job_id=job_id,
            segments=segments,
            output_dir=output_dir,
            format_name="mp3",
            segment_bytes=segment_bytes,
        )

        output_path = output_dir / f"{job_id}.mp3"
        output_path.write_bytes(full_audio_bytes)
        return {
            "accepted": True,
            "job_id": job_id,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "ready",
            "text": text,
            "segments": segments,
            "segment_count": len(segments),
            "segment_statuses": segment_statuses,
            "format": "mp3",
            "output_path": str(output_path),
            "bytes": len(full_audio_bytes),
            "voice_id": voice_id,
        }


class MiniMaxTTSAdapter:
    """Legacy HTTP adapter kept in-repo as reference only."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "speech-2.8-hd",
        voice_id: str = "",
        base_url: str = "https://api.minimaxi.com/v1/t2a_v2",
        timeout_seconds: float = 15.0,
        output_dir: str | Path = ".runtime/tts",
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
        job_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.output_dir = Path(output_dir)
        self._request_sender = request_sender or self._send_request
        self._job_id_provider = job_id_provider or (lambda: uuid4().hex)

    def speak(
        self,
        text: str,
        *,
        reply: dict | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        voice_id: str | None = None,
    ) -> dict:
        segments = _build_tts_segments(text, reply=reply)
        job_id = self._job_id_provider()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        effective_voice_id = _effective_voice_id(voice_id, self.voice_id)
        segment_payloads = [self._synthesize_audio(segment, voice_id=effective_voice_id) for segment in segments]
        format_name = segment_payloads[0]["format"] if segment_payloads else "mp3"
        segment_bytes = [item["audio_bytes"] for item in segment_payloads]
        segment_statuses = build_segment_statuses(
            job_id=job_id,
            segments=segments,
            output_dir=self.output_dir,
            format_name=format_name,
            segment_bytes=segment_bytes,
        )

        output_path = self.output_dir / f"{job_id}.{format_name}"
        output_path.write_bytes(b"".join(segment_bytes))
        return {
            "accepted": True,
            "job_id": job_id,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "ready",
            "text": text,
            "segments": segments,
            "segment_count": len(segments),
            "segment_statuses": segment_statuses,
            "format": format_name,
            "output_path": str(output_path),
            "bytes": output_path.stat().st_size,
            "voice_id": effective_voice_id,
        }

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        return self._synthesize_audio(text, voice_id=voice_id)

    def _synthesize_audio(self, text: str, voice_id: str | None = None) -> dict:
        effective_voice_id = _effective_voice_id(voice_id, self.voice_id)
        body = json.dumps(
            {
                "model": self.model,
                "text": text,
                "stream": False,
                "voice_setting": {
                    "voice_id": effective_voice_id,
                    "speed": 1,
                    "vol": 1,
                    "pitch": 0,
                },
                "audio_setting": {
                    "sample_rate": 32000,
                    "bitrate": 128000,
                    "format": "mp3",
                    "channel": 1,
                },
                "subtitle_enable": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response_bytes = self._request_sender(self.base_url, body, headers, self.timeout_seconds)
        payload = json.loads(response_bytes.decode("utf-8"))
        base_resp = payload.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            raise RuntimeError(base_resp.get("status_msg", "MiniMax TTS failed"))
        audio_hex = payload["data"]["audio"]
        return {
            "audio_bytes": _decode_audio_blob(audio_hex),
            "format": payload.get("extra_info", {}).get("audio_format", "mp3"),
        }

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()


class MiniMaxWebSocketTTSAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "speech-2.8-hd",
        voice_id: str = "",
        base_url: str = "wss://api.minimaxi.com/ws/v1/t2a_v2",
        connect_timeout_seconds: float = 15.0,
        output_dir: str | Path = ".runtime/tts",
        connect_ws: WsConnect | None = None,
        job_id_provider: Callable[[], str] | None = None,
        async_runner: AsyncRunner | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.base_url = base_url
        self.connect_timeout_seconds = connect_timeout_seconds
        self.output_dir = Path(output_dir)
        self._connect_ws = connect_ws or self._default_connect_ws
        self._job_id_provider = job_id_provider or (lambda: uuid4().hex)
        self._async_runner = async_runner or _run_coro_in_worker_thread

    def speak(
        self,
        text: str,
        *,
        reply: dict | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        voice_id: str | None = None,
    ) -> dict:
        return self._async_runner(
            self._speak_async(
                text,
                reply=reply,
                turn_id=turn_id,
                reply_id=reply_id,
                voice_id=voice_id,
            )
        )

    def prepare_job(
        self,
        text: str,
        *,
        reply: dict | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        voice_id: str | None = None,
    ) -> dict:
        segments = _build_tts_segments(text, reply=reply)
        job_id = self._job_id_provider()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        format_name = "mp3"
        segment_statuses = [
            {
                "index": index,
                "text": segment,
                "status": "queued",
                "format": format_name,
                "bytes": 0,
                "output_path": str(self.output_dir / f"{job_id}-segment-{index}.{format_name}"),
            }
            for index, segment in enumerate(segments)
        ]
        return {
            "accepted": True,
            "job_id": job_id,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "preparing",
            "text": text,
            "segments": segments,
            "segment_count": len(segments),
            "segment_statuses": segment_statuses,
            "format": format_name,
            "output_path": str(self.output_dir / f"{job_id}.{format_name}"),
            "bytes": 0,
            "voice_id": voice_id,
        }

    def stream_job_audio(
        self,
        speech_job: dict,
        *,
        on_segment_audio: Callable[..., None],
    ) -> None:
        self._async_runner(self._stream_job_audio_async(speech_job, on_segment_audio=on_segment_audio))

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        return self._async_runner(self._synthesize_segment_async(text, voice_id=voice_id))

    async def _speak_async(
        self,
        text: str,
        *,
        reply: dict | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        voice_id: str | None = None,
    ) -> dict:
        job = self.prepare_job(
            text,
            reply=reply,
            turn_id=turn_id,
            reply_id=reply_id,
            voice_id=voice_id,
        )
        segment_bytes: list[bytes] = []
        output_format = job.get("format", "mp3")

        def on_segment_audio(*, segment_index: int, text: str, audio_bytes: bytes, format_name: str) -> None:
            nonlocal output_format
            output_format = format_name or output_format
            segment_bytes.append(audio_bytes)

        await self._stream_job_audio_async(job, on_segment_audio=on_segment_audio)
        segment_statuses = build_segment_statuses(
            job_id=job["job_id"],
            segments=job["segments"],
            output_dir=self.output_dir,
            format_name=output_format,
            segment_bytes=segment_bytes,
        )
        output_path = Path(job["output_path"])
        output_path.write_bytes(b"".join(segment_bytes))
        return {
            "accepted": True,
            "job_id": job["job_id"],
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "ready",
            "text": text,
            "segments": job["segments"],
            "segment_count": len(job["segments"]),
            "segment_statuses": segment_statuses,
            "format": output_format,
            "output_path": str(output_path),
            "bytes": output_path.stat().st_size,
            "voice_id": voice_id,
        }

    async def _stream_job_audio_async(
        self,
        speech_job: dict,
        *,
        on_segment_audio: Callable[..., None],
    ) -> None:
        connection = self._connect_ws(
            self.base_url,
            {"Authorization": f"Bearer {self.api_key}"},
            self.connect_timeout_seconds,
        )
        if asyncio.iscoroutine(connection):
            session = await connection
        else:
            session = connection

        format_name = speech_job.get("format", "mp3")
        job_voice_id = speech_job.get("voice_id")
        try:
            await self._expect_event(session, "connected_success")
            await self._send_event(session, self._build_task_start(voice_id=job_voice_id))
            await self._expect_event(session, "task_started")
            for index, segment in enumerate(speech_job.get("segments", [])):
                await self._send_event(
                    session,
                    {
                        "event": "task_continue",
                        "text": segment,
                    },
                )
                segment_audio_parts: list[bytes] = []
                while True:
                    payload = await self._expect_event(session, "task_continued")
                    audio_blob = ((payload.get("data") or {}).get("audio") or "")
                    is_final = bool(payload.get("is_final", False))
                    audio_bytes = _decode_audio_blob(audio_blob) if audio_blob else b""
                    if audio_bytes:
                        segment_audio_parts.append(audio_bytes)
                    elif not is_final:
                        raise RuntimeError("MiniMax WebSocket TTS returned empty audio payload")
                    format_name = (payload.get("extra_info") or {}).get("audio_format", format_name)
                    if is_final:
                        break
                if not segment_audio_parts:
                    raise RuntimeError("MiniMax WebSocket TTS returned no audio before final frame")
                audio_bytes = b"".join(segment_audio_parts)
                on_segment_audio(
                    segment_index=index,
                    text=segment,
                    audio_bytes=audio_bytes,
                    format_name=format_name,
                )
            await self._send_event(session, {"event": "task_finish"})
            await self._expect_event(session, "task_finished")
        finally:
            await session.close()

    async def _synthesize_segment_async(self, text: str, *, voice_id: str | None = None) -> dict:
        connection = self._connect_ws(
            self.base_url,
            {"Authorization": f"Bearer {self.api_key}"},
            self.connect_timeout_seconds,
        )
        if asyncio.iscoroutine(connection):
            session = await connection
        else:
            session = connection

        format_name = "mp3"
        audio_parts: list[bytes] = []
        try:
            await self._expect_event(session, "connected_success")
            await self._send_event(session, self._build_task_start(voice_id=voice_id))
            await self._expect_event(session, "task_started")
            await self._send_event(
                session,
                {
                    "event": "task_continue",
                    "text": text,
                },
            )
            while True:
                payload = await self._expect_event(session, "task_continued")
                audio_blob = ((payload.get("data") or {}).get("audio") or "")
                is_final = bool(payload.get("is_final", False))
                audio_bytes = _decode_audio_blob(audio_blob) if audio_blob else b""
                if audio_bytes:
                    audio_parts.append(audio_bytes)
                elif not is_final:
                    raise RuntimeError("MiniMax WebSocket TTS returned empty audio payload")
                format_name = (payload.get("extra_info") or {}).get("audio_format", format_name)
                if is_final:
                    break
            await self._send_event(session, {"event": "task_finish"})
            await self._expect_event(session, "task_finished")
        finally:
            await session.close()

        if not audio_parts:
            raise RuntimeError("MiniMax WebSocket TTS returned no audio before final frame")
        return {
            "audio_bytes": b"".join(audio_parts),
            "format": format_name,
        }

    def _build_task_start(self, voice_id: str | None = None) -> dict:
        effective_voice_id = _effective_voice_id(voice_id, self.voice_id)
        return {
            "event": "task_start",
            "model": self.model,
            "voice_setting": {
                "voice_id": effective_voice_id,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }

    @staticmethod
    async def _send_event(session: Any, payload: dict) -> None:
        await session.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    async def _expect_event(session: Any, expected_event: str) -> dict:
        payload = json.loads(await session.recv())
        event = payload.get("event")
        if event == "task_failed":
            message = payload.get("error_msg") or payload.get("message") or "MiniMax WebSocket TTS failed"
            raise RuntimeError(message)
        if event != expected_event:
            raise RuntimeError(f"MiniMax WebSocket TTS expected {expected_event} but received {event}")
        return payload

    @staticmethod
    async def _default_connect_ws(url: str, headers: dict[str, str], timeout: float) -> Any:
        return await websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=timeout,
        )


def build_tts_adapter(settings: Settings):
    if settings.minimax_api_key:
        return MiniMaxWebSocketTTSAdapter(
            api_key=settings.minimax_api_key,
            model=settings.minimax_tts_model,
            voice_id=settings.minimax_tts_voice_id,
            base_url=settings.minimax_tts_base_url,
            connect_timeout_seconds=settings.minimax_tts_timeout_seconds,
            output_dir=settings.minimax_tts_output_dir,
        )
    return TTSAdapter()
