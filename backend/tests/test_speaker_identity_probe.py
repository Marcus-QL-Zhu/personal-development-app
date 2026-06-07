from __future__ import annotations

import wave
from pathlib import Path

import pytest


class FakeTranscriber:
    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = list(transcripts)
        self.calls: list[tuple[str, str, bytes]] = []

    def transcribe(self, table_id: str, filename: str, clip_bytes: bytes) -> str:
        self.calls.append((table_id, filename, clip_bytes))
        return self.transcripts.pop(0)


class FakeDiarizer:
    def diarize(self, *, table_id: str, live_session_id: str, audio_chunks, audio_bytes):
        return [
            {
                "diarized_speaker_id": "SPEAKER_00",
                "speaker_profile_id": "profile-00",
                "segment_start_ms": 0,
                "segment_end_ms": 1000,
                "transcript_text": "Alice: hello",
                "diarization_confidence": 0.98,
            },
            {
                "diarized_speaker_id": "SPEAKER_01",
                "speaker_profile_id": "profile-01",
                "segment_start_ms": 1000,
                "segment_end_ms": 2000,
                "transcript_text": "Bob: hi",
                "diarization_confidence": 0.97,
            },
        ]


class FakeEmbedder:
    def embed(self, *, table_id: str, live_session_id: str, audio_chunks, audio_bytes, diarization_segments):
        return [
            {
                "diarized_speaker_id": "SPEAKER_00",
                "embedding": [0.1, 0.2, 0.3],
                "sample_count": 1,
            },
            {
                "diarized_speaker_id": "SPEAKER_01",
                "embedding": [0.4, 0.5, 0.6],
                "sample_count": 1,
            },
        ]


class FakeRewriteClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def rewrite_speaker_alias_map(self, *, dialogue_events, current_alias_map):
        self.calls.append(
            {
                "dialogue_events": list(dialogue_events),
                "current_alias_map": dict(current_alias_map),
            }
        )
        rewritten = {}
        for speaker_id in sorted(current_alias_map):
            if speaker_id == "player_a":
                rewritten[speaker_id] = ["Alice"]
            elif speaker_id == "player_b":
                rewritten[speaker_id] = ["Bob"]
            else:
                rewritten[speaker_id] = []
        return rewritten


class FailingRewriteClient:
    def rewrite_speaker_alias_map(self, *, dialogue_events, current_alias_map):
        raise RuntimeError("rewrite failed")


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 16000)


def test_speaker_identity_probe_runs_transcript_diarization_and_alias_rewrite(
    tmp_path,
    monkeypatch,
):
    from gamevoice_server import speaker_identity_probe as probe_module
    from gamevoice_server.config import settings

    input_path = tmp_path / "round.m4a"
    input_path.write_bytes(b"fake-m4a")
    converted_wav = tmp_path / "converted.wav"
    _write_wav(converted_wav)

    def fake_run_ffmpeg_to_wav(input_path, output_path, *, sample_rate, channels):
        output_path.write_bytes(converted_wav.read_bytes())
        return {"ffmpeg_exe": "ffmpeg", "command": ["ffmpeg"], "stdout": "", "stderr": ""}

    monkeypatch.setattr(probe_module, "_run_ffmpeg_to_wav", fake_run_ffmpeg_to_wav)
    monkeypatch.setattr(probe_module, "_read_wav_chunks", lambda path, *, chunk_seconds: [b"chunk-0", b"chunk-1"])
    monkeypatch.setattr(probe_module, "_load_wav_metadata", lambda path: {"sample_rate": 16000, "channels": 1, "frame_count": 16000, "duration_seconds": 1.0, "sample_width_bytes": 2})

    summary = probe_module.run_speaker_identity_probe(
        settings_obj=settings,
        input_path=input_path,
        output_dir=tmp_path,
        chunk_seconds=1.0,
        transcriber=FakeTranscriber(["Alice and", "Bob are here."]),
        diarizer=FakeDiarizer(),
        embedder=FakeEmbedder(),
        rewrite_client=FakeRewriteClient(),
    )

    assert summary["transcript"]["content"] == "Alice and Bob are here."
    assert summary["speaker_identities"]
    assert summary["speaker_alias_map"].get("player_a") == ["宝宝", "Alice"]
    assert summary["alias_rewrite_results"][-1]["speaker_alias_map"].get("player_a") == ["宝宝", "Alice"]
    assert summary["transcript"]["filename"] == "input.wav"
    assert len(summary["transcript_parts"]) == 2
    assert summary["transcript_parts"][0]["filename"].startswith("input-chunk-")
    assert summary["transcript"]["content"] == "Alice and Bob are here."


def test_speaker_identity_probe_preserves_transcript_even_if_alias_rewrite_fails(
    tmp_path,
    monkeypatch,
):
    from gamevoice_server import speaker_identity_probe as probe_module
    from gamevoice_server.config import settings

    input_path = tmp_path / "round.m4a"
    input_path.write_bytes(b"fake-m4a")
    converted_wav = tmp_path / "converted.wav"
    _write_wav(converted_wav)

    def fake_run_ffmpeg_to_wav(input_path, output_path, *, sample_rate, channels):
        output_path.write_bytes(converted_wav.read_bytes())
        return {"ffmpeg_exe": "ffmpeg", "command": ["ffmpeg"], "stdout": "", "stderr": ""}

    monkeypatch.setattr(probe_module, "_run_ffmpeg_to_wav", fake_run_ffmpeg_to_wav)
    monkeypatch.setattr(probe_module, "_read_wav_chunks", lambda path, *, chunk_seconds: [b"chunk-0"])
    monkeypatch.setattr(probe_module, "_load_wav_metadata", lambda path: {"sample_rate": 16000, "channels": 1, "frame_count": 16000, "duration_seconds": 1.0, "sample_width_bytes": 2})

    summary = probe_module.run_speaker_identity_probe(
        settings_obj=settings,
        input_path=input_path,
        output_dir=tmp_path,
        chunk_seconds=1.0,
        transcriber=FakeTranscriber(["Only one chunk."]),
        diarizer=FakeDiarizer(),
        embedder=FakeEmbedder(),
        rewrite_client=FailingRewriteClient(),
    )

    assert summary["transcript"]["content"] == "Only one chunk."
    assert summary["speaker_identities"]
    assert summary["alias_rewrite_results"][-1]["status"] == "failed"
