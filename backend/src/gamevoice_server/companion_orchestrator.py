from __future__ import annotations

import logging

from .dialog_client import normalize_reply_payload
logger = logging.getLogger(__name__)
CONVERSATION_MODE = "conversation"


def _lead_only_reply(reply: dict, *, default_source: str) -> dict | None:
    normalized = normalize_reply_payload(reply, default_source=default_source)
    content = (normalized.get("content") or "").strip()
    lead = (normalized.get("lead") or content).strip()
    if not lead:
        return None
    return {
        "source": normalized.get("source", default_source),
        "lead": lead,
        "tail": "",
        "content": lead,
    }


def _fallback_heartbeat_reply(player_names: list[str]) -> dict:
    target = player_names[0] if player_names else "宝宝们"
    content = f"{target}，别摸鱼了，到你表演了。" if player_names else "宝宝们别安静了，谁先来整点动静？"
    return normalize_reply_payload({"source": "companion_fallback", "content": content}, default_source="companion")


def _looks_unplayable_heartbeat_reply(content: str) -> bool:
    cleaned = str(content or "").strip()
    if not cleaned:
        return True
    if len(cleaned) > 45:
        return True
    bad_markers = (
        "用户要求",
        "当前信息",
        "需要一句",
        "不要JSON",
        "不要 JSON",
        "Markdown",
        "计时器",
        "heartbeat",
        "系统",
        "触发规则",
    )
    return any(marker in cleaned for marker in bad_markers)


