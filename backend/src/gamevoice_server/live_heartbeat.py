from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol


class _RandomLike(Protocol):
    def uniform(self, minimum: float, maximum: float) -> float:
        ...


def reliable_heartbeat_player_names(
    alias_map: dict[str, list[str]],
    *,
    assistant_name: str = "",
) -> list[str]:
    assistant = " ".join(str(assistant_name or "").split()).strip()
    names: list[str] = []
    seen: set[str] = set()
    for raw_aliases in alias_map.values():
        for raw_alias in raw_aliases or []:
            alias = " ".join(str(raw_alias or "").split()).strip()
            if not alias or alias in seen:
                continue
            if alias in {assistant, "宝宝"}:
                continue
            if alias.startswith("speaker_") or alias.startswith("player_"):
                continue
            seen.add(alias)
            names.append(alias)
    return names


@dataclass
class _HeartbeatState:
    deadline_monotonic: float
    inflight: bool = False


class LiveHeartbeatScheduler:
    def __init__(
        self,
        *,
        min_seconds: float,
        max_seconds: float,
        rng: _RandomLike | None = None,
    ) -> None:
        self.min_seconds = max(0.0, float(min_seconds))
        self.max_seconds = max(self.min_seconds, float(max_seconds))
        self._rng = rng or random.Random()
        self._states: dict[str, _HeartbeatState] = {}

    def on_listening_started(
        self,
        table_id: str,
        *,
        now_monotonic: float | None = None,
    ) -> dict:
        state = self._states.get(table_id)
        if state is None:
            state = self._new_state(now_monotonic)
            self._states[table_id] = state
        return self.snapshot(table_id)

    def on_listening_stopped(self, table_id: str) -> None:
        self._states.pop(table_id, None)

    def on_agent_speech_started(
        self,
        table_id: str,
        *,
        now_monotonic: float | None = None,
    ) -> dict:
        state = self._new_state(now_monotonic)
        self._states[table_id] = state
        return self.snapshot(table_id)

    def mark_inflight(self, table_id: str) -> dict:
        state = self._states.get(table_id)
        if state is not None:
            state.inflight = True
        return self.snapshot(table_id)

    def mark_finished(
        self,
        table_id: str,
        *,
        now_monotonic: float | None = None,
    ) -> dict:
        return self.on_agent_speech_started(table_id, now_monotonic=now_monotonic)

    def should_fire(
        self,
        table_id: str,
        *,
        now_monotonic: float | None = None,
        is_listening: bool,
        is_agent_speaking: bool,
        has_pending_assistant_audio: bool,
        user_voice_active: bool,
    ) -> bool:
        state = self._states.get(table_id)
        if state is None:
            return False
        if state.inflight:
            return False
        if not is_listening:
            return False
        # Continuous table talk should not suppress heartbeat forever. Playback
        # still gets its own short barge-in grace window after the reply is ready.
        _ = user_voice_active
        if is_agent_speaking or has_pending_assistant_audio:
            return False
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now >= state.deadline_monotonic

    def snapshot(self, table_id: str) -> dict:
        state = self._states.get(table_id)
        if state is None:
            return {
                "table_id": table_id,
                "active": False,
                "deadline_monotonic": None,
                "inflight": False,
            }
        return {
            "table_id": table_id,
            "active": True,
            "deadline_monotonic": state.deadline_monotonic,
            "inflight": state.inflight,
        }

    def _new_state(self, now_monotonic: float | None) -> _HeartbeatState:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        delay = self._rng.uniform(self.min_seconds, self.max_seconds)
        return _HeartbeatState(deadline_monotonic=now + delay)
