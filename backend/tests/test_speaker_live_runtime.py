from __future__ import annotations

from pathlib import Path

import torchaudio

from gamevoice_server.speaker_live_runtime import (
    PyannoteAudioDiarizer,
    WeSpeakerEmbedder,
    _ensure_torchaudio_legacy_api,
)


class FakePyannotePipeline:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, audio_input: object, hook=None):
        self.calls.append(audio_input)

        class Turn:
            def __init__(self, start: float, end: float) -> None:
                self.start = start
                self.end = end

        return type(
            "FakeOutput",
            (),
            {
                "speaker_diarization": [
                    (Turn(0.0, 1.25), "SPEAKER_00"),
                    (Turn(1.25, 2.5), "SPEAKER_01"),
                ]
            },
        )()


class FakeWeSpeakerModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract_embedding(self, audio_path: str):
        self.calls.append(audio_path)
        return [0.1, 0.2, 0.3]


def _pcm_silence_bytes(seconds: float, sample_rate: int = 16000) -> bytes:
    sample_count = int(seconds * sample_rate)
    return b"\x00\x00" * sample_count


def test_pyannote_audio_diarizer_normalizes_pipeline_output(tmp_path):
    diarizer = PyannoteAudioDiarizer(
        pipeline=FakePyannotePipeline(),
        sample_rate=16000,
        channels=1,
        sample_width_bytes=2,
    )
    result = diarizer.diarize(
        table_id="table-a",
        live_session_id="live-1",
        audio_chunks=[{"chunk_index": 0, "byte_count": 10}],
        audio_bytes=[_pcm_silence_bytes(2.5)],
    )

    assert len(result) == 2
    assert isinstance(diarizer.pipeline.calls[0], dict)
    assert "waveform" in diarizer.pipeline.calls[0]
    assert diarizer.pipeline.calls[0]["sample_rate"] == 16000
    assert result[0]["diarized_speaker_id"] == "SPEAKER_00"
    assert result[0]["segment_start_ms"] == 0
    assert result[0]["segment_end_ms"] == 1250
    assert result[1]["diarized_speaker_id"] == "SPEAKER_01"
    assert result[1]["segment_start_ms"] == 1250
    assert result[1]["segment_end_ms"] == 2500


def test_wespeaker_embedder_slices_each_diarized_segment(tmp_path):
    embedder = WeSpeakerEmbedder(
        model=FakeWeSpeakerModel(),
        sample_rate=16000,
        channels=1,
        sample_width_bytes=2,
    )
    result = embedder.embed(
        table_id="table-a",
        live_session_id="live-1",
        audio_chunks=[{"chunk_index": 0, "byte_count": 10}],
        audio_bytes=[_pcm_silence_bytes(3.0)],
        diarization_segments=[
            {
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 0,
                "segment_end_ms": 1000,
            },
            {
                "diarized_speaker_id": "SPEAKER_01",
                "segment_start_ms": 1000,
                "segment_end_ms": 2500,
            },
        ],
    )

    assert len(result) == 2
    assert result[0]["diarized_speaker_id"] == "SPEAKER_00"
    assert result[0]["embedding"] == [0.1, 0.2, 0.3]
    assert result[1]["diarized_speaker_id"] == "SPEAKER_01"
    assert result[1]["embedding"] == [0.1, 0.2, 0.3]
    assert len(embedder.model.calls) == 2
    assert all(Path(path).exists() for path in embedder.model.calls)


def test_torchaudio_legacy_shim_adds_missing_backend_api():
    original = getattr(torchaudio, "set_audio_backend", None)
    original_sox_effects = getattr(torchaudio, "sox_effects", None)
    if original is not None:
        delattr(torchaudio, "set_audio_backend")
    if original_sox_effects is not None:
        delattr(torchaudio, "sox_effects")

    _ensure_torchaudio_legacy_api()

    assert hasattr(torchaudio, "set_audio_backend")
    assert hasattr(torchaudio, "sox_effects")
    assert hasattr(torchaudio.sox_effects, "apply_effects_tensor")
    torchaudio.set_audio_backend("sox_io")
    waveform, sample_rate = torchaudio.sox_effects.apply_effects_tensor("wave", 16000, [])
    assert waveform == "wave"
    assert sample_rate == 16000

    if original is not None:
        torchaudio.set_audio_backend = original
    if original_sox_effects is not None:
        torchaudio.sox_effects = original_sox_effects
