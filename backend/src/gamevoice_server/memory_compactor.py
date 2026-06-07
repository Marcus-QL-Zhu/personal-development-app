from __future__ import annotations

from typing import Iterable


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


class MemoryCompactor:
    def __init__(self, dialog_client=None) -> None:
        self.dialog_client = dialog_client

    def compact(self, payload: dict) -> dict:
        previous_summary = _clean_text(payload.get("previous_summary"))
        active_events = list(payload.get("active_events") or [])
        summary_text = self._generate_summary(
            previous_summary=previous_summary,
            active_events=active_events,
        )
        return {
            "status": "compacted",
            "summary_text": summary_text,
            "metadata": {
                "input_event_count": len(active_events),
                "used_previous_summary": bool(previous_summary),
            },
        }

    def _generate_summary(self, *, previous_summary: str, active_events: list[dict]) -> str:
        client = self.dialog_client
        if client is not None and hasattr(client, "generate_memory_summary"):
            summary = client.generate_memory_summary(
                previous_summary=previous_summary,
                events=active_events,
            )
            if _clean_text(summary):
                return _clean_text(summary)
        return self._fallback_summary(previous_summary=previous_summary, active_events=active_events)

    @staticmethod
    def _fallback_summary(*, previous_summary: str, active_events: list[dict]) -> str:
        if not active_events and previous_summary:
            return previous_summary
        lines: list[str] = []
        if previous_summary:
            lines.append(previous_summary)
        compacted = []
        for item in active_events:
            content = _clean_text(item.get("content"))
            if not content:
                continue
            compacted.append(content)
        if not compacted:
            return previous_summary or "No important dialogue to compact."
        if len(compacted) == 1:
            lines.append(compacted[0])
        else:
            lines.append(compacted[0])
            lines.append(compacted[-1])
        return " ".join(part for part in lines if part).strip()
