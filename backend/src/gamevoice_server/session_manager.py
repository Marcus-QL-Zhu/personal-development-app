from __future__ import annotations

import random
import re

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import TableSession

CONVERSATION_MODE = "conversation"
from .table_store import TableStore


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split()).strip()


def _clip_text(value: str, limit: int) -> str:
    cleaned = value.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip()


def _normalize_alias(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_alias_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        alias = _normalize_alias(value)
        if not alias or alias in seen:
            continue
        seen.add(alias)
        normalized.append(alias)
    return normalized


def normalize_realtime_speaker_id(value: object) -> str | None:
    if value in (None, "", -1, "-1"):
        return None
    speaker_id = str(value).strip()
    if not speaker_id or speaker_id == "-1":
        return None
    if speaker_id.startswith("speaker_"):
        return speaker_id
    if speaker_id.isdigit():
        return f"speaker_{speaker_id}"
    return speaker_id


def _strip_speaker_prefix(value: str) -> str:
    cleaned = _normalize_whitespace(value)
    prefix_pattern = re.compile(r"^(?:speaker_\d+|player_[A-Za-z0-9_]+|玩家[A-Z])\s*[：:]\s*")
    while True:
        stripped = prefix_pattern.sub("", cleaned, count=1)
        if stripped == cleaned:
            return cleaned
        cleaned = stripped.strip()


def _strip_named_prefix(value: str, name: str) -> str:
    cleaned = _normalize_whitespace(value)
    assistant_name = _normalize_whitespace(name)
    if not cleaned or not assistant_name:
        return cleaned
    patterns = [
        rf"^{re.escape(assistant_name)}\s*[：:]\s*",
        rf"^{re.escape(assistant_name)}\s*（未说）\s*[：:]\s*",
        rf"^{re.escape(assistant_name)}\s*\(未说\)\s*[：:]\s*",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            stripped = re.sub(pattern, "", cleaned, count=1)
            if stripped != cleaned:
                cleaned = stripped.strip()
                changed = True
    inline_pattern = rf"([。！？.!?；;]\s*){re.escape(assistant_name)}\s*[：:]\s*"
    cleaned = re.sub(inline_pattern, r"\1", cleaned)
    return cleaned


def _default_speaker_label(speaker_id: str) -> str:
    aliases = {
        "player_a": "宝宝",
        "player_b": "玩家B",
        "player_c": "玩家C",
        "player_d": "玩家D",
    }
    return aliases.get(speaker_id, speaker_id)


def _default_speaker_aliases(speaker_id: str) -> list[str]:
    return ["宝宝"]


def _merge_default_speaker_aliases(speaker_id: str, aliases: list[str] | None) -> list[str]:
    return _normalize_alias_list([*_default_speaker_aliases(speaker_id), *(aliases or [])])


class SessionManager:
    def __init__(self, store: TableStore | None = None) -> None:
        self.tables: dict[str, TableSession] = {}
        self.on_context_event_appended = None
        self._store = store
        self._last_timestamp = ""

    def _now_iso(self) -> str:
        now = datetime.now(timezone.utc)
        if self._last_timestamp:
            try:
                previous = datetime.fromisoformat(self._last_timestamp)
                if now <= previous:
                    now = previous + timedelta(microseconds=1)
            except ValueError:
                pass
        self._last_timestamp = now.isoformat()
        return self._last_timestamp

    def load_from_store(self, store: TableStore) -> None:
        """Hydrate in-memory tables from a persistent store on startup."""
        for meta in store.list_tables():
            table_id = meta["id"]
            table = TableSession(
                id=meta["id"],
                name=meta["name"],
                assistant_name=meta.get("assistant_name", "宝子"),
                assistant_name_locked=bool(meta.get("assistant_name_locked")),
                assistant_personality=meta.get("assistant_personality", ""),
                assistant_voice_id=meta.get("assistant_voice_id", ""),
                origin=meta.get("origin", "manual"),
                status=meta.get("status", "active"),
                messages=store.list_messages(table_id),
                runtime_events=store.list_runtime_events(table_id),
                assistant_replies=store.list_assistant_replies(table_id),
                speaker_identities=store.get_speaker_identities(table_id) or {},
                compaction_checkpoint=meta.get("compaction_checkpoint", 0),
                compaction_version=meta.get("compaction_version", 0),
                compaction_summary_event=meta.get("compaction_summary"),
                created_at=meta.get("created_at", ""),
                last_active_at=meta.get("last_active_at", ""),
            )
            self.tables[table.id] = table

    def start_table(
        self,
        name: str,
        assistant_name: str = "宝子",
        assistant_personality: str = "",
        assistant_voice_id: str = "",
        origin: str = "manual",
    ) -> TableSession:
        now = self._now_iso()
        table = TableSession(
            id=str(uuid4()),
            name=name,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
            assistant_voice_id=assistant_voice_id,
            origin=origin,
            created_at=now,
            last_active_at=now,
        )
        self.tables[table.id] = table
        if self._store is not None:
            self._store.create_table({
                "id": table.id,
                "name": table.name,
                "assistant_name": table.assistant_name,
                "assistant_personality": table.assistant_personality,
                "assistant_voice_id": table.assistant_voice_id,
                "origin": table.origin,
                "status": table.status,
                "created_at": table.created_at,
                "last_active_at": table.last_active_at,
            })
        return table

    def touch_table(self, table_id: str) -> None:
        table = self.tables[table_id]
        table.last_active_at = self._now_iso()
        if self._store is not None:
            self._store.update_table_metadata(table_id, {"last_active_at": table.last_active_at})

    def stop_table(self, table_id: str) -> None:
        if table_id in self.tables:
            del self.tables[table_id]
        if self._store is not None:
            self._store.delete_table(table_id)

    def rename_table(self, table_id: str, name: str) -> TableSession:
        table = self.tables[table_id]
        table.name = name.strip()
        if self._store is not None:
            self._store.update_table_metadata(table_id, {"name": table.name})
        return table

    def _lock_assistant_name(self, table: TableSession) -> None:
        table.assistant_name_locked = True

    def get_assistant_name(self, table_id: str) -> str:
        return self.tables[table_id].assistant_name

    def get_assistant_personality(self, table_id: str) -> str:
        return self.tables[table_id].assistant_personality

    def get_assistant_voice_id(self, table_id: str) -> str:
        return self.tables[table_id].assistant_voice_id

    def set_assistant_name(self, table_id: str, assistant_name: str) -> str:
        cleaned = assistant_name.strip()
        if not cleaned:
            raise ValueError("assistant name must not be empty")
        table = self.tables[table_id]
        if table.assistant_name_locked:
            raise RuntimeError("assistant name is frozen after conversation starts")
        table.assistant_name = cleaned
        return cleaned

    def _ensure_speaker_identity(
        self,
        table: TableSession,
        *,
        speaker_id: str,
        speaker_label: str | None = None,
    ) -> dict:
        record = table.speaker_identities.get(speaker_id)
        if record is None:
            record = {
                "speaker_id": speaker_id,
                "status": "anonymous",
                "display_label": speaker_label or _default_speaker_label(speaker_id),
                "linked_name": None,
                "bridge_active": False,
                "aliases": _default_speaker_aliases(speaker_id),
                "last_observed_cycle_version": None,
            }
            table.speaker_identities[speaker_id] = record
        elif speaker_label:
            record["display_label"] = speaker_label
        return record

    def link_speaker_identity(
        self,
        table_id: str,
        speaker_id: str,
        linked_name: str,
        *,
        speaker_label: str | None = None,
    ) -> dict:
        table = self.tables[table_id]
        record = self._ensure_speaker_identity(
            table,
            speaker_id=speaker_id,
            speaker_label=speaker_label,
        )
        record["linked_name"] = linked_name.strip()
        record["status"] = "linked" if record["linked_name"] else "anonymous"
        record["bridge_active"] = bool(record["linked_name"])
        aliases = _merge_default_speaker_aliases(speaker_id, list(record.get("aliases") or []))
        if record["linked_name"] and record["linked_name"] not in aliases:
            aliases.append(record["linked_name"])
        record["aliases"] = aliases
        record["name_link_source"] = "manual"
        record["name_link_reason"] = "manual_override"
        record["name_link_score"] = 1.0 if record["linked_name"] else 0.0
        result = dict(record)
        if self._store is not None:
            self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        return result

    def ensure_speaker_identity(
        self,
        table_id: str,
        speaker_id: str,
        *,
        speaker_label: str | None = None,
        mark_observed: bool = False,
    ) -> dict:
        table = self.tables[table_id]
        record = self._ensure_speaker_identity(
            table,
            speaker_id=speaker_id,
            speaker_label=speaker_label,
        )
        if mark_observed:
            record["last_observed_cycle_version"] = table.compaction_version
            record["observation_count"] = int(record.get("observation_count", 0) or 0) + 1
        result = dict(record)
        if self._store is not None:
            self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        return result

    def observe_speaker_identity(self, table_id: str, observation: dict) -> dict:
        table = self.tables[table_id]
        record = self._ensure_speaker_identity(
            table,
            speaker_id=observation["speaker_id"],
            speaker_label=observation.get("display_label"),
        )
        record["last_observed_cycle_version"] = table.compaction_version
        record["status"] = observation.get("status", record.get("status", "anonymous"))
        record["observation_count"] = observation.get("observation_count", record.get("observation_count", 0))
        record["diarized_speaker_ids"] = list(observation.get("diarized_speaker_ids") or [])
        record["speaker_profile_ids"] = list(observation.get("speaker_profile_ids") or record.get("speaker_profile_ids", []))
        record["name_hints"] = list(observation.get("name_hints") or record.get("name_hints", []))
        if "aliases" in observation:
            record["aliases"] = _merge_default_speaker_aliases(
                observation["speaker_id"],
                list(observation.get("aliases") or []),
            )
        if "linked_name" in observation:
            record["linked_name"] = observation.get("linked_name")
        if "bridge_active" in observation:
            record["bridge_active"] = bool(observation.get("bridge_active"))
        if "name_link_source" in observation:
            record["name_link_source"] = observation.get("name_link_source")
        if "name_link_reason" in observation:
            record["name_link_reason"] = observation.get("name_link_reason")
        if "name_link_confidence" in observation:
            record["name_link_confidence"] = observation.get("name_link_confidence")
        if "name_link_count" in observation:
            record["name_link_count"] = observation.get("name_link_count")
        if "name_link_override_suggested" in observation:
            record["name_link_override_suggested"] = bool(observation.get("name_link_override_suggested"))
        if "name_link_override_candidate" in observation:
            record["name_link_override_candidate"] = observation.get("name_link_override_candidate")
        if "name_link_override_reason" in observation:
            record["name_link_override_reason"] = observation.get("name_link_override_reason")
        if "name_link_override_confidence" in observation:
            record["name_link_override_confidence"] = observation.get("name_link_override_confidence")
        if "name_link_override_count" in observation:
            record["name_link_override_count"] = observation.get("name_link_override_count")
        if "name_link_override_score" in observation:
            record["name_link_override_score"] = observation.get("name_link_override_score")
        if "last_observed_cycle_version" in observation:
            record["last_observed_cycle_version"] = observation.get("last_observed_cycle_version")
        record["embedding_sample_count"] = observation.get(
            "embedding_sample_count",
            record.get("embedding_sample_count", 0),
        )
        record["embedding_centroid"] = observation.get("embedding_centroid")
        result = dict(record)
        if self._store is not None:
            self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        return result

    def ingest_speaker_identity_batch(self, table_id: str, batch: dict) -> dict:
        for observation in batch.get("records") or []:
            self.observe_speaker_identity(table_id, observation)
        return {
            "source": batch.get("source"),
            "session_id": batch.get("session_id"),
            "ingested_count": int(batch.get("ingested_count", 0)),
            "observations": list(batch.get("observations") or []),
            "speaker_identities": self.list_speaker_identities(table_id),
            "recent_segments_retained": int(batch.get("recent_segments_retained", 0)),
        }

    def list_speaker_identities(self, table_id: str) -> list[dict]:
        table = self.tables[table_id]

        def sort_key(item: dict) -> tuple[int, str]:
            speaker_id = str(item.get("speaker_id", ""))
            order = {
                "player_a": 0,
                "player_b": 1,
                "player_c": 2,
                "player_d": 3,
            }
            return (order.get(speaker_id, 99), speaker_id)

        return sorted((dict(item) for item in table.speaker_identities.values()), key=sort_key)

    def list_speaker_alias_map(self, table_id: str) -> dict[str, list[str]]:
        return {
            str(record.get("speaker_id")): _normalize_alias_list(list(record.get("aliases") or []))
            for record in self.list_speaker_identities(table_id)
        }

    def apply_speaker_alias_map(self, table_id: str, alias_map: dict[str, list[str]]) -> dict[str, list[str]]:
        table = self.tables[table_id]
        normalized_map = {
            str(speaker_id): _merge_default_speaker_aliases(str(speaker_id), list(aliases or []))
            for speaker_id, aliases in alias_map.items()
        }
        for speaker_id, record in table.speaker_identities.items():
            record["aliases"] = list(
                normalized_map.get(
                    speaker_id,
                    _merge_default_speaker_aliases(speaker_id, list(record.get("aliases") or [])),
                )
            )
        if self._store is not None:
            self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        return self.list_speaker_alias_map(table_id)

    def choose_speaker_alias(
        self,
        table_id: str,
        speaker_id: str,
        *,
        rng: random.Random | None = None,
    ) -> str | None:
        table = self.tables[table_id]
        record = self._ensure_speaker_identity(table, speaker_id=speaker_id)
        aliases = _normalize_alias_list(list(record.get("aliases") or []))
        if not aliases:
            return None
        chooser = rng or random
        return chooser.choice(aliases)

    def list_active_speaker_ids(self, table_id: str) -> list[str]:
        table = self.tables[table_id]
        active_cycle_version = table.compaction_version
        active: list[str] = []
        for record in self.list_speaker_identities(table_id):
            if record.get("last_observed_cycle_version") == active_cycle_version:
                active.append(str(record.get("speaker_id")))
        return active

    def list_speaker_identity_review_candidates(self, table_id: str) -> list[dict]:
        candidates = []
        for record in self.list_speaker_identities(table_id):
            if record.get("name_link_override_suggested"):
                candidates.append(dict(record))
        return candidates

    def accept_speaker_identity_name_override(
        self,
        table_id: str,
        speaker_id: str,
        linked_name: str,
    ) -> dict:
        table = self.tables[table_id]
        record = self._ensure_speaker_identity(table, speaker_id=speaker_id)
        cleaned = linked_name.strip()
        record["linked_name"] = cleaned
        record["status"] = "linked" if cleaned else "anonymous"
        record["bridge_active"] = bool(cleaned)
        aliases = _merge_default_speaker_aliases(speaker_id, list(record.get("aliases") or []))
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
        record["aliases"] = aliases
        record["name_link_source"] = "review_override"
        record["name_link_reason"] = "accepted_override_suggestion"
        record["name_link_score"] = float(record.get("name_link_override_score", record.get("name_link_score", 0.0)) or 0.0)
        record["name_link_override_suggested"] = False
        record["name_link_override_candidate"] = None
        record["name_link_override_reason"] = None
        record["name_link_override_confidence"] = None
        record["name_link_override_count"] = None
        record["name_link_override_score"] = None
        result = dict(record)
        if self._store is not None:
            self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        return result

    def finish_identity_bridge_after_compaction(self, table_id: str) -> None:
        table = self.tables[table_id]
        for record in table.speaker_identities.values():
            if record.get("linked_name"):
                record["bridge_active"] = False

    def _resolve_user_speaker_label(
        self,
        *,
        table_id: str | None,
        speaker_id: str,
        speaker_label: str | None,
        use_linked_name: bool = True,
    ) -> str:
        if table_id is None:
            return speaker_label or _default_speaker_label(speaker_id)

        table = self.tables[table_id]
        record = self._ensure_speaker_identity(
            table,
            speaker_id=speaker_id,
            speaker_label=speaker_label,
        )
        if not use_linked_name:
            return speaker_label or record["display_label"]
        linked_name = (record.get("linked_name") or "").strip()
        if not linked_name:
            return record["display_label"]
        if record.get("bridge_active"):
            return f'{record["display_label"]}（{linked_name}）'
        return linked_name

    def list_context(self, table_id: str) -> list[dict]:
        table = self.tables[table_id]
        visible = list(table.messages[table.compaction_checkpoint :])
        if table.compaction_summary_event is not None:
            return [dict(table.compaction_summary_event)] + visible
        return list(table.messages)

    def list_raw_context(self, table_id: str) -> list[dict]:
        table = self.tables[table_id]
        return list(table.messages)

    def list_dialog_context(self, table_id: str) -> list[dict]:
        events: list[dict] = []
        for item in self.list_context(table_id):
            if item.get("kind") == "assistant_reply":
                continue
            events.append(item)
        return events

    def build_memory_compaction_payload(self, table_id: str) -> dict:
        table = self.tables[table_id]
        return {
            "table_id": table_id,
            "checkpoint": len(table.messages),
            "compaction_version": table.compaction_version + 1,
            "previous_summary": (
                str(table.compaction_summary_event.get("content", ""))
                if table.compaction_summary_event is not None
                else ""
            ),
            "active_events": list(table.messages[table.compaction_checkpoint :]),
            "raw_event_count": len(table.messages),
        }

    def apply_memory_compaction(
        self,
        table_id: str,
        *,
        checkpoint: int,
        summary_text: str,
        compaction_id: str,
        metadata: dict | None = None,
    ) -> dict:
        table = self.tables[table_id]
        if checkpoint < 0 or checkpoint > len(table.messages):
            raise ValueError("invalid compaction checkpoint")
        cleaned_summary = _normalize_whitespace(summary_text)
        table.compaction_version += 1
        table.compaction_summary_event = {
            "kind": "context_summary",
            "source": "memory_compactor",
            "content": cleaned_summary,
            "compaction_id": compaction_id,
            "version": table.compaction_version,
            "metadata": dict(metadata or {}),
        }
        table.compaction_checkpoint = checkpoint
        self.finish_identity_bridge_after_compaction(table_id)
        if self._store is not None:
            self._store.apply_compaction(
                table_id,
                checkpoint=checkpoint,
                summary_event=dict(table.compaction_summary_event) if table.compaction_summary_event else None,
                compaction_version=table.compaction_version,
            )
        return dict(table.compaction_summary_event)

    def list_runtime_events(self, table_id: str) -> list[dict]:
        table = self.tables[table_id]
        return list(table.runtime_events)

    def list_assistant_replies(self, table_id: str) -> list[dict]:
        table = self.tables[table_id]
        return list(table.assistant_replies)

    def append_context_event(self, table_id: str, event: dict) -> dict:
        table = self.tables[table_id]
        if event.get("kind") in {"voice_transcript", "assistant_spoken", "assistant_unspoken"}:
            self._lock_assistant_name(table)
        table.messages.append(event)
        self.touch_table(table_id)
        if self._store is not None:
            self._store.append_message(table_id, event)
        if self.on_context_event_appended is not None:
            self.on_context_event_appended(table_id, dict(event))
        return event

    def append_runtime_event(self, table_id: str, event: dict) -> dict:
        table = self.tables[table_id]
        table.runtime_events.append(event)
        if self._store is not None:
            self._store.append_runtime_event(table_id, event)
        return event

    def append_assistant_reply(self, table_id: str, event: dict) -> dict:
        table = self.tables[table_id]
        self._lock_assistant_name(table)
        table.assistant_replies.append(event)
        if self._store is not None:
            self._store.append_assistant_reply(table_id, event)
        return event

    def format_user_utterance(
        self,
        *,
        text: str,
        interrupted: bool = False,
        table_id: str | None = None,
        speaker_id: str = "player_a",
        speaker_label: str | None = None,
        use_linked_name: bool = True,
    ) -> str:
        cleaned = _strip_speaker_prefix(text)
        suffix = "（打断）" if interrupted else ""
        resolved_speaker = self._resolve_user_speaker_label(
            table_id=table_id,
            speaker_id=speaker_id,
            speaker_label=speaker_label,
            use_linked_name=use_linked_name,
        )
        return f"{resolved_speaker}{suffix}：{cleaned}"

    def format_assistant_spoken(self, table_id: str, text: str) -> str:
        cleaned = self.strip_assistant_prefix(table_id, text)
        return f"{self.get_assistant_name(table_id)}：{cleaned}"

    def format_assistant_unspoken(self, table_id: str, text: str) -> str:
        cleaned = self.strip_assistant_prefix(table_id, text)
        return f"{self.get_assistant_name(table_id)}（未说）：{cleaned}"

    def strip_assistant_prefix(self, table_id: str, text: str) -> str:
        return _strip_named_prefix(text, self.get_assistant_name(table_id))

    def upsert_live_transcript(
        self,
        table_id: str,
        live_session_id: str,
        slice_index: int,
        content: str,
        *,
        speaker_id: str | None = None,
        speaker_label: str | None = None,
        speaker_context_id: str | None = None,
    ) -> dict:
        table = self.tables[table_id]
        normalized_speaker_id = normalize_realtime_speaker_id(speaker_id)
        normalized_speaker_label = speaker_label or normalized_speaker_id
        if normalized_speaker_id:
            record = self._ensure_speaker_identity(
                table,
                speaker_id=normalized_speaker_id,
                speaker_label=normalized_speaker_label,
            )
            record["last_observed_cycle_version"] = table.compaction_version
            record["observation_count"] = int(record.get("observation_count", 0) or 0) + 1
            if self._store is not None:
                self._store.save_speaker_identities(table_id, dict(table.speaker_identities))
        session_slices = table.live_transcript_slices.setdefault(live_session_id, {})
        session_slices[slice_index] = content
        stable_text = _strip_speaker_prefix(content)
        table.latest_live_stable_text = stable_text or None
        if normalized_speaker_id:
            table.latest_live_stable_speaker_id = normalized_speaker_id
            table.latest_live_stable_speaker_label = normalized_speaker_label or normalized_speaker_id
        else:
            table.latest_live_stable_speaker_id = None
            table.latest_live_stable_speaker_label = None
        if speaker_context_id:
            table.latest_live_speaker_context_id = speaker_context_id
        table.latest_live_session_id = live_session_id
        return {
            "live_session_id": live_session_id,
            "slice_index": slice_index,
            "content": content,
            "stable_text": stable_text,
            "speaker_id": normalized_speaker_id,
            "speaker_label": normalized_speaker_label,
            "speaker_context_id": speaker_context_id,
        }

    def commit_live_transcript(
        self,
        table_id: str,
        *,
        source: str,
        text: str | None = None,
        interrupted: bool = False,
        live_session_id: str | None = None,
        speaker_id: str | None = None,
        speaker_label: str | None = None,
        speaker_context_id: str | None = None,
    ) -> dict | None:
        table = self.tables[table_id]
        content = _strip_speaker_prefix(text or table.latest_live_stable_text or "")
        if not content:
            return None
        normalized_speaker_id = normalize_realtime_speaker_id(speaker_id)
        resolved_speaker_id = normalized_speaker_id or table.latest_live_stable_speaker_id or "player_a"
        resolved_speaker_label = speaker_label or table.latest_live_stable_speaker_label
        resolved_speaker_context_id = speaker_context_id or table.latest_live_speaker_context_id
        event = {
            "kind": "voice_transcript",
            "source": source,
            "content": self.format_user_utterance(
                text=content,
                interrupted=interrupted,
                table_id=table_id,
                speaker_id=resolved_speaker_id,
                speaker_label=resolved_speaker_label,
                use_linked_name=source != "live_asr",
            ),
            "speaker_id": resolved_speaker_id,
            "speaker_label": resolved_speaker_label,
            "speaker_context_id": resolved_speaker_context_id,
        }
        self.append_context_event(table_id, event)
        if live_session_id and live_session_id in table.live_transcript_slices:
            table.live_transcript_slices.pop(live_session_id, None)
        elif table.latest_live_session_id:
            table.live_transcript_slices.pop(table.latest_live_session_id, None)
        table.latest_live_stable_text = None
        table.latest_live_stable_speaker_id = None
        table.latest_live_stable_speaker_label = None
        table.latest_live_session_id = None
        return event

    def find_assistant_reply_by_job(self, table_id: str, job_id: str) -> dict | None:
        for item in reversed(self.list_assistant_replies(table_id)):
            speech_job = item.get("speech_job")
            if speech_job and speech_job.get("job_id") == job_id:
                return item
        return None

    def commit_spoken_reply(self, table_id: str, job_id: str) -> dict | None:
        table = self.tables[table_id]
        for item in table.messages:
            if item.get("kind") == "assistant_spoken" and item.get("job_id") == job_id:
                return item

        reply = self.find_assistant_reply_by_job(table_id, job_id)
        if reply is None:
            return None
        content = _normalize_whitespace(reply.get("content", ""))
        if not content:
            return None
        event = {
            "kind": "assistant_spoken",
            "source": reply.get("source", "companion"),
            "mode": reply.get("mode", CONVERSATION_MODE),
            "content": self.format_assistant_spoken(table_id, content),
            "job_id": job_id,
            "turn_id": reply.get("turn_id"),
            "reply_id": reply.get("reply_id"),
        }
        self.append_context_event(table_id, event)
        return event

    def commit_interrupted_reply(self, table_id: str, job_id: str, *, unspoken_limit: int = 200) -> dict:
        table = self.tables[table_id]
        reply = self.find_assistant_reply_by_job(table_id, job_id)
        if reply is None:
            return {"spoken": None, "unspoken": None}

        speech_job = reply.get("speech_job") or {}
        segments = speech_job.get("segment_statuses", [])
        spoken_text = " ".join(
            _normalize_whitespace(segment.get("text", ""))
            for segment in segments
            if segment.get("status") == "completed"
        ).strip()
        remaining_text = " ".join(
            _normalize_whitespace(segment.get("text", ""))
            for segment in segments
            if segment.get("status") != "completed"
        ).strip()

        spoken_event = None
        if spoken_text:
            spoken_event = {
                "kind": "assistant_spoken",
                "source": reply.get("source", "companion"),
                "mode": reply.get("mode", CONVERSATION_MODE),
                "content": self.format_assistant_spoken(table_id, spoken_text),
                "job_id": job_id,
                "turn_id": reply.get("turn_id"),
                "reply_id": reply.get("reply_id"),
            }
            self.append_context_event(table_id, spoken_event)

        unspoken_event = None
        clipped_remaining = _clip_text(remaining_text, unspoken_limit)
        if len(clipped_remaining) > 20:
            unspoken_event = {
                "kind": "assistant_unspoken",
                "source": reply.get("source", "companion"),
                "mode": reply.get("mode", CONVERSATION_MODE),
                "content": self.format_assistant_unspoken(table_id, clipped_remaining),
                "job_id": job_id,
                "turn_id": reply.get("turn_id"),
                "reply_id": reply.get("reply_id"),
            }
            self.append_context_event(table_id, unspoken_event)

        return {"spoken": spoken_event, "unspoken": unspoken_event}
