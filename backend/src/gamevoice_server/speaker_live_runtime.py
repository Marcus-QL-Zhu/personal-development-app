from __future__ import annotations

import importlib.util
import contextlib
import os
import sys
import tempfile
import types
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings

def _coerce_float_embedding(value: Any) -> list[float] | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return None
    embedding: list[float] = []
    for item in value:
        try:
            embedding.append(float(item))
        except (TypeError, ValueError):
            return None
    return embedding or None


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _write_pcm_wav(
    path: Path,
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    channels: int,
    sample_width_bytes: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width_bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)


def _slice_pcm_wav(
    source_path: Path,
    destination_path: Path,
    *,
    sample_rate: int,
    start_ms: int | None,
    end_ms: int | None,
) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source_path), "rb") as source:
        channels = source.getnchannels()
        sample_width_bytes = source.getsampwidth()
        frame_rate = source.getframerate() or sample_rate
        total_frames = source.getnframes()
        start_frame = max(0, int(round((start_ms or 0) * frame_rate / 1000.0)))
        end_frame = total_frames if end_ms is None else max(
            start_frame,
            min(total_frames, int(round(end_ms * frame_rate / 1000.0))),
        )
        start_frame = min(start_frame, total_frames)
        source.setpos(start_frame)
        frame_count = max(0, end_frame - start_frame)
        frames = source.readframes(frame_count)

    with wave.open(str(destination_path), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width_bytes)
        destination.setframerate(frame_rate)
        destination.writeframes(frames)


def _pad_wav_to_min_frames(path: Path, *, min_frames: int) -> None:
    if min_frames <= 0:
        return
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width_bytes = source.getsampwidth()
        frame_rate = source.getframerate()
        current_frames = source.getnframes()
        frames = source.readframes(current_frames)
    if current_frames >= min_frames:
        return
    missing_frames = min_frames - current_frames
    padding = b"\x00" * missing_frames * channels * sample_width_bytes
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width_bytes)
        destination.setframerate(frame_rate)
        destination.writeframes(frames + padding)


def _load_pcm_waveform(
    source_path: Path,
    *,
    sample_rate: int,
) -> dict[str, Any]:
    import torch

    with wave.open(str(source_path), "rb") as source:
        channels = source.getnchannels()
        sample_width_bytes = source.getsampwidth()
        frame_rate = source.getframerate() or sample_rate
        frame_count = source.getnframes()
        frames = source.readframes(frame_count)

    if sample_width_bytes == 1:
        dtype = torch.uint8
    elif sample_width_bytes == 2:
        dtype = torch.int16
    elif sample_width_bytes == 4:
        dtype = torch.int32
    else:
        dtype = torch.int16
    waveform = torch.frombuffer(memoryview(frames), dtype=dtype)
    if channels > 1:
        waveform = waveform.reshape(-1, channels).transpose(0, 1)
    else:
        waveform = waveform.unsqueeze(0)
    if dtype.is_floating_point:
        waveform = waveform.to(torch.float32)
    else:
        max_value = float(2 ** (8 * sample_width_bytes - 1))
        waveform = waveform.to(torch.float32) / max_value
    return {"waveform": waveform, "sample_rate": frame_rate}


