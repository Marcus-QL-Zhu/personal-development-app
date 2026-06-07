from __future__ import annotations

import re
from math import sqrt


_DEFAULT_PLAYER_SLOTS = ["player_a", "player_b", "player_c", "player_d"]
_RECENT_SEGMENT_LIMIT = 200
_AUTO_LINK_MIN_HINT_COUNT = 2
_AUTO_LINK_STRONG_SINGLE_CONFIDENCE = 0.97
_AUTO_LINK_MIN_CONFIDENCE = 0.88
_AUTO_LINK_MIN_MARGIN = 0.08
_AUTO_LINK_MIN_SCORE = 0.85

def _default_display_label(speaker_id: str) -> str:
    aliases = {
        "player_a": "玩家A",
        "player_b": "玩家B",
        "player_c": "玩家C",
        "player_d": "玩家D",
    }
    return aliases.get(speaker_id, speaker_id)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return -1.0
    return dot / (left_norm * right_norm)


def _normalize_candidate_name(value: str | None) -> str:
    return str(value or "").strip()


def _extract_candidate_name_from_transcript(text: str) -> tuple[str | None, float | None]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None, None
    patterns = (
        r"(?:我是|我叫|我的名字是)\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·._ -]{0,23})",
        r"\b(?:i am|i'm|my name is)\s+([A-Z][A-Za-z0-9._ -]{0,23})",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        name = match.group(1).strip(" ，。,.!?！？:：;；")
        if name:
            return name, 0.92
    return None, None



def _weighted_average_embeddings(entries: list[dict]) -> list[float] | None:
    total_weight = 0.0
    accumulator: list[float] | None = None
    for item in entries:
        embedding = list(item.get("embedding") or [])
        if not embedding:
            continue
        weight = float(item.get("sample_count") or 1.0)
        if weight <= 0:
            continue
        if accumulator is None:
            accumulator = [0.0 for _ in embedding]
        if len(accumulator) != len(embedding):
            continue
        total_weight += weight
        for index, value in enumerate(embedding):
            accumulator[index] += float(value) * weight
    if accumulator is None or total_weight <= 0:
        return None
    return [value / total_weight for value in accumulator]


class IdentityLinker:
    def __init__(
        self,
        *,
        player_slots: list[str] | None = None,
        similarity_threshold: float = 0.82,
    ) -> None:
        self._player_slots = list(player_slots or _DEFAULT_PLAYER_SLOTS)
        self._similarity_threshold = similarity_threshold

    def bootstrap(self, speaker_ids: list[str]) -> list[dict]:
        return [{"speaker_id": speaker_id, "status": "anonymous"} for speaker_id in speaker_ids]

    def _ensure_state(self, state: dict) -> dict:
        state.setdefault("slot_order", list(self._player_slots))
        state.setdefault("slot_by_diarized", {})
        state.setdefault("slot_by_profile", {})
        state.setdefault("records", {})
        state.setdefault("recent_segments", [])
        state.setdefault("name_hints", {})
        return state

    def _record_name_hint(
        self,
        state: dict,
        *,
        speaker_id: str,
        candidate_name: str | None,
        candidate_confidence: float | int | None,
    ) -> None:
        normalized_name = _normalize_candidate_name(candidate_name)
        if not normalized_name:
            return
        confidence = float(candidate_confidence or 0.0)
        speaker_hints = state["name_hints"].setdefault(speaker_id, {})
        hint = speaker_hints.setdefault(
            normalized_name,
            {
                "name": normalized_name,
                "count": 0,
                "confidence_max": 0.0,
            },
        )
        hint["count"] = int(hint.get("count", 0)) + 1
        hint["confidence_max"] = max(float(hint.get("confidence_max", 0.0)), confidence)

    def _sorted_name_hints(self, state: dict, speaker_id: str) -> list[dict]:
        speaker_hints = state.get("name_hints", {}).get(speaker_id, {})
        hints = [dict(item) for item in speaker_hints.values()]
        hints.sort(
            key=lambda item: (
                -float(item.get("confidence_max", 0.0)),
                -int(item.get("count", 0)),
                str(item.get("name", "")),
            )
        )
        return hints

    def _name_link_score(self, *, top_count: int, top_confidence: float, margin: float) -> float:
        count_component = min(1.0, max(0.0, float(top_count)) / 3.0)
        confidence_component = min(1.0, max(0.0, float(top_confidence)))
        margin_component = min(1.0, max(0.0, float(margin)) / 0.2)
        return (confidence_component * 0.55) + (count_component * 0.25) + (margin_component * 0.20)

    def _maybe_auto_link_name(self, record: dict) -> dict | None:
        hints = list(record.get("name_hints") or [])
        if not hints:
            return None
        top = hints[0]
        top_name = _normalize_candidate_name(top.get("name"))
        if not top_name:
            return None
        top_count = int(top.get("count", 0) or 0)
        top_confidence = float(top.get("confidence_max", 0.0) or 0.0)
        runner_up_confidence = float(hints[1].get("confidence_max", 0.0) or 0.0) if len(hints) > 1 else 0.0
        margin = top_confidence - runner_up_confidence if len(hints) > 1 else top_confidence
        score = self._name_link_score(
            top_count=top_count,
            top_confidence=top_confidence,
            margin=margin,
        )

        linked_name = _normalize_candidate_name(record.get("linked_name"))
        if linked_name:
            if top_name != linked_name and (top_confidence >= 0.95 or score >= _AUTO_LINK_MIN_SCORE):
                record["name_link_override_suggested"] = True
                record["name_link_override_candidate"] = top_name
                record["name_link_override_reason"] = "competing_hint_stronger_than_existing_link"
                record["name_link_override_confidence"] = top_confidence
                record["name_link_override_count"] = top_count
                record["name_link_override_score"] = score
                return dict(record)
            return None

        eligible = False
        reason = ""
        if (
            top_count >= _AUTO_LINK_MIN_HINT_COUNT
            and top_confidence >= _AUTO_LINK_MIN_CONFIDENCE
            and margin >= _AUTO_LINK_MIN_MARGIN
            and score >= _AUTO_LINK_MIN_SCORE
        ):
            eligible = True
            reason = "repeated_confident_hint"
        elif (
            top_confidence >= _AUTO_LINK_STRONG_SINGLE_CONFIDENCE
            and margin >= _AUTO_LINK_MIN_MARGIN
            and score >= 0.80
        ):
            eligible = True
            reason = "strong_single_hint"

        if not eligible:
            return None

        record["linked_name"] = top_name
        record["status"] = "linked"
        record["bridge_active"] = True
        record["name_link_source"] = "auto_text_hint"
        record["name_link_reason"] = reason
        record["name_link_confidence"] = top_confidence
        record["name_link_count"] = top_count
        record["name_link_score"] = score
        record["name_link_state"] = "auto_linked"
        return dict(record)

    def _find_first_free_slot(self, state: dict) -> str:
        used = set(state["records"].keys())
        for slot in state["slot_order"]:
            if slot not in used:
                return slot
        return f"player_extra_{len(used) + 1}"

    def _choose_slot_by_embedding(self, state: dict, embedding: list[float]) -> str | None:
        best_slot = None
        best_score = self._similarity_threshold
        for speaker_id, record in state["records"].items():
            centroid = record.get("embedding_centroid")
            if not centroid:
                continue
            score = _cosine_similarity(embedding, centroid)
            if score >= best_score:
                best_slot = speaker_id
                best_score = score
        return best_slot

    def _update_centroid(self, record: dict, embedding: list[float] | None) -> None:
        if not embedding:
            return
        sample_count = int(record.get("embedding_sample_count", 0))
        previous = list(record.get("embedding_centroid") or [])
        if not previous or len(previous) != len(embedding):
            record["embedding_centroid"] = list(embedding)
            record["embedding_sample_count"] = 1
            return
        updated = []
        for old_value, new_value in zip(previous, embedding):
            updated.append(((old_value * sample_count) + new_value) / (sample_count + 1))
        record["embedding_centroid"] = updated
        record["embedding_sample_count"] = sample_count + 1

    def observe(
        self,
        state: dict,
        *,
        diarized_speaker_id: str,
        speaker_profile_id: str | None = None,
        embedding: list[float] | None = None,
    ) -> dict:
        state = self._ensure_state(state)
        slot_by_diarized = state["slot_by_diarized"]
        slot_by_profile = state["slot_by_profile"]
        records = state["records"]

        speaker_id = slot_by_diarized.get(diarized_speaker_id)
        if speaker_id is None and speaker_profile_id:
            speaker_id = slot_by_profile.get(speaker_profile_id)
        if speaker_id is None and embedding:
            speaker_id = self._choose_slot_by_embedding(state, embedding)
        if speaker_id is None:
            speaker_id = self._find_first_free_slot(state)

        slot_by_diarized[diarized_speaker_id] = speaker_id
        if speaker_profile_id:
            slot_by_profile[speaker_profile_id] = speaker_id
        record = records.setdefault(
            speaker_id,
            {
                "speaker_id": speaker_id,
                "status": "anonymous",
                "display_label": _default_display_label(speaker_id),
                "linked_name": None,
                "bridge_active": False,
                "observation_count": 0,
                "diarized_speaker_ids": [],
                "speaker_profile_ids": [],
                "embedding_sample_count": 0,
                "embedding_centroid": None,
            },
        )
        record["observation_count"] = int(record.get("observation_count", 0)) + 1
        diarized_ids = set(record.get("diarized_speaker_ids") or [])
        diarized_ids.add(diarized_speaker_id)
        record["diarized_speaker_ids"] = sorted(diarized_ids)
        if speaker_profile_id:
            profile_ids = set(record.get("speaker_profile_ids") or [])
            profile_ids.add(speaker_profile_id)
            record["speaker_profile_ids"] = sorted(profile_ids)
        self._update_centroid(record, embedding)
        return dict(record)

    def ingest_segments(
        self,
        state: dict,
        *,
        source: str,
        observations: list[dict],
        session_id: str | None = None,
    ) -> dict:
        state = self._ensure_state(state)
        recent_segments = state["recent_segments"]
        touched_records: dict[str, dict] = {}
        ingested: list[dict] = []

        for index, observation in enumerate(observations):
            diarized_speaker_id = str(observation["diarized_speaker_id"]).strip()
            speaker_profile_id = str(observation.get("speaker_profile_id") or "").strip() or None
            embedding = observation.get("embedding")
            record = self.observe(
                state,
                diarized_speaker_id=diarized_speaker_id,
                speaker_profile_id=speaker_profile_id,
                embedding=embedding,
            )
            speaker_id = record["speaker_id"]
            candidate_name = observation.get("candidate_name")
            candidate_confidence = observation.get("candidate_confidence")
            if not _normalize_candidate_name(candidate_name):
                candidate_name, candidate_confidence = _extract_candidate_name_from_transcript(
                    str(observation.get("transcript_text") or "")
                )
            self._record_name_hint(
                state,
                speaker_id=speaker_id,
                candidate_name=candidate_name,
                candidate_confidence=candidate_confidence,
            )
            record["name_hints"] = self._sorted_name_hints(state, speaker_id)
            auto_linked = self._maybe_auto_link_name(record)
            if auto_linked is not None:
                record = auto_linked
            touched_records[speaker_id] = record
            segment = {
                "index": index,
                "source": source,
                "session_id": session_id,
                "speaker_id": speaker_id,
                "diarized_speaker_id": diarized_speaker_id,
                "speaker_profile_id": speaker_profile_id,
                "segment_id": observation.get("segment_id"),
                "segment_start_ms": observation.get("segment_start_ms"),
                "segment_end_ms": observation.get("segment_end_ms"),
                "transcript_text": str(observation.get("transcript_text") or "").strip(),
                "channel": observation.get("channel"),
                "diarization_confidence": observation.get("diarization_confidence"),
                "candidate_name": _normalize_candidate_name(candidate_name),
                "candidate_confidence": candidate_confidence,
            }
            recent_segments.append(dict(segment))
            ingested.append(segment)

        if len(recent_segments) > _RECENT_SEGMENT_LIMIT:
            del recent_segments[:-_RECENT_SEGMENT_LIMIT]

        return {
            "source": source,
            "session_id": session_id,
            "ingested_count": len(ingested),
            "observations": ingested,
            "records": [dict(record) for record in touched_records.values()],
            "recent_segments_retained": len(recent_segments),
        }

    def ingest_pipeline_batch(
        self,
        state: dict,
        *,
        source: str,
        session_id: str | None = None,
        diarization_segments: list[dict],
        speaker_embeddings: list[dict] | None = None,
        name_candidates: list[dict] | None = None,
    ) -> dict:
        embedding_entries_by_speaker: dict[str, list[dict]] = {}
        embedding_entries_by_profile: dict[str, list[dict]] = {}
        for item in speaker_embeddings or []:
            diarized_speaker_id = str(item.get("diarized_speaker_id") or "").strip()
            speaker_profile_id = str(item.get("speaker_profile_id") or "").strip()
            if not diarized_speaker_id:
                diarized_speaker_id = ""
            if diarized_speaker_id:
                embedding_entries_by_speaker.setdefault(diarized_speaker_id, []).append(dict(item))
            if speaker_profile_id:
                embedding_entries_by_profile.setdefault(speaker_profile_id, []).append(dict(item))

        averaged_embedding_by_speaker = {
            speaker_id: _weighted_average_embeddings(entries)
            for speaker_id, entries in embedding_entries_by_speaker.items()
        }
        averaged_embedding_by_profile = {
            profile_id: _weighted_average_embeddings(entries)
            for profile_id, entries in embedding_entries_by_profile.items()
        }

        best_name_candidate_by_speaker: dict[str, dict] = {}
        best_name_candidate_by_profile: dict[str, dict] = {}
        for item in name_candidates or []:
            diarized_speaker_id = str(item.get("diarized_speaker_id") or "").strip()
            speaker_profile_id = str(item.get("speaker_profile_id") or "").strip()
            candidate_name = _normalize_candidate_name(item.get("candidate_name"))
            if not candidate_name:
                continue
            confidence = float(item.get("candidate_confidence") or 0.0)
            if diarized_speaker_id:
                current = best_name_candidate_by_speaker.get(diarized_speaker_id)
                if current is None or confidence > float(current.get("candidate_confidence") or 0.0):
                    best_name_candidate_by_speaker[diarized_speaker_id] = {
                        "candidate_name": candidate_name,
                        "candidate_confidence": confidence,
                    }
            if speaker_profile_id:
                current = best_name_candidate_by_profile.get(speaker_profile_id)
                if current is None or confidence > float(current.get("candidate_confidence") or 0.0):
                    best_name_candidate_by_profile[speaker_profile_id] = {
                        "candidate_name": candidate_name,
                        "candidate_confidence": confidence,
                    }

        normalized_observations: list[dict] = []
        for segment in diarization_segments:
            diarized_speaker_id = str(segment.get("diarized_speaker_id") or "").strip()
            speaker_profile_id = str(segment.get("speaker_profile_id") or "").strip() or None
            if not diarized_speaker_id:
                continue
            best_name_candidate = (
                best_name_candidate_by_profile.get(speaker_profile_id or "")
                or best_name_candidate_by_speaker.get(diarized_speaker_id)
                or {}
            )
            normalized_observations.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "diarized_speaker_id": diarized_speaker_id,
                    "speaker_profile_id": speaker_profile_id,
                    "segment_start_ms": segment.get("segment_start_ms"),
                    "segment_end_ms": segment.get("segment_end_ms"),
                    "transcript_text": str(segment.get("transcript_text") or "").strip(),
                    "channel": segment.get("channel"),
                    "diarization_confidence": segment.get("diarization_confidence"),
                    "embedding": (
                        averaged_embedding_by_profile.get(speaker_profile_id or "")
                        or averaged_embedding_by_speaker.get(diarized_speaker_id)
                    ),
                    "candidate_name": best_name_candidate.get("candidate_name"),
                    "candidate_confidence": best_name_candidate.get("candidate_confidence"),
                }
            )

        return self.ingest_segments(
            state,
            source=source,
            session_id=session_id,
            observations=normalized_observations,
        )

    def link_name(self, speaker_id: str, name: str) -> dict:
        return {"speaker_id": speaker_id, "name": name, "status": "linked"}
