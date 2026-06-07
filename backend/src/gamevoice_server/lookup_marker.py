from __future__ import annotations

import re


LOOKUP_MARKER = "<lookup>"
_LOOKUP_MARKER_RE = re.compile(r"\s*<lookup>\s*$", re.IGNORECASE)


def split_preview_lookup_marker(text: str | None) -> tuple[str, bool]:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return "", False
    if not _LOOKUP_MARKER_RE.search(cleaned):
        return cleaned, False
    return _LOOKUP_MARKER_RE.sub("", cleaned).strip(), True
