from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


RECEIVE_BURST_THRESHOLD_MS = 50.0
RECEIVE_WINDOW_MS = 1000.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveDiagnosticsStore:
    def __init__(self) -> None:
        self._by_table: dict[str, dict] = {}

    def ensure_table(self, table_id: str) -> dict:
        if table_id not in self._by_table:
            self._by_table[table_id] = {
                "websocket_connects": 0,
                "websocket_disconnects": 0,
                "audio_chunks_received": 0,
                "audio_bytes_received": 0,
                "audio_receive_monotonic_ms": None,
                "audio_inter_arrival_ms": None,
                "max_audio_inter_arrival_ms": None,
                "receive_burst_count": 0,
                "max_receive_burst_chunks_per_second": 0,
                "audio_queue_depth_on_enqueue": None,
                "max_audio_queue_depth_on_enqueue": 0,
                "audio_queue_depth_on_dequeue": None,
                "max_audio_queue_depth_on_dequeue": 0,
                "send_worker_lag_ms": None,
                "max_send_worker_lag_ms": None,
                "send_audio_elapsed_ms": None,
                "max_send_audio_elapsed_ms": None,
                "tencent_payload_send_elapsed_ms": None,
                "max_tencent_payload_send_elapsed_ms": None,
                "send_audio_pacing_requested_ms": None,
                "send_audio_pacing_actual_ms": None,
                "max_send_audio_pacing_actual_ms": None,
                "event_loop_lag_ms": None,
                "max_event_loop_lag_ms": None,
                "last_event_loop_lag_at": None,
                "draft_transcripts_forwarded": 0,
                "stable_transcripts_forwarded": 0,
                "final_transcripts_forwarded": 0,
                "last_audio_chunk_at": None,
                "last_draft_transcript_at": None,
                "last_stable_transcript_at": None,
                "last_final_transcript_at": None,
                "last_reconnect_at": None,
                "realtime_reconnects": 0,
                "silence_gate_state": "unknown",
                "silence_gate_passed_chunks": 0,
                "silence_gate_suppressed_chunks": 0,
                "silence_gate_suppressed_bytes": 0,
                "silence_gate_preroll_flushes": 0,
                "silence_gate_last_decision": None,
                "silence_gate_last_error": None,
                "last_error": None,
                "_recent_audio_receive_monotonic_ms": [],
            }
        return self._by_table[table_id]

    def snapshot(self, table_id: str) -> dict:
        state = deepcopy(self.ensure_table(table_id))
        return {
            key: value
            for key, value in state.items()
            if not key.startswith("_")
        }

    def mark_websocket_connected(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["websocket_connects"] += 1

    def mark_websocket_disconnected(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["websocket_disconnects"] += 1

    def mark_audio_chunk_received(
        self,
        table_id: str,
        byte_count: int,
        *,
        monotonic_ms: float | None = None,
    ) -> None:
        state = self.ensure_table(table_id)
        state["audio_chunks_received"] += 1
        state["audio_bytes_received"] += byte_count
        if monotonic_ms is not None:
            previous_ms = state["audio_receive_monotonic_ms"]
            state["audio_receive_monotonic_ms"] = monotonic_ms
            if previous_ms is not None:
                inter_arrival_ms = max(0.0, monotonic_ms - previous_ms)
                state["audio_inter_arrival_ms"] = inter_arrival_ms
                state["max_audio_inter_arrival_ms"] = (
                    inter_arrival_ms
                    if state["max_audio_inter_arrival_ms"] is None
                    else max(state["max_audio_inter_arrival_ms"], inter_arrival_ms)
                )
                if inter_arrival_ms < RECEIVE_BURST_THRESHOLD_MS:
                    state["receive_burst_count"] += 1
            recent = state["_recent_audio_receive_monotonic_ms"]
            recent.append(monotonic_ms)
            cutoff_ms = monotonic_ms - RECEIVE_WINDOW_MS
            first_kept_index = next(
                (
                    index
                    for index, value in enumerate(recent)
                    if value >= cutoff_ms
                ),
                len(recent),
            )
            del recent[:first_kept_index]
            state["max_receive_burst_chunks_per_second"] = max(
                state["max_receive_burst_chunks_per_second"],
                len(recent),
            )
        state["last_audio_chunk_at"] = _utc_now()

    def mark_audio_enqueue(self, table_id: str, *, queue_depth: int) -> None:
        state = self.ensure_table(table_id)
        state["audio_queue_depth_on_enqueue"] = queue_depth
        state["max_audio_queue_depth_on_enqueue"] = max(
            state["max_audio_queue_depth_on_enqueue"],
            queue_depth,
        )

    def mark_audio_dequeue(
        self,
        table_id: str,
        *,
        queue_depth: int,
        send_worker_lag_ms: float | None = None,
    ) -> None:
        state = self.ensure_table(table_id)
        state["audio_queue_depth_on_dequeue"] = queue_depth
        state["max_audio_queue_depth_on_dequeue"] = max(
            state["max_audio_queue_depth_on_dequeue"],
            queue_depth,
        )
        if send_worker_lag_ms is None:
            return
        state["send_worker_lag_ms"] = send_worker_lag_ms
        state["max_send_worker_lag_ms"] = (
            send_worker_lag_ms
            if state["max_send_worker_lag_ms"] is None
            else max(state["max_send_worker_lag_ms"], send_worker_lag_ms)
        )

    def mark_audio_send_complete(
        self,
        table_id: str,
        *,
        send_audio_elapsed_ms: float,
        tencent_payload_send_elapsed_ms: float | None = None,
        send_audio_pacing_requested_ms: float | None = None,
        send_audio_pacing_actual_ms: float | None = None,
    ) -> None:
        state = self.ensure_table(table_id)
        state["send_audio_elapsed_ms"] = send_audio_elapsed_ms
        state["max_send_audio_elapsed_ms"] = (
            send_audio_elapsed_ms
            if state["max_send_audio_elapsed_ms"] is None
            else max(state["max_send_audio_elapsed_ms"], send_audio_elapsed_ms)
        )
        if tencent_payload_send_elapsed_ms is not None:
            state["tencent_payload_send_elapsed_ms"] = tencent_payload_send_elapsed_ms
            state["max_tencent_payload_send_elapsed_ms"] = (
                tencent_payload_send_elapsed_ms
                if state["max_tencent_payload_send_elapsed_ms"] is None
                else max(
                    state["max_tencent_payload_send_elapsed_ms"],
                    tencent_payload_send_elapsed_ms,
                )
            )
        if send_audio_pacing_requested_ms is not None:
            state["send_audio_pacing_requested_ms"] = send_audio_pacing_requested_ms
        if send_audio_pacing_actual_ms is not None:
            state["send_audio_pacing_actual_ms"] = send_audio_pacing_actual_ms
            state["max_send_audio_pacing_actual_ms"] = (
                send_audio_pacing_actual_ms
                if state["max_send_audio_pacing_actual_ms"] is None
                else max(
                    state["max_send_audio_pacing_actual_ms"],
                    send_audio_pacing_actual_ms,
                )
            )

    def mark_event_loop_lag(self, table_id: str, *, lag_ms: float) -> None:
        state = self.ensure_table(table_id)
        state["event_loop_lag_ms"] = lag_ms
        state["max_event_loop_lag_ms"] = (
            lag_ms
            if state["max_event_loop_lag_ms"] is None
            else max(state["max_event_loop_lag_ms"], lag_ms)
        )
        state["last_event_loop_lag_at"] = _utc_now()

    def mark_draft_forwarded(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["draft_transcripts_forwarded"] += 1
        state["last_draft_transcript_at"] = _utc_now()

    def mark_stable_forwarded(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["stable_transcripts_forwarded"] += 1
        state["last_stable_transcript_at"] = _utc_now()

    def mark_final_forwarded(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["final_transcripts_forwarded"] += 1
        state["last_final_transcript_at"] = _utc_now()

    def mark_realtime_reconnected(self, table_id: str) -> None:
        state = self.ensure_table(table_id)
        state["realtime_reconnects"] += 1
        state["last_reconnect_at"] = _utc_now()

    def mark_silence_gate_decision(
        self,
        table_id: str,
        *,
        state: str,
        input_bytes: int,
        forwarded_bytes: int,
        suppressed_bytes: int,
        voiced_frames: int,
        total_frames: int,
        preroll_flushed: bool,
        error: str | None,
    ) -> None:
        state_map = self.ensure_table(table_id)
        state_map["silence_gate_state"] = state
        if forwarded_bytes > 0:
            state_map["silence_gate_passed_chunks"] += 1
        if suppressed_bytes > 0:
            state_map["silence_gate_suppressed_chunks"] += 1
            state_map["silence_gate_suppressed_bytes"] += suppressed_bytes
        if preroll_flushed:
            state_map["silence_gate_preroll_flushes"] += 1
        state_map["silence_gate_last_decision"] = {
            "input_bytes": input_bytes,
            "forwarded_bytes": forwarded_bytes,
            "suppressed_bytes": suppressed_bytes,
            "voiced_frames": voiced_frames,
            "total_frames": total_frames,
        }
        state_map["silence_gate_last_error"] = error

    def mark_error(self, table_id: str, message: str) -> None:
        state = self.ensure_table(table_id)
        state["last_error"] = message
