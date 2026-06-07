from __future__ import annotations

import asyncio
import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .tts_adapter import MiniMaxWebSocketTTSAdapter, build_tts_adapter


def _sanitize_ws_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
    data = sanitized.get("data")
    if isinstance(data, dict) and isinstance(data.get("audio"), str):
        audio_value = data["audio"]
        data["audio"] = f"<redacted:{len(audio_value)} chars>"
        data["audio_chars"] = len(audio_value)
        data["audio_present"] = bool(audio_value)
    return sanitized


class _RecordingWebSocketSession:
    def __init__(self, inner: Any, trace: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._trace = trace

    async def send(self, payload: str) -> None:
        parsed: Any
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = payload
        self._trace.append(
            {
                "direction": "send",
                "payload": _sanitize_ws_payload(parsed),
            }
        )
        await self._inner.send(payload)

    async def recv(self) -> str:
        raw = await self._inner.recv()
        parsed: Any
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        self._trace.append(
            {
                "direction": "recv",
                "payload": _sanitize_ws_payload(parsed),
            }
        )
        return raw

    async def close(self) -> None:
        self._trace.append({"direction": "close"})
        await self._inner.close()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


def _build_probe_reply(text: str) -> dict:
    return {
        "source": "tts_probe",
        "lead": "TTS probe lead.",
        "tail": "TTS probe tail.",
        "content": text,
    }


def _record_progressive_probe(adapter: Any, *, text: str, output_dir: Path) -> dict:
    if not hasattr(adapter, "prepare_job") or not hasattr(adapter, "stream_job_audio"):
        return {
            "mode": "progressive",
            "supported": False,
            "error": "adapter does not support progressive prepare_job/stream_job_audio",
        }

    reply = _build_probe_reply(text)
    job = adapter.prepare_job(
        text,
        reply=reply,
        turn_id="tts-probe-progressive-turn",
        reply_id="tts-probe-progressive-reply",
    )
    chunk_records: list[dict[str, Any]] = []
    all_bytes: list[bytes] = []
    output_format = job.get("format", "mp3")
    started = time.perf_counter()

    def on_segment_audio(*, segment_index: int, text: str, audio_bytes: bytes, format_name: str) -> None:
        nonlocal output_format
        output_format = format_name or output_format
        all_bytes.append(audio_bytes)
        segment_path = Path(job["segment_statuses"][segment_index]["output_path"])
        segment_path.parent.mkdir(parents=True, exist_ok=True)
        segment_path.write_bytes(audio_bytes)
        job["segment_statuses"][segment_index]["bytes"] = len(audio_bytes)
        job["segment_statuses"][segment_index]["status"] = "completed"
        chunk_records.append(
            {
                "segment_index": segment_index,
                "text": text,
                "bytes": len(audio_bytes),
                "format": output_format,
                "path": str(segment_path),
            }
        )

    try:
        adapter.stream_job_audio(job, on_segment_audio=on_segment_audio)
        full_output_path = Path(job["output_path"])
        full_output_path.parent.mkdir(parents=True, exist_ok=True)
        full_output_path.write_bytes(b"".join(all_bytes))
        job["status"] = "ready"
        job["format"] = output_format
        job["bytes"] = full_output_path.stat().st_size
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "mode": "progressive",
            "supported": True,
            "accepted": True,
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "segment_count": job.get("segment_count", 0),
            "segments": job.get("segments", []),
            "chunk_count": len(chunk_records),
            "chunks": chunk_records,
            "output_path": str(full_output_path),
            "output_exists": full_output_path.exists(),
            "output_bytes": full_output_path.stat().st_size if full_output_path.exists() else 0,
            "segment_files": [
                {
                    "index": item.get("index"),
                    "text": item.get("text"),
                    "status": item.get("status"),
                    "path": item.get("output_path"),
                    "exists": Path(item.get("output_path", "")).exists(),
                    "bytes": Path(item.get("output_path", "")).stat().st_size
                    if Path(item.get("output_path", "")).exists()
                    else 0,
                }
                for item in job.get("segment_statuses", [])
            ],
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        return {
            "mode": "progressive",
            "supported": True,
            "accepted": False,
            "error": repr(exc),
            "job_id": job.get("job_id"),
            "segments": job.get("segments", []),
        }


def _record_speak_probe(adapter: Any, *, text: str) -> dict:
    reply = _build_probe_reply(text)
    started = time.perf_counter()
    try:
        job = adapter.speak(
            text,
            reply=reply,
            turn_id="tts-probe-speak-turn",
            reply_id="tts-probe-speak-reply",
        )
    except Exception as exc:
        return {
            "mode": "speak",
            "accepted": False,
            "error": repr(exc),
        }
    output_path = Path(job["output_path"])
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "mode": "speak",
        "accepted": bool(job.get("accepted")),
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "segment_count": job.get("segment_count", 0),
        "segments": job.get("segments", []),
        "output_path": str(output_path),
        "output_exists": output_path.exists(),
        "output_bytes": output_path.stat().st_size if output_path.exists() else 0,
        "segment_files": [
            {
                "index": item.get("index"),
                "text": item.get("text"),
                "status": item.get("status"),
                "path": item.get("output_path"),
                "exists": Path(item.get("output_path", "")).exists(),
                "bytes": Path(item.get("output_path", "")).stat().st_size
                if Path(item.get("output_path", "")).exists()
                else 0,
            }
            for item in job.get("segment_statuses", [])
        ],
        "elapsed_ms": elapsed_ms,
    }


def run_tts_probe(
    *,
    settings_obj: Settings,
    text: str,
    output_dir: Path,
) -> dict:
    if not settings_obj.minimax_api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set in the current shell environment")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = output_dir / f"tts-probe-{timestamp}-summary.json"
    adapter = build_tts_adapter(settings_obj)
    ws_trace: list[dict[str, Any]] = []
    if isinstance(adapter, MiniMaxWebSocketTTSAdapter):
        original_connect = adapter._connect_ws

        async def recording_connect(url: str, headers: dict[str, str], timeout: float) -> Any:
            session = original_connect(url, headers, timeout)
            if asyncio.iscoroutine(session):
                session = await session
            return _RecordingWebSocketSession(session, ws_trace)

        adapter._connect_ws = recording_connect

    summary = {
        "adapter": type(adapter).__name__,
        "tts_model": settings_obj.minimax_tts_model,
        "voice_id": settings_obj.minimax_tts_voice_id,
        "base_url": settings_obj.minimax_tts_base_url,
        "text": text,
        "progressive": _record_progressive_probe(adapter, text=text, output_dir=output_dir),
        "speak": _record_speak_probe(adapter, text=text),
        "ws_trace": ws_trace,
    }

    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the current MiniMax TTS runtime adapter.")
    parser.add_argument(
        "--text",
        default="TTS probe. First sentence. Second sentence.",
        help="Text used for both progressive and speak probes.",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/tts-probe",
        help="Where to write the probe summary and generated audio files.",
    )
    args = parser.parse_args()

    try:
        summary = run_tts_probe(
            settings_obj=settings,
            text=args.text,
            output_dir=Path(args.output_dir),
        )
    except Exception as exc:
        print(f"TTS probe failed: {exc}")
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
