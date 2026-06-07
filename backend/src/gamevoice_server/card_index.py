from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

_CARD_QUERY_STOPWORDS = {
    "what",
    "does",
    "do",
    "which",
    "card",
    "the",
    "a",
    "an",
    "is",
    "are",
    "of",
    "and",
    "to",
    "get",
}


def _normalize_card_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).lower()


def _tokenize_card_text(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if token}


def _meaningful_query_tokens(query: str) -> set[str]:
    tokens = _tokenize_card_text(query)
    filtered = {token for token in tokens if token not in _CARD_QUERY_STOPWORDS}
    return filtered or tokens


def _build_card_answer(card: dict) -> str:
    name = str(card.get("name", "")).strip()
    code = str(card.get("code", "")).strip()
    faction = str(card.get("faction_name", "")).strip()
    card_type = str(card.get("type_name", "")).strip()
    xp = card.get("xp")
    traits = str(card.get("traits", "")).strip()
    text = str(card.get("text", "")).strip() or str(card.get("real_text", "")).strip()

    lines = [f"{name} ({code})".strip()]
    if faction:
        lines.append(f"Faction: {faction}")
    if card_type:
        lines.append(f"Type: {card_type}")
    if xp not in (None, ""):
        lines.append(f"XP: {xp}")
    if traits:
        lines.append(f"Traits: {traits}")
    if text:
        lines.append(f"Text: {text}")
    return "\n".join(lines).strip()


class CardIndex:
    def __init__(
        self,
        entries: dict[str, str] | None = None,
        *,
        arkham_cards_zip_path: str | None = None,
    ) -> None:
        self.entries = entries or {}
        self._cards: list[dict] = []
        if arkham_cards_zip_path:
            self._cards = self._load_cards_from_zip(Path(arkham_cards_zip_path))
        self._search_entries = self._build_search_entries()

    def search(self, query: str) -> str | None:
        direct = self.entries.get(query)
        if direct:
            return direct

        normalized_query = _normalize_card_text(query)
        if not normalized_query:
            return None
        query_tokens = _meaningful_query_tokens(query)

        best_match: tuple[int, str] | None = None
        for entry in self._search_entries:
            answer = entry["answer"]
            score = self._score_entry(entry, normalized_query=normalized_query, query_tokens=query_tokens)
            if score <= 0:
                continue
            if best_match is None or score > best_match[0]:
                best_match = (score, answer)

        return best_match[1] if best_match else None

    def _score_entry(self, entry: dict, *, normalized_query: str, query_tokens: set[str]) -> int:
        primary_fields: list[str] = entry["primary_fields"]
        secondary_fields: list[str] = entry["secondary_fields"]
        primary_tokens: list[set[str]] = entry["primary_tokens"]
        secondary_tokens: list[set[str]] = entry["secondary_tokens"]

        exact_primary = [field for field in primary_fields if field == normalized_query]
        if exact_primary:
            return 10000 + max(len(field) for field in exact_primary)

        primary_substring = [
            field for field in primary_fields if field and (normalized_query in field or field in normalized_query)
        ]
        if primary_substring:
            return 8000 + max(len(field) for field in primary_substring)

        if query_tokens:
            primary_overlap = max((len(query_tokens & field_tokens) for field_tokens in primary_tokens), default=0)
            if primary_overlap >= 1:
                return 6000 + primary_overlap * 100

        secondary_substring = [
            field
            for field in secondary_fields
            if field and (normalized_query in field or field in normalized_query)
        ]
        if secondary_substring:
            return 4000 + max(len(field) for field in secondary_substring)

        if query_tokens:
            secondary_overlap = max(
                (len(query_tokens & field_tokens) for field_tokens in secondary_tokens),
                default=0,
            )
            if secondary_overlap >= 2:
                return 2000 + secondary_overlap * 100

        return 0

    def _build_search_entries(self) -> list[dict]:
        entries: list[dict] = []

        for key, value in self.entries.items():
            normalized = _normalize_card_text(key)
            if not normalized:
                continue
            entries.append(
                {
                    "primary_fields": [normalized],
                    "secondary_fields": [],
                    "primary_tokens": [_tokenize_card_text(key)],
                    "secondary_tokens": [],
                    "answer": value,
                }
            )

        for card in self._cards:
            answer = _build_card_answer(card)
            primary_raw_fields = [
                str(card.get(field, "")).strip()
                for field in ("name", "real_name", "subname", "code")
                if str(card.get(field, "")).strip()
            ]
            secondary_raw_fields = [
                str(card.get(field, "")).strip()
                for field in ("text", "real_text", "traits")
                if str(card.get(field, "")).strip()
            ]
            primary_fields = [
                _normalize_card_text(field) for field in primary_raw_fields if _normalize_card_text(field)
            ]
            secondary_fields = [
                _normalize_card_text(field)
                for field in secondary_raw_fields
                if _normalize_card_text(field)
            ]
            if not primary_fields and not secondary_fields:
                continue
            entries.append(
                {
                    "primary_fields": primary_fields,
                    "secondary_fields": secondary_fields,
                    "primary_tokens": [_tokenize_card_text(field) for field in primary_raw_fields],
                    "secondary_tokens": [_tokenize_card_text(field) for field in secondary_raw_fields],
                    "answer": answer,
                }
            )
        return entries

    @staticmethod
    def _load_cards_from_zip(zip_path: Path) -> list[dict]:
        if not zip_path.exists():
            return []
        with zipfile.ZipFile(zip_path) as archive:
            try:
                payload = archive.read(
                    "arkhamdb-cards/data/indexes/master_index_with_tags.json"
                ).decode("utf-8")
            except KeyError:
                return []
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, list) else []
