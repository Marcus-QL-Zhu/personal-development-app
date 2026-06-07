import time


class DialogRuntime:
    def __init__(self) -> None:
        self.state = "listening"
        self.is_user_speaking = False
        self.is_agent_speaking = False
        self.last_event = "initialized"
        self.interrupted = False
        self.current_job_id: str | None = None
        self.pending_reply_text: str | None = None
        self.pending_source_text: str | None = None
        self.preview_reply_text: str | None = None
        self.preview_source_text: str | None = None
        self.preview_stream_id: str | None = None
        self.preview_job_id: str | None = None
        self.preview_lookup_marker = False
        self.pending_formal_text: str | None = None
        self.pending_formal_source_text: str | None = None
        self.pending_formal_preview_text: str | None = None
        self.pending_formal_preview_job_id: str | None = None
        self.last_completed_job_id: str | None = None
        self.queue_depth = 0
        self.current_segment_index: int | None = None
        self.started_segment_count = 0
        self.completed_segment_count = 0
        self.priority_reply_job_id: str | None = None
        self.barge_in_grace_until_monotonic: float | None = None

    def _is_stale_job_update(self, job_id: str | None) -> bool:
        if not job_id:
            return False
        if self.current_job_id is None and self.last_completed_job_id == job_id:
            return True
        if not self.current_job_id:
            return False
        return self.current_job_id != job_id

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "is_user_speaking": self.is_user_speaking,
            "is_agent_speaking": self.is_agent_speaking,
            "last_event": self.last_event,
            "interrupted": self.interrupted,
            "current_job_id": self.current_job_id,
            "pending_reply_text": self.pending_reply_text,
            "pending_source_text": self.pending_source_text,
            "preview_reply_text": self.preview_reply_text,
            "preview_source_text": self.preview_source_text,
            "preview_stream_id": self.preview_stream_id,
            "preview_job_id": self.preview_job_id,
            "preview_lookup_marker": self.preview_lookup_marker,
            "pending_formal_text": self.pending_formal_text,
            "pending_formal_source_text": self.pending_formal_source_text,
            "pending_formal_preview_text": self.pending_formal_preview_text,
            "pending_formal_preview_job_id": self.pending_formal_preview_job_id,
            "last_completed_job_id": self.last_completed_job_id,
            "queue_depth": self.queue_depth,
            "current_segment_index": self.current_segment_index,
            "started_segment_count": self.started_segment_count,
            "completed_segment_count": self.completed_segment_count,
            "priority_reply_job_id": self.priority_reply_job_id,
            "barge_in_grace_until_monotonic": self.barge_in_grace_until_monotonic,
            "barge_in_protected": self.is_barge_in_protected(),
        }

    def on_user_audio(self) -> dict:
        interrupted = self.is_agent_speaking or self.state == "agent_speaking"
        self.is_user_speaking = True
        self.is_agent_speaking = False if interrupted else self.is_agent_speaking
        self.interrupted = interrupted
        self.state = "interrupted" if interrupted else "user_turn"
        self.last_event = "user_audio"
        return self.snapshot()

    def on_user_turn_committed(self) -> dict:
        self.is_user_speaking = False
        self.interrupted = False
        self.state = "agent_thinking"
        self.last_event = "user_turn_committed"
        return self.snapshot()

    def on_agent_preview_ready(
        self,
        *,
        reply_text: str | None = None,
        source_text: str | None = None,
        stream_id: str | None = None,
        job_id: str | None = None,
        lookup_marker: bool = False,
    ) -> dict:
        self.preview_reply_text = reply_text
        self.preview_source_text = source_text
        self.preview_stream_id = stream_id
        self.preview_job_id = job_id
        self.preview_lookup_marker = bool(lookup_marker)
        self.clear_pending_formal_text()
        self.last_event = "agent_preview_ready"
        return self.snapshot()

    def set_pending_formal_text(
        self,
        text: str | None,
        *,
        source_text: str | None = None,
        preview_text: str | None = None,
        preview_job_id: str | None = None,
    ) -> None:
        self.pending_formal_text = text
        self.pending_formal_source_text = source_text if text else None
        self.pending_formal_preview_text = preview_text if text else None
        self.pending_formal_preview_job_id = preview_job_id if text else None

    def clear_pending_formal_text(self) -> None:
        self.pending_formal_text = None
        self.pending_formal_source_text = None
        self.pending_formal_preview_text = None
        self.pending_formal_preview_job_id = None

    def on_agent_reply_ready(
        self,
        *,
        job_id: str | None = None,
        reply_text: str | None = None,
        source_text: str | None = None,
        segment_count: int = 0,
    ) -> dict:
        self.is_user_speaking = False
        self.state = "assistant_ready"
        self.current_job_id = job_id
        self.pending_reply_text = reply_text
        self.pending_source_text = source_text
        self.preview_reply_text = None
        self.preview_source_text = None
        self.preview_stream_id = None
        self.preview_job_id = None
        self.preview_lookup_marker = False
        self.clear_pending_formal_text()
        self.queue_depth = segment_count
        self.current_segment_index = None
        self.started_segment_count = 0
        self.completed_segment_count = 0
        self.priority_reply_job_id = None
        self.barge_in_grace_until_monotonic = None
        self.last_event = "agent_reply_ready"
        return self.snapshot()

    def on_priority_agent_reply_ready(
        self,
        *,
        job_id: str | None = None,
        reply_text: str | None = None,
        source_text: str | None = None,
        segment_count: int = 0,
        barge_in_grace_seconds: float = 2.5,
        now_monotonic: float | None = None,
    ) -> dict:
        self.on_agent_reply_ready(
            job_id=job_id,
            reply_text=reply_text,
            source_text=source_text,
            segment_count=segment_count,
        )
        now = time.monotonic() if now_monotonic is None else now_monotonic
        self.priority_reply_job_id = job_id
        self.barge_in_grace_until_monotonic = now + max(0.0, barge_in_grace_seconds)
        self.last_event = "priority_agent_reply_ready"
        return self.snapshot()

    def is_barge_in_protected(self, *, now_monotonic: float | None = None) -> bool:
        if not self.priority_reply_job_id:
            return False
        if self.current_job_id != self.priority_reply_job_id:
            return False
        if self.completed_segment_count < 1:
            return True
        if self.barge_in_grace_until_monotonic is None:
            return False
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now < self.barge_in_grace_until_monotonic

    def on_agent_speaking_started(
        self,
        *,
        job_id: str | None = None,
        segment_index: int | None = None,
    ) -> dict:
        if self._is_stale_job_update(job_id):
            self.last_event = "stale_agent_speaking_started"
            return self.snapshot()
        self.is_user_speaking = False
        self.is_agent_speaking = True
        self.interrupted = False
        self.state = "agent_speaking"
        if job_id is not None:
            self.current_job_id = job_id
        self.current_segment_index = segment_index
        if segment_index is not None:
            self.started_segment_count = max(self.started_segment_count, segment_index + 1)
        self.last_event = "agent_speaking_started"
        return self.snapshot()

    def on_agent_segment_completed(
        self,
        *,
        job_id: str | None = None,
        segment_index: int | None = None,
    ) -> dict:
        if self._is_stale_job_update(job_id):
            self.last_event = "stale_agent_segment_completed"
            return self.snapshot()
        if job_id is not None:
            self.current_job_id = job_id
        if segment_index is not None:
            self.current_segment_index = segment_index
        self.completed_segment_count += 1
        self.queue_depth = max(0, self.queue_depth - 1)
        self.last_event = "agent_segment_completed"
        return self.snapshot()

    def on_agent_reply_interrupted(self, *, job_id: str | None = None) -> dict:
        self.is_user_speaking = False
        self.is_agent_speaking = False
        self.interrupted = True
        self.state = "interrupted"
        self.last_event = "agent_reply_interrupted"
        if job_id is not None:
            self.current_job_id = job_id
        self.pending_source_text = None
        self.preview_reply_text = None
        self.preview_source_text = None
        self.preview_stream_id = None
        self.preview_job_id = None
        self.preview_lookup_marker = False
        self.clear_pending_formal_text()
        self.current_segment_index = None
        self.started_segment_count = 0
        self.priority_reply_job_id = None
        self.barge_in_grace_until_monotonic = None
        return self.snapshot()

    def on_agent_reply_skipped(self) -> dict:
        self.is_user_speaking = False
        self.is_agent_speaking = False
        self.interrupted = False
        self.state = "listening"
        self.last_event = "agent_reply_skipped"
        self.current_job_id = None
        self.pending_reply_text = None
        self.pending_source_text = None
        self.preview_reply_text = None
        self.preview_source_text = None
        self.preview_stream_id = None
        self.preview_job_id = None
        self.preview_lookup_marker = False
        self.clear_pending_formal_text()
        self.queue_depth = 0
        self.current_segment_index = None
        self.started_segment_count = 0
        self.completed_segment_count = 0
        self.priority_reply_job_id = None
        self.barge_in_grace_until_monotonic = None
        return self.snapshot()

    def on_agent_speaking_finished(self, *, job_id: str | None = None) -> dict:
        if self._is_stale_job_update(job_id):
            self.last_completed_job_id = job_id
            self.last_event = "stale_agent_speaking_finished"
            return self.snapshot()
        self.is_user_speaking = False
        self.is_agent_speaking = False
        self.interrupted = False
        self.state = "listening"
        self.last_event = "agent_speaking_finished"
        self.last_completed_job_id = job_id or self.current_job_id
        self.current_job_id = None
        self.pending_reply_text = None
        self.pending_source_text = None
        self.preview_stream_id = None
        self.preview_job_id = None
        self.preview_lookup_marker = False
        self.queue_depth = 0
        self.current_segment_index = None
        self.started_segment_count = 0
        if self.priority_reply_job_id == job_id:
            self.priority_reply_job_id = None
            self.barge_in_grace_until_monotonic = None
        return self.snapshot()
