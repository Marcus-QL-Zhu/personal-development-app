import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib import request

from .config import Settings
from .lookup_marker import split_preview_lookup_marker

logger = logging.getLogger(__name__)
TEXT_POST_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
SILICONFLOW_CHAT_COMPLETIONS_URL = "https://api.siliconflow.cn/v1/chat/completions"

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?\.])")
_CONVERSATION_COMPLETE_ENDINGS = tuple("。！？!?）)”」』")

_PREVIEW_COMPLETE_ENDINGS = tuple("。！？!?…）)”」』\"'")


class NoUsableReplyError(RuntimeError):
    pass


_PREVIEW_FRAGMENT_SUFFIXES = (
    "\u7ed9\u4f60",
    "\u6211\u7ed9\u4f60",
    "\u6211\u5148\u7ed9\u4f60",
    "\u5148\u7ed9\u4f60",
    "\u8fd9\u4e2a\u6211",
    "\u6211\u6765\u7ed9\u4f60",
    "\u6211\u6765",
    "\u8ba9\u6211",
    "\u54b1\u4eec\u5148",
    "\u5148\u8bf4",
    "\u5148\u7ed9",
    "\u7ed9\u4f60\u8bb2",
    "\u7ed9\u4f60\u4ecb\u7ecd",
)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _ensure_heartbeat_terminal_punctuation(value: object) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    if cleaned[-1] in "。！？!?~～…)]}\"'”’」』》":
        return cleaned
    return f"{cleaned}。"


def _fallback_heartbeat_content(player_names: list[str]) -> str:
    names = [name for name in (_clean_text(name) for name in player_names) if name]
    if names:
        target = names[time.monotonic_ns() % len(names)]
        templates = (
            "{target}，到你了，别装没听见。",
            "{target}，牌都等凉了，快点。",
            "{target}，别光想了，先动一下吧。",
            "{target}，你这回合还要盘多久啊？",
        )
        return templates[time.monotonic_ns() % len(templates)].format(target=target)
    templates = (
        "宝宝们别安静了，谁来整点动静？",
        "宝宝们，别光发呆，动起来。",
        "来来来，谁先开个头？",
        "都别装沉思了，该谁了？",
    )
    return templates[time.monotonic_ns() % len(templates)]


def _strip_named_prefix(value: object, name: object) -> str:
    cleaned = _clean_text(value)
    assistant_name = _clean_text(name)
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
    return re.sub(inline_pattern, r"\1", cleaned)


