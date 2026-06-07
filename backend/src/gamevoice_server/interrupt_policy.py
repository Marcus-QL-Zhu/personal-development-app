import time
from collections.abc import Callable


class InterruptPolicy:
    def __init__(
        self,
        *,
        cooldown_seconds: float = 4.0,
        conversation_window_seconds: float = 20.0,
        max_conversation_replies_per_window: int = 1,
        time_provider: Callable[[], float] | None = None,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.conversation_window_seconds = conversation_window_seconds
        self.max_conversation_replies_per_window = max_conversation_replies_per_window
        self._time_provider = time_provider or time.monotonic
        self._table_state: dict[str, dict[str, str | float]] = {}

    def should_allow(self, table_id: str, transcript: str, *, mode: str, decision_reason: str) -> dict:
        normalized = self._normalize(transcript)
        if not normalized:
            return {"allowed": False, "reason": "empty_transcript"}

        state = self._table_state.setdefault(table_id, {})
        self._prune_conversation_timestamps(state)
        if state.get("last_transcript") == normalized:
            return {"allowed": False, "reason": "duplicate_transcript"}

        explicit_user_request = self._is_explicit_user_request(decision_reason)

        last_triggered_at = state.get("last_triggered_at")
        if isinstance(last_triggered_at, float) and not explicit_user_request:
            elapsed = self._time_provider() - last_triggered_at
            if elapsed < self.cooldown_seconds:
                return {"allowed": False, "reason": "cooldown"}

        if (
            mode == "conversation"
            and not explicit_user_request
            and len(state.get("conversation_reply_times", [])) >= self.max_conversation_replies_per_window
        ):
            return {"allowed": False, "reason": "conversation_quota"}

        return {"allowed": True, "reason": "allowed"}

    def record_trigger(self, table_id: str, transcript: str, *, mode: str, decision_reason: str) -> None:
        state = self._table_state.setdefault(table_id, {})
        now = self._time_provider()
        state["last_transcript"] = self._normalize(transcript)
        state["last_triggered_at"] = now
        conversation_times = list(state.get("conversation_reply_times", []))
        if mode == "conversation" and not self._is_explicit_user_request(decision_reason):
            conversation_times.append(now)
        state["conversation_reply_times"] = conversation_times
        self._prune_conversation_timestamps(state)

    @staticmethod
    def _normalize(transcript: str) -> str:
        return " ".join(transcript.strip().lower().split())

    @staticmethod
    def _is_explicit_user_request(decision_reason: str) -> bool:
        return decision_reason in {"direct_address", "assistant_name_called"}

    def _prune_conversation_timestamps(self, state: dict[str, str | float]) -> None:
        cutoff = self._time_provider() - self.conversation_window_seconds
        conversation_times = [
            value
            for value in list(state.get("conversation_reply_times", []))
            if isinstance(value, float) and value >= cutoff
        ]
        state["conversation_reply_times"] = conversation_times
