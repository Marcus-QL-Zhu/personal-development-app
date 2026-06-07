from __future__ import annotations

import base64
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .speaker_live_connector import SpeakerLiveConnector


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpeakerLiveDiarizer(Protocol):
    def diarize(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
    ) -> list[dict[str, Any]]:
        ...


class SpeakerLiveEmbedder(Protocol):
    def embed(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
        diarization_segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...


class PlaceholderSpeakerLiveDiarizer:
    def diarize(
        self,
        *,
        table_id: str,
        live_session_id: str,
        audio_chunks: list[dict[str, Any]],
        audio_bytes: list[bytes],
    ) -> list[dict[str, Any]]:
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
        return []


@dataclass
class SpeakerLiveWorkerState:
    table_id: str
    live_session_id: str
    last_processed_chunk_index: int = -1
    processed_batch_count: int = 0
    processed_chunk_count: int = 0
    last_run_at: str | None = None
    last_result_source: str | None = None
    last_status: str = "idle"
    last_ingested_count: int = 0
    last_ingested_observation_count: int = 0


class SpeakerLiveWorker:
    def __init__(
        self,
        *,
        connector: SpeakerLiveConnector,
        diarizer: SpeakerLiveDiarizer | None = None,
        embedder: SpeakerLiveEmbedder | None = None,
        source: str = "pyannote_wespeaker",
    ) -> None:
        self.connector = connector
        self.diarizer = diarizer or PlaceholderSpeakerLiveDiarizer()
        self.embedder = embedder or PlaceholderSpeakerLiveEmbedder()
        self.source = source
        self._states: dict[str, dict[str, SpeakerLiveWorkerState]] = {}
        self._locks: dict[str, dict[str, threading.Lock]] = {}

    def _get_state(self, table_id: str, live_session_id: str) -> SpeakerLiveWorkerState:
        table_states = self._states.setdefault(table_id, {})
        state = table_states.get(live_session_id)
        if state is None:
            state = SpeakerLiveWorkerState(table_id=table_id, live_session_id=live_session_id)
            table_states[live_session_id] = state
        return state

    def _get_lock(self, table_id: str, live_session_id: str) -> threading.Lock:
        table_locks = self._locks.setdefault(table_id, {})
        lock = table_locks.get(live_session_id)
        if lock is None:
            lock = threading.Lock()
            table_locks[live_session_id] = lock
        return lock

    def describe_worker_session(self, table_id: str, live_session_id: str) -> dict[str, Any] | None:
        state = self._states.get(table_id, {}).get(live_session_id)
        if state is None:
            return None
        return {
            "table_id": state.table_id,
            "live_session_id": state.live_session_id,
            "last_processed_chunk_index": state.last_processed_chunk_index,
            "processed_batch_count": state.processed_batch_count,
            "processed_chunk_count": state.processed_chunk_count,
            "last_run_at": state.last_run_at,
            "last_result_source": state.last_result_source,
            "last_status": state.last_status,
            "last_ingested_count": state.last_ingested_count,
            "last_ingested_observation_count": state.last_ingested_observation_count,
        }

    def process_session(
        self,
        table_id: str,
        live_session_id: str,
        *,
        after_chunk_index: int | None = None,
        limit: int = 32,
    ) -> dict[str, Any]:
        state = self._get_state(table_id, live_session_id)
        lock = self._get_lock(table_id, live_session_id)
        if not lock.acquire(blocking=False):
            state.last_run_at = _now_iso()
            state.last_status = "busy"
            return {
                "status": "busy",
                "table_id": table_id,
                "live_session_id": live_session_id,
                "worker_state": self.describe_worker_session(table_id, live_session_id),
                "pulled": {
                    "table_id": table_id,
                    "live_session_id": live_session_id,
                    "after_chunk_index": state.last_processed_chunk_index if after_chunk_index is None else after_chunk_index,
                    "chunks": [],
                    "next_after_chunk_index": state.last_processed_chunk_index if after_chunk_index is None else after_chunk_index,
                    "live_session_state": self.connector.describe_session(table_id, live_session_id),
                },
                "speaker_identity_batch": None,
            }
        cursor = state.last_processed_chunk_index if after_chunk_index is None else after_chunk_index
        try:
            pulled = self.connector.pull_audio_chunks(
                table_id,
                live_session_id,
                after_chunk_index=cursor,
                limit=limit,
            )
            chunks = list(pulled.get("chunks") or [])
            state.last_run_at = _now_iso()
            if not chunks:
                state.last_status = "idle"
                return {
                    "status": "idle",
                    "table_id": table_id,
                    "live_session_id": live_session_id,
                    "worker_state": self.describe_worker_session(table_id, live_session_id),
                    "pulled": pulled,
                    "speaker_identity_batch": None,
                }

            audio_bytes = [base64.b64decode(item["audio_base64"]) for item in chunks]
            diarization_segments = self.diarizer.diarize(
                table_id=table_id,
                live_session_id=live_session_id,
                audio_chunks=chunks,
                audio_bytes=audio_bytes,
            )
            speaker_embeddings = self.embedder.embed(
                table_id=table_id,
                live_session_id=live_session_id,
                audio_chunks=chunks,
                audio_bytes=audio_bytes,
                diarization_segments=diarization_segments,
            )
            result = self.connector.ingest_live_pipeline_batch(
                table_id,
                live_session_id,
                source=self.source,
                pyannote_segments=diarization_segments,
                speaker_embeddings=speaker_embeddings,
            )
            state.last_processed_chunk_index = int(chunks[-1]["chunk_index"])
            state.processed_batch_count += 1
            state.processed_chunk_count += len(chunks)
            state.last_result_source = self.source
            state.last_status = "processed"
            state.last_ingested_count = int(result["speaker_identity_batch"].get("ingested_count", 0))
            state.last_ingested_observation_count = int(result["speaker_identity_batch"].get("ingested_count", 0))
            return {
                "status": "processed",
                "table_id": table_id,
                "live_session_id": live_session_id,
                "worker_state": self.describe_worker_session(table_id, live_session_id),
                "pulled": pulled,
                "speaker_identity_batch": result["speaker_identity_batch"],
            }
        finally:
            lock.release()
