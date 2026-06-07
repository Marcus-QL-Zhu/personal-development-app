class FakeSentenceTranscriber:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def transcribe(self, table_id: str, filename: str, clip_bytes: bytes) -> str:
        self.calls.append((table_id, filename, clip_bytes))
        return "actual transcript"


def test_audio_gateway_accepts_stream_chunk(audio_gateway):
    result = audio_gateway.ingest_chunk(table_id="t1", chunk=b"abc")
    assert result["accepted"] is True


def test_audio_gateway_returns_transcriber_result_for_clip():
    transcriber = FakeSentenceTranscriber()
    from gamevoice_server.audio_gateway import AudioGateway

    gateway = AudioGateway(transcriber=transcriber)
    result = gateway.ingest_clip(
        table_id="t1",
        filename="round-1.wav",
        clip_bytes=b"voice-bytes",
    )
    assert result["kind"] == "voice_transcript"
    assert result["filename"] == "round-1.wav"
    assert result["content"] == "actual transcript"
    assert transcriber.calls == [("t1", "round-1.wav", b"voice-bytes")]
