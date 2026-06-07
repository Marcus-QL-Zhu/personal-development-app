from __future__ import annotations

from typing import Any



def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _normalize_time_to_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value * 1000.0))
    try:
        return int(round(float(value) * 1000.0))
    except (TypeError, ValueError):
        return None


def _normalize_embedding_value(value: Any) -> list[float] | None:
    if value is None or not isinstance(value, list):
        return None
    embedding: list[float] = []
    for item in value:
        try:
            embedding.append(float(item))
        except (TypeError, ValueError):
            return None
    return embedding or None


def _normalize_candidate_name(value: Any) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned.strip(" \t\r\n，。！？!?；;：:、（）()[]{}\"'“”‘’《》<>")
    cleaned = cleaned.replace(" ", "")
    return cleaned


class SpeakerPipelineAdapter:
    def build_batch(
        self,
        *,
        source: str,
        session_id: str | None = None,
        pyannote_segments: list[dict] | None = None,
        diarization_segments: list[dict] | None = None,
        speaker_embeddings: list[dict] | None = None,
        name_candidates: list[dict] | None = None,
    ) -> dict:
        raw_segments = list(pyannote_segments or diarization_segments or [])
        normalized_segments = []
        for item in raw_segments:
            normalized = self._normalize_segment(item)
            if normalized is not None:
                normalized_segments.append(normalized)

        normalized_embeddings = []
        for item in speaker_embeddings or []:
            normalized = self._normalize_embedding(item)
            if normalized is not None:
                normalized_embeddings.append(normalized)

        normalized_candidates = []
        for item in name_candidates or []:
            normalized = self._normalize_name_candidate(item)
            if normalized is not None:
                normalized_candidates.append(normalized)

        return {
            "source": source,
            "session_id": session_id,
            "diarization_segments": normalized_segments,
            "speaker_embeddings": normalized_embeddings,
            "name_candidates": normalized_candidates,
        }

    def _normalize_segment(self, item: dict) -> dict | None:
        diarized_speaker_id = _normalize_text(
            _item_value(item, "diarized_speaker_id")
            or _item_value(item, "speaker")
            or _item_value(item, "speaker_label")
        )
        if not diarized_speaker_id:
            return None
        segment_start_ms = _item_value(item, "segment_start_ms")
        if segment_start_ms is None:
            segment_start_ms = _normalize_time_to_ms(_item_value(item, "start"))
        segment_end_ms = _item_value(item, "segment_end_ms")
        if segment_end_ms is None:
            segment_end_ms = _normalize_time_to_ms(_item_value(item, "end"))
        return {
            "segment_id": _normalize_text(_item_value(item, "segment_id")) or None,
            "diarized_speaker_id": diarized_speaker_id,
            "speaker_profile_id": _normalize_text(
                _item_value(item, "speaker_profile_id") or _item_value(item, "speaker_profile")
            )
            or None,
            "segment_start_ms": segment_start_ms,
            "segment_end_ms": segment_end_ms,
            "transcript_text": _normalize_text(_item_value(item, "transcript_text") or _item_value(item, "text")),
            "channel": _item_value(item, "channel"),
            "diarization_confidence": _item_value(item, "diarization_confidence") or _item_value(item, "confidence"),
        }

    def _normalize_embedding(self, item: dict) -> dict | None:
        embedding = _normalize_embedding_value(_item_value(item, "embedding") or _item_value(item, "vector"))
        if not embedding:
            return None
        diarized_speaker_id = _normalize_text(
            _item_value(item, "diarized_speaker_id")
            or _item_value(item, "speaker")
            or _item_value(item, "speaker_label")
        )
        speaker_profile_id = _normalize_text(
            _item_value(item, "speaker_profile_id") or _item_value(item, "speaker_profile")
        ) or None
        if not diarized_speaker_id and not speaker_profile_id:
            return None
        normalized: dict[str, Any] = {
            "embedding": embedding,
            "sample_count": _item_value(item, "sample_count"),
        }
        if diarized_speaker_id:
            normalized["diarized_speaker_id"] = diarized_speaker_id
        if speaker_profile_id:
            normalized["speaker_profile_id"] = speaker_profile_id
        return normalized

    def _normalize_name_candidate(self, item: dict) -> dict | None:
        candidate_name = _normalize_candidate_name(_item_value(item, "candidate_name") or _item_value(item, "name"))
        if not candidate_name:
            return None
        diarized_speaker_id = _normalize_text(
            _item_value(item, "diarized_speaker_id")
            or _item_value(item, "speaker")
            or _item_value(item, "speaker_label")
        )
        speaker_profile_id = _normalize_text(
            _item_value(item, "speaker_profile_id") or _item_value(item, "speaker_profile")
        ) or None
        if not diarized_speaker_id and not speaker_profile_id:
            return None
        normalized: dict[str, Any] = {
            "candidate_name": candidate_name,
            "candidate_confidence": _item_value(item, "candidate_confidence") or _item_value(item, "confidence"),
        }
        if diarized_speaker_id:
            normalized["diarized_speaker_id"] = diarized_speaker_id
        if speaker_profile_id:
            normalized["speaker_profile_id"] = speaker_profile_id
        return normalized
