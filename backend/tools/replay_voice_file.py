from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
import urllib.request
import urllib.error
import wave
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_or_none(url: str) -> dict[str, Any] | None:
    try:
        return _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _convert_to_wav(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(output_path),
        ],
        check=True,
    )


def _read_chunks(wav_path: Path, chunk_seconds: float) -> tuple[dict[str, Any], list[bytes]]:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        frames_per_chunk = max(1, int(sample_rate * chunk_seconds))
        chunks: list[bytes] = []
        while True:
            data = wav.readframes(frames_per_chunk)
            if not data:
                break
            chunks.append(data)
    info = {
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
        "duration_seconds": round(frame_count / sample_rate, 3) if sample_rate else 0,
        "chunk_count": len(chunks),
        "chunk_seconds": chunk_seconds,
    }
    return info, chunks


async def _replay(
    *,
    backend_url: str,
    table_id: str,
    chunks: list[bytes],
    chunk_seconds: float,
    receive_timeout_seconds: float,
    post_final_wait_seconds: float,
    continue_until_input_end: bool,
) -> list[dict[str, Any]]:
    ws_url = backend_url.replace("https://", "wss://").replace("http://", "ws://")
    uri = f"{ws_url}/ws/tables/{table_id}/listen"
    events: list[dict[str, Any]] = []

    async with websockets.connect(
        uri,
        max_size=8 * 1024 * 1024,
        ping_interval=None,
        ping_timeout=None,
    ) as ws:
        sending_done = asyncio.Event()

        async def receive_events() -> None:
            final_seen = False
            timeout_seconds = receive_timeout_seconds
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    events.append({"event": "receive_timeout"})
                    return
                except ConnectionClosed as exc:
                    events.append(
                        {
                            "event": "connection_closed",
                            "code": exc.rcvd.code if exc.rcvd else None,
                            "reason": exc.rcvd.reason if exc.rcvd else "",
                        }
                    )
                    return
                if isinstance(raw, bytes):
                    events.append({"event": "binary", "bytes": len(raw)})
                    continue
                event = json.loads(raw)
                events.append(event)
                print(json.dumps({"kind": "ws_event", "event": event}, ensure_ascii=False), flush=True)
                if event.get("event") == "assistant_ready" and not continue_until_input_end:
                    return
                if event.get("event") == "error":
                    return
                if event.get("event") == "final" and event.get("stream_final", True):
                    if continue_until_input_end and not sending_done.is_set():
                        continue
                    if post_final_wait_seconds <= 0 or final_seen:
                        return
                    final_seen = True
                    timeout_seconds = post_final_wait_seconds

        receiver = asyncio.create_task(receive_events())
        for index, chunk in enumerate(chunks):
            await ws.send(chunk)
            print(
                json.dumps(
                    {"kind": "sent_chunk", "index": index, "bytes": len(chunk)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            await asyncio.sleep(chunk_seconds)
        await ws.send(json.dumps({"type": "end"}))
        sending_done.set()
        await receiver
    return events


def _summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    transcript_events = [
        event
        for event in events
        if event.get("event") == "transcript" and str(event.get("text") or "").strip()
    ]
    final_events = [event for event in events if event.get("event") == "final"]
    assistant_ready_events = [event for event in events if event.get("event") == "assistant_ready"]
    error_events = [event for event in events if event.get("event") == "error"]
    return {
        "event_count": len(events),
        "transcript_count": len(transcript_events),
        "final_count": len(final_events),
        "assistant_ready_count": len(assistant_ready_events),
        "error_count": len(error_events),
        "texts": [event.get("text") for event in transcript_events],
        "final_texts": [event.get("text") for event in final_events if event.get("text")],
        "assistant_texts": [event.get("content") for event in assistant_ready_events if event.get("content")],
    }


def _drain_tts_stream(backend_url: str, table_id: str, stream_id: str, max_chunks: int = 100) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    total_audio_base64_chars = 0
    for _ in range(max_chunks):
        chunk = _get_json_or_none(f"{backend_url}/tables/{table_id}/tts-streams/{stream_id}/next")
        if chunk is None:
            break
        audio_base64 = chunk.pop("audio_base64", "")
        total_audio_base64_chars += len(audio_base64)
        chunk["audio_base64_chars"] = len(audio_base64)
        chunks.append(chunk)
        if chunk.get("is_final"):
            break
    return {
        "stream_id": stream_id,
        "chunk_count": len(chunks),
        "total_audio_base64_chars": total_audio_base64_chars,
        "chunks": chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a local voice file through the backend live WebSocket.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--backend-url", default="http://localhost:8010")
    parser.add_argument("--table-id")
    parser.add_argument("--table-name", default="Replay Voice Probe")
    parser.add_argument("--chunk-seconds", type=float, default=0.2)
    parser.add_argument("--receive-timeout-seconds", type=float, default=20)
    parser.add_argument("--post-final-wait-seconds", type=float, default=15)
    parser.add_argument(
        "--continue-until-input-end",
        action="store_true",
        help="Keep receiving server events while sending long input files instead of stopping at the first assistant_ready/final.",
    )
    parser.add_argument("--drain-tts-stream", action="store_true")
    parser.add_argument("--output-dir", default=".runtime/replay-voice-file")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    slug = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in input_path.stem
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output_dir = Path(args.output_dir) / f"{timestamp}-{slug}"
    wav_path = output_dir / "input.wav"
    _convert_to_wav(input_path, wav_path)
    wav_info, chunks = _read_chunks(wav_path, args.chunk_seconds)

    table_id = args.table_id
    if table_id is None:
        table = _post_json(
            f"{args.backend_url}/tables",
            {"name": args.table_name, "assistant_name": "宝子", "origin": "replay"},
        )
        table_id = table["id"]

    events = asyncio.run(
        _replay(
            backend_url=args.backend_url,
            table_id=table_id,
            chunks=chunks,
            chunk_seconds=args.chunk_seconds,
            receive_timeout_seconds=args.receive_timeout_seconds,
            post_final_wait_seconds=args.post_final_wait_seconds,
            continue_until_input_end=args.continue_until_input_end,
        )
    )
    context = _get_json(f"{args.backend_url}/tables/{table_id}/context")
    diagnostics = _get_json(f"{args.backend_url}/tables/{table_id}/live-diagnostics")
    runtime_events = _get_json(f"{args.backend_url}/tables/{table_id}/runtime/events")
    tts_jobs = _get_json(f"{args.backend_url}/tables/{table_id}/tts-jobs")
    tts_streams: list[dict[str, Any]] = []
    if args.drain_tts_stream:
        for event in events:
            stream = event.get("tts_stream") if isinstance(event, dict) else None
            stream_id = stream.get("stream_id") if isinstance(stream, dict) else None
            if stream_id:
                tts_streams.append(_drain_tts_stream(args.backend_url, table_id, stream_id))
    summary = {
        "input": str(input_path),
        "wav_path": str(wav_path),
        "table_id": table_id,
        "wav": wav_info,
        "events": events,
        "event_summary": _summarize_events(events),
        "context": context,
        "live_diagnostics": diagnostics,
        "runtime_events": runtime_events,
        "tts_jobs": tts_jobs,
        "tts_streams": tts_streams,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"kind": "summary", "summary_path": str(summary_path), **summary["event_summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
