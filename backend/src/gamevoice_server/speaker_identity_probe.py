from __future__ import annotations

import io
import argparse
import base64
import json
import subprocess
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

from imageio_ffmpeg import get_ffmpeg_exe

from .audio_gateway import AudioGateway
from .config import Settings, settings
from .dialog_client import build_dialog_client
from .identity_linker import IdentityLinker
from .session_manager import SessionManager
from .speaker_alias_rewrite_service import SpeakerAliasRewriteService
from .speaker_live_connector import SpeakerLiveConnector
from .speaker_live_runtime import build_speaker_live_runtime
from .speaker_live_worker import SpeakerLiveWorker
from .speaker_pipeline_adapter import SpeakerPipelineAdapter
from .tencent_asr import build_sentence_transcriber


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _ensure_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _run_ffmpeg_to_wav(
    input_path: Path,
    output_path: Path,
    *,
    sample_rate: int,
    channels: int,
) -> dict[str, Any]:
    ffmpeg_exe = get_ffmpeg_exe()
    command = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=False, check=False)
    stdout = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
    stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr else ""
    if completed.returncode != 0:
        raise RuntimeError(
            "ffmpeg conversion failed:\n"
            f"command: {' '.join(command)}\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
    return {
        "ffmpeg_exe": ffmpeg_exe,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
    }


def _load_wav_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav_file:
        return {
            "channels": wav_file.getnchannels(),
            "sample_width_bytes": wav_file.getsampwidth(),
            "sample_rate": wav_file.getframerate(),
            "frame_count": wav_file.getnframes(),
            "duration_seconds": round(wav_file.getnframes() / float(wav_file.getframerate() or 1), 3),
        }


def _read_wav_chunks(path: Path, *, chunk_seconds: float) -> list[bytes]:
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be > 0")
    chunks: list[bytes] = []
    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames_per_chunk = max(1, int(round(sample_rate * chunk_seconds)))
        while True:
            frames = wav_file.readframes(frames_per_chunk)
            if not frames:
                break
            chunks.append(frames)
    return chunks


def _encode_wav_chunk(
    frames: bytes,
    *,
    channels: int,
    sample_width_bytes: int,
    sample_rate: int,
) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width_bytes)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)
        return buffer.getvalue()


def _drain_worker(worker: SpeakerLiveWorker, table_id: str, live_session_id: str, *, max_passes: int = 64) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _ in range(max_passes):
        result = worker.process_session(table_id, live_session_id)
        results.append(result)
        live_state = worker.connector.describe_session(table_id, live_session_id) or {}
        if result.get("status") == "idle":
            break
        if int(live_state.get("pending_audio_chunk_count", 0)) <= 0:
            break
    return results


def _drain_alias_rewrite(service: SpeakerAliasRewriteService, table_id: str, *, max_passes: int = 4) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _ in range(max_passes):
        result = service.rewrite_table_aliases(table_id)
        results.append(result)
        if result.get("stopped"):
            break
    return results


