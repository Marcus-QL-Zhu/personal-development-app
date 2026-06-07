import json
import logging
import re
from typing import Callable
from urllib import request

from .config import Settings

logger = logging.getLogger(__name__)
CONVERSATION_MODE = "conversation"


class RuleBasedTurnHeuristics:
    rule_triggers = (
        "规则",
        "不对",
        "争议",
        "有问题",
        "到底怎么判",
        "rule",
        "timing feels wrong",
        "rules feel wrong",
    )
    direct_address_triggers = (
        "你觉得",
        "你说",
        "你来",
        "帮我",
        "帮忙",
        "你帮",
        "告诉我",
        "怎么看",
        "能不能",
        "可不可以",
        "what should i do",
        "how should i",
        "can you help",
        "what do you think",
    )
    banter_triggers = (
        "哈哈",
        "笑死",
        "离谱",
        "绝了",
        "牛啊",
        "好耶",
        "太秀了",
        "太搞了",
        "crazy",
        "wild",
        "that is insane",
    )
    playful_request_triggers = (
        "讲个笑话",
        "讲笑话",
        "说个笑话",
        "逗我",
        "夸我",
        "tell me a joke",
        "say something funny",
        "make me laugh",
    )
    heckle_triggers = (
        "傻逼",
        "傻比",
        "煞笔",
        "笨比",
        "菜逼",
        "菜比",
        "太菜",
        "真菜",
        "好菜",
        "菜死",
        "蠢",
        "废物",
        "拉胯",
        "抽象",
    )
    question_markers = (
        "?",
        "？",
        "吗",
        "么",
        "怎么",
        "呢",
        "要不要",
        "行不行",
        "能不能",
        "可不可以",
    )

    def decide(
        self,
        transcript: str,
        events: list[dict] | None = None,
        *,
        assistant_name: str = "宝子",
    ) -> dict:
        text = transcript.strip()
        lowered = text.lower()
        recent_events = events or []
        if not text:
            return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "empty"}

        if self._contains_assistant_name(text, lowered, assistant_name):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "assistant_name_called"}

        if any(token in text or token in lowered for token in self.rule_triggers):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "rule_trigger"}

        if any(token in text or token in lowered for token in self.direct_address_triggers):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "direct_address"}

        if self._is_followup_after_name_call(text, lowered, recent_events, assistant_name):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "followup_after_name_call"}

        if any(token in text or token in lowered for token in self.playful_request_triggers):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "playful_request"}

        if self._looks_like_heckle_hook(
            text=text,
            lowered=lowered,
            events=recent_events,
            assistant_name=assistant_name,
        ):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "heckle_hook"}

        if self._looks_like_banter_hook(text=text, lowered=lowered, events=recent_events):
            return {"interrupt": True, "mode": CONVERSATION_MODE, "reason": "banter_hook"}

        if len(text) <= 8 and not any(marker in text for marker in self.question_markers):
            return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "table_talk"}

        return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "local_no_trigger"}

    def _mentions_rule_context(self, text: str, lowered: str) -> bool:
        return any(token in text or token in lowered for token in self.rule_triggers)

    def _contains_assistant_name(self, text: str, lowered: str, assistant_name: str) -> bool:
        name = assistant_name.strip()
        if not name:
            return False
        lowered_name = name.lower()
        if self._is_ascii_word(lowered_name):
            return re.search(rf"\b{re.escape(lowered_name)}\b", lowered) is not None
        return lowered_name in lowered

    @staticmethod
    def _is_ascii_word(text: str) -> bool:
        return re.fullmatch(r"[a-z0-9_-]+", text) is not None

    def _is_followup_after_name_call(
        self,
        text: str,
        lowered: str,
        events: list[dict],
        assistant_name: str,
    ) -> bool:
        user_turns = [
            item.get("content", "")
            for item in events
            if item.get("kind") == "voice_transcript" and item.get("content")
        ]
        if not user_turns:
            return False

        recent_turns = user_turns[-2:]
        if not any(
            self._contains_assistant_name(turn, turn.lower(), assistant_name) for turn in recent_turns
        ):
            return False

        if any(token in text or token in lowered for token in self.direct_address_triggers):
            return True
        if any(marker in text for marker in self.question_markers):
            return True
        return len(text) <= 20

    def _looks_like_banter_hook(self, *, text: str, lowered: str, events: list[dict]) -> bool:
        if len(text) > 20:
            return False
        if not any(token in text or token in lowered for token in self.banter_triggers):
            return False
        recent_user_turns = [
            item
            for item in events[-4:]
            if item.get("kind") == "voice_transcript" and item.get("content")
        ]
        return bool(recent_user_turns)

    def _looks_like_heckle_hook(
        self,
        *,
        text: str,
        lowered: str,
        events: list[dict],
        assistant_name: str,
    ) -> bool:
        if not any(token in text or token in lowered for token in self.heckle_triggers):
            return False
        known_aliases = self._known_player_aliases(events, assistant_name=assistant_name)
        return any(alias in text for alias in known_aliases)

    def _known_player_aliases(self, events: list[dict], *, assistant_name: str) -> list[str]:
        assistant = assistant_name.strip()
        aliases: list[str] = []
        for item in events:
            alias_map = item.get("speaker_alias_map")
            if isinstance(alias_map, dict):
                for raw_aliases in alias_map.values():
                    if not isinstance(raw_aliases, list):
                        continue
                    aliases.extend(str(alias).strip() for alias in raw_aliases)
            identities = item.get("speaker_identities")
            if isinstance(identities, list):
                for identity in identities:
                    if not isinstance(identity, dict):
                        continue
                    linked_name = str(identity.get("linked_name") or "").strip()
                    if linked_name:
                        aliases.append(linked_name)
                    raw_aliases = identity.get("aliases")
                    if isinstance(raw_aliases, list):
                        aliases.extend(str(alias).strip() for alias in raw_aliases)

        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            cleaned = alias.strip()
            if not cleaned or cleaned == assistant:
                continue
            if cleaned.startswith("speaker_") or cleaned.startswith("player_"):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return sorted(normalized, key=len, reverse=True)


