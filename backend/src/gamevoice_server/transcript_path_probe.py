from __future__ import annotations

import argparse
import asyncio
import json
import logging
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .speaker_identity_probe import _ensure_path, _load_wav_metadata, _now_slug, _read_wav_chunks, _run_ffmpeg_to_wav
from .tencent_realtime_asr import build_realtime_session_factory

logger = logging.getLogger(__name__)


def _log_probe_line(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


async def run_transcript_path_probe(
    *,
    settings_obj: Settings,
    input_path: Path,
    output_dir: Path,
    speaker_context_id: str | None = None,
    chunk_seconds: float = 0.04,
    send_delay_seconds: float | None = None,
    receive_timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    output_dir.mkdir(parents=True, exist_ok=True)
    probe_dir = output_dir / f"transcript-path-probe-{_now_slug()}"
    probe_dir.mkdir(parents=True, exist_ok=True)

    converted_wav = probe_dir / "input.wav"
    ffmpeg_info = _run_ffmpeg_to_wav(
        input_path,
        converted_wav,
        sample_rate=settings_obj.speaker_live_sample_rate,
        channels=settings_obj.speaker_live_channels,
    )
    wav_info = _load_wav_metadata(converted_wav)
    audio_chunks = _read_wav_chunks(converted_wav, chunk_seconds=chunk_seconds)
    pace_seconds = send_delay_seconds if send_delay_seconds is not None else chunk_seconds

    session_factory = build_realtime_session_factory(settings_obj)
    session = session_factory(speaker_context_id=speaker_context_id)
    collected_events: list[dict[str, Any]] = []
    send_errors: list[str] = []
    connect_error: str | None = None
    final_event: dict[str, Any] | None = None

    async def collect_events() -> None:
        nonlocal final_event
        while True:
            event = await asyncio.wait_for(session.receive_event(), timeout=receive_timeout_seconds)
            if event is None:
                _log_probe_line({"kind": "probe_event", "event": None})
                break
            collected_events.append(event)
            _log_probe_line({"kind": "probe_event", "event": event})
            if event.get("event") == "final":
                final_event = event
                break
            if event.get("event") == "error":
                break

    collector_task: asyncio.Task | None = None
    try:
        await session.connect()
        _log_probe_line(
            {
                "kind": "connected",
                "voice_id": getattr(session, "voice_id", None),
                "speaker_context_id": speaker_context_id,
            }
        )
        collector_task = asyncio.create_task(collect_events())
        for index, pcm_chunk in enumerate(audio_chunks):
            _log_probe_line(
                {
                    "kind": "sent_chunk",
                    "index": index,
                    "bytes": len(pcm_chunk),
                    "chunk_seconds": chunk_seconds,
                }
            )
            try:
                await session.send_audio(pcm_chunk)
            except Exception as exc:
                send_errors.append(f"chunk[{index}]: {exc!r}")
                _log_probe_line(
                    {
                        "kind": "send_error",
                        "index": index,
                        "error": repr(exc),
                    }
                )
                raise
            if pace_seconds > 0:
                await asyncio.sleep(pace_seconds)
        _log_probe_line({"kind": "sending_end"})
        await session.end()
        if collector_task is not None:
            try:
                await asyncio.wait_for(collector_task, timeout=receive_timeout_seconds)
            except asyncio.TimeoutError:
                _log_probe_line({"kind": "collector_timeout"})
    except Exception as exc:
        connect_error = repr(exc)
        _log_probe_line({"kind": "probe_failed", "error": connect_error})
        raise
    finally:
        with contextlib.suppress(Exception):
            await session.close()

    summary = {
        "input_path": str(input_path),
        "output_dir": str(probe_dir),
        "converted_wav_path": str(converted_wav),
        "ffmpeg": ffmpeg_info,
        "wav": wav_info,
        "chunk_seconds": chunk_seconds,
        "send_delay_seconds": pace_seconds,
        "chunk_count": len(audio_chunks),
        "voice_id": getattr(session, "voice_id", None),
        "speaker_context_id": speaker_context_id,
        "send_errors": send_errors,
        "connect_error": connect_error,
        "final_event": final_event,
        "events": collected_events,
        "transcript_events": [event for event in collected_events if event.get("event") == "transcript"],
        "final_events": [event for event in collected_events if event.get("event") == "final"],
        "error_events": [event for event in collected_events if event.get("event") == "error"],
        "status": "ok" if collected_events else "no_events",
    }
    summary_path = probe_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe realtime transcript path only.")
    parser.add_argument("--input", required=True, help="Path to a local audio file, e.g. .m4a or .wav")
    parser.add_argument(
        "--output-dir",
        default=".runtime/transcript-path-probe",
        help="Directory where probe artifacts will be written",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=float,
        default=0.04,
        help="Chunk size used to simulate live audio pacing",
    )
    parser.add_argument(
        "--send-delay-seconds",
        type=float,
        default=None,
        help="Override pacing delay between chunks",
    )
    parser.add_argument(
        "--speaker-context-id",
        default=None,
        help="Optional Tencent speaker_context_id to reuse",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        run_transcript_path_probe(
            settings_obj=settings,
            input_path=_ensure_path(args.input),
            output_dir=_ensure_path(args.output_dir),
            speaker_context_id=args.speaker_context_id,
            chunk_seconds=args.chunk_seconds,
            send_delay_seconds=args.send_delay_seconds,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
