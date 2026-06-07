from __future__ import annotations

import re
import zipfile
from pathlib import Path

_DEFAULT_RULE_ENTRIES: dict[str, str] = {}

_ARKHAM_SPECIAL_FILES = {
    "golden rule": "arkham-rules/data/golden_rules.md",
    "golden rules": "arkham-rules/data/golden_rules.md",
    "grim rule": "arkham-rules/data/grim_rule.md",
    "silver rule": "arkham-rules/data/silver_rule.md",
}
_MIN_FUZZY_RULE_KEY_LENGTH = 4


def _normalize_rules_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).lower()


def _stem_rules_text(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("es") and len(value) > 2:
        return value[:-2]
    if value.endswith("s") and len(value) > 1:
        return value[:-1]
    return value


def _iter_glossary_entries(markdown: str):
    sections = re.split(r"\n## ", markdown)
    for section in sections[1:]:
        lines = section.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        body = "## " + section.strip()
        yield title, body
        subsection_splits = re.split(r"\n### ", section.strip())
        if len(subsection_splits) <= 1:
            continue
        for subsection in subsection_splits[1:]:
            subsection_lines = subsection.strip().splitlines()
            if not subsection_lines:
                continue
            subsection_title = subsection_lines[0].strip()
            subsection_body = "### " + subsection.strip()
            yield subsection_title, subsection_body


def _is_ascii_single_term(value: str) -> bool:
    stripped = value.strip()
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z -]*", stripped)) and " " not in stripped


def _query_looks_like_term_definition(query: str, key: str) -> bool:
    lowered = query.lower()
    key_lower = key.lower()
    patterns = (
        rf"\bwhat\s+is\s+{re.escape(key_lower)}\b",
        rf"\bhow\s+does\s+{re.escape(key_lower)}\s+work\b",
        rf"\b{re.escape(key_lower)}\s+means?\b",
        rf"\b{re.escape(key_lower)}\s+definition\b",
        rf"\b{re.escape(key_lower)}\s+rule\b",
        rf"\b{re.escape(key_lower)}\s+rules\b",
        rf"\bexplain\s+{re.escape(key_lower)}\b",
    )
    if any(re.search(pattern, lowered) for pattern in patterns):
        return True
    if f"{key_lower} 是什么" in lowered or f"解释 {key_lower}" in lowered:
        return True
    return False


class RulesIndex:
    def __init__(
        self,
        entries: dict[str, str] | None = None,
        *,
        arkham_rules_zip_path: str | None = None,
    ) -> None:
        raw_entries = dict(_DEFAULT_RULE_ENTRIES)
        if entries:
            raw_entries.update(entries)
        if arkham_rules_zip_path:
            raw_entries.update(self._load_arkham_rules_from_zip(Path(arkham_rules_zip_path)))
        self.entries = raw_entries
        self._normalized_entries = [
            (_normalize_rules_text(key), key, value)
            for key, value in self.entries.items()
            if _normalize_rules_text(key)
        ]

    def search(self, query: str) -> str | None:
        normalized_query = _normalize_rules_text(query)
        if not normalized_query:
            return None
        stemmed_query = _stem_rules_text(normalized_query)

        direct = self.entries.get(query)
        if direct:
            return direct

        best_match: tuple[int, str] | None = None
        for normalized_key, raw_key, value in self._normalized_entries:
            stemmed_key = _stem_rules_text(normalized_key)
            if normalized_key == normalized_query or stemmed_key == stemmed_query:
                return value
            if len(normalized_key) < _MIN_FUZZY_RULE_KEY_LENGTH:
                continue
            if _is_ascii_single_term(raw_key):
                if not _query_looks_like_term_definition(query, raw_key):
                    continue
            if (
                normalized_key in normalized_query
                or normalized_query in normalized_key
                or stemmed_key in stemmed_query
                or stemmed_query in stemmed_key
            ):
                score = len(normalized_key)
                if best_match is None or score > best_match[0]:
                    best_match = (score, value)

        return best_match[1] if best_match else None

    def _load_arkham_rules_from_zip(self, zip_path: Path) -> dict[str, str]:
        if not zip_path.exists():
            return {}

        loaded: dict[str, str] = {}
        with zipfile.ZipFile(zip_path) as archive:
            for alias, entry_name in _ARKHAM_SPECIAL_FILES.items():
                try:
                    content = archive.read(entry_name).decode("utf-8")
                except KeyError:
                    continue
                loaded[alias] = content.strip()

            for name in archive.namelist():
                if not name.startswith("arkham-rules/data/glossary/") or not name.endswith(".md"):
                    continue
                content = archive.read(name).decode("utf-8")
                for title, body in _iter_glossary_entries(content):
                    loaded.setdefault(title, body)
        return loaded