def split_lead_tail(text: str) -> tuple[str, str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return "", ""

    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]).strip()

    if len(cleaned) <= 40:
        return cleaned, ""

    for idx, char in enumerate(cleaned):
        if char in "。！？!?." and idx >= 6:
            lead = cleaned[: idx + 1].strip()
            tail = cleaned[idx + 1 :].strip()
            if lead and tail:
                return lead, tail

    midpoint = max(1, min(len(cleaned) // 2, 28))
    return cleaned[:midpoint].strip(), cleaned[midpoint:].strip()


def normalize_reply(reply: dict, *, default_source: str = "companion") -> dict:
    payload = dict(reply or {})
    source = _clean_text(payload.get("source")) or default_source
    lead = _clean_text(payload.get("lead"))
    tail = _clean_text(payload.get("tail"))
    content = _clean_text(payload.get("content"))

    if not content and (lead or tail):
        content = " ".join(item for item in [lead, tail] if item).strip()

    if not lead and not tail and content:
        lead, tail = split_lead_tail(content)
    elif lead and not tail and content:
        inferred_lead, inferred_tail = split_lead_tail(content)
        if inferred_tail:
            lead = inferred_lead or lead
            tail = inferred_tail
    elif tail and not lead and content:
        inferred_lead, inferred_tail = split_lead_tail(content)
        if inferred_lead:
            lead = inferred_lead
        if inferred_tail:
            tail = inferred_tail

    if not content:
        content = " ".join(item for item in [lead, tail] if item).strip()

    if not lead:
        lead = content

    return {
        "source": source,
        "lead": lead,
        "tail": tail,
        "content": content,
    }


def normalize_reply_payload(reply: dict, *, default_source: str = "companion") -> dict:
    return normalize_reply(reply, default_source=default_source)


def _build_reply_contract(*, source: str, content: str) -> dict:
    lead, tail = split_lead_tail(content)
    return normalize_reply(
        {
            "source": source,
            "lead": lead,
            "tail": tail,
            "content": content,
        },
        default_source=source,
    )



def _looks_truncated_conversation_reply(content: str) -> bool:
    cleaned = _clean_text(content)
    if not cleaned:
        return False
    if cleaned.endswith(_CONVERSATION_COMPLETE_ENDINGS):
        return False
    if len(cleaned) >= 24:
        return True
    return len(cleaned) >= 6 and bool(re.fullmatch(r"[\w\u4e00-\u9fff\s]+", cleaned))


def looks_truncated_conversation_reply(reply: dict | None) -> bool:
    if not reply:
        return False
    content = _clean_text(reply.get("content"))
    tail = _clean_text(reply.get("tail"))
    if _looks_truncated_conversation_reply(content):
        return True
    if content and tail and (tail.startswith(content) or content.startswith(tail)):
        return True
    return False


def _looks_complete_preview_lead(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    if cleaned.endswith(_PREVIEW_COMPLETE_ENDINGS):
        return True
    return cleaned[-1:] in {".", "!", "?", "…"}


def _looks_publishable_preview_lead(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    if _looks_complete_preview_lead(cleaned):
        return True
    if any(cleaned.endswith(suffix) for suffix in _PREVIEW_FRAGMENT_SUFFIXES):
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    latin_digit_count = len(re.findall(r"[A-Za-z0-9]", cleaned))
    if cjk_count >= 12:
        return True
    if latin_digit_count >= 24:
        return True
    return False


class PlaceholderDialogClient:
    def generate_reply(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        strict: bool = False,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        content = f"我先记下这句：{transcript}" if transcript else "我在，接着说。"
        return normalize_reply(
            {
                "source": "companion",
                "content": content,
            },
            default_source="companion",
        )

    def generate_lead_preview(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict | None:
        reply = self.generate_reply(
            mode=mode,
            transcript=transcript,
            events=events,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        lead = _clean_text(reply.get("lead")) or _clean_text(reply.get("content"))
        if not lead:
            return None
        return {
            "source": _clean_text(reply.get("source")) or "companion",
            "lead": lead,
            "tail": "",
            "content": lead,
        }

    def generate_heartbeat(
        self,
        *,
        events: list[dict],
        player_names: list[str],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        target = player_names[0] if player_names else "宝宝们"
        return normalize_reply(
            {
                "source": "companion",
                "content": f"{target}，别沉默了，出来整点动静。",
            },
            default_source="companion",
        )

    def generate_memory_summary(self, *, previous_summary: str, events: list[dict]) -> str:
        lines = [previous_summary.strip()] if previous_summary.strip() else []
        visible = [_clean_text(item.get("content")) for item in events if _clean_text(item.get("content"))]
        if visible:
            lines.append(visible[0])
        if len(visible) > 1:
            lines.append(visible[-1])
        return " ".join(item for item in lines if item).strip() or "No important dialogue to compact."

    def rewrite_speaker_alias_map(
        self,
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        return {
            str(speaker_id): [str(alias).strip() for alias in aliases if str(alias).strip()]
            for speaker_id, aliases in current_alias_map.items()
        }


class MiniMaxDialogClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "MiniMax-M2.7-highspeed",
        base_url: str = TEXT_POST_URL,
        timeout_seconds: float = 20.0,
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
        stream_request_sender: Callable[
            [str, bytes, dict[str, str], float], Iterable[bytes | str]
        ]
        | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or self._send_request
        self._stream_request_sender = stream_request_sender or self._stream_request
        self._prefer_stream_request = stream_request_sender is not None or request_sender is None

    def generate_reply(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        strict: bool = False,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        settings = self._reply_settings(mode)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(settings["attempts"]):
            payload = {
                "model": self.model,
                "stream": settings["stream"],
                "max_completion_tokens": settings["max_completion_tokens"],
                "temperature": settings["temperature"],
                "top_p": settings["top_p"],
                "messages": [
                    {
                        "role": "system",
                        "name": "MiniMax AI",
                        "content": self._build_plain_reply_system_prompt(mode),
                    },
                    {
                        "role": "user",
                        "name": "用户",
                        "content": self._build_plain_reply_user_prompt(
                            mode=mode,
                            transcript=transcript,
                            events=events,
                            attempt=attempt,
                            assistant_name=assistant_name,
                            assistant_personality=assistant_personality,
                        ),
                    }
                ],
            }
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            t0 = time.monotonic()
            if self._prefer_stream_request and settings["stream"]:
                stream_sender = self._stream_request_sender or self._stream_request
                response = self._consume_text_post_stream_response(
                    stream_sender(
                        self.base_url,
                        body,
                        headers,
                        self.timeout_seconds,
                    )
                )
            else:
                response_bytes = self._request_sender(
                    self.base_url,
                    body,
                    headers,
                    self.timeout_seconds,
                )
                response = self._parse_text_post_response(response_bytes)
            elapsed_s = time.monotonic() - t0
            logger.info("minimax_generate_reply elapsed=%.3fs mode=%s transcript=%r", elapsed_s, mode, transcript[:50])
            text = self._extract_text(response)
            if self._should_retry(
                response=response,
                text=text,
                attempt=attempt,
                attempts=settings["attempts"],
            ):
                continue
            if text:
                return _build_reply_contract(source="minimax", content=text)

        logger.warning("MiniMax dialog returned no text for mode=%s transcript=%r", mode, transcript[:120])
        if strict:
            raise NoUsableReplyError(f"no usable {mode} reply from minimax")
        return _build_reply_contract(
            source="minimax_fallback",
            content=self._fallback_reply(mode=mode, transcript=transcript, events=events),
        )

    def generate_heartbeat(
        self,
        *,
        events: list[dict],
        player_names: list[str],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": False,
            "enable_thinking": False,
            "max_completion_tokens": 420,
            "temperature": 0.75,
            "top_p": 0.9,
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_heartbeat_system_prompt(),
                },
                {
                    "role": "user",
                    "name": "用户",
                    "content": self._build_heartbeat_user_prompt(
                        events=events,
                        player_names=player_names,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t0 = time.monotonic()
        response = self._parse_text_post_response(
            self._request_sender(
                self.base_url,
                body,
                headers,
                self.timeout_seconds,
            )
        )
        elapsed_s = time.monotonic() - t0
        logger.info("minimax_generate_heartbeat elapsed=%.3fs players=%s", elapsed_s, player_names[:4])
        text = _ensure_heartbeat_terminal_punctuation(self._extract_visible_text(response))
        if text:
            return _build_reply_contract(source="minimax", content=text)
        return _build_reply_contract(source="minimax_fallback", content=_fallback_heartbeat_content(player_names))

    def stream_reply_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str | None = None,
        continue_only: bool = False,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ):
        settings = self._reply_settings(mode)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": settings["stream"],
            "max_completion_tokens": settings["max_completion_tokens"],
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_plain_reply_system_prompt(mode),
                },
                {
                    "role": "user",
                    "name": "鐢ㄦ埛",
                    "content": self._build_plain_reply_user_prompt(
                        mode=mode,
                        transcript=transcript,
                        events=events,
                        attempt=0,
                        already_spoken_text=already_spoken_text,
                        continue_only=continue_only,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        chunks = self._stream_request_sender(
            self.base_url,
            body,
            headers,
            self.timeout_seconds,
        )
        yield from self._iter_text_post_stream_texts(chunks)


    def stream_continuation_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ):
        settings = self._reply_settings(mode)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": settings["stream"],
            "max_completion_tokens": settings["max_completion_tokens"],
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_continuation_system_prompt(mode),
                },
                {
                    "role": "user",
                    "name": "用户",
                    "content": self._build_continuation_user_prompt(
                        mode=mode,
                        transcript=transcript,
                        events=events,
                        already_spoken_text=already_spoken_text,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        chunks = self._stream_request_sender(
            self.base_url,
            body,
            headers,
            self.timeout_seconds,
        )
        yield from self._iter_text_post_stream_cumulative_texts(chunks)

    def generate_lead_preview(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict | None:
        latest_reply: dict | None = None
        for lead in self.stream_preview_text(
            mode=mode,
            transcript=transcript,
            events=events,
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        ):
            if not lead:
                continue
            latest_reply = {
                "source": "minimax",
                "lead": lead,
                "tail": "",
                "content": lead,
            }
            if _looks_complete_preview_lead(lead):
                return latest_reply
        if latest_reply and _looks_publishable_preview_lead(latest_reply["content"]):
            return latest_reply
        return None

    def stream_preview_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ):
        settings = self._reply_settings(mode)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": settings["stream"],
            "max_completion_tokens": settings["max_completion_tokens"],
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_preview_system_prompt(mode),
                },
                {
                    "role": "user",
                    "name": "用户",
                    "content": self._build_preview_user_prompt(
                        mode=mode,
                        transcript=transcript,
                        events=events,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        chunks = self._stream_request_sender(
            self.base_url,
            body,
            headers,
            self.timeout_seconds,
        )
        latest_text = None
        for text in self._iter_text_post_stream_cumulative_texts(chunks):
            cleaned = _clean_text(text)
            if not cleaned or cleaned == latest_text:
                continue
            latest_text = cleaned
            yield cleaned

    def generate_memory_summary(self, *, previous_summary: str, events: list[dict]) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": True,
            "max_completion_tokens": 900,
            "temperature": 0.2,
            "top_p": 0.9,
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_memory_compaction_system_prompt(),
                },
                {
                    "role": "user",
                    "name": "用户",
                    "content": self._build_memory_compaction_user_prompt(
                        previous_summary=previous_summary,
                        events=events,
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = self._consume_text_post_stream_response(
            self._stream_request_sender(
                self.base_url,
                body,
                headers,
                self.timeout_seconds,
            )
        )
        return _clean_text(self._extract_text(response))

    def rewrite_speaker_alias_map(
        self,
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "stream": False,
            "max_completion_tokens": 4096,
            "temperature": 0.1,
            "top_p": 0.9,
            "messages": [
                {
                    "role": "system",
                    "name": "MiniMax AI",
                    "content": self._build_alias_map_rewrite_system_prompt(),
                },
                {
                    "role": "user",
                    "name": "铁人",
                    "content": self._build_alias_map_rewrite_user_prompt(
                        dialogue_events=dialogue_events,
                        current_alias_map=current_alias_map,
                    ),
                },
            ],
            "tools": [
                self._build_alias_map_rewrite_tool(list(current_alias_map.keys())),
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_speaker_alias_map"},
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response_bytes = self._request_sender(
            self.base_url,
            body,
            headers,
            max(self.timeout_seconds, 120.0),
        )
        response = self._parse_text_post_response(response_bytes)
        tool_arguments = self._extract_alias_map_rewrite_tool_arguments(response)
        if tool_arguments is not None:
            text = json.dumps(tool_arguments, ensure_ascii=False)
        elif isinstance(response, dict) and all(str(key) in current_alias_map for key in response.keys()):
            text = json.dumps(response, ensure_ascii=False)
        else:
            text = _clean_text(self._extract_visible_text(response))
        if not text:
            raise NoUsableReplyError("no usable alias rewrite from minimax")
        parsed = self._parse_alias_map_rewrite_response(
            text,
            expected_speaker_ids=list(current_alias_map.keys()),
        )
        return self._filter_alias_map_by_evidence(parsed, dialogue_events=dialogue_events)

    @staticmethod
    def _filter_alias_map_by_evidence(
        alias_map: dict[str, list[str]],
        *,
        dialogue_events: list[dict],
    ) -> dict[str, list[str]]:
        visible_events = MiniMaxDialogClient._build_alias_rewrite_visible_events(dialogue_events)
        evidence_text = "\n".join(
            _clean_text(item.get("content"))
            for item in (visible_events or dialogue_events)
            if _clean_text(item.get("content"))
        )
        if not evidence_text:
            return alias_map
        return {
            speaker_id: [
                alias
                for alias in aliases
                if alias
                and alias in evidence_text
                and MiniMaxDialogClient._alias_has_high_confidence_support(
                    speaker_id=speaker_id,
                    alias=alias,
                    visible_events=visible_events or dialogue_events,
                )
            ]
            for speaker_id, aliases in alias_map.items()
        }

    @staticmethod
    def _alias_has_high_confidence_support(
        *,
        speaker_id: str,
        alias: str,
        visible_events: list[dict],
    ) -> bool:
        cleaned_alias = _clean_text(alias)
        if not cleaned_alias:
            return False
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", cleaned_alias)
        if cjk_chars and len(cjk_chars) < 2:
            return False
        matches: list[tuple[str, str]] = []
        event_pairs: list[tuple[str, str]] = []
        for item in visible_events:
            content = _clean_text(item.get("content"))
            event_speaker_id = MiniMaxDialogClient._alias_event_speaker_id(item, content)
            event_pairs.append((event_speaker_id, content))
            if cleaned_alias not in content:
                continue
            matches.append((event_speaker_id, content))
        if not matches:
            return False
        if MiniMaxDialogClient._alias_has_direct_address_support(
            speaker_id=speaker_id,
            alias=cleaned_alias,
            event_pairs=event_pairs,
        ):
            return True
        if MiniMaxDialogClient._alias_has_action_owner_support(
            speaker_id=speaker_id,
            alias=cleaned_alias,
            event_pairs=event_pairs,
        ):
            return True
        if not cjk_chars and any(match_speaker_id != speaker_id for match_speaker_id, _ in matches):
            return True
        own_text = "\n".join(content for _, content in matches)
        if (
            re.search(rf"(我叫|我是|叫我|I am|I'm|my name is)\s*{re.escape(cleaned_alias)}", own_text, re.IGNORECASE)
            and not MiniMaxDialogClient._looks_like_roleplay_self_intro(own_text)
        ):
            return True
        if cjk_chars:
            return False
        return True

    @staticmethod
    def _alias_has_direct_address_support(
        *,
        speaker_id: str,
        alias: str,
        event_pairs: list[tuple[str, str]],
    ) -> bool:
        for index, (event_speaker_id, content) in enumerate(event_pairs):
            if event_speaker_id == speaker_id or alias not in content:
                continue
            if not MiniMaxDialogClient._looks_like_direct_address(content, alias):
                continue
            return True
        return False

    @staticmethod
    def _alias_has_action_owner_support(
        *,
        speaker_id: str,
        alias: str,
        event_pairs: list[tuple[str, str]],
    ) -> bool:
        for index, (event_speaker_id, content) in enumerate(event_pairs):
            if event_speaker_id == speaker_id or alias not in content:
                continue
            if not MiniMaxDialogClient._looks_like_action_owner_clue(content, alias):
                continue
            if MiniMaxDialogClient._speaker_replies_soon(
                speaker_id=speaker_id,
                event_pairs=event_pairs,
                after_index=index,
            ):
                return True
        return False

    @staticmethod
    def _speaker_replies_soon(
        *,
        speaker_id: str,
        event_pairs: list[tuple[str, str]],
        after_index: int,
        max_turns: int = 3,
    ) -> bool:
        for event_speaker_id, content in event_pairs[after_index + 1 : after_index + 1 + max_turns]:
            if not content:
                continue
            if (
                event_speaker_id == speaker_id
                and MiniMaxDialogClient._looks_like_action_owner_response(content)
            ):
                return True
        return False

    @staticmethod
    def _looks_like_action_owner_response(content: str) -> bool:
        cleaned = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s*[:：]\s*", "", _clean_text(content))
        if not cleaned:
            return False
        if re.fullmatch(r"(?i)(oh my god|omg|wow|ok|okay)[。.!！?？\s]*", cleaned):
            return False
        action_markers = (
            "我",
            "那我",
            "试",
            "用",
            "拿",
            "捅",
            "骰",
            "投",
            "行动",
            "检查",
            "调查",
            "可以",
            "能不能",
        )
        return any(marker in cleaned for marker in action_markers)

    @staticmethod
    def _looks_like_direct_address(content: str, alias: str) -> bool:
        cleaned = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s*[:：]\s*", "", _clean_text(content))
        escaped = re.escape(alias)
        return bool(
            re.search(rf"(^|[，,。！？!?、\s])(哎|欸|诶|喂|那个)?[，,、\s]*{escaped}([，,。！？!?、\s]|$)", cleaned)
        )

    @staticmethod
    def _looks_like_action_owner_clue(content: str, alias: str) -> bool:
        cleaned = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s*[:：]\s*", "", _clean_text(content))
        escaped = re.escape(alias)
        patterns = (
            rf"(下一个行动的是|下个行动的是|轮到|该|到你了|你来吧).{{0,12}}{escaped}",
            rf"(不愧是你|可以啊|漂亮啊|厉害啊).{{0,8}}{escaped}",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    @staticmethod
    def _looks_like_action_owner_clue_turn(content: str) -> bool:
        cleaned = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s*[:：]\s*", "", _clean_text(content))
        patterns = (
            r"(下一个行动的是|下个行动的是|轮到|该|到你了|你来吧)\s*[\u4e00-\u9fff]{2,8}",
            r"(不愧是你|可以啊|漂亮啊|厉害啊)\s*[\u4e00-\u9fff]{2,8}",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    @staticmethod
    def _looks_like_roleplay_self_intro(content: str) -> bool:
        cleaned = _clean_text(content)
        roleplay_markers = (
            "NPC",
            "npc",
            "我是来",
            "不是来",
            "来带你们",
            "带你们去",
            "你们叫我",
            "这里是",
            "我的旧屋",
            "旅馆",
            "码头",
        )
        return any(marker in cleaned for marker in roleplay_markers)

    @staticmethod
    def _alias_event_speaker_id(item: dict, content: str) -> str:
        speaker_id = _clean_text(item.get("speaker_id"))
        if speaker_id:
            return speaker_id
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*[:：]", content)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _looks_like_alias_clue_turn(content: str) -> bool:
        cleaned = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_-]*\s*[:：]\s*", "", _clean_text(content))
        if not cleaned:
            return False
        clue_patterns = (
            r"(我叫|我是|叫我)\s*[\u4e00-\u9fffA-Za-z0-9_-]{2,12}",
            r"(I am|I'm|my name is)\s*[A-Za-z][A-Za-z0-9_-]{1,31}",
            r"(哎|喂|欸|诶)[，,、\s]*[\u4e00-\u9fff]{2,4}",
            r"(下一个行动的是|下个行动的是|轮到|该)\s*[\u4e00-\u9fff]{2,6}",
            r"(不愧是你|到你了|你来吧)[，,、\s]*[\u4e00-\u9fff]{2,6}",
            r"(老|小|阿)[\u4e00-\u9fff]{1,3}",
            r"[\u4e00-\u9fff]{1,4}(哥|姐|叔|姨|爷|总|老师|师傅|老板)",
        )
        return any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in clue_patterns)

    @staticmethod
    def _build_alias_rewrite_visible_events(
        dialogue_events: list[dict],
        *,
        max_windows: int = 32,
    ) -> list[dict]:
        records: list[dict] = []
        for item in dialogue_events:
            content = _clean_text(item.get("content"))
            if not content:
                continue
            records.append(
                {
                    "speaker_id": MiniMaxDialogClient._alias_event_speaker_id(item, content),
                    "content": content,
                }
            )
        candidate_indexes = [
            index
            for index, record in enumerate(records)
            if MiniMaxDialogClient._looks_like_alias_clue_turn(str(record.get("content") or ""))
        ]
        if not candidate_indexes and len(records) <= 12:
            return records

        ranges: list[tuple[int, int]] = []
        seen_ranges: set[tuple[int, int]] = set()
        for index in candidate_indexes:
            record = records[index]
            speaker_id = record.get("speaker_id") or ""
            next_speaker_id = records[index + 1].get("speaker_id") if index + 1 < len(records) else ""
            previous_speaker_id = records[index - 1].get("speaker_id") if index > 0 else ""
            if MiniMaxDialogClient._looks_like_action_owner_clue_turn(str(record.get("content") or "")):
                start = max(0, index - 4)
                end = min(len(records), index + 3)
            elif speaker_id and next_speaker_id and next_speaker_id != speaker_id:
                start = index
                end = min(len(records), index + 2)
            elif speaker_id and previous_speaker_id and previous_speaker_id != speaker_id:
                start = max(0, index - 1)
                end = index + 1
            else:
                start = index
                end = index + 1
            key = (start, end)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            ranges.append(key)

        if not ranges:
            for index, record in enumerate(records):
                speaker_id = record.get("speaker_id") or ""
                previous_speaker_id = records[index - 1].get("speaker_id") if index > 0 else ""
                next_speaker_id = records[index + 1].get("speaker_id") if index + 1 < len(records) else ""
                has_neighbor_reply = bool(
                    speaker_id
                    and (
                        (previous_speaker_id and previous_speaker_id != speaker_id)
                        or (next_speaker_id and next_speaker_id != speaker_id)
                    )
                )
                if not has_neighbor_reply:
                    continue
                start = max(0, index - 1)
                end = min(len(records), index + 2)
                key = (start, end)
                if key in seen_ranges:
                    continue
                seen_ranges.add(key)
                ranges.append(key)
        if not ranges:
            return records[-min(len(records), 12):]

        if len(ranges) <= max_windows:
            selected_ranges = ranges
        else:
            selected_indexes = {
                round(index * (len(ranges) - 1) / (max_windows - 1))
                for index in range(max_windows)
            }
            selected_ranges = [item for index, item in enumerate(ranges) if index in selected_indexes]
        visible: list[dict] = []
        seen_turns: set[int] = set()
        for start, end in selected_ranges:
            for index in range(start, end):
                if index in seen_turns:
                    continue
                seen_turns.add(index)
                visible.append(records[index])
        return visible

    @staticmethod
    def _format_alias_evidence_windows(dialogue_events: list[dict]) -> str:
        visible_events = MiniMaxDialogClient._build_alias_rewrite_visible_events(dialogue_events)
        if not visible_events:
            return "Alias evidence windows:\nNo usable alias evidence windows."
        lines = [
            "Alias evidence windows:",
            "这些窗口是从长对话里剪出的高价值片段；只根据这些窗口判断，不要把长对话整段重组。",
        ]
        action_focus_events: list[dict] = []
        seen_focus_turns: set[int] = set()
        for index, item in enumerate(visible_events):
            if not MiniMaxDialogClient._looks_like_action_owner_clue_turn(_clean_text(item.get("content"))):
                continue
            for focus_index in range(max(0, index - 4), min(len(visible_events), index + 4)):
                if focus_index in seen_focus_turns:
                    continue
                seen_focus_turns.add(focus_index)
                action_focus_events.append(visible_events[focus_index])
        if action_focus_events:
            lines.extend(
                [
                    "行动归属重点片段:",
                    "这些片段优先用于判断跑团里“轮到谁行动 / 不愧是你 NAME”这类称呼线索；短惊叹不等于承接，第一人称行动更重要。",
                ]
            )
            for index, item in enumerate(action_focus_events, start=1):
                speaker_id = _clean_text(item.get("speaker_id")) or "unknown"
                content = _clean_text(item.get("content"))
                lines.append(f"[focus {index}] {speaker_id}: {content}")
        for index, item in enumerate(visible_events, start=1):
            speaker_id = _clean_text(item.get("speaker_id")) or "unknown"
            content = _clean_text(item.get("content"))
            lines.append(f"[turn {index}] {speaker_id}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_alias_map_rewrite_tool(speaker_ids: list[str]) -> dict:
        properties = {
            str(speaker_id): {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Confident aliases for {speaker_id}.",
            }
            for speaker_id in speaker_ids
        }
        return {
            "type": "function",
            "function": {
                "name": "submit_speaker_alias_map",
                "description": "Submit the rewritten speaker alias map. Use only confident aliases from the dialogue context.",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [str(speaker_id) for speaker_id in speaker_ids],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _extract_alias_map_rewrite_tool_arguments(response: dict) -> dict | None:
        choices = response.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            if function.get("name") != "submit_speaker_alias_map":
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                return arguments
            if isinstance(arguments, str) and arguments.strip():
                parsed = json.loads(arguments)
                if isinstance(parsed, dict):
                    return parsed
        return None

    @staticmethod
    def _extract_visible_text(response: dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = _clean_text(block.get("text"))
                    if text:
                        return text
        if isinstance(content, str):
            text = _clean_text(content)
            if text:
                return text
        for key in ("text", "output_text"):
            text = _clean_text(message.get(key))
            if text:
                return text
        delta = choices[0].get("delta") or {}
        for key in ("content", "text", "output_text"):
            text = _clean_text(delta.get(key))
            if text:
                return text
        return ""

    @staticmethod
    def _reply_settings(mode: str) -> dict[str, int | float | bool]:
        return {
            "max_completion_tokens": 900,
            "temperature": 0.40,
            "top_p": 0.95,
            "stream": True,
            "attempts": 2,
        }

    @staticmethod
    def _should_retry(
        *,
        response: dict,
        text: str,
        attempt: int,
        attempts: int,
    ) -> bool:
        if attempt + 1 >= attempts:
            return False
        return not text

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except request.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            print(f"[DEBUG] HTTP {exc.code} error body: {error_body[:500]}")
            raise
        except Exception as exc:
            print(f"[DEBUG] SiliconFlow request failed: {exc} timeout={timeout}s url={url}")
            raise

    @staticmethod
    def _stream_request(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> Iterable[bytes]:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as response:
            for line in response:
                yield line

    @staticmethod
    def _parse_text_post_response(response_bytes: bytes) -> dict:
        decoded = response_bytes.decode("utf-8", errors="ignore")
        if "data:" not in decoded:
            try:
                return json.loads(decoded)
            except json.JSONDecodeError:
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": decoded,
                            },
                        }
                    ]
                }
        return MiniMaxDialogClient._consume_text_post_stream_response(decoded.splitlines(keepends=True))

    @staticmethod
    def _consume_text_post_stream_response(chunks: Iterable[bytes | str]) -> dict:
        latest_text = ""
        finish_reason: str | None = None
        raw_chunks: list[dict] = []
        for chunk in MiniMaxDialogClient._iter_text_post_stream_packets(chunks):
            raw_chunks.append(chunk)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            text = delta.get("content") or message.get("content") or ""
            if text:
                latest_text = text

        return {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": latest_text,
                    },
                }
            ],
            "raw_chunks": raw_chunks,
        }

    @staticmethod
    def _iter_text_post_stream_texts(chunks: Iterable[bytes | str]):
        latest_text = ""
        accumulated_delta = ""
        for packet in MiniMaxDialogClient._iter_text_post_stream_packets(chunks):
            choices = packet.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            delta_text = delta.get("content") or ""
            if delta_text:
                if accumulated_delta and delta_text.startswith(accumulated_delta):
                    accumulated_delta = delta_text
                else:
                    accumulated_delta += delta_text
                text = accumulated_delta
            else:
                text = message.get("content") or ""
                if text:
                    accumulated_delta = text
            if text and text != latest_text:
                latest_text = text
                yield latest_text

    @staticmethod
    def _iter_text_post_stream_cumulative_texts(chunks: Iterable[bytes | str]):
        latest_text = ""
        for packet in MiniMaxDialogClient._iter_text_post_stream_packets(chunks):
            choices = packet.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            message = choice.get("message") or {}
            full_text = message.get("content") or ""
            if full_text:
                if full_text != latest_text:
                    latest_text = full_text
                    yield latest_text
                continue

            delta = choice.get("delta") or {}
            delta_text = delta.get("content") or ""
            if not delta_text:
                continue

            if not latest_text:
                candidate = delta_text
            elif delta_text.startswith(latest_text):
                candidate = delta_text
            else:
                candidate = f"{latest_text}{delta_text}"

            if candidate != latest_text:
                latest_text = candidate
                yield latest_text

    @staticmethod
    def _iter_text_post_stream_packets(chunks: Iterable[bytes | str]):
        buffer = ""
        for chunk in chunks:
            if isinstance(chunk, bytes):
                buffer += chunk.decode("utf-8", errors="ignore")
            else:
                buffer += str(chunk)

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                payload = stripped[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue

        stripped = buffer.strip()
        if stripped.startswith("data:"):
            payload = stripped[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    return


    @staticmethod
    def _event_timestamp(item: dict) -> datetime | None:
        raw = item.get("at") or item.get("created_at") or item.get("timestamp")
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _recent_preview_context_events(events: list[dict]) -> list[dict]:
        allowed_kinds = {"voice_transcript", "assistant_spoken", "document_upload_fact"}
        filtered = [
            item
            for item in events
            if item.get("kind") in allowed_kinds and _clean_text(item.get("content"))
        ]
        by_count = filtered[-10:]
        timestamped = [
            (item, MiniMaxDialogClient._event_timestamp(item))
            for item in filtered
        ]
        known_times = [stamp for _, stamp in timestamped if stamp is not None]
        if not known_times:
            return by_count
        latest = max(known_times)
        by_time = [
            item
            for item, stamp in timestamped
            if stamp is not None and (latest - stamp).total_seconds() <= 60
        ]
        return by_time if len(by_time) < len(by_count) else by_count

    @staticmethod
    def _format_recent_context_line(item: dict, *, assistant_name: str | None = None) -> str:
        kind = item.get("kind")
        source = item.get("source", "unknown")
        content = _clean_text(item.get("content"))
        if not content:
            return ""
        if kind == "voice_transcript":
            return f"用户({source}): {content}"
        if kind == "assistant_spoken":
            return f"助手: {_strip_named_prefix(content, assistant_name)}"
        if kind == "rule_reference":
            return f"参考({source}): {content}"
        if kind == "document_upload_fact":
            return f"系统事实({source}): {content}"
        return ""

    @staticmethod
    def _build_heckle_preview_hint(
        transcript: str,
        events: list[dict],
        *,
        assistant_name: str | None = None,
    ) -> str:
        text = _clean_text(transcript)
        if not text:
            return ""

        triggers = [
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
        ]
        matched_trigger = next((token for token in triggers if token in text), "")
        if not matched_trigger:
            return ""

        aliases: list[str] = []
        for item in events:
            alias_map = item.get("speaker_alias_map")
            if not isinstance(alias_map, dict):
                continue
            for raw_aliases in alias_map.values():
                if not isinstance(raw_aliases, list):
                    continue
                aliases.extend(_clean_text(alias) for alias in raw_aliases)

        speaker_name = ""
        body = text
        speaker_match = re.match(r"^\s*([^:：]{1,20})\s*[:：]\s*(.+)$", text)
        if speaker_match:
            speaker_name = _clean_text(speaker_match.group(1))
            body = _clean_text(speaker_match.group(2))

        assistant = _clean_text(assistant_name)
        candidates: list[str] = []
        seen: set[str] = set()
        for alias in sorted(aliases, key=len, reverse=True):
            if not alias or alias in seen:
                continue
            if alias == assistant or alias.startswith("speaker_") or alias.startswith("player_"):
                continue
            seen.add(alias)
            candidates.append(alias)

        target = next(
            (alias for alias in candidates if alias != speaker_name and alias in body),
            "",
        )
        if not target:
            return ""

        speaker_line = f"说话人：{speaker_name}\n" if speaker_name else ""
        return (
            "朋友局起哄场景：\n"
            f"{speaker_line}"
            f"被吐槽玩家：{target}\n"
            f"槽点：{matched_trigger}\n"
            "不要回应说话人；只顺着槽点短促起哄被吐槽玩家，一句就撤。\n"
            "禁止词：这波操作、让人笑出声、确实有点、属实、有点迷、离谱现场。\n"
            "方向词：别送、下饭、醒醒；不要照抄示例，按当前玩家名自然说。\n"
            "优先用“咱”或“你”来起哄，回复里不要出现“我”字；不要说“我换人”“我报警”这类把助手自己塞进牌局的动作。\n"
        )

    @staticmethod
    def _build_continuation_system_prompt(mode: str) -> str:
        shared = (
            "你是一个桌游陪玩语音助手，像坐在桌边的搭子。\n"
            "你正在续接一段语音回复，助手前面已经把开场 preview 说出口了。\n"
            "只输出可播报的后续正文。\n"
            "不要输出结构化字段、Markdown 或代码块。\n"
            "不要在回复开头加助手名字或“名字：”这类说话人前缀。\n"
            "不要重复已经说过的开场，也不要重新复述用户请求。\n"
            "直接从下一句有意义的话开始。\n"
            "语气要自然，像真人说话。\n"
            "控制 preview 加 formal 的总长度：如果 preview 已经说了一两句，formal 通常只补一句短而有用的后续，不要再补两句完整回答。\n"
            "不要让 preview 加 formal 叠成四句，那会不像真人接话。\n"
            "根据最近上下文判断是闲聊、解释规则，还是引用证据。\n"
            "系统有异步查询能力；<lookup> 是唯一查询触发标记，只能放在 formal 后续正文整句最后。\n"
            "凡是用户明确说出想让你搜索、查询、联网、查找、浏览，或明确询问天气、新闻、网页信息、规则资料、FAQ、文档查询、事实核查，并且信息足够开始查询时，后续正文要自然承接并在句尾追加 <lookup>。\n"
            "不要承诺已经查到结果；<lookup> 只表示让后台去查。\n"
            "如果最近上下文已经包含“你刚刚查询得到的结果是：”，说明后台查询已经回流；这时要直接根据结果回答，不要追加 <lookup>。\n"
            "如果查询请求缺少关键信息，先问一句很短的澄清问题，不要追加 <lookup>。\n"
            "不要通过检测用户文本或“查一下”等自然语言词来决定后台动作；后台只检测你输出的句尾 <lookup>。\n"
            "优先使用简洁中文；专有名词、牌名和规则术语可以保留英文。\n"
        )
        return shared

    @staticmethod
    def _assistant_profile_block(
        *,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        name = _clean_text(assistant_name) or "宝子"
        personality = _clean_text(assistant_personality)
        if not personality:
            personality = "未设置，保持自然、亲切、像桌边搭子。"
        return f"当前助手设定：\n名字：{name}\n性格：{personality}"

    @staticmethod
    def _build_heartbeat_system_prompt() -> str:
        return (
            "你是一个桌游陪玩语音助手，像坐在桌边的搭子。\n"
            "你现在只写一句马上要说出口的桌边插话。\n"
            "只输出一句可播报的中文短句，也就是台词本身。\n"
            "不要输出 JSON、Markdown、代码块或任何结构化字段。\n"
            "不要在回复开头加助手名字或说话人前缀。\n"
            "不要提到计时器、heartbeat、系统、聆听、VAD、后台或触发规则。\n"
            "不要分析上下文，不要总结桌况，不要复述任务，不要写“用户是”“当前”“根据上下文”“需要”。\n"
            "不要说没听清、没收到、信号不好、识别错误或让玩家再说一遍；你是在顺嘴插话，不是在报错。\n"
            "句尾要有自然的句号、问号或感叹号，不要输出没说完的半句话。\n"
            "语气自然、像真人顺嘴插话；可以点名一个玩家，也可以不定主语。\n"
            "没有可靠玩家名时，可以用“宝宝们”叫全桌。\n"
        )

    @staticmethod
    def _build_heartbeat_user_prompt(
        *,
        events: list[dict],
        player_names: list[str],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        profile_block = MiniMaxDialogClient._assistant_profile_block(
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        recent_lines: list[str] = []
        for item in events[-8:]:
            kind = item.get("kind")
            source = item.get("source", "unknown")
            content = _clean_text(item.get("content"))
            if not content:
                continue
            if kind in {"voice_transcript", "speaker_alias_evidence"}:
                recent_lines.append(f"用户({source}): {content}")
            elif kind in {"assistant_spoken", "assistant_reply", "assistant_preview", "assistant_heartbeat"}:
                recent_lines.append(f"助手: {_strip_named_prefix(content, assistant_name)}")
        context_block = "\n".join(recent_lines) if recent_lines else "暂无"
        assistant = _clean_text(assistant_name)
        cleaned_names = [
            name
            for name in (_clean_text(name) for name in player_names)
            if name and name != assistant
        ]
        if cleaned_names:
            player_block = "可以用的玩家称呼：" + "、".join(cleaned_names[:6])
            fallback_line = ""
        else:
            player_block = "没有可靠玩家名。"
            fallback_line = "没有可靠玩家名时，可以用“宝宝们”自然叫全桌。\n"
        return (
            f"{profile_block}\n"
            f"{player_block}\n"
            f"{fallback_line}"
            f"刚刚桌上在聊：\n{context_block}\n"
            "如果上面有桌上内容，优先接这段内容；如果没有内容，再自然叫全桌或催一下节奏。\n"
            "直接写一句新的台词，像坐在桌边顺嘴插话。\n"
            "可以接桌上的话题、催一下节奏、轻轻吐槽一句，别像主持人报幕。\n"
            "只输出这句台词，不要解释，不要查询，不要说自己为什么开口，不要复述这些要求。\n"
        )

    @staticmethod
    def _build_preview_system_prompt(mode: str) -> str:
        shared = (
            "你正在为语音助手生成第一句 preview 接话。\n"
            "只输出可播报的 preview 短句。\n"
            "不要输出 JSON、结构化字段、Markdown 或代码块。\n"
            "不要在回复开头加助手名字或“名字：”这类说话人前缀。\n"
            "只写一句很短的口语接话。\n"
            "这句话要自然，并且能立刻说出口。\n"
            "不要使用固定口头禅，也不要套用示例式情绪反应；要根据用户原话自然回应。\n"
            "如果用户是在朋友局里点名吐槽另一位玩家，用桌边朋友互损的语气短促起哄，一句就撤。\n"
            "这类起哄要像真人顺嘴插话，别像总结或评论；禁止使用“这波操作”“让人笑出声”“确实有点”这类模型腔。\n"
            "不要替被吐槽的人解围，不要说“别太狠”“别急着下结论”这种和事佬话。\n"
            "可以稍微损一点，顺着原话里的槽点说，但不要长篇辱骂、不要升级攻击、不要脱离当前玩家名乱编梗。\n"
            "朋友局起哄的语感参考：老黄，咱别送了行吗。/ 阿珍这把有点下饭了。/ 大雄你醒醒啊。\n"
            "如果用户要求查询、联网、查新闻、查天气或查资料，只需要自然接一句，不能编造结果，也不要输出任何控制标记。\n"
            "根据用户请求和最近上下文选择合适语气，不要依赖外部模式标签。\n"
        )
        return shared

    @staticmethod
    def _build_continuation_user_prompt(
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        recent_lines: list[str] = []
        for item in events[-8:]:
            line = MiniMaxDialogClient._format_recent_context_line(item, assistant_name=assistant_name)
            if line:
                recent_lines.append(line)

        context_block = "\n".join(recent_lines) if recent_lines else "暂无"
        spoken = _clean_text(already_spoken_text)
        profile_block = MiniMaxDialogClient._assistant_profile_block(
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        return (
            f"{profile_block}\n"
            f"最新用户请求：{transcript}\n"
            f"最近上下文：\n{context_block}\n"
            f"已经说出口的开场：{spoken}\n"
            "只输出可播报的后续正文。\n"
            "如果最新用户请求明确要求查询、联网、查天气、查新闻、查网页、查规则或查资料，并且信息足够开始查，后续正文末尾必须追加 <lookup>；即使开场 preview 已经答应去查，也仍然必须追加 <lookup>，后台才会启动查询。\n"
            "需要查询时不要说自己没有联网能力，也不要说已经有结果。\n"
        )

    @staticmethod
    def _build_preview_user_prompt(
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        recent_lines: list[str] = []
        for item in MiniMaxDialogClient._recent_preview_context_events(events):
            line = MiniMaxDialogClient._format_recent_context_line(item, assistant_name=assistant_name)
            if line:
                recent_lines.append(line)

        context_block = "\n".join(recent_lines) if recent_lines else "暂无"
        profile_block = MiniMaxDialogClient._assistant_profile_block(
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        heckle_hint = MiniMaxDialogClient._build_heckle_preview_hint(
            transcript,
            events,
            assistant_name=assistant_name,
        )
        return (
            f"{profile_block}\n"
            f"最新用户请求：{transcript}\n"
            f"{heckle_hint}"
            f"最近上下文：\n{context_block}\n"
            "只给第一句很短的口语接话；不要输出查询控制标记，也不要编造实时信息或查询结果。\n"
        )

    @staticmethod
    def _build_plain_reply_system_prompt(mode: str) -> str:
        shared = (
            "你是一个桌游陪玩语音助手，像坐在桌边的搭子。\n"
            "只输出可播报的回复文本。\n"
            "不要输出 JSON、结构化字段、Markdown 或代码块。\n"
            "不要在回复开头加助手名字或“名字：”这类说话人前缀。\n"
            "只输出可播报纯文本，不要输出 JSON、标签、隐藏指令或不可播报控制字段。\n"
            "默认用中文回答，专有名词、牌名、规则术语可以保留英文。\n"
            "语气要像真人接话，别机械，别重复套话。\n"
            "平时回复以一两句短句闲聊为主。\n"
            "只有在讨论规则、玩法、卡牌效果、网页资料、天气或其他确实需要外部资料/较长解释的问题时，才展开更长。\n"
            "系统有异步查询能力；<lookup> 是唯一查询触发标记，只能放在 formal 回复整句最后。\n"
            "凡是用户明确说出想让你搜索、查询、联网、查找、浏览，或明确询问天气、新闻、网页信息、规则资料、FAQ、文档查询、事实核查，并且信息足够开始查询时，先自然承诺查询，并且必须在句尾追加 <lookup>。\n"
            "不要承诺已经查到结果；<lookup> 只表示让后台去查。\n"
            "如果最近上下文已经包含“你刚刚查询得到的结果是：”，说明后台查询已经回流；这时要直接根据结果回答，不要追加 <lookup>。\n"
            "如果查询请求缺少关键信息，先问一句很短的澄清问题，不要追加 <lookup>。\n"
            "不要通过检测用户文本或“查一下”等自然语言词来决定后台动作；后台只检测你输出的句尾 <lookup>。\n"
            "普通闲聊、笑话、情绪反应，或者可以直接根据上下文回答的问题，不要追加 <lookup>。\n"
            "如果信息不够，先问一个简短澄清问题。\n"
            "不要依赖外部模式标签；只根据用户最新请求、上下文和证据决定回答方式。\n"
            "如果是规则、玩法、卡牌效果或争议判定，直接给清晰可靠的中文解释，并优先使用上下文里的 rule_reference 或工具结果。\n"
            "如果是闲聊、笑话或普通陪聊，短而自然即可。\n"
            "如果是朋友局里点名吐槽另一位玩家，可以跟着起哄调侃，但保持像桌边玩笑，不要长篇辱骂或升级攻击。\n"
        )
        return shared

    @staticmethod
    def _build_plain_reply_user_prompt(
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        attempt: int = 0,
        already_spoken_text: str | None = None,
        continue_only: bool = False,
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        recent_lines: list[str] = []
        for item in events[-8:]:
            line = MiniMaxDialogClient._format_recent_context_line(item, assistant_name=assistant_name)
            if line:
                recent_lines.append(line)

        context_block = "\n".join(recent_lines) if recent_lines else "暂无"
        directives: list[str] = []
        lowered = transcript.lower()
        if any(token in lowered for token in ("joke", "funny", "laugh")) or any(
            token in transcript for token in ("笑话", "逗我")
        ):
            directives.append("这是讲笑话请求：正文控制在 80 字内，只讲一个短笑话，不要解释。")
        elif any(token in lowered for token in ("introduce", "intro", "about")) or any(
            token in transcript for token in ("介绍", "讲讲")
        ):
            directives.append("这是游戏介绍请求：正文控制在 2 到 4 句、140 字内，先讲玩法感觉，再讲核心机制。")
        if attempt > 0:
            directives.append("上一版回复不可用，请直接给更短、更完整的 concise 版本。")
        if continue_only:
            directives.append("这次只继续输出后续还没说出口的正文，不要重复前面已经说过的话。")
        if already_spoken_text:
            directives.append(f"前面已经说过：{_clean_text(already_spoken_text)}")

        directives_block = "\n".join(f"- {item}" for item in directives) if directives else "- 正常自然回答。"
        profile_block = MiniMaxDialogClient._assistant_profile_block(
            assistant_name=assistant_name,
            assistant_personality=assistant_personality,
        )
        return (
            f"{profile_block}\n"
            f"最新用户请求：{transcript}\n"
            f"最近上下文：\n{context_block}\n"
            f"附加要求：\n{directives_block}\n"
            "只输出可播报的回复文本。\n"
            "不要输出 JSON。\n"
            "如果最新用户请求明确要求查询、联网、查天气、查新闻、查网页、查规则或查资料，并且信息足够开始查，回复末尾必须追加 <lookup>，后台才会启动查询。\n"
            "需要查询时不要说自己没有联网能力，也不要说已经有结果。\n"
        )

    @staticmethod
    def _build_memory_compaction_system_prompt() -> str:
        return (
            "You compress spoken dialogue history into one narrative memory block.\n"
            "Preserve who said what when it matters, stance changes, unfinished but still relevant threads,"
            " and any assistant unspoken residue that still matters.\n"
            "Drop low-value chatter.\n"
            "Return plain Chinese prose only, no markdown, no bullet list.\n"
        )

    @staticmethod
    def _build_alias_map_rewrite_system_prompt() -> str:
        return (
            "你负责重写桌面里的说话人称呼表。\n"
            "必须调用 submit_speaker_alias_map 工具提交结果，不要用普通文本回答。\n"
            "工具参数必须是 JSON 对象；不要输出 Markdown、代码块、解释、推理过程或额外字段。\n"
            "必须使用输入里给出的 speaker bucket 键；每个 bucket 都必须出现，值必须是字符串数组。\n"
            "数组里只能放别人会用来称呼这个 bucket 的名字、昵称或称谓；不要输出台词、身份描述、性格描述、关系描述。\n"
            "alias 必须逐字出现在 Dialogue context 里，不能编造，也不能把一整句话当 alias。\n"
            "只在有把握时贴名；不确定就输出空数组。\n"
            "宁可漏贴，也不要错贴。\n"
            "名字出现在某个 speaker 的发言里，通常不是这个 speaker 自己的名字，而是TA正在称呼或提到的人。\n"
            "形如 speaker_X：哎，NAME。 时，NAME 不应该贴给 speaker_X；如果紧接着 speaker_Y 接话，NAME 通常应贴给 speaker_Y。\n"
            "不要把“当前说话人提到的人”自动贴给当前说话人。\n"
            "不要把“当前说话人正在呼唤的人”贴给当前说话人。\n"
            "如果 A 呼唤某个名字，紧接着 B 接话，通常说明 B 可能就是这个名字。\n"
            "如果文本是“X说……”，通常只是提到 X，不代表当前说话人是 X。\n"
            "如果文本是“我X……”，例如“我三叔……”，X 是当前说话人提到的关系人，不代表当前说话人是 X。\n"
            "旧称呼表可能是错的，只用于审计，不是证据；不能因为旧表里有某个 alias 就保留它。\n"
            "默认保底称呼“宝宝”会由系统另行补回；除非 Dialogue context 明确证明某个 bucket 被别人称为“宝宝”，否则不要输出“宝宝”。\n"
            "跑团里主持人或玩家可能会代演 NPC；“我叫 NAME”“你们叫我 NAME”如果出现在旁白/NPC 口吻里，通常只是角色自我介绍，不要直接贴成真实 speaker alias。\n"
            "跑团行动归属线索可以更积极使用：例如“下一个行动的是 NAME”“不愧是你 NAME”“轮到 NAME”，如果紧接着某个 speaker 用第一人称执行行动、投骰或回应，这个 speaker 可以贴 NAME。\n"
            "Few-shot 示例 6：\n"
            "Dialogue context:\n"
            "speaker_0：下一个行动的是小杨。\n"
            "speaker_5：那我就试一下。\n"
            "旧称呼表:\n"
            "speaker_0: [\"宝宝\"]\n"
            "speaker_5: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[],\"speaker_5\":[\"小杨\"]}\n"
            "含义：speaker_0 在宣布行动归属，紧接着 speaker_5 用第一人称执行行动，所以 speaker_5 可以贴“小杨”。\n"
            "Few-shot 示例 7：\n"
            "Dialogue context:\n"
            "speaker_2：哈喽哈喽，我是来带你们去旅馆的人，你们叫我有希子就好了。\n"
            "speaker_0：然后有希子就带你们走出了码头。\n"
            "旧称呼表:\n"
            "speaker_2: [\"宝宝\",\"有希子\"]\n"
            "speaker_0: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_2\":[],\"speaker_0\":[]}\n"
            "含义：这是 NPC/剧情角色自我介绍和旁白提及，不是真实 speaker 名；旧表里的有希子也要删掉。\n"
            "Few-shot 示例 8：\n"
            "Dialogue context:\n"
            "speaker_0：不愧是你空条吉子。\n"
            "speaker_9：我拿拐杖去捅一下。\n"
            "旧称呼表:\n"
            "speaker_0: [\"宝宝\"]\n"
            "speaker_9: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[],\"speaker_9\":[\"空条吉子\"]}\n"
            "含义：speaker_0 在评价/点名空条吉子，紧接着 speaker_9 用第一人称行动，所以 speaker_9 可以贴“空条吉子”。\n"
            "Few-shot 示例 9：\n"
            "Dialogue context:\n"
            "speaker_0：不愧是你空条吉子，刚才摸到了机关。\n"
            "speaker_7：Oh my god.\n"
            "speaker_9：我拿我的拐杖去捅一下。\n"
            "旧称呼表:\n"
            "speaker_7: [\"宝宝\"]\n"
            "speaker_9: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_7\":[],\"speaker_9\":[\"空条吉子\"]}\n"
            "含义：短惊叹不算行动归属承接；后面真正用第一人称执行行动的 speaker_9 才更可能是空条吉子。\n"
            "Few-shot 示例 1：\n"
            "Dialogue context:\n"
            "speaker_2：阿珍说想看星星哦，你不是要上太空吗？\n"
            "旧称呼表:\n"
            "speaker_2: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_2\":[]}\n"
            "含义：阿珍是被 speaker_2 提到的人，不是 speaker_2。\n"
            "Few-shot 示例 2：\n"
            "Dialogue context:\n"
            "speaker_0：哎，老黄。\n"
            "speaker_2：怎么了？\n"
            "旧称呼表:\n"
            "speaker_0: [\"宝宝\"]\n"
            "speaker_2: [\"宝宝\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[],\"speaker_2\":[\"老黄\"]}\n"
            "含义：speaker_0 在和老黄搭话，接话的人就是老黄。\n"
            "Few-shot 示例 3：\n"
            "Dialogue context:\n"
            "speaker_0：上次孙哥上太空，就是他叫我去给我摘星星的。\n"
            "speaker_2：今晚要陪我三叔去结扎。\n"
            "旧称呼表:\n"
            "speaker_0: [\"孙哥\"]\n"
            "speaker_2: [\"三叔\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[],\"speaker_2\":[]}\n"
            "含义：孙哥和三叔都只是被说话人提到的人，不是当前说话人。\n"
            "Few-shot 示例 4：\n"
            "Dialogue context:\n"
            "speaker_2：孙哥。\n"
            "speaker_0：哎，老黄。\n"
            "旧称呼表:\n"
            "speaker_0: [\"老黄\"]\n"
            "speaker_2: [\"孙哥\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[\"孙哥\"],\"speaker_2\":[\"老黄\"]}\n"
            "含义：speaker_2 在叫孙哥，接话的 speaker_0 可能是孙哥；speaker_0 又在叫老黄，对方 speaker_2 可能是老黄。\n"
            "错误输出示例：\n"
            "{\"speaker_0\":[\"老黄\"],\"speaker_2\":[\"孙哥\"]}\n"
            "这是错的，因为它把名字贴给了说出这个名字的人。\n"
            "Few-shot 示例 5：\n"
            "Dialogue context:\n"
            "speaker_0：哎，老黄。\n"
            "speaker_2：阿珍说想看星星哦，你不是要上太空吗？\n"
            "旧称呼表:\n"
            "speaker_0: [\"老黄\"]\n"
            "speaker_2: [\"阿珍\"]\n"
            "正确输出：\n"
            "{\"speaker_0\":[],\"speaker_2\":[\"老黄\"]}\n"
            "含义：老黄是 speaker_0 正在呼唤的人，不是 speaker_0；阿珍只是 speaker_2 提到的人，不是 speaker_2。\n"
        )

    @staticmethod
    def _build_alias_map_rewrite_user_prompt(
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> str:
        dialogue_block = MiniMaxDialogClient._format_alias_evidence_windows(dialogue_events)
        alias_lines = []
        for speaker_id, aliases in current_alias_map.items():
            alias_lines.append(f"{speaker_id}: {json.dumps([str(alias).strip() for alias in aliases if str(alias).strip()], ensure_ascii=False)}")
        alias_block = "\n".join(alias_lines) if alias_lines else "{}"
        return (
            "Dialogue context:\n"
            f"{dialogue_block}\n"
            "旧称呼表（可能是错的，只用于审计，不是证据）:\n"
            f"{alias_block}\n"
            "请重写完整称呼表，只输出 JSON。\n"
            "必须通过 submit_speaker_alias_map 工具提交，不要普通文本回答。\n"
            "不要包含输入 keys 以外的任何字段。\n"
            "不要把 Dialogue context 按说话人分组整理；输出的不是台词列表，而是每个 speaker bucket 的称呼数组。\n"
        )

    @staticmethod
    def _parse_alias_map_rewrite_response(text: str, *, expected_speaker_ids: list[str]) -> dict[str, list[str]]:
        candidate = text.strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            parsed = json.loads(candidate[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("alias rewrite response must be a JSON object")
        expected = [str(speaker_id) for speaker_id in expected_speaker_ids]
        normalized: dict[str, list[str]] = {}
        for speaker_id in expected:
            aliases = parsed.get(speaker_id, [])
            if not isinstance(aliases, list):
                raise ValueError(f"alias rewrite bucket must be an array: {speaker_id}")
            normalized[speaker_id] = [
                alias
                for alias in (_clean_text(item) for item in aliases)
                if alias
            ]
        return normalized

    @staticmethod
    def _build_memory_compaction_user_prompt(*, previous_summary: str, events: list[dict]) -> str:
        lines: list[str] = []
        if _clean_text(previous_summary):
            lines.append(f"previous summary: {_clean_text(previous_summary)}")
        for item in events:
            kind = item.get("kind", "event")
            source = item.get("source", "unknown")
            content = _clean_text(item.get("content"))
            if not content:
                continue
            lines.append(f"{kind}({source}): {content}")
        context = "\n".join(lines) if lines else "no dialogue"
        return (
            "Compact the following active dialogue context into one narrative memory block.\n"
            "Keep what matters for future continuation and remove low-value chatter.\n"
            f"{context}"
        )

    @staticmethod
    def _extract_text(response: dict) -> str:
        """
        Extract readable text from MiniMax API response.

        Handles two formats:
        - Anthropic format (type="message"): top-level content list with blocks
        - Legacy MiniMax format: choices[0].message.content
        """
        # Detect Anthropic format: response has "type": "message" or no choices
        if response.get("type") == "message" or "choices" not in response:
            content_blocks = response.get("content", [])
            if not isinstance(content_blocks, list):
                content_blocks = [content_blocks]
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text = _clean_text(block.get("text"))
                    if text:
                        return text
                # tool_use block: return the tool name for parsing
                elif btype == "tool_use":
                    text = _clean_text(block.get("name") or "")
                    if text:
                        return text
                # skip thinking blocks
            return ""

        # Legacy MiniMax format: choices[0].message.content
        choices = response.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = _clean_text(block.get("text"))
                        if text:
                            return text
                    elif block.get("type") == "tool_use":
                        text = _clean_text(block.get("name") or "")
                        if text:
                            return text
        for key in ("content", "reasoning_content", "text", "output_text"):
            val = message.get(key)
            if isinstance(val, str):
                text = _clean_text(val)
                if text:
                    return text
        # Try streaming format: choices[0].delta.content
        delta = choices[0].get("delta") or {}
        delta_content = delta.get("content")
        if isinstance(delta_content, list):
            for block in delta_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = _clean_text(block.get("text"))
                    if text:
                        return text
        for key in ("content", "reasoning_content", "text", "output_text"):
            text = _clean_text(delta.get(key))
            if text:
                return text
        return ""

    @staticmethod
    def _fallback_reply(*, mode: str, transcript: str, events: list[dict]) -> str:
        lowered = transcript.lower()
        references = [
            _clean_text(item.get("content"))
            for item in events
            if item.get("kind") == "rule_reference" and item.get("content")
        ]
        if references:
            return "我先按手头这条规则给你顺一下，细节我尽量讲清楚。"
        if "斗地主" in transcript:
            return "我先按常见斗地主规则给你顺一遍，你要是这桌有特别约定再告诉我。"
        if any(token in lowered for token in ("joke", "funny", "laugh")) or any(
            token in transcript for token in ("笑话", "逗我")
        ):
            return "来，我给你讲一个。"
        if any(token in transcript for token in ("晚上好", "你好", "在吗")):
            return "我在，晚上好呀。"
        if transcript.strip():
            return "我听见了，你继续。"
        return "我在，接着说。"


class SiliconFlowPreviewClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "Qwen/Qwen3.5-4B",
        base_url: str = SILICONFLOW_CHAT_COMPLETIONS_URL,
        timeout_seconds: float = 8.0,
        max_tokens: int = 50,
        temperature: float = 0.45,
        top_p: float = 0.8,
        top_k: int = 40,
        min_p: float = 0.05,
        frequency_penalty: float = 0.2,
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
    ) -> None:
        self.api_key = self._normalize_api_key(api_key)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.min_p = min_p
        self.frequency_penalty = frequency_penalty
        self._request_sender = request_sender or MiniMaxDialogClient._send_request

    def generate_preview_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": MiniMaxDialogClient._build_preview_system_prompt(mode),
                },
                {
                    "role": "user",
                    "content": MiniMaxDialogClient._build_preview_user_prompt(
                        mode=mode,
                        transcript=transcript,
                        events=events,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    ),
                },
            ],
            "stream": False,
            "enable_thinking": False,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "frequency_penalty": self.frequency_penalty,
            "n": 1,
        }
        # min_p is not supported by all models (e.g. inclusionAI/Ling-mini-2.0); skip if zero
        if self.min_p > 0:
            payload["min_p"] = self.min_p
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t0 = time.monotonic()
        response_bytes = self._request_sender(
            self.base_url,
            body,
            headers,
            self.timeout_seconds,
        )
        elapsed_s = time.monotonic() - t0
        logger.info("siliconflow_preview elapsed=%.3fs transcript=%r", elapsed_s, transcript[:50])
        response = MiniMaxDialogClient._parse_text_post_response(response_bytes)
        text = _clean_text(MiniMaxDialogClient._extract_text(response))
        if not text:
            raise NoUsableReplyError("no usable preview text from siliconflow")
        self._write_trace(
            payload=payload,
            response_text=text,
            raw_response_text=text,
            elapsed_s=elapsed_s,
        )
        return text

    def _write_trace(
        self,
        *,
        payload: dict,
        response_text: str,
        raw_response_text: str,
        elapsed_s: float,
    ) -> None:
        trace_path = os.getenv("GAMEVOICE_SILICONFLOW_PREVIEW_TRACE_PATH")
        if not trace_path:
            return
        _, marker_detected = split_preview_lookup_marker(response_text)
        record = {
            "at": datetime.now().isoformat(timespec="milliseconds"),
            "model": self.model,
            "base_url": self.base_url,
            "elapsed_ms": round(elapsed_s * 1000, 2),
            "messages": payload.get("messages", []),
            "request_parameters": {
                key: payload.get(key)
                for key in (
                    "stream",
                    "enable_thinking",
                    "max_tokens",
                    "temperature",
                    "top_p",
                    "top_k",
                    "frequency_penalty",
                    "n",
                    "min_p",
                )
                if key in payload
            },
            "raw_response_text": raw_response_text,
            "response_text": response_text,
            "marker_detected": marker_detected,
        }
        path = Path(trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _normalize_api_key(api_key: str) -> str:
        token = str(api_key or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        try:
            token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise RuntimeError(
                "SILICONFLOW_API_KEY must be the raw API token, using ASCII characters only. "
                "Do not paste the example text such as '你的key' or any surrounding Chinese label."
            ) from exc
        if not token:
            raise RuntimeError("SILICONFLOW_API_KEY is empty")
        return token


class SiliconFlowAliasRewriteClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-ai/DeepSeek-V4-Flash",
        base_url: str = SILICONFLOW_CHAT_COMPLETIONS_URL,
        timeout_seconds: float = 30.0,
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
    ) -> None:
        self.api_key = SiliconFlowPreviewClient._normalize_api_key(api_key)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or MiniMaxDialogClient._send_request

    def rewrite_speaker_alias_map(
        self,
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": MiniMaxDialogClient._build_alias_map_rewrite_system_prompt(),
                },
                {
                    "role": "user",
                    "content": MiniMaxDialogClient._build_alias_map_rewrite_user_prompt(
                        dialogue_events=dialogue_events,
                        current_alias_map=current_alias_map,
                    ),
                },
            ],
            "tools": [
                MiniMaxDialogClient._build_alias_map_rewrite_tool(list(current_alias_map.keys())),
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_speaker_alias_map"},
            },
            "stream": False,
            "enable_thinking": False,
            "max_tokens": 4096,
            "temperature": 0.1,
            "top_p": 0.9,
            "n": 1,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t0 = time.monotonic()
        response_bytes = self._request_sender(
            self.base_url,
            body,
            headers,
            self.timeout_seconds,
        )
        elapsed_s = time.monotonic() - t0
        logger.info("siliconflow_alias_rewrite elapsed=%.3fs events=%d", elapsed_s, len(dialogue_events))
        response = MiniMaxDialogClient._parse_text_post_response(response_bytes)
        tool_arguments = MiniMaxDialogClient._extract_alias_map_rewrite_tool_arguments(response)
        if tool_arguments is not None:
            text = json.dumps(tool_arguments, ensure_ascii=False)
        else:
            text = _clean_text(MiniMaxDialogClient._extract_visible_text(response))
        if not text:
            raise NoUsableReplyError("no usable alias rewrite from siliconflow")
        parsed = MiniMaxDialogClient._parse_alias_map_rewrite_response(
            text,
            expected_speaker_ids=list(current_alias_map.keys()),
        )
        return MiniMaxDialogClient._filter_alias_map_by_evidence(
            parsed,
            dialogue_events=dialogue_events,
        )


class PreviewRoutingDialogClient:
    def __init__(self, *, reply_client, preview_client, alias_rewrite_client=None) -> None:
        self.reply_client = reply_client
        self.preview_client = preview_client
        self.alias_rewrite_client = alias_rewrite_client

    def generate_lead_preview(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict | None:
        try:
            text = _clean_text(self.preview_client.generate_preview_text(
                mode=mode,
                transcript=transcript,
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            ))
        except TypeError as exc:
            if "assistant_name" not in str(exc) and "assistant_personality" not in str(exc):
                raise
            text = _clean_text(self.preview_client.generate_preview_text(
                mode=mode,
                transcript=transcript,
                events=events,
            ))
        if not text:
            return None
        return {
            "source": "siliconflow",
            "lead": text,
            "tail": "",
            "content": text,
        }

    def rewrite_speaker_alias_map(
        self,
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        if self.alias_rewrite_client is not None:
            return self.alias_rewrite_client.rewrite_speaker_alias_map(
                dialogue_events=dialogue_events,
                current_alias_map=current_alias_map,
            )
        return self.reply_client.rewrite_speaker_alias_map(
            dialogue_events=dialogue_events,
            current_alias_map=current_alias_map,
        )

    def __getattr__(self, name: str):
        if name == "generate_context_reply":
            raise AttributeError(name)
        return getattr(self.reply_client, name)


def build_dialog_client(settings: Settings):
    if settings.minimax_api_key:
        reply_client = MiniMaxDialogClient(
            api_key=settings.minimax_api_key,
            model=settings.minimax_text_model,
            base_url=settings.minimax_text_base_url,
            timeout_seconds=settings.minimax_text_timeout_seconds,
        )
        if settings.siliconflow_api_key:
            return PreviewRoutingDialogClient(
                reply_client=reply_client,
                preview_client=SiliconFlowPreviewClient(
                    api_key=settings.siliconflow_api_key,
                    model=settings.siliconflow_preview_model,
                    base_url=settings.siliconflow_preview_base_url,
                    timeout_seconds=settings.siliconflow_preview_timeout_seconds,
                    max_tokens=settings.siliconflow_preview_max_tokens,
                    temperature=settings.siliconflow_preview_temperature,
                    top_p=settings.siliconflow_preview_top_p,
                    top_k=settings.siliconflow_preview_top_k,
                    min_p=settings.siliconflow_preview_min_p,
                    frequency_penalty=settings.siliconflow_preview_frequency_penalty,
                ),
                alias_rewrite_client=SiliconFlowAliasRewriteClient(
                    api_key=settings.siliconflow_api_key,
                    model=settings.siliconflow_alias_rewrite_model,
                    base_url=settings.siliconflow_preview_base_url,
                    timeout_seconds=settings.siliconflow_alias_rewrite_timeout_seconds,
                ),
            )
        return reply_client
    return PlaceholderDialogClient()
