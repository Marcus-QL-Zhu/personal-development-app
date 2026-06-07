from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol


class FrameClassifierUnavailable(RuntimeError):
    pass


class FrameClassifier(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        ...


@dataclass(frozen=True)
class SilenceGateConfig:
    enabled: bool = True
    sample_rate: int = 16000
    sample_width_bytes: int = 2
    channels: int = 1
    frame_ms: int = 20
    pre_roll_ms: int = 300
    speech_start_window_ms: int = 200
    speech_start_voiced_ms: int = 60
    hangover_ms: int = 700

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.sample_width_bytes * self.channels

    @property
    def frame_bytes(self) -> int:
        return int(self.bytes_per_second * self.frame_ms / 1000)

    @property
    def pre_roll_bytes(self) -> int:
        return int(self.bytes_per_second * self.pre_roll_ms / 1000)

    @property
    def speech_start_window_frames(self) -> int:
        return max(1, int(self.speech_start_window_ms / self.frame_ms))

    @property
    def speech_start_voiced_frames(self) -> int:
        return max(1, int(self.speech_start_voiced_ms / self.frame_ms))

    @property
    def hangover_frames(self) -> int:
        return max(1, int(self.hangover_ms / self.frame_ms))


@dataclass
class SilenceGateDecision:
    forward_chunks: list[bytes]
    state: str
    input_bytes: int
    forwarded_bytes: int
    suppressed_bytes: int
    voiced_frames: int
    total_frames: int
    preroll_flushed: bool = False
    error: str | None = None


class WebRtcVadFrameClassifier:
    def __init__(self, mode: int = 1) -> None:
        try:
            import webrtcvad
        except Exception as exc:  # pragma: no cover - depends on local native package
            raise FrameClassifierUnavailable(str(exc)) from exc
        self._vad = webrtcvad.Vad(mode)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        return bool(self._vad.is_speech(frame, sample_rate))


@dataclass
class SilenceGate:
    config: SilenceGateConfig = field(default_factory=SilenceGateConfig)
    frame_classifier: FrameClassifier | None = None

    def __post_init__(self) -> None:
        self._state = "idle"
        self._disabled_by_error = False
        self._last_error: str | None = None
        self._frame_buffer = bytearray()
        self._preroll = bytearray()
        self._speech_window: deque[bool] = deque(maxlen=self.config.speech_start_window_frames)
        self._silent_frames_after_speech = 0
        if self.frame_classifier is None and self.config.enabled:
            try:
                self.frame_classifier = WebRtcVadFrameClassifier()
            except FrameClassifierUnavailable as exc:
                self.frame_classifier = None
                self._disabled_by_error = True
                self._last_error = str(exc)

    def process_chunk(self, chunk: bytes) -> SilenceGateDecision:
        if not self.config.enabled:
            return self._pass_through(chunk, "disabled")
        if self._disabled_by_error or self.frame_classifier is None:
            return self._pass_through(chunk, "failed_open", error=self._last_error)

        input_bytes = len(chunk)
        self._frame_buffer.extend(chunk)
        frames: list[bytes] = []
        while len(self._frame_buffer) >= self.config.frame_bytes:
            frames.append(bytes(self._frame_buffer[: self.config.frame_bytes]))
            del self._frame_buffer[: self.config.frame_bytes]

        if not frames:
            self._append_preroll(chunk)
            return SilenceGateDecision(
                [],
                self._state,
                input_bytes,
                0,
                input_bytes,
                0,
                0,
            )

        try:
            frame_decisions = [
                bool(self.frame_classifier.is_speech(frame, self.config.sample_rate))
                for frame in frames
            ]
        except Exception as exc:
            self._disabled_by_error = True
            self._last_error = str(exc)
            return SilenceGateDecision(
                [chunk],
                "failed_open",
                input_bytes,
                input_bytes,
                0,
                0,
                len(frames),
                error=str(exc),
            )

        voiced_frames = sum(1 for item in frame_decisions if item)
        forward_chunks: list[bytes] = []
        preroll_flushed = False

        if self._state == "idle":
            for is_voiced in frame_decisions:
                self._speech_window.append(is_voiced)
            if sum(1 for item in self._speech_window if item) >= self.config.speech_start_voiced_frames:
                if self._preroll:
                    forward_chunks.append(bytes(self._preroll))
                    self._preroll.clear()
                    preroll_flushed = True
                forward_chunks.append(chunk)
                self._state = "speech"
                self._silent_frames_after_speech = 0
            else:
                self._append_preroll(chunk)
        else:
            if voiced_frames:
                forward_chunks.append(chunk)
                self._state = "speech"
                self._silent_frames_after_speech = 0
            else:
                self._silent_frames_after_speech += len(frames)
                if self._silent_frames_after_speech >= self.config.hangover_frames:
                    self._state = "idle"
                    self._speech_window.clear()
                    self._append_preroll(chunk)
                else:
                    forward_chunks.append(chunk)

        forwarded_bytes = sum(len(item) for item in forward_chunks)
        return SilenceGateDecision(
            forward_chunks,
            self._state,
            input_bytes,
            forwarded_bytes,
            max(0, input_bytes - forwarded_bytes),
            voiced_frames,
            len(frames),
            preroll_flushed=preroll_flushed,
        )

    def _append_preroll(self, chunk: bytes) -> None:
        self._preroll.extend(chunk)
        overflow = len(self._preroll) - self.config.pre_roll_bytes
        if overflow > 0:
            del self._preroll[:overflow]

    def _pass_through(self, chunk: bytes, state: str, *, error: str | None = None) -> SilenceGateDecision:
        return SilenceGateDecision(
            [chunk],
            state,
            len(chunk),
            len(chunk),
            0,
            0,
            0,
            error=error,
        )
