from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4


class TTSStreamBridge:
    def __init__(self) -> None:
        self._streams: dict[str, dict] = {}
        self._lock = threading.RLock()

    def start_stream(self, speech_job: dict) -> dict:
        with self._lock:
            stream = self._build_empty_stream(
                job_id=speech_job.get("job_id"),
                turn_id=speech_job.get("turn_id"),
                reply_id=speech_job.get("reply_id"),
                segment_count=len(speech_job.get("segment_statuses", [])),
            )
            for segment in speech_job.get("segment_statuses", []):
                output_path = segment.get("output_path")
                if not output_path:
                    raise ValueError("tts segment has no output_path")
                path = Path(output_path)
                if not path.exists():
                    raise FileNotFoundError(output_path)
                self._append_chunk_locked(
                    stream,
                    segment_index=segment.get("index", 0),
                    text=segment.get("text", ""),
                    audio_bytes=path.read_bytes(),
                )
            self._finish_stream_locked(stream)
            return self.snapshot(stream["stream_id"])

    def open_stream(
        self,
        *,
        job_id: str,
        turn_id: str | None = None,
        reply_id: str | None = None,
        segment_count: int = 0,
    ) -> dict:
        with self._lock:
            stream = self._build_empty_stream(
                job_id=job_id,
                turn_id=turn_id,
                reply_id=reply_id,
                segment_count=segment_count,
            )
            return self.snapshot(stream["stream_id"])

    def open_stream_with_id(
        self,
        stream_id: str,
        *,
        job_id: str,
        turn_id: str | None = None,
        reply_id: str | None = None,
        segment_count: int = 0,
    ) -> dict:
        """Re-open an existing stream by its ID, allowing chunks to be appended to it."""
        with self._lock:
            if stream_id in self._streams:
                return self.snapshot(stream_id)
            stream = self._build_empty_stream(
                job_id=job_id,
                turn_id=turn_id,
                reply_id=reply_id,
                segment_count=segment_count,
            )
            stream["stream_id"] = stream_id
            self._streams[stream_id] = stream
            return self.snapshot(stream_id)

    def append_chunk(
        self,
        stream_id: str,
        *,
        segment_index: int,
        text: str,
        audio_bytes: bytes,
    ) -> dict:
        with self._lock:
            stream = self._get_stream(stream_id)
            self._append_chunk_locked(
                stream,
                segment_index=segment_index,
                text=text,
                audio_bytes=audio_bytes,
            )
            return self.snapshot(stream_id)

    def finish_stream(self, stream_id: str) -> dict:
        with self._lock:
            stream = self._get_stream(stream_id)
            self._finish_stream_locked(stream)
            return self.snapshot(stream_id)

    def next_chunk(self, stream_id: str, wait_timeout: float | None = None) -> dict | None:
        with self._lock:
            stream = self._get_stream(stream_id)
            if stream["state"] == "cancelled":
                return None

            if wait_timeout:
                while (
                    stream["next_index"] >= len(stream["chunks"])
                    and stream["state"] == "streaming"
                    and wait_timeout > 0
                ):
                    stream["condition"].wait(timeout=wait_timeout)
                    if stream["next_index"] < len(stream["chunks"]) or stream["state"] != "streaming":
                        break
                    break

            next_index = stream["next_index"]
            chunks = stream["chunks"]
            if next_index >= len(chunks):
                if stream["state"] == "finished":
                    stream["state"] = "completed"
                return None

            chunk = chunks[next_index]
            stream["next_index"] += 1
            is_final = stream["next_index"] >= len(chunks) and stream["state"] == "finished"
            if is_final:
                stream["state"] = "completed"

            return {
                "stream_id": stream_id,
                "job_id": stream["job_id"],
                "chunk_index": next_index,
                "segment_index": chunk["segment_index"],
                "text": chunk["text"],
                "audio_bytes": chunk["audio_bytes"],
                "is_final": is_final,
                "turn_id": stream.get("turn_id"),
                "reply_id": stream.get("reply_id"),
            }

    def cancel_stream(self, stream_id: str) -> dict:
        with self._lock:
            stream = self._get_stream(stream_id)
            stream["state"] = "cancelled"
            stream["condition"].notify_all()
            return self.snapshot(stream_id)

    def snapshot(self, stream_id: str) -> dict:
        with self._lock:
            stream = self._get_stream(stream_id)
            visible_state = stream["state"]
            if visible_state == "finished" and stream["next_index"] < len(stream["chunks"]):
                visible_state = "streaming"
            return {
                "stream_id": stream["stream_id"],
                "job_id": stream["job_id"],
                "state": visible_state,
                "next_index": stream["next_index"],
                "segment_count": stream["segment_count"],
                "turn_id": stream.get("turn_id"),
                "reply_id": stream.get("reply_id"),
            }

    def _build_empty_stream(
        self,
        *,
        job_id: str,
        turn_id: str | None,
        reply_id: str | None,
        segment_count: int,
    ) -> dict:
        stream_id = uuid4().hex
        condition = threading.Condition(self._lock)
        stream = {
            "stream_id": stream_id,
            "job_id": job_id,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "state": "streaming",
            "chunks": [],
            "next_index": 0,
            "segment_count": segment_count,
            "condition": condition,
        }
        self._streams[stream_id] = stream
        return stream

    @staticmethod
    def _append_chunk_locked(
        stream: dict,
        *,
        segment_index: int,
        text: str,
        audio_bytes: bytes,
    ) -> None:
        stream["chunks"].append(
            {
                "segment_index": segment_index,
                "text": text,
                "audio_bytes": audio_bytes,
            }
        )
        stream["condition"].notify_all()

    @staticmethod
    def _finish_stream_locked(stream: dict) -> None:
        if stream["state"] == "streaming":
            stream["state"] = "finished"
        stream["condition"].notify_all()

    def _get_stream(self, stream_id: str) -> dict:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise KeyError(stream_id)
        return stream