class CompanionOrchestrator:
    def __init__(self, timing, dialog_client) -> None:
        self.timing = timing
        self.dialog_client = dialog_client

    def _generate_reply(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str,
        assistant_personality: str | None,
    ) -> dict:
        try:
            return self.dialog_client.generate_reply(
                mode=mode,
                transcript=transcript,
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
        except TypeError as exc:
            if "assistant_name" not in str(exc) and "assistant_personality" not in str(exc):
                raise
            return self.dialog_client.generate_reply(
                mode=mode,
                transcript=transcript,
                events=events,
            )

    def _generate_lead_preview(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str,
        assistant_personality: str | None,
    ) -> dict | None:
        try:
            return self.dialog_client.generate_lead_preview(
                mode=mode,
                transcript=transcript,
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
        except TypeError as exc:
            if "assistant_name" not in str(exc) and "assistant_personality" not in str(exc):
                raise
            return self.dialog_client.generate_lead_preview(
                mode=mode,
                transcript=transcript,
                events=events,
            )

    def plan_reply(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        """
        Plan a reply for the current dialog context.

        Replies delegate to plan_lead_preview() which uses
            generate_lead_preview() (SiliconFlow pure text when available), then TTS.
        SkillAgent spawning is handled later from completed spoken replies, not here.
        No interrupt -> returns without TTS.
        """
        transcripts = [
            item for item in events if item.get("kind") == "voice_transcript" and item.get("content")
        ]
        if not transcripts:
            return {
                "mode": "idle",
                "should_interrupt": False,
                "decision_reason": "idle",
                "transcript": "",
                "reply": normalize_reply_payload(
                    {"source": "companion", "content": "我在桌边听着。"},
                    default_source="companion",
                ),
                "analysis_needed": False,
                "analysis_query": None,
            }

        latest = transcripts[-1]["content"]
        decision = self.timing.should_interrupt(
            latest,
            events,
            assistant_name=assistant_name,
        )
        if not decision["interrupt"]:
            return {
                "mode": CONVERSATION_MODE,
                "should_interrupt": False,
                "decision_reason": decision.get("reason", CONVERSATION_MODE),
                "transcript": latest,
                "reply": None,
                "analysis_needed": False,
                "analysis_query": None,
            }

        mode = decision.get("mode", CONVERSATION_MODE)
        # Prefer preview, but final reply must fail open.
        try:
            plan = self.plan_lead_preview(
                events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
        except Exception as exc:
            logger.warning(
                "lead preview failed during final planning; falling back to formal reply mode=%s transcript=%r error=%s",
                mode,
                latest[:120],
                exc,
            )
            reply = normalize_reply_payload(
                self._generate_reply(
                    mode=mode,
                    transcript=latest,
                    events=events,
                    assistant_name=assistant_name,
                    assistant_personality=assistant_personality,
                ),
                default_source="companion",
            )
            plan = {
                "mode": mode,
                "should_interrupt": True,
                "decision_reason": decision.get("reason", "interrupt"),
                "transcript": latest,
                "reply": reply,
            }
        plan["analysis_needed"] = False
        plan["analysis_query"] = None
        return plan

    def plan_lead_preview(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        transcripts = [
            item for item in events if item.get("kind") == "voice_transcript" and item.get("content")
        ]
        if not transcripts:
            return {
                "mode": "idle",
                "should_interrupt": False,
                "decision_reason": "idle",
                "transcript": "",
                "reply": None,
            }

        latest = transcripts[-1]["content"]
        decision = self.timing.should_interrupt(
            latest,
            events,
            assistant_name=assistant_name,
        )
        if not decision["interrupt"]:
            return {
                "mode": decision.get("mode", CONVERSATION_MODE),
                "should_interrupt": False,
                "decision_reason": decision.get("reason", CONVERSATION_MODE),
                "transcript": latest,
                "reply": None,
            }

        mode = decision.get("mode", CONVERSATION_MODE)
        reply = _lead_only_reply(
            self._generate_lead_preview(
                mode=CONVERSATION_MODE,
                transcript=latest,
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
            or {},
            default_source="companion",
        )

        if not reply:
            return {
                "mode": mode,
                "should_interrupt": False,
                "decision_reason": "preview_unavailable",
                "transcript": latest,
                "reply": None,
            }

        return {
            "mode": mode,
            "should_interrupt": True,
            "decision_reason": decision.get("reason", "interrupt"),
            "transcript": latest,
            "reply": reply,
        }

    def plan_heartbeat(
        self,
        events: list[dict],
        *,
        player_names: list[str],
        assistant_name: str = "瀹濆瓙",
        assistant_personality: str | None = None,
    ) -> dict:
        try:
            reply = self.dialog_client.generate_heartbeat(
                events=events,
                player_names=player_names,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
        except AttributeError:
            target = player_names[0] if player_names else "宝宝们"
            reply = self._generate_reply(
                mode=CONVERSATION_MODE,
                transcript=f"桌面安静了几分钟，主动Q一下{target}，带动桌面气氛。",
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )
        normalized = normalize_reply_payload(reply, default_source="companion")
        if _looks_unplayable_heartbeat_reply(normalized.get("content", "")):
            normalized = _fallback_heartbeat_reply(player_names)
        if not normalized.get("content"):
            return {
                "mode": CONVERSATION_MODE,
                "should_interrupt": False,
                "decision_reason": "heartbeat_unavailable",
                "transcript": "",
                "reply": None,
            }
        return {
            "mode": CONVERSATION_MODE,
            "should_interrupt": True,
            "decision_reason": "heartbeat",
            "transcript": "",
            "reply": normalized,
        }

    def plan_progressive_reply(
        self,
        events: list[dict],
        *,
        assistant_name: str = "宝子",
        assistant_personality: str | None = None,
    ) -> dict:
        transcripts = [
            item for item in events if item.get("kind") == "voice_transcript" and item.get("content")
        ]
        if not transcripts:
            return {
                "mode": "idle",
                "should_interrupt": False,
                "decision_reason": "idle",
                "transcript": "",
                "reply": None,
                "deferred_generation": False,
                "analysis_needed": False,
                "analysis_query": None,
            }

        latest = transcripts[-1]["content"]
        decision = self.timing.should_interrupt(
            latest,
            events,
            assistant_name=assistant_name,
        )
        if not decision["interrupt"]:
            return {
                "mode": decision.get("mode", CONVERSATION_MODE),
                "should_interrupt": False,
                "decision_reason": decision.get("reason", CONVERSATION_MODE),
                "transcript": latest,
                "reply": None,
                "deferred_generation": False,
                "analysis_needed": False,
                "analysis_query": None,
            }

        mode = decision.get("mode", CONVERSATION_MODE)

        return {
            "mode": mode,
            "should_interrupt": True,
            "decision_reason": decision.get("reason", "interrupt"),
            "transcript": latest,
            "reply": None,
            "deferred_generation": True,
            "analysis_needed": False,
            "analysis_query": None,
        }
