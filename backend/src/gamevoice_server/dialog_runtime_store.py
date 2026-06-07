from .dialog_runtime import DialogRuntime


class DialogRuntimeStore:
    def __init__(self) -> None:
        self._runtimes: dict[str, DialogRuntime] = {}

    def ensure_table(self, table_id: str) -> DialogRuntime:
        runtime = self._runtimes.get(table_id)
        if runtime is None:
            runtime = DialogRuntime()
            self._runtimes[table_id] = runtime
        return runtime

    def snapshot(self, table_id: str) -> dict:
        return self.ensure_table(table_id).snapshot()

    def on_user_audio(self, table_id: str) -> dict:
        return self.ensure_table(table_id).on_user_audio()

    def on_user_turn_committed(self, table_id: str) -> dict:
        return self.ensure_table(table_id).on_user_turn_committed()

    def on_agent_preview_ready(
        self,
        table_id: str,
        *,
        reply_text: str | None = None,
        source_text: str | None = None,
        stream_id: str | None = None,
        job_id: str | None = None,
        lookup_marker: bool = False,
    ) -> dict:
        return self.ensure_table(table_id).on_agent_preview_ready(
            reply_text=reply_text,
            source_text=source_text,
            stream_id=stream_id,
            job_id=job_id,
            lookup_marker=lookup_marker,
        )

    def on_agent_reply_ready(
        self,
        table_id: str,
        *,
        job_id: str | None = None,
        reply_text: str | None = None,
        source_text: str | None = None,
        segment_count: int = 0,
    ) -> dict:
        return self.ensure_table(table_id).on_agent_reply_ready(
            job_id=job_id,
            reply_text=reply_text,
            source_text=source_text,
            segment_count=segment_count,
        )

    def on_priority_agent_reply_ready(
        self,
        table_id: str,
        *,
        job_id: str | None = None,
        reply_text: str | None = None,
        source_text: str | None = None,
        segment_count: int = 0,
        barge_in_grace_seconds: float = 2.5,
    ) -> dict:
        return self.ensure_table(table_id).on_priority_agent_reply_ready(
            job_id=job_id,
            reply_text=reply_text,
            source_text=source_text,
            segment_count=segment_count,
            barge_in_grace_seconds=barge_in_grace_seconds,
        )

    def set_pending_formal_text(
        self,
        table_id: str,
        text: str | None,
        *,
        source_text: str | None = None,
        preview_text: str | None = None,
        preview_job_id: str | None = None,
    ) -> None:
        self.ensure_table(table_id).set_pending_formal_text(
            text,
            source_text=source_text,
            preview_text=preview_text,
            preview_job_id=preview_job_id,
        )

    def clear_pending_formal_text(self, table_id: str) -> None:
        self.ensure_table(table_id).clear_pending_formal_text()

    def on_agent_speaking_started(
        self,
        table_id: str,
        *,
        job_id: str | None = None,
        segment_index: int | None = None,
    ) -> dict:
        return self.ensure_table(table_id).on_agent_speaking_started(
            job_id=job_id,
            segment_index=segment_index,
        )

    def on_agent_segment_completed(
        self,
        table_id: str,
        *,
        job_id: str | None = None,
        segment_index: int | None = None,
    ) -> dict:
        return self.ensure_table(table_id).on_agent_segment_completed(
            job_id=job_id,
            segment_index=segment_index,
        )

    def on_agent_reply_interrupted(self, table_id: str, *, job_id: str | None = None) -> dict:
        return self.ensure_table(table_id).on_agent_reply_interrupted(job_id=job_id)

    def on_agent_reply_skipped(self, table_id: str) -> dict:
        return self.ensure_table(table_id).on_agent_reply_skipped()

    def on_agent_speaking_finished(self, table_id: str, *, job_id: str | None = None) -> dict:
        return self.ensure_table(table_id).on_agent_speaking_finished(job_id=job_id)
