from gamevoice_server.live_silence_gate import (
    FrameClassifierUnavailable,
    SilenceGate,
    SilenceGateConfig,
)


class FakeFrameClassifier:
    def __init__(self, decisions: list[bool]) -> None:
        self.decisions = list(decisions)
        self.frames: list[bytes] = []

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        self.frames.append(frame)
        if not self.decisions:
            return False
        return self.decisions.pop(0)


def make_gate(decisions: list[bool]) -> SilenceGate:
    return SilenceGate(
        SilenceGateConfig(
            enabled=True,
            sample_rate=16000,
            sample_width_bytes=2,
            channels=1,
            frame_ms=20,
            pre_roll_ms=60,
            speech_start_window_ms=100,
            speech_start_voiced_ms=40,
            hangover_ms=60,
        ),
        frame_classifier=FakeFrameClassifier(decisions),
    )


def frame(byte: bytes = b"a") -> bytes:
    return byte * 640


def test_idle_silence_is_suppressed_after_preroll_buffering():
    gate = make_gate([False, False, False])

    first = gate.process_chunk(frame(b"0"))
    second = gate.process_chunk(frame(b"1"))
    third = gate.process_chunk(frame(b"2"))

    assert first.forward_chunks == []
    assert second.forward_chunks == []
    assert third.forward_chunks == []
    assert third.state == "idle"
    assert third.suppressed_bytes == 640


def test_speech_start_flushes_preroll_before_current_speech():
    gate = make_gate([False, False, True, True])

    gate.process_chunk(frame(b"0"))
    gate.process_chunk(frame(b"1"))
    start = gate.process_chunk(frame(b"2") + frame(b"3"))

    assert start.state == "speech"
    assert start.forward_chunks == [frame(b"0") + frame(b"1"), frame(b"2") + frame(b"3")]
    assert start.preroll_flushed is True


def test_speech_hangover_forwards_tail_silence_then_returns_to_idle():
    gate = make_gate([True, True, False, False, False, False])

    start = gate.process_chunk(frame(b"s") + frame(b"t"))
    tail1 = gate.process_chunk(frame(b"1"))
    tail2 = gate.process_chunk(frame(b"2"))
    idle = gate.process_chunk(frame(b"3"))

    assert start.state == "speech"
    assert tail1.forward_chunks == [frame(b"1")]
    assert tail2.forward_chunks == [frame(b"2")]
    assert idle.forward_chunks == []
    assert idle.state == "idle"


def test_disabled_gate_forwards_audio_without_classification():
    classifier = FakeFrameClassifier([False])
    gate = SilenceGate(
        SilenceGateConfig(enabled=False),
        frame_classifier=classifier,
    )

    decision = gate.process_chunk(b"raw audio")

    assert decision.forward_chunks == [b"raw audio"]
    assert decision.state == "disabled"
    assert classifier.frames == []


def test_classifier_error_fails_open_and_disables_gate():
    class BrokenClassifier:
        def is_speech(self, frame: bytes, sample_rate: int) -> bool:
            raise FrameClassifierUnavailable("missing native dependency")

    gate = SilenceGate(
        SilenceGateConfig(enabled=True),
        frame_classifier=BrokenClassifier(),
    )

    decision = gate.process_chunk(frame())

    assert decision.forward_chunks == [frame()]
    assert decision.state == "failed_open"
    assert decision.error == "missing native dependency"


def test_partial_frame_is_buffered_without_classification():
    classifier = FakeFrameClassifier([True])
    gate = SilenceGate(
        SilenceGateConfig(enabled=True, sample_rate=16000, sample_width_bytes=2, channels=1, frame_ms=20),
        frame_classifier=classifier,
    )

    decision = gate.process_chunk(b"short")

    assert decision.forward_chunks == []
    assert decision.total_frames == 0
    assert classifier.frames == []
