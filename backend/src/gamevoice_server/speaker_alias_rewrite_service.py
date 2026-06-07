from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .dialog_client import MiniMaxDialogClient
from .session_manager import SessionManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_alias(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_alias_list(values: list[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        alias = _normalize_alias(value)
        if not alias or alias in seen:
            continue
        seen.add(alias)
        normalized.append(alias)
    return normalized


def _normalize_alias_map(alias_map: dict[str, list[Any]], *, expected_speaker_ids: list[str]) -> dict[str, list[str]]:
    expected = [str(speaker_id) for speaker_id in expected_speaker_ids]
    expected_set = set(expected)
    normalized: dict[str, list[str]] = {}
    for key, values in alias_map.items():
        speaker_id = str(key)
        if speaker_id not in expected_set:
            raise ValueError(f"unexpected speaker bucket: {speaker_id}")
        normalized[speaker_id] = _normalize_alias_list(list(values or []))
    missing = [speaker_id for speaker_id in expected if speaker_id not in normalized]
    if missing:
        raise ValueError(f"missing speaker buckets: {', '.join(missing)}")
    return normalized


def _alias_map_signature(alias_map: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (speaker_id, tuple(sorted(_normalize_alias_list(aliases))))
        for speaker_id, aliases in sorted(alias_map.items(), key=lambda item: item[0])
    )


@dataclass
class SpeakerAliasRewriteState:
    table_id: str
    last_run_at: str | None = None
    last_cycle_version: int | None = None
    last_active_speaker_ids: list[str] = field(default_factory=list)
    last_context_active_at: str | None = None
    last_dialog_context_count: int = 0
    last_signature: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    consecutive_same_count: int = 0
    stopped: bool = False
    last_status: str = "idle"
    last_result: dict[str, list[str]] | None = None
    last_error: str | None = None


class SpeakerAliasRewriteService:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        dialog_client: MiniMaxDialogClient,
        poll_interval_seconds: float = 300.0,
        active_window_seconds: float = 300.0,
    ) -> None:
        self.session_manager = session_manager
        self.dialog_client = dialog_client
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.active_window_seconds = float(active_window_seconds)
        self._states: dict[str, SpeakerAliasRewriteState] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_updated = None
        self.on_failed = None

    def _get_state(self, table_id: str) -> SpeakerAliasRewriteState:
        state = self._states.get(table_id)
        if state is None:
            state = SpeakerAliasRewriteState(table_id=table_id)
            self._states[table_id] = state
        return state

    def describe_table_state(self, table_id: str) -> dict[str, Any] | None:
        state = self._states.get(table_id)
        if state is None:
            return None
        return {
            "table_id": state.table_id,
            "last_run_at": state.last_run_at,
            "last_cycle_version": state.last_cycle_version,
            "last_active_speaker_ids": list(state.last_active_speaker_ids),
            "last_context_active_at": state.last_context_active_at,
            "last_dialog_context_count": state.last_dialog_context_count,
            "consecutive_same_count": state.consecutive_same_count,
            "stopped": state.stopped,
            "last_status": state.last_status,
            "last_result": dict(state.last_result or {}),
            "last_error": state.last_error,
        }

    def _build_dialogue_events(self, table_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in self.session_manager.list_dialog_context(table_id):
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            events.append(dict(item))
        for item in self.session_manager.list_runtime_events(table_id):
            if item.get("kind") != "speaker_alias_evidence":
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            events.append(dict(item))
        return events

    def _should_skip(self, *, state: SpeakerAliasRewriteState, table_cycle_version: int, active_ids: list[str]) -> bool:
        return (
            state.stopped
            and state.last_cycle_version == table_cycle_version
            and list(state.last_active_speaker_ids) == list(active_ids)
        )

    def _should_stop(self, *, alias_map: dict[str, list[str]], active_ids: list[str], consecutive_same_count: int) -> bool:
        if not active_ids:
            return False
        if consecutive_same_count < 2:
            return False
        for speaker_id in active_ids:
            if not alias_map.get(speaker_id):
                return False
        return True

    def rewrite_table_aliases(self, table_id: str) -> dict[str, Any]:
        if table_id not in self.session_manager.tables:
            raise KeyError(table_id)

        table = self.session_manager.tables[table_id]
        state = self._get_state(table_id)
        current_cycle_version = int(table.compaction_version)
        active_ids = self.session_manager.list_active_speaker_ids(table_id)
        current_alias_map = self.session_manager.list_speaker_alias_map(table_id)

        if state.stopped and self._should_skip(state=state, table_cycle_version=current_cycle_version, active_ids=active_ids):
            state.last_run_at = _now_iso()
            state.last_status = "skipped"
            return {
                "table_id": table_id,
                "status": "skipped",
                "stopped": True,
                "consecutive_same_count": state.consecutive_same_count,
                "active_speaker_ids": list(active_ids),
                "speaker_alias_map": dict(current_alias_map),
            }

        if state.stopped and not self._should_skip(state=state, table_cycle_version=current_cycle_version, active_ids=active_ids):
            state.stopped = False
            state.consecutive_same_count = 0
            state.last_signature = None

        dialogue_events = self._build_dialogue_events(table_id)
        rewritten_raw = None
        for attempt in range(2):
            try:
                rewritten_raw = self.dialog_client.rewrite_speaker_alias_map(
                    dialogue_events=dialogue_events,
                    current_alias_map=current_alias_map,
                )
                break
            except Exception as exc:
                if attempt == 1:
                    raise
        rewritten = _normalize_alias_map(
            dict(rewritten_raw or {}),
            expected_speaker_ids=list(current_alias_map.keys()),
        )
        applied_alias_map = self.session_manager.apply_speaker_alias_map(table_id, rewritten)
        signature = _alias_map_signature(applied_alias_map)
        if state.last_signature == signature:
            state.consecutive_same_count += 1
        else:
            state.consecutive_same_count = 1
        state.last_signature = signature
        state.last_cycle_version = current_cycle_version
        state.last_active_speaker_ids = list(active_ids)
        state.last_context_active_at = str(table.last_active_at or "")
        state.last_dialog_context_count = len(self.session_manager.list_dialog_context(table_id))
        state.last_result = dict(applied_alias_map)
        state.last_error = None
        state.last_run_at = _now_iso()
        state.stopped = self._should_stop(
            alias_map=applied_alias_map,
            active_ids=active_ids,
            consecutive_same_count=state.consecutive_same_count,
        )
        state.last_status = "stopped" if state.stopped else "updated"
        result = {
            "table_id": table_id,
            "status": state.last_status,
            "stopped": state.stopped,
            "consecutive_same_count": state.consecutive_same_count,
            "active_speaker_ids": list(active_ids),
            "speaker_alias_map": dict(applied_alias_map),
            "current_cycle_version": current_cycle_version,
            "last_run_at": state.last_run_at,
        }
        if self.on_updated is not None:
            self.on_updated(result)
        return result

    def poll_once(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for table_id in list(self.session_manager.tables.keys()):
            if not self._should_poll_table(table_id, now=now):
                continue
            try:
                results.append(self.rewrite_table_aliases(table_id))
            except Exception as exc:
                state = self._get_state(table_id)
                state.last_run_at = _now_iso()
                state.last_status = "failed"
                state.last_error = str(exc)
                result = {
                    "table_id": table_id,
                    "status": "failed",
                    "error": str(exc),
                }
                if self.on_failed is not None:
                    self.on_failed(result)
                results.append(result)
        return results

    def _should_poll_table(self, table_id: str, *, now: datetime) -> bool:
        table = self.session_manager.tables.get(table_id)
        if table is None:
            return False
        active_ids = self.session_manager.list_active_speaker_ids(table_id)
        if not active_ids:
            return False
        last_active_at = _parse_iso_datetime(getattr(table, "last_active_at", None))
        if last_active_at is None:
            return True
        if self.active_window_seconds > 0:
            age_seconds = (now - last_active_at).total_seconds()
            if age_seconds > self.active_window_seconds:
                return False
        state = self._states.get(table_id)
        if state is not None and state.last_context_active_at == str(table.last_active_at or ""):
            current_dialog_context_count = len(self.session_manager.list_dialog_context(table_id))
            if current_dialog_context_count <= state.last_dialog_context_count:
                return False
        last_run_at = _parse_iso_datetime(state.last_run_at if state is not None else None)
        if last_run_at is not None and last_active_at <= last_run_at:
            return False
        return True

    def start_background_polling(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()

        def loop() -> None:
            while not self._stop_event.wait(self.poll_interval_seconds):
                self.poll_once()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background_polling(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