def run_speaker_identity_probe(
    *,
    settings_obj: Settings,
    input_path: Path,
    output_dir: Path,
    chunk_seconds: float = 1.0,
    transcriber=None,
    diarizer=None,
    embedder=None,
    rewrite_client=None,
) -> dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    output_dir.mkdir(parents=True, exist_ok=True)
    probe_dir = output_dir / f"speaker-identity-probe-{_now_slug()}"
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

    session_manager = SessionManager()
    table = session_manager.start_table(name="Speaker Identity Probe Table", origin="probe")

    audio_gateway = AudioGateway(transcriber=transcriber or build_sentence_transcriber(settings_obj))
    transcript_parts: list[dict[str, Any]] = []
    transcript_text_parts: list[str] = []
    for chunk_index, chunk_frames in enumerate(audio_chunks):
        chunk_wav_bytes = _encode_wav_chunk(
            chunk_frames,
            channels=int(wav_info["channels"]),
            sample_width_bytes=int(wav_info["sample_width_bytes"]),
            sample_rate=int(wav_info["sample_rate"]),
        )
        chunk_filename = f"{converted_wav.stem}-chunk-{chunk_index:04d}.wav"
        transcript_part = audio_gateway.ingest_clip(
            table.id,
            chunk_filename,
            chunk_wav_bytes,
        )
        transcript_parts.append(transcript_part)
        part_text = str(transcript_part.get("content") or "").strip()
        if part_text:
            transcript_text_parts.append(part_text)
    transcript_text = " ".join(transcript_text_parts).strip()
    transcript_result = {
        "kind": "voice_transcript",
        "filename": converted_wav.name,
        "content": transcript_text,
        "parts": transcript_parts,
    }
    transcript_event = {
        "kind": "voice_transcript",
        "source": "speaker_identity_probe",
        "content": session_manager.format_user_utterance(
            text=transcript_text,
            table_id=table.id,
        ),
        "filename": transcript_result.get("filename"),
    }
    session_manager.append_context_event(table.id, transcript_event)

    identity_linker = IdentityLinker()
    pipeline_adapter = SpeakerPipelineAdapter()
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=identity_linker,
        pipeline_adapter=pipeline_adapter,
    )
    diarizer_obj, embedder_obj = (
        (diarizer, embedder)
        if diarizer is not None and embedder is not None
        else build_speaker_live_runtime(settings_obj)
    )
    worker = SpeakerLiveWorker(connector=connector, diarizer=diarizer_obj, embedder=embedder_obj)
    live_session_id = f"probe-{_now_slug()}"

    connector.start_session(table.id, live_session_id)
    for chunk in audio_chunks:
        connector.ingest_audio_chunk(table.id, live_session_id, chunk)

    worker_results = _drain_worker(worker, table.id, live_session_id)
    connector.finish_session(table.id, live_session_id)
    worker_results_after_finish = _drain_worker(worker, table.id, live_session_id)

    rewrite_client_obj = rewrite_client or build_dialog_client(settings_obj)
    alias_rewrite_service = SpeakerAliasRewriteService(
        session_manager=session_manager,
        dialog_client=rewrite_client_obj,
        poll_interval_seconds=300.0,
    )
    try:
        alias_rewrite_results = _drain_alias_rewrite(alias_rewrite_service, table.id)
    except Exception as exc:
        alias_rewrite_results = [
            {
                "table_id": table.id,
                "status": "failed",
                "error": str(exc),
            }
        ]

    live_session_state = connector.describe_session(table.id, live_session_id)
    worker_state = worker.describe_worker_session(table.id, live_session_id)
    speaker_identities = session_manager.list_speaker_identities(table.id)
    speaker_alias_map = session_manager.list_speaker_alias_map(table.id)
    dialog_context = session_manager.list_dialog_context(table.id)

    summary = {
        "input_path": str(input_path),
        "output_dir": str(probe_dir),
        "converted_wav_path": str(converted_wav),
        "ffmpeg": ffmpeg_info,
        "wav": wav_info,
        "chunk_seconds": chunk_seconds,
        "chunk_count": len(audio_chunks),
        "transcript": transcript_result,
        "transcript_parts": transcript_parts,
        "transcript_event": transcript_event,
        "runtime": {
            "transcriber_type": type(audio_gateway.transcriber).__name__,
            "diarizer_type": type(diarizer_obj).__name__,
            "embedder_type": type(embedder_obj).__name__,
            "rewrite_client_type": type(rewrite_client_obj).__name__,
            "transcriber_is_placeholder": audio_gateway.transcriber.__class__.__name__.startswith("Placeholder"),
            "diarizer_is_placeholder": diarizer_obj.__class__.__name__.startswith("Placeholder"),
            "embedder_is_placeholder": embedder_obj.__class__.__name__.startswith("Placeholder"),
            "rewrite_client_is_placeholder": rewrite_client_obj.__class__.__name__.startswith("Placeholder"),
        },
        "live_session_id": live_session_id,
        "table_id": table.id,
        "worker_result": worker_results[0] if worker_results else None,
        "worker_results": worker_results,
        "worker_result_after_finish": worker_results_after_finish[0] if worker_results_after_finish else None,
        "worker_results_after_finish": worker_results_after_finish,
        "alias_rewrite_results": alias_rewrite_results,
        "alias_rewrite_state": alias_rewrite_service.describe_table_state(table.id),
        "live_session_state": live_session_state,
        "worker_state": worker_state,
        "speaker_identities": speaker_identities,
        "speaker_alias_map": speaker_alias_map,
        "dialog_context": dialog_context,
    }

    summary_path = probe_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe transcript + diarization + alias rewrite pipeline.")
    parser.add_argument("--input", required=True, help="Path to a local audio file, e.g. .m4a or .wav")
    parser.add_argument(
        "--output-dir",
        default=".runtime/speaker-identity-probe",
        help="Directory where probe artifacts will be written",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=float,
        default=1.0,
        help="Chunk size used to simulate live audio ingestion",
    )
    args = parser.parse_args()

    summary = run_speaker_identity_probe(
        settings_obj=settings,
        input_path=_ensure_path(args.input),
        output_dir=_ensure_path(args.output_dir),
        chunk_seconds=args.chunk_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