class PlaceholderTurnDecisionClient:
    def decide_turn(self, *, transcript: str, events: list[dict], assistant_name: str) -> dict:
        return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "model_skipped"}


class MiniMaxTurnDecisionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "MiniMax-M2.7-highspeed",
        base_url: str = "https://api.minimaxi.com/anthropic",
        timeout_seconds: float = 20.0,
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or self._send_request

    def decide_turn(self, *, transcript: str, events: list[dict], assistant_name: str) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": 120,
            "temperature": 0,
            "system": (
                "You decide whether the assistant should interrupt now. "
                "Output only JSON with keys interrupt and reason. "
                'Schema: {"interrupt": true/false, "reason": "short_reason"}. '
                "If user is talking to the assistant, interrupt=true. "
                "If players are chatting among themselves, interrupt=false."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._build_prompt(
                                transcript=transcript,
                                events=events,
                                assistant_name=assistant_name,
                            ),
                        }
                    ],
                }
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        response_bytes = self._request_sender(
            f"{self.base_url}/v1/messages",
            body,
            headers,
            self.timeout_seconds,
        )
        response = json.loads(response_bytes.decode("utf-8"))
        text = "".join(
            block.get("text", "")
            for block in response.get("content", [])
            if block.get("type") == "text" and block.get("text")
        ).strip()
        parsed = self._parse_decision_payload(text)
        if parsed is None:
            logger.warning("turn decision parse failed, raw_text=%r", text[:240])
            return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "model_parse_error"}
        return {
            "interrupt": bool(parsed.get("interrupt", False)),
            "mode": CONVERSATION_MODE,
            "reason": str(parsed.get("reason", "model")),
        }

    @staticmethod
    def _build_prompt(*, transcript: str, events: list[dict], assistant_name: str) -> str:
        recent_lines: list[str] = []
        for item in events[-6:]:
            kind = item.get("kind")
            source = item.get("source", "unknown")
            content = item.get("content")
            if not content:
                continue
            if kind == "voice_transcript":
                recent_lines.append(f"user({source}): {content}")
            elif kind == "assistant_spoken":
                recent_lines.append(f"assistant: {content}")
        context_block = "\n".join(recent_lines) if recent_lines else "(empty)"
        return (
            f"assistant_name: {assistant_name}\n"
            f"latest_user_text: {transcript}\n"
            f"recent_context:\n{context_block}"
        )

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()

    @staticmethod
    def _parse_decision_payload(text: str) -> dict | None:
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        candidate = MiniMaxTurnDecisionClient._extract_first_json_object(text)
        if not candidate:
            fence_stripped = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
            if fence_stripped and fence_stripped != text:
                try:
                    parsed = json.loads(fence_stripped)
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
            return None
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_first_json_object(text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
                continue
            if ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None


class TurnDecisionEngine:
    def __init__(
        self,
        *,
        heuristics: RuleBasedTurnHeuristics,
        decision_client,
        use_model_fallback: bool = False,
    ) -> None:
        self.heuristics = heuristics
        self.decision_client = decision_client
        self.use_model_fallback = use_model_fallback

    def decide_turn(self, *, transcript: str, events: list[dict], assistant_name: str) -> dict:
        heuristic = self.heuristics.decide(
            transcript,
            events,
            assistant_name=assistant_name,
        )
        if heuristic["reason"] != "local_no_trigger":
            return heuristic
        if not self.use_model_fallback or self.decision_client is None:
            return {"interrupt": False, "mode": CONVERSATION_MODE, "reason": "local_no_trigger"}
        return self.decision_client.decide_turn(
            transcript=transcript,
            events=events,
            assistant_name=assistant_name,
        )


def build_turn_decision_engine(settings: Settings) -> TurnDecisionEngine:
    if settings.minimax_api_key:
        client = MiniMaxTurnDecisionClient(
            api_key=settings.minimax_api_key,
            model=settings.minimax_text_model,
            base_url=settings.minimax_text_base_url,
            timeout_seconds=settings.minimax_text_timeout_seconds,
        )
    else:
        client = PlaceholderTurnDecisionClient()

    return TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=client,
        use_model_fallback=False,
    )
