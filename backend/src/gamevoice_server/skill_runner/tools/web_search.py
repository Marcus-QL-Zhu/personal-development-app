from __future__ import annotations

import json
import re
from datetime import date, timedelta
from html import unescape
from urllib.request import Request, urlopen

from ..tool_registry import Tool, ToolResult

_METASO_API_URL = "https://metaso.cn/api/v1/chat/completions"
_HTTP_TIMEOUT_SECONDS = 20
_SUBPROCESS_TIMEOUT_SECONDS = 30.0
_MAX_ATTEMPTS = 3
_MAX_HIGHLIGHTS = 4


def _today_iso() -> str:
    return date.today().isoformat()


def _tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _normalize_query(query: str) -> str:
    cleaned = query.strip()
    cleaned = re.sub(r"^[a-zA-Z_0-9]+[：:]\s*", "", cleaned)
    cleaned = re.sub(r"^(玩家[A-Z]|speaker_\d+)[：:]\s*", "", cleaned, flags=re.IGNORECASE)
    for token in ("宝子", "保子", "帮我", "联网", "查询一下", "查一下", "查一查", "查查", "给我"):
        cleaned = cleaned.replace(token, " ")
    cleaned = cleaned.replace("关于", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" 。？！,.，")
    if "特朗普" in cleaned and "新闻" in cleaned:
        return "特朗普 最新新闻"
    if "trump" in cleaned.lower() and "news" in cleaned.lower():
        return "Donald Trump latest news"
    lower = cleaned.lower()
    if ("天气" in cleaned or "weather" in lower) and ("明天" in cleaned or "tomorrow" in lower):
        return cleaned
    if ("天气" in cleaned or "weather" in lower) and ("今天" in cleaned or "today" in lower or "现在" in cleaned):
        return cleaned
    if ("最新" in cleaned or "最近" in cleaned or "latest" in lower or "recent" in lower) and not re.search(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", cleaned):
        return f"{cleaned} {_today_iso()}"
    return cleaned or query.strip()


def _repair_mojibake(text: str) -> str:
    if not any(marker in text for marker in ("Ã", "æ", "ç", "è", "é", "å")):
        return text
    best = text
    for encoding in ("latin1", "cp1252"):
        try:
            repaired = text.encode(encoding, errors="ignore").decode("utf-8", errors="ignore")
        except UnicodeError:
            continue
        if _cjk_count(repaired) > _cjk_count(best):
            best = repaired
    return best


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _strip_inline_citations(text: str) -> str:
    cleaned = re.sub(r"\[\[\d+\]\]", "", str(text or ""))
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _strip_markup(text: str) -> str:
    cleaned = re.sub(r"</?mark>", "", str(text or ""), flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return _strip_inline_citations(unescape(cleaned))


def _parse_metaso_chat_sse(stream_text: str) -> dict:
    answer_parts: list[str] = []
    citations: list[dict] = []
    highlights: list[str] = []
    finish_reason = None

    for raw_line in str(stream_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        for choice in item.get("choices") or []:
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                answer_parts.append(str(content))
            for citation in delta.get("citations") or []:
                if isinstance(citation, dict):
                    citations.append(dict(citation))
            for highlight in delta.get("highlights") or []:
                text = _strip_markup(str(highlight))
                if text:
                    highlights.append(text)

    return {
        "answer": _strip_inline_citations("".join(answer_parts)),
        "citations": citations,
        "highlights": highlights,
        "finish_reason": finish_reason,
    }


def _format_metaso_chat_answer(
    parsed: dict,
    *,
    max_citations: int = 5,
    max_highlights: int = _MAX_HIGHLIGHTS,
) -> str:
    answer = _strip_inline_citations(str(parsed.get("answer") or ""))
    if not answer:
        return ""

    parts = [f"Answer:\n{answer}"]
    citations = [
        item for item in parsed.get("citations") or []
        if isinstance(item, dict) and (item.get("title") or item.get("link"))
    ][:max(0, max_citations)]
    if citations:
        lines = []
        for index, item in enumerate(citations, start=1):
            title = _strip_markup(str(item.get("title") or "Untitled"))
            date_text = _strip_markup(str(item.get("date") or "")).strip()
            link = str(item.get("link") or "").strip()
            date_suffix = f" ({date_text})" if date_text else ""
            link_suffix = f" {link}" if link else ""
            lines.append(f"{index}. {title}{date_suffix}{link_suffix}")
        parts.append("Citations:\n" + "\n".join(lines))

    highlights = []
    seen_highlights: set[str] = set()
    for raw in parsed.get("highlights") or []:
        text = _strip_markup(str(raw))
        if not text or text in seen_highlights:
            continue
        seen_highlights.add(text)
        highlights.append(text)
        if len(highlights) >= max(0, max_highlights):
            break
    if highlights:
        parts.append(
            "Highlights:\n"
            + "\n".join(f"{index}. {text}" for index, text in enumerate(highlights, start=1))
        )

    return "\n\n".join(parts)


def _request_metaso_chat_completion(*, api_key: str, query: str) -> dict:
    payload = json.dumps(
        {
            "model": "fast",
            "stream": True,
            "messages": [{"role": "user", "content": query}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        _METASO_API_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    chunks: list[str] = []
    with urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
        for raw in resp:
            chunks.append(raw.decode("utf-8", errors="replace"))
    return _parse_metaso_chat_sse("".join(chunks))


def _execute(arguments: dict) -> ToolResult:
    # Import settings using absolute path to avoid relative import issues
    import gamevoice_server.config as config_module
    settings = config_module.settings

    query = _normalize_query(arguments.get("query", ""))
    if not query:
        return ToolResult.failure("query cannot be empty")

    api_key = settings.metaso_api_key
    if not api_key:
        return ToolResult.failure(
            "web search is not configured: METASO_API_KEY environment variable is not set.",
            content="",
        )

    max_results = int(arguments.get("max_results", 5) or 5)
    last_error = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            parsed = _request_metaso_chat_completion(api_key=api_key, query=query)
            content = _format_metaso_chat_answer(parsed, max_citations=max_results)
            if content:
                return ToolResult.success(_repair_mojibake(content))
            last_error = "no answer returned"
        except Exception as exc:
            last_error = str(exc)
        error_text = str(last_error or "").lower()
        if "timed out" not in error_text and "timeout" not in error_text:
            break

    return ToolResult.failure(f"web search failed: {last_error}", content="")


TOOL_SCHEMA = {
    "name": "web_search",
    "description": (
        "Use this for web-connected lookup via 秘塔AI answer mode. "
        "Returns an answer with citations and evidence highlights. "
        "Use this for current web information, weather, news, fact checks, "
        "general webpages, and non-game questions. "
        "Prefer Arkham-specific tools first for Arkham rules, cards, FAQ, and "
        "card-interaction questions; use web_search only when local Arkham tools "
        "do not cover the needed information."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Web search query. Be specific and preserve the user's topic. "
                    "For relative dates, include the resolved date. "
                    "Examples: 'Shanghai weather tomorrow 2026-05-25', "
                    "'Donald Trump latest news', "
                    "'Arkham Horror LCG Dark Papermind FAQ ruling'"
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return. Default: 5.",
            },
        },
        "required": ["query"],
    },
}


def build_web_search_tool() -> Tool:
    return Tool(
        name="web_search",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