def _ensure_torchaudio_legacy_api() -> None:
    try:
        import torchaudio
    except Exception:
        return
    if hasattr(torchaudio, "set_audio_backend"):
        return

    def _set_audio_backend(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    torchaudio.set_audio_backend = _set_audio_backend  # type: ignore[attr-defined]

    if "torchaudio.sox_effects" not in sys.modules:
        sox_effects = types.ModuleType("torchaudio.sox_effects")

        def _apply_effects_tensor(
            waveform: Any,
            sample_rate: int,
            effects: Any,
        ) -> tuple[Any, int]:
            del effects
            return waveform, sample_rate

        sox_effects.apply_effects_tensor = _apply_effects_tensor  # type: ignore[attr-defined]
        sys.modules["torchaudio.sox_effects"] = sox_effects
        setattr(torchaudio, "sox_effects", sox_effects)


@dataclass(frozen=True)
class SpeakerLiveAudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2


class PlaceholderSpeakerLiveDiarizer:
    def diarize(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
    ) -> list[dict[str, Any]]:
        del table_id, live_session_id, audio_chunks, audio_bytes
        return []


class PlaceholderSpeakerLiveEmbedder:
    def embed(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
        diarization_segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del table_id, live_session_id, audio_chunks, audio_bytes, diarization_segments
        return []


class PyannoteAudioDiarizer:
    def __init__(
        self,
        *,
        pipeline: Any | None = None,
        model_id: str = "pyannote/speaker-diarization-community-1",
        token: str | None = None,
        audio_format: SpeakerLiveAudioFormat | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        sample_width_bytes: int | None = None,
    ) -> None:
        self._pipeline = pipeline
        self.pipeline = pipeline
        self.model_id = model_id
        self.token = token
        self.audio_format = audio_format or SpeakerLiveAudioFormat(
            sample_rate=sample_rate or 16000,
            channels=channels or 1,
            sample_width_bytes=sample_width_bytes or 2,
        )

    def _load_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        from pyannote.audio import Pipeline

        if self.token:
            self._pipeline = Pipeline.from_pretrained(self.model_id, token=self.token)
        else:
            self._pipeline = Pipeline.from_pretrained(self.model_id)
        self.pipeline = self._pipeline
        return self._pipeline

    def diarize(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
    ) -> list[dict[str, Any]]:
        del table_id, live_session_id, audio_chunks
        pcm_path = Path(tempfile.mkdtemp(prefix="speaker-live-pyannote-")) / "session.wav"
        _write_pcm_wav(
            pcm_path,
            b"".join(audio_bytes),
            sample_rate=self.audio_format.sample_rate,
            channels=self.audio_format.channels,
            sample_width_bytes=self.audio_format.sample_width_bytes,
        )
        pipeline = self._load_pipeline()
        output = pipeline(_load_pcm_waveform(pcm_path, sample_rate=self.audio_format.sample_rate))
        diarization = getattr(output, "speaker_diarization", None)
        if diarization is None:
            return []
        result: list[dict[str, Any]] = []
        for index, item in enumerate(diarization):
            turn, speaker = item
            speaker_label = str(speaker)
            result.append(
                {
                    "segment_id": f"segment-{index}",
                    "diarized_speaker_id": speaker_label,
                    "speaker_profile_id": speaker_label,
                    "segment_start_ms": int(round(float(turn.start) * 1000.0)),
                    "segment_end_ms": int(round(float(turn.end) * 1000.0)),
                    "transcript_text": "",
                    "channel": 0,
                    "diarization_confidence": None,
                }
            )
        return result


class WeSpeakerEmbedder:
    def __init__(
        self,
        *,
        model: Any | None = None,
        model_name: str = "chinese",
        model_home: str | None = None,
        audio_format: SpeakerLiveAudioFormat | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        sample_width_bytes: int | None = None,
        min_embedding_frames: int = 400,
    ) -> None:
        self._model = model
        self.model = model
        self.model_name = model_name
        self.model_home = model_home
        self.min_embedding_frames = min_embedding_frames
        self.audio_format = audio_format or SpeakerLiveAudioFormat(
            sample_rate=sample_rate or 16000,
            channels=channels or 1,
            sample_width_bytes=sample_width_bytes or 2,
        )

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self.model_home:
            os.environ.setdefault("WESPEAKER_HOME", self.model_home)
        _ensure_torchaudio_legacy_api()
        import wespeaker

        self._model = wespeaker.load_model(self.model_name)
        self.model = self._model
        return self._model

    def embed(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
        diarization_segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del table_id, live_session_id, audio_chunks
        if not diarization_segments:
            return []

        pcm_path = Path(tempfile.mkdtemp(prefix="speaker-live-wespeaker-")) / "session.wav"
        _write_pcm_wav(
            pcm_path,
            b"".join(audio_bytes),
            sample_rate=self.audio_format.sample_rate,
            channels=self.audio_format.channels,
            sample_width_bytes=self.audio_format.sample_width_bytes,
        )
        model = self._load_model()
        embeddings: list[dict[str, Any]] = []
        for index, segment in enumerate(diarization_segments):
            segment_start_ms = _as_int(segment.get("segment_start_ms"), 0)
            segment_end_ms = segment.get("segment_end_ms")
            segment_path = pcm_path.parent / f"{pcm_path.stem}-{index}.wav"
            _slice_pcm_wav(
                pcm_path,
                segment_path,
                sample_rate=self.audio_format.sample_rate,
                start_ms=segment_start_ms,
                end_ms=_as_int(segment_end_ms, segment_start_ms),
            )
            _pad_wav_to_min_frames(segment_path, min_frames=self.min_embedding_frames)
            embedding = model.extract_embedding(str(segment_path))
            embeddings.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "diarized_speaker_id": segment.get("diarized_speaker_id"),
                    "speaker_profile_id": segment.get("speaker_profile_id") or segment.get("diarized_speaker_id"),
                    "embedding": _coerce_float_embedding(embedding) or [],
                    "sample_count": segment.get("sample_count") or 1,
                }
            )
        return embeddings


def build_speaker_live_runtime(
    settings: Settings,
    *,
    diarizer: Any | None = None,
    embedder: Any | None = None,
) -> tuple[Any, Any]:
    audio_format = SpeakerLiveAudioFormat(
        sample_rate=settings.speaker_live_sample_rate,
        channels=settings.speaker_live_channels,
        sample_width_bytes=settings.speaker_live_sample_width_bytes,
    )
    if diarizer is None:
        if _module_available("pyannote.audio"):
            diarizer = PyannoteAudioDiarizer(
                model_id=settings.speaker_live_pyannote_model_id,
                token=settings.speaker_live_pyannote_token,
                audio_format=audio_format,
            )
        else:
            diarizer = PlaceholderSpeakerLiveDiarizer()
    if embedder is None:
        if _module_available("wespeaker"):
            embedder = WeSpeakerEmbedder(
                model_name=settings.speaker_live_wespeaker_model_name,
                model_home=settings.speaker_live_wespeaker_home,
                audio_format=audio_format,
            )
        else:
            embedder = PlaceholderSpeakerLiveEmbedder()
    return diarizer, embedder
