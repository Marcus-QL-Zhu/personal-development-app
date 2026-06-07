from uuid import uuid4
import re

from .lookup_marker import split_preview_lookup_marker


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _strip_assistant_prefix(text: str, assistant_name: str) -> str:
    cleaned = _normalize_text(text)
    name = _normalize_text(assistant_name)
    if not cleaned or not name:
        return cleaned
    patterns = [
        rf"^{re.escape(name)}\s*[：:]\s*",
        rf"^{re.escape(name)}\s*（未说）\s*[：:]\s*",
        rf"^{re.escape(name)}\s*\(未说\)\s*[：:]\s*",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            stripped = re.sub(pattern, "", cleaned, count=1)
            if stripped != cleaned:
                cleaned = stripped.strip()
                changed = True
    inline_pattern = rf"([。！？.!?；;]\s*){re.escape(name)}\s*[：:]\s*"
    cleaned = re.sub(inline_pattern, r"\1", cleaned)
    return cleaned


def _sanitize_reply_for_speech(reply: dict, *, assistant_name: str) -> dict:
    sanitized = dict(reply or {})
    for key in ("lead", "tail", "content"):
        if key in sanitized:
            sanitized[key] = _strip_assistant_prefix(str(sanitized.get(key) or ""), assistant_name)
    if not sanitized.get("content"):
        sanitized["content"] = sanitized.get("lead") or sanitized.get("tail") or ""
    return sanitized


class AutoInterruptService:
    def __init__(self, orchestrator, tts_adapter) -> None:
        self.orchestrator = orchestrator
        self.tts_adapter = tts_adapter

    def plan(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        return self.orchestrator.plan_reply(
            events,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )

    def plan_progressive(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        return self.orchestrator.plan_progressive_reply(
            events,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )

    def preview(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
        assistant_voice_id: str | None = None,
    ) -> dict:
        plan = self.orchestrator.plan_lead_preview(
            events,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        if not plan["should_interrupt"]:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": plan.get("decision_reason", "no_interrupt"),
                "reply": plan.get("reply"),
                "speech_job": None,
                "assistant_event": None,
                "turn_id": None,
                "reply_id": None,
                "transcript": plan.get("transcript", ""),
            }

        turn_id = uuid4().hex
        reply_id = uuid4().hex
        plan["reply"] = _sanitize_reply_for_speech(plan["reply"], assistant_name=assistant_name)
        raw_preview_text = plan["reply"].get("content", "")
        spoken_preview_text, preview_had_marker = split_preview_lookup_marker(raw_preview_text)
        if preview_had_marker:
            plan["reply"] = {
                **plan["reply"],
                "content": spoken_preview_text,
                "lead": spoken_preview_text,
                "tail": "",
            }
        try:
            speech_job = self.tts_adapter.speak(
                plan["reply"]["content"],
                reply=plan["reply"],
                turn_id=turn_id,
                reply_id=reply_id,
                voice_id=assistant_voice_id,
            )
        except Exception:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": "preview_tts_error",
                "reply": plan["reply"],
                "speech_job": None,
                "assistant_event": None,
                "turn_id": turn_id,
                "reply_id": reply_id,
                "transcript": plan.get("transcript", ""),
            }

        assistant_event = {
            "kind": "assistant_preview",
            "source": "runtime_preview",
            "mode": plan["mode"],
            "content": plan["reply"]["content"],
            "speech_job": speech_job,
            "turn_id": turn_id,
            "reply_id": reply_id,
        }
        return {
            "interrupt": True,
            "mode": plan["mode"],
            "decision_reason": plan.get("decision_reason", "interrupt"),
            "reply": plan["reply"],
            "speech_job": speech_job,
            "assistant_event": assistant_event,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "transcript": plan.get("transcript", ""),
            "lookup_marker": False,
            "raw_preview_text": raw_preview_text,
        }

    def build_response(
        self,
        plan: dict,
        *,
        assistant_name: str = "宝子",
        assistant_voice_id: str | None = None,
    ) -> dict:
        if not plan["should_interrupt"]:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": plan.get("decision_reason", "no_interrupt"),
                "reply": plan["reply"],
                "speech_job": None,
                "assistant_event": None,
                "turn_id": None,
                "reply_id": None,
                "analysis_needed": plan.get("analysis_needed", False),
                "analysis_query": plan.get("analysis_query"),
            }

        turn_id = uuid4().hex
        reply_id = uuid4().hex
        plan["reply"] = _sanitize_reply_for_speech(plan["reply"], assistant_name=assistant_name)
        raw_formal_text = plan["reply"].get("content", "")
        spoken_formal_text, lookup_marker = split_preview_lookup_marker(raw_formal_text)
        if lookup_marker:
            plan["reply"] = {
                **plan["reply"],
                "content": spoken_formal_text,
                "lead": spoken_formal_text,
                "tail": "",
            }
        try:
            if hasattr(self.tts_adapter, "prepare_job"):
                speech_job = self.tts_adapter.prepare_job(
                    plan["reply"]["content"],
                    reply=plan["reply"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    voice_id=assistant_voice_id,
                )
            else:
                speech_job = self.tts_adapter.speak(
                    plan["reply"]["content"],
                    reply=plan["reply"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    voice_id=assistant_voice_id,
                )
        except Exception:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": "tts_error",
                "reply": plan["reply"],
                "speech_job": None,
                "assistant_event": None,
                "turn_id": turn_id,
                "reply_id": reply_id,
                "analysis_needed": plan.get("analysis_needed", False),
                "analysis_query": plan.get("analysis_query"),
            }
        assistant_event = {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": plan["mode"],
            "content": plan["reply"]["content"],
            "speech_job": speech_job,
            "turn_id": turn_id,
            "reply_id": reply_id,
        }
        return {
            "interrupt": True,
            "mode": plan["mode"],
            "decision_reason": plan.get("decision_reason", "interrupt"),
            "reply": plan["reply"],
            "speech_job": speech_job,
            "assistant_event": assistant_event,
            "turn_id": turn_id,
            "reply_id": reply_id,
            "analysis_needed": plan.get("analysis_needed", False),
            "analysis_query": plan.get("analysis_query"),
            "lookup_marker": lookup_marker,
            "raw_formal_text": raw_formal_text,
        }

    def heartbeat(
        self,
        events: list[dict],
        *,
        player_names: list[str],
        assistant_name: str = "瀹濆瓙",
        assistant_personality: str | None = None,
        assistant_voice_id: str | None = None,
    ) -> dict:
        plan = self.orchestrator.plan_heartbeat(
            events,
            player_names=player_names,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        if not plan["should_interrupt"]:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": plan.get("decision_reason", "heartbeat_unavailable"),
                "reply": plan.get("reply"),
                "speech_job": None,
                "assistant_event": None,
                "turn_id": None,
                "reply_id": None,
            }

        turn_id = uuid4().hex
        reply_id = uuid4().hex
        plan["reply"] = _sanitize_reply_for_speech(plan["reply"], assistant_name=assistant_name)
        try:
            if hasattr(self.tts_adapter, "prepare_job"):
                speech_job = self.tts_adapter.prepare_job(
                    plan["reply"]["content"],
                    reply=plan["reply"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    voice_id=assistant_voice_id,
                )
            else:
                speech_job = self.tts_adapter.speak(
                    plan["reply"]["content"],
                    reply=plan["reply"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    voice_id=assistant_voice_id,
                )
        except Exception:
            return {
                "interrupt": False,
                "mode": plan["mode"],
                "decision_reason": "heartbeat_tts_error",
                "reply": plan["reply"],
                "speech_job": None,
                "assistant_event": None,
                "turn_id": turn_id,
                "reply_id": reply_id,
            }
        assistant_event = {
            "kind": "assistant_heartbeat",
            "source": "companion_heartbeat",
            "mode": plan["mode"],
            "content": plan["reply"]["content"],
            "speech_job": speech_job,
            "turn_id": turn_id,
            "reply_id": reply_id,
        }
        return {
            "interrupt": True,
            "mode": plan["mode"],
            "decision_reason": "heartbeat",
            "reply": plan["reply"],
            "speech_job": speech_job,
            "assistant_event": assistant_event,
            "turn_id": turn_id,
            "reply_id": reply_id,
        }

    def run_once(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
        assistant_voice_id: str | None = None,
    ) -> dict:
        return self.build_response(
            self.plan(
                events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            ),
            assistant_name=assistant_name,
            assistant_voice_id=assistant_voice_id,
        )
