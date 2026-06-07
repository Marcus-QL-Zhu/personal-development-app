from .tencent_asr import PlaceholderSentenceTranscriber, SentenceTranscriber


class AudioGateway:
    def __init__(self, transcriber: SentenceTranscriber | None = None) -> None:
        self.buffers: dict[str, bytearray] = {}
        self.clips: dict[str, list[dict[str, object]]] = {}
        self.transcriber = transcriber or PlaceholderSentenceTranscriber()

    def ingest_chunk(self, table_id: str, chunk: bytes) -> dict:
        self.buffers.setdefault(table_id, bytearray()).extend(chunk)
        return {"accepted": True, "bytes": len(chunk)}

    def ingest_clip(self, table_id: str, filename: str, clip_bytes: bytes) -> dict:
        transcript = self.transcriber.transcribe(table_id, filename, clip_bytes)
        record = {
            "filename": filename,
            "bytes": len(clip_bytes),
            "content": transcript,
        }
        self.clips.setdefault(table_id, []).append(record)
        return {
            "kind": "voice_transcript",
            "filename": filename,
            "content": record["content"],
        }
