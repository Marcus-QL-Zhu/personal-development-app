from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import contextlib
from typing import Any

from .identity_linker import IdentityLinker
from .speaker_pipeline_adapter import SpeakerPipelineAdapter
from .session_manager import SessionManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SpeakerLiveSessionState:
    table_id: str
    live_session_id: str
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    ended_at: str | None = None
    audio_chunk_count: int = 0
    audio_byte_count: int = 0
    audio_chunk_enqueued_count: int = 0
    audio_chunk_pulled_count: int = 0
    last_transcript: str = ""
    last_speaker_id: str | None = None
    last_speaker_label: str | None = None
    ingested_batch_count: int = 0
    ingested_observation_count: int = 0
    last_batch_source: str | None = None


class SpeakerLiveConnector:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        identity_linker: IdentityLinker,
        pipeline_adapter: SpeakerPipelineAdapter,
    ) -> None:
        self.session_manager = session_manager
        self.identity_linker = identity_linker
        self.pipeline_adapter = pipeline_adapter
        self.on_audio_chunk_enqueued = None
        self.on_identity_batch_ingested = None
        self._live_sessions: dict[str, dict[str, SpeakerLiveSessionState]] = {}
        self._audio_chunks: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._identity_listeners: dict[str, list] = {}

    def add_identity_listener(self, table_id: str, listener) -> None:
        listeners = self._identity_listeners.setdefault(table_id, [])
        if listener not in listeners:
            listeners.append(listener)

    def remove_identity_listener(self, table_id: str, listener) -> None:
        listeners = self._identity_listeners.get(table_id)
        if not listeners:
            return
        with contextlib.suppress(ValueError):
            listeners.remove(listener)
        if not listeners:
            self._identity_listeners.pop(table_id, None)

    def _notify_identity_listeners(self, table_id: str, payload: dict[str, Any]) -> None:
        listeners = list(self._identity_listeners.get(table_id, []))
        if not listeners:
            return
        for listener in listeners:
            try:
                listener(dict(payload))
            except Exception:
                continue

    def start_session(self, table_id: str, live_session_id: str) -> SpeakerLiveSessionState:
        table_sessions = self._live_sessions.setdefault(table_id, {})
        state = table_sessions.get(live_session_id)
        if state is None:
            state = SpeakerLiveSessionState(table_id=table_id, live_session_id=live_session_id)
            table_sessions[live_session_id] = state
        state.updated_at = _now_iso()
        return state

    def ingest_audio_chunk(self, table_id: str, live_session_id: str, chunk: bytes) -> SpeakerLiveSessionState:
        state = self.start_session(table_id, live_session_id)
        session_chunks = self._audio_chunks.setdefault(table_id, {}).setdefault(live_session_id, [])
        chunk_record = {
            "chunk_index": len(session_chunks),
            "received_at": _now_iso(),
            "byte_count": len(chunk),
            "audio_base64": base64.b64encode(chunk).decode("ascii"),
        }
        session_chunks.append(chunk_record)
        state.audio_chunk_count += 1
        state.audio_byte_count += len(chunk)
        state.audio_chunk_enqueued_count += 1
        state.updated_at = _now_iso()
        if self.on_audio_chunk_enqueued is not None:
            self.on_audio_chunk_enqueued(table_id, live_session_id)
        return state

    def pull_audio_chunks(
        self,
        table_id: str,
        live_session_id: str,
        *,
        after_chunk_index: int = -1,
        limit: int = 32,
    ) -> dict[str, Any]:
        state = self.start_session(table_id, live_session_id)
        session_chunks = self._audio_chunks.get(table_id, {}).get(live_session_id, [])
        chunks = [dict(item) for item in session_chunks if int(item["chunk_index"]) > after_chunk_index][:limit]
        state.audio_chunk_pulled_count += len(chunks)
        state.updated_at = _now_iso()
        return {
            "table_id": table_id,
            "live_session_id": live_session_id,
            "after_chunk_index": after_chunk_index,
            "chunks": chunks,
            "next_after_chunk_index": chunks[-1]["chunk_index"] if chunks else after_chunk_index,
            "live_session_state": self.describe_session(table_id, live_session_id),
        }

    def ingest_live_pipeline_batch(
        self,
        table_id: str,
        live_session_id: str,
        *,
        source: str,
        pyannote_segments: list[dict[str, Any]] | None = None,
        diarization_segments: list[dict[str, Any]] | None = None,
        speaker_embeddings: list[dict[str, Any]] | None = None,
        name_candidates: list[dict[str, Any]] | None = None,
    ) -> dict:
        state = self.start_session(table_id, live_session_id)
        batch = self.pipeline_adapter.build_batch(
            source=source,
            session_id=live_session_id,
            pyannote_segments=pyannote_segments,
            diarization_segments=diarization_segments,
            speaker_embeddings=speaker_embeddings,
            name_candidates=name_candidates,
        )
        linked = self.identity_linker.ingest_pipeline_batch(
            self.session_manager.tables[table_id].speaker_identity_state,
            source=batch["source"],
            session_id=batch["session_id"],
            diarization_segments=batch["diarization_segments"],
            speaker_embeddings=batch["speaker_embeddings"],
            name_candidates=batch["name_candidates"],
        )
        ingested = self.session_manager.ingest_speaker_identity_batch(table_id, linked)
        state.ingested_batch_count += 1
        state.ingested_observation_count += int(linked.get("ingested_count", 0))
        state.last_batch_source = batch["source"]
        state.updated_at = _now_iso()
        payload = {
            "event": "speaker_identity_batch",
            "live_session_id": live_session_id,
            "table_id": table_id,
            "live_session_state": self.describe_session(table_id, live_session_id),
            "speaker_identity_batch": ingested,
        }
        if self.on_identity_batch_ingested is not None:
            self.on_identity_batch_ingested(dict(payload))
        self._notify_identity_listeners(table_id, payload)
        return payload

    def ingest_observations(
        self,
        table_id: str,
        live_session_id: str,
        *,
        source: str,
        observations: list[dict[str, Any]],
    ) -> dict:
        state = self.start_session(table_id, live_session_id)
        batch = self.identity_linker.ingest_segments(
            self.session_manager.tables[table_id].speaker_identity_state,
            source=source,
            session_id=live_session_id,
            observations=observations,
        )
        ingested = self.session_manager.ingest_speaker_identity_batch(table_id, batch)
        state.ingested_batch_count += 1
        state.ingested_observation_count += int(batch.get("ingested_count", 0))
        state.last_batch_source = source
        state.updated_at = _now_iso()
        payload = {
            "event": "speaker_identity_batch",
            "live_session_id": live_session_id,
            "table_id": table_id,
            "live_session_state": self.describe_session(table_id, live_session_id),
            "speaker_identity_batch": ingested,
        }
        if self.on_identity_batch_ingested is not None:
            self.on_identity_batch_ingested(dict(payload))
        self._notify_identity_listeners(table_id, payload)
        return payload

    def update_transcript(
        self,
        table_id: str,
        live_session_id: str,
        transcript: str,
        *,
        speaker_id: str | None = None,
        speaker_label: str | None = None,
    ) -> SpeakerLiveSessionState:
        state = self.start_session(table_id, live_session_id)
        state.last_transcript = transcript.strip()
        if speaker_id:
            state.last_speaker_id = speaker_id
            state.last_speaker_label = speaker_label or speaker_id
        state.updated_at = _now_iso()
        return state

    def finish_session(self, table_id: str, live_session_id: str) -> SpeakerLiveSessionState | None:
        table_sessions = self._live_sessions.get(table_id)
        if not table_sessions:
            return None
        state = table_sessions.get(live_session_id)
        if state is None:
            return None
        state.ended_at = _now_iso()
        state.updated_at = state.ended_at
        if self.on_audio_chunk_enqueued is not None and state.audio_chunk_pulled_count < state.audio_chunk_enqueued_count:
            self.on_audio_chunk_enqueued(table_id, live_session_id)
        return state

    def describe_session(self, table_id: str, live_session_id: str) -> dict[str, Any] | None:
        table_sessions = self._live_sessions.get(table_id)
        if not table_sessions:
            return None
        state = table_sessions.get(live_session_id)
        if state is None:
            return None
        return {
            "table_id": state.table_id,
            "live_session_id": state.live_session_id,
            "started_at": state.started_at,
            "updated_at": state.updated_at,
            "ended_at": state.ended_at,
            "audio_chunk_count": state.audio_chunk_count,
            "audio_byte_count": state.audio_byte_count,
            "audio_chunk_enqueued_count": state.audio_chunk_enqueued_count,
            "audio_chunk_pulled_count": state.audio_chunk_pulled_count,
            "pending_audio_chunk_count": max(
                0,
                state.audio_chunk_enqueued_count - state.audio_chunk_pulled_count,
            ),
            "last_transcript": state.last_transcript,
            "last_speaker_id": state.last_speaker_id,
            "last_speaker_label": state.last_speaker_label,
            "ingested_batch_count": state.ingested_batch_count,
            "ingested_observation_count": state.ingested_observation_count,
            "last_batch_source": state.last_batch_source,
        }
