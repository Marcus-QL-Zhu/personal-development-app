from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..dialog_client import MiniMaxDialogClient, _clean_text
from .tool_registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


def print(*args: Any, **kwargs: Any) -> None:  # noqa: A001
    """Module-local debug sink that cannot break SkillAgent on console encoding."""
    try:
        text = " ".join(_ascii_log_text(arg, 1000) for arg in args)
        logger.debug("SkillAgent debug: %s", text)
    except Exception:
        pass

#: Maximum number of tool-call iterations in a single agent run
MAX_ITERATIONS = 10

#: Overall timeout for the entire agent run (seconds)
AGENT_TIMEOUT_SECONDS = 300.0  # 5 minutes

SPEAKABLE_TEXT_GUARDRAIL = (
    "Except for native tool calls, output only ordinary speakable natural-language text. "
    "Do not output emoji, decorative Markdown, hidden tags, JSON control text, invisible "
    "control characters, or non-spoken directives."
)


@dataclass
class AgentAnswer:
    """Final output of the SkillAgent."""
    content: str
    iterations: int
    timed_out: bool
    trace: list[dict[str, Any]] = field(default_factory=list)


class RecoverableAgentError(RuntimeError):
    """A per-iteration failure that can be retried from the last checkpoint."""


def _trace_text(value: Any, limit: int = 500) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _ascii_log_text(value: Any, limit: int = 1000) -> str:
    return _trace_text(value, limit).encode("ascii", "backslashreplace").decode("ascii")


def _current_date_context() -> str:
    today = date.today().isoformat()
    return f"当前日期：{today}。用户说今天、明天、最近、最新时，必须以这个日期为基准，不要引用过期日期。"


def _trace_event(trace: list[dict[str, Any]], stage: str, **data: Any) -> None:
    try:
        event: dict[str, Any] = {"stage": stage}
        for key, value in data.items():
            if isinstance(value, str):
                event[key] = _trace_text(value)
            elif isinstance(value, (int, float, bool)) or value is None:
                event[key] = value
            else:
                event[key] = _trace_text(json.dumps(value, ensure_ascii=False, default=str), 1000)
        trace.append(event)
    except Exception:
        logger.debug("SkillAgent trace append failed", exc_info=True)


def _trace_debug(trace: list[dict[str, Any]], stage: str, **data: Any) -> None:
    _trace_event(trace, stage, **data)
    try:
        logger.debug("SkillAgent %s %s", stage, _ascii_log_text(data, 1500))
    except Exception:
        pass


_SYSTEM_PROMPT_TEMPLATE = """你是一个桌游陪玩，负责分析桌游规则问题，也会被要求做一些和桌游无关的问答。

你通过 MiniMax API 的 function calling 机制调用工具。每次 API 调用中，你需要直接生成 tool_calls，API 会自动执行工具并返回结果给你。你不需要也不应该用文字描述"我将使用XX工具"。

可用工具：{tool_schemas}

工具调用规则：
- 你可以直接在 API 响应中生成 tool_calls，API 会自动执行并返回结果
- 当你需要查询卡牌/规则/FAQ 时，生成对应的 tool_call
- 当用户问天气、网页信息、一般事实核查或其他游戏无关内容时，也允许使用 web_search；不要把这类请求拒绝成"功能范围外"
- 如果用户问题里有"今天"、"明天"、"最近"、"最新"等相对时间，请先根据当前日期换算成具体日期，再把具体日期放进 web_search 查询词里
- 不要重复查询同一张卡或同一规则——如果已经查过，就直接基于已有信息继续下一步
- 当你已有足够信息回答用户的问题时，不要再调用工具，直接输出文字结论
- 严禁"既不调用工具、也不输出结论"的情况。如果判断信息已足够，直接给出结论；如果信息仍不足，继续调用工具。绝对禁止输出类似"让我再查查"之类的过渡性文字然后停止。

分析规则和卡牌互动时，请遵循以下优先级：
1. Errata（勘误）— 最高优先级，覆盖所有其他规则
2. Rules and Clarification（规则澄清）
3. Official FAQ v2.5（官方 FAQ）
4. Taboo List（禁忌列表）
5. ArkhamDB 网页 FAQ
6. PDF 规则书 / 用户上传的文档

重要原则：
- 严谨求实，只引用你实际查询到的信息
- 绝不要编造或推测规则、FAQ 或卡牌效果
- 如果本地数据无法回答，再使用 web_search 联网搜索
- 给出结论时，用中文解释，保留英文专有名词

回答策略示例（Arkham规则查询思路）：
1. 把问题中涉及的卡牌效果都通过 arkham_cards 了解卡牌效果
2. 同时在 official_faq 中查询是否被提及
3. 同时在 arkham_rules 了解基本规则
4. 获取到这些第一批信息后检查是否有更多的卡牌和/或规则被提及
5. 如果有的话则继续调用工具查询，直到对相关信息完全清楚，输出答案"""


_SYSTEM_PROMPT_TEMPLATE += """

Arkham orientation rule:
- For Arkham Horror LCG rules, card effects, FAQ, or card-interaction questions, first call arkham_rules_orient with the user's question before detailed lookup.
- Use the orientation result to choose rule modules, search terms, and next tools; then call arkham_cards, official_faq, arkham_rules, or web_faq as needed.
- Do not skip orientation for timing, skill test, trigger window, errata, taboo, or multi-card interaction questions.
"""

_SYSTEM_PROMPT_TEMPLATE += """

并行工具调用规则：
- 如果一个问题需要查多个关键词、多个文件或多个不同来源，你可以在同一次 API 响应里一次性发出多个 tool_calls，后端会并行执行。
- 多个 tool_calls 可以调用不同工具，也可以多次调用同一个工具并传入不同参数。
- 查询用户上传文件时，当前桌已上传文件名会直接出现在用户提示里，不需要额外调用列文件工具。
- 当用户问上传文件的主要内容、整体结构、总结、概述、"这个文件讲什么"时，先使用 inspect_uploaded_file 建立文档地图；这会返回标题、章节、开头和短预览，不会读取整份文件。
- 当用户问具体词句、规则、人物、术语或某个局部问题时，优先使用 search_uploaded_files 做 grep 式搜索，只返回命中片段；搜索命中后需要核对上下文时，才使用 read_uploaded_file_excerpt 读取小范围片段。
- 不要请求或读取整份上传文件。
"""

_SUMMARIZE_SYSTEM_PROMPT = """你是一个专业的分析助手。

以下是某次多轮工具调用所收集到的所有信息。请仔细阅读这些信息，直接给出完整的回答结论。

回答要求：
- 只基于以下提供的实际信息进行总结，不允许编造或推测未查询到的内容
- 如果某些关键信息在查询中未能确认，请在结论中明确指出这一点
- If the original user question asks for a numeric/count result, such as "how many", "几个", "多少", or "最终可以获得几个", start with the direct numeric answer and show the short arithmetic from the user-stated numbers and retrieved rules.
- If tool results include a "Scenario arithmetic hint", use it as the anchor for the numeric/count answer unless another retrieved official rule directly contradicts it.
- 用中文回答，保留英文专有名词
- 如果信息已经足够回答用户问题，直接给出结论；如果信息不足，明确说明哪些方面仍不明确
- 用户问题可能是桌游规则，也可能是天气、网页信息、新闻、事实核查或其他游戏无关内容；不要把非游戏问题改写成规则分析，也不要因为它和游戏无关而拒绝
- 输出普通可播报纯文本，不要使用 Markdown 标题、项目符号、emoji 或装饰性格式
- 严禁凭空猜测内容"""


def _convert_to_anthropic_tools(tool_schemas: list[dict]) -> list[dict]:
    """Convert OpenAPI-style tool schemas to Anthropic tool format.

    Anthropic/MiniMax API requires nested 'function' object:
    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    anthropic_tools = []
    for schema in tool_schemas:
        anthropic_tools.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return anthropic_tools


def _format_uploaded_documents(documents: list[dict] | None) -> str:
    if not documents:
        return "当前桌没有上传文件。"
    lines = ["当前桌已上传文件："]
    for item in documents:
        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        size = int(item.get("size_bytes") or 0)
        uploaded_at = str(item.get("uploaded_at") or "").strip()
        suffix = f"，上传时间 {uploaded_at}" if uploaded_at else ""
        lines.append(f"- {filename}，{size} bytes{suffix}")
    return "\n".join(lines) if len(lines) > 1 else "当前桌没有上传文件。"


def _build_user_prompt(query: str, context: str, documents: list[dict] | None = None) -> str:
    return (
        f"{_current_date_context()}\n\n"
        f"用户问题：{query}\n\n"
        f"对话上下文：\n{context}\n\n"
        f"{_format_uploaded_documents(documents)}\n\n"
        f"请使用工具查询相关信息，然后给出完整答案。"
    )


def _looks_like_external_web_query(query: str) -> bool:
    text = _clean_text(query).lower()
    if not text:
        return False
    english_tokens = ("web", "search", "weather", "news", "trump", "latest", "internet")
    chinese_tokens = ("联网", "搜索", "查一下", "查一查", "查查", "天气", "新闻", "网页", "网上", "特朗普", "最近")
    return any(token in text for token in english_tokens) or any(token in text for token in chinese_tokens)


def _looks_like_out_of_scope_refusal(text: str) -> bool:
    normalized = _clean_text(text).lower()
    if not normalized:
        return False
    refusal_tokens = (
        "超出范围",
        "功能范围",
        "无法执行",
        "不能执行",
        "无法使用",
        "不符合",
        "游戏无关",
        "非游戏",
        "现实政治新闻",
        "out of scope",
        "outside",
    )
    return any(token in normalized for token in refusal_tokens)


def _has_web_search_tool(tool_schemas: list[dict]) -> bool:
    return any(schema.get("name") == "web_search" for schema in tool_schemas)


def _looks_like_arkham_rules_question(query: str) -> bool:
    text = _clean_text(query).lower()
    if not text:
        return False
    tokens = (
        "arkham",
        "阿卡姆",
        "诡镇",
        "eye of chaos",
        "rod of carnamagos",
        "chaos token",
        "curse token",
        "skill test",
        "混乱标记",
        "诅咒",
        "调查检定",
        "技能检定",
    )
    return any(token in text for token in tokens)


def _clean_speakable_summary(text: str) -> str:
    cleaned = _clean_text(text)
    cleaned = re.sub(r"#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(
        r"[\U0001f1e6-\U0001f1ff\U0001f300-\U0001f5ff\U0001f600-\U0001f64f"
        r"\U0001f680-\U0001f6ff\U0001f700-\U0001f77f\U0001f780-\U0001f7ff"
        r"\U0001f800-\U0001f8ff\U0001f900-\U0001f9ff\U0001fa00-\U0001fa6f"
        r"\U0001fa70-\U0001faff\u2600-\u27bf\ufe0f]",
        "",
        cleaned,
    )
    for token in ("✅", "❌", "⚠️", "📰", "💡", "🔎", "🔍", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "0️⃣"):
        cleaned = cleaned.replace(token, "")
    cleaned = re.sub(r"\s+[-*]\s+", " ", cleaned)
    return _clean_text(cleaned)


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """
    Parse tool call suggestions from LLM text output.

    Supports:
    - XML-wrapped DSL: [TOOL_CALL] {tool => "name", args => { --key "value" }} [/TOOL_CALL]
    - Plain DSL: {tool => "name", args => { --key "value" }}
    - JSON array: [{"name": "tool", "arguments": {...}}, ...]
    - Inline call/name: tool_name(...) or name: tool_name(...)
    """
    # Strip XML wrapper tags first
    text = re.sub(r'\[TOOL_CALL\]\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\[/TOOL_CALL\]', '', text, flags=re.IGNORECASE)

    # Try DSL format: {tool => "name", args => { --key "value" }}
    dsl_pattern = re.compile(
        r'\{'
        r'\s*tool\s*=>\s*["\']([^"\']+)["\']'
        r'.*?'
        r'\{[^}]*\}'
        r'\}',
        re.DOTALL,
    )
    for match in dsl_pattern.finditer(text):
        block = match.group()
        name_match = re.search(r'tool\s*=>\s*["\']([^"\']+)["\']', block)
        if not name_match:
            continue
        tool_name = name_match.group(1).strip()
        args: dict[str, Any] = {}
        # Parse --key "value" patterns
        for arg_match in re.finditer(r'--([a-zA-Z_][a-zA-Z0-9_]*)\s+"([^"]*)"', block):
            args[arg_match.group(1)] = arg_match.group(2)
        if args:
            return [{"name": tool_name, "arguments": args}]

    # Try JSON array
    try:
        json_match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, list):
                return [c for c in parsed if isinstance(c, dict) and c.get("name")]
    except (json.JSONDecodeError, Exception):
        pass

    # Try "call: tool_name(...)" inline pattern
    calls = []
    for match in re.finditer(
        r'(?:call|tool|invoke)[_ ]*(?:tool)?[_ ]*(?:name)?\s*[:=]\s*"?([a-zA-Z_][a-zA-Z0-9_]*)"?\s*\(\s*(.*?)\s*\)',
        text,
        re.DOTALL,
    ):
        tool_name = match.group(1).strip()
        args_raw = match.group(2).strip()
        try:
            args = json.loads(f"{{{args_raw}}}")
        except json.JSONDecodeError:
            args = {}
            for kv_match in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"', args_raw):
                args[kv_match.group(1)] = kv_match.group(2)
        calls.append({"name": tool_name, "arguments": args})

    return calls


class SkillAgent:
    """
    LLM Agent with tool-call loop for rule analysis.

    Runs in a worker thread managed by RuleAnalysisService.
    """

    def __init__(
        self,
        dialog_client: MiniMaxDialogClient,
        tool_registry: ToolRegistry,
        *,
        system_prompt: str | None = None,
        max_iterations: int = MAX_ITERATIONS,
        timeout_seconds: float = AGENT_TIMEOUT_SECONDS,
        max_parallel_tool_calls: int = 4,
    ) -> None:
        self.dialog_client = dialog_client
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt or _SYSTEM_PROMPT_TEMPLATE
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.max_parallel_tool_calls = max(1, max_parallel_tool_calls)

    def _fallback_summary(self, messages: list[dict]) -> str:
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "") or block.get("content", "")
                        if text:
                            parts.append(_trace_text(text, 1000))
            elif content:
                parts.append(_trace_text(content, 1000))
        return "\n".join(parts)[-2000:] or "抱歉，暂时没有得到足够信息。"

    def _summarize(self, messages: list[dict], reason: str, trace: list[dict[str, Any]] | None = None) -> str:
        """Make a final summarization call with no tools, using all accumulated info."""
        # Collect the assistant reasoning and plain-text tool results. Tool results
        # are appended as user messages in this agent loop, so filtering only for
        # assistant messages drops the evidence needed for the final answer.
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "") or block.get("content", "")
                        if text and role in {"assistant", "user"}:
                            parts.append(f"{role}: {text}")
            elif isinstance(content, str) and content and role in {"assistant", "user"}:
                parts.append(f"{role}: {content}")

        combined = "\n\n---\n\n".join(parts)

        # Also collect raw reasoning_content from the raw message dicts stored in _messages_ref
        # Build a lightweight context string
        summary_prompt = (
            f"{_current_date_context()}\n\n"
            f"用户原始问题：{getattr(self, '_original_query', '未知')}\n\n"
            f"分析过程汇总（包含思维链和已获取的信息）：\n{combined[-8000:]}\n\n"
            f"请根据以上信息，给出完整的回答结论。"
        )

        payload: dict[str, Any] = {
            "model": self.dialog_client.model,
            "stream": False,
            "max_tokens": 4096,
            "temperature": 0.1,
            "top_p": 0.95,
            "system": f"{SPEAKABLE_TEXT_GUARDRAIL}\n\n{_SUMMARIZE_SYSTEM_PROMPT}",
            "messages": [{"role": "user", "content": [{"type": "text", "text": summary_prompt}]}],
        }

        if trace is not None:
            _trace_debug(trace, "summarize_started", reason=reason, combined_parts=len(parts), total_chars=len(combined))

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            response_bytes = self.dialog_client._request_sender(
                self.dialog_client.base_url,
                body,
                {"Authorization": f"Bearer {self.dialog_client.api_key}", "Content-Type": "application/json"},
                60.0,
            )
            response = self.dialog_client._parse_text_post_response(response_bytes)
        except Exception as exc:
            if trace is not None:
                _trace_debug(trace, "summarize_failed", reason=reason, error=repr(exc))
            return self._fallback_summary(messages)

        base_resp = response.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            if trace is not None:
                _trace_debug(trace, "summarize_api_error", reason=reason, base_resp=base_resp)
            return combined[:500] if combined else "信息汇总失败。"

        summary = _clean_speakable_summary(self.dialog_client._extract_text(response))
        if trace is not None:
            _trace_debug(trace, "summarize_completed", reason=reason, summary_chars=len(summary))
        return summary

    def run(
        self,
        query: str,
        context: str = "暂无",
        *,
        table_id: str | None = None,
        documents: list[dict] | None = None,
    ) -> AgentAnswer:
        """
        Run the agent loop synchronously.

        Returns AgentAnswer with content, iteration count, and timed_out flag.
        """
        tool_schemas = self.tool_registry.tool_schemas()
        tool_schemas_json = json.dumps(tool_schemas, ensure_ascii=False, indent=2)
        system_prompt = (
            f"{SPEAKABLE_TEXT_GUARDRAIL}\n\n"
            f"{_current_date_context()}\n\n"
            f"{self.system_prompt.replace('{tool_schemas}', tool_schemas_json)}"
        )

        # Convert to Anthropic tools format for native tool_call support
        anthropic_tools = _convert_to_anthropic_tools(tool_schemas)

        # Build conversation messages in Anthropic API format.
        # content must be [{"type": "text", "text": "..."}], NO "name" field.
        messages: list[dict] = [
            {
                "role": "user",
                "content": [{"type": "text", "text": _build_user_prompt(query, context, documents)}],
            },
        ]

        # Store for _call_llm
        self._system_prompt = system_prompt
        self._anthropic_tools = anthropic_tools
        self._original_query = query

        start_time = time.monotonic()
        iterations = 0
        timed_out = False
        trace: list[dict[str, Any]] = []
        _trace_debug(trace, "run_started", query=query, context_chars=len(context), tool_count=len(tool_schemas))

        while iterations < self.max_iterations:
            # Check timeout
            elapsed = time.monotonic() - start_time
            if elapsed >= self.timeout_seconds:
                logger.warning("SkillAgent timed out after %.1fs (%d iterations)", elapsed, iterations)
                timed_out = True
                break

            iterations += 1

            # On second-to-last iteration, force LLM to output answer (no more tool calls)
            tools_enabled = iterations < self.max_iterations - 1
            checkpoint_messages = deepcopy(messages)
            _trace_debug(
                trace,
                "iteration_started",
                iteration=iterations,
                message_count=len(messages),
                tools_enabled=tools_enabled,
            )

            # Print what goes into this iteration
            print(f"\n{'='*60}")
            print(f"=== Iteration {iterations} ===")
            print(f"--- Messages to LLM ({len(messages)}) ---")
            for i, msg in enumerate(messages):
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_result":
                                print(f"  [{i}] role={role} [tool_result id={block.get('tool_use_id', '?')}] text_len={len(block.get('content', ''))}")
                            else:
                                print(f"  [{i}] role={role} [text] len={len(str(block))}: {str(block)[:300]}")
                        else:
                            print(f"  [{i}] role={role} [text] len={len(str(block))}: {str(block)[:300]}")
                else:
                    print(f"  [{i}] role={role} content={repr(str(content)[:500])}")
            print(f"--- Tools enabled: {tools_enabled} (iteration {iterations}/{self.max_iterations}) ---")

            # For iteration 2, print full prompt details
            if iterations == 2:
                print(f"\n{'='*60}")
                print("=== ITERATION 2 FULL DETAILS ===")
                print(f"\n--- System Prompt (first 2000 chars) ---")
                print(self._system_prompt[:2000])
                print(f"\n--- Messages to LLM ---")
                for i, msg in enumerate(messages):
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                ctype = block.get("type", "text")
                                ctext = block.get("text", "") or block.get("content", "")
                                if ctype == "tool_result":
                                    print(f"\n  [msg {i}] role={role} type={ctype} id={block.get('tool_use_id', '?')}")
                                    print(f"  FULL CONTENT ({len(ctext)} chars):")
                                    print(ctext)
                                else:
                                    print(f"\n  [msg {i}] role={role} type={ctype}")
                                    print(f"  FULL CONTENT ({len(ctext)} chars):")
                                    print(ctext)
                            else:
                                print(f"\n  [msg {i}] role={role} [block type={type(block).__name__}]")
                                print(f"  FULL: {block}")
                    else:
                        print(f"\n  [msg {i}] role={role} [content is string]")
                        print(f"  FULL: {content}")

            # Call LLM
            try:
                response = self._call_llm(messages, tools_enabled=tools_enabled)
            except Exception as exc:
                logger.exception("LLM call failed in SkillAgent iteration %d", iterations)
                _trace_debug(trace, "llm_call_failed", iteration=iterations, error=repr(exc), retry=True)
                messages = checkpoint_messages
                continue

            # Extract tool calls and text content from response
            try:
                tool_calls = self._extract_tool_calls(response)
            except Exception as exc:
                _trace_debug(trace, "parse_tool_calls_failed", iteration=iterations, error=repr(exc), retry=True)
                messages = checkpoint_messages
                continue
            if (
                iterations == 1
                and self.tool_registry.get("arkham_rules_orient") is not None
                and _looks_like_arkham_rules_question(query)
                and not any(call.get("name") == "arkham_rules_orient" for call in tool_calls)
            ):
                tool_calls = [
                    {
                        "name": "arkham_rules_orient",
                        "arguments": {"query": query},
                        "id": f"injected_arkham_rules_orient_{iterations}",
                    },
                    *tool_calls,
                ]
                _trace_debug(trace, "arkham_orientation_injected", iteration=iterations, query=query)
            raw_choices = response.get("choices") or []
            raw_msg = raw_choices[0].get("message", {}) if raw_choices else {}
            content_text = _clean_text(self.dialog_client._extract_text(response))
            reasoning_text = _clean_text(raw_msg.get("reasoning_content", ""))

            # Combine reasoning and content for display and storage
            if reasoning_text and reasoning_text != content_text:
                assistant_text = f"{reasoning_text}\n\n{content_text}" if content_text else reasoning_text
            else:
                assistant_text = content_text

            # Print what came back from LLM
            print(f"--- LLM Response ---")
            print(f"  tool_calls: {[c.get('name') for c in tool_calls]}")
            print(f"  content_text ({len(content_text)} chars): {content_text[:200]}")
            print(f"  reasoning_text ({len(reasoning_text)} chars): {reasoning_text[:200]}")

            # For iteration 2 and any iteration with tool_calls=[], print full LLM response
            if iterations == 2 or (not tool_calls and iterations >= 4):
                print(f"\n--- ITERATION {iterations} FULL LLM RESPONSE ---")
                if raw_choices:
                    print(f"finish_reason: {raw_choices[0].get('finish_reason', '?')}")
                    print(f"content: {repr(raw_msg.get('content', ''))[:2000]}")
                    print(f"reasoning_content: {repr(raw_msg.get('reasoning_content', ''))[:2000]}")
                    print(f"tool_calls: {json.dumps(raw_msg.get('tool_calls', []), ensure_ascii=False)[:3000]}")

            if not tool_calls:
                if (
                    _has_web_search_tool(tool_schemas)
                    and _looks_like_external_web_query(query)
                    and _looks_like_out_of_scope_refusal(assistant_text)
                    and not any(event.get("stage") == "out_of_scope_refusal_retried" for event in trace)
                    and iterations < self.max_iterations - 1
                ):
                    messages = checkpoint_messages
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "上一次回复把联网查询误判成了功能范围外。"
                                        "当前规则明确允许天气、网页信息、一般事实核查、新闻和其他游戏无关内容使用 web_search。"
                                        "请不要拒绝；如果需要外部资料，请直接调用 web_search。"
                                    ),
                                }
                            ],
                        }
                    )
                    _trace_debug(
                        trace,
                        "out_of_scope_refusal_retried",
                        iteration=iterations,
                        query=query,
                        refusal=assistant_text,
                    )
                    continue
                # Append the final assistant message before summarizing
                if assistant_text.strip():
                    messages.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": assistant_text}],
                    })
                # Summarize all accumulated info before returning
                summary = self._summarize(messages, f"no_tool_calls_iter_{iterations}", trace=trace)
                return AgentAnswer(
                    content=summary,
                    iterations=iterations,
                    timed_out=False,
                    trace=trace,
                )

            tool_result_blocks = self._execute_tool_calls_parallel(
                tool_calls,
                iteration=iterations,
                trace=trace,
                runtime_context={"table_id": table_id or ""},
            )

            # After iteration 1+: replace conversation with plain-text tool results + fresh tools.
            # The API should NOT receive structured tool_calls in the user message history.
            # Instead, convert iteration 1's tool results to plain text so the model can
            # read them as normal context and generate NEW tool calls (or a final answer).
            # tools parameter is still sent so the model CAN generate tool calls.
            combined_results = "\n\n".join(
                f"工具 {block['tool_use_id']} 返回:\n{block['content']}"
                for block in tool_result_blocks
            )

            # Append assistant's reasoning text as assistant message
            if assistant_text.strip():
                messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                })

            # Append tool results as user message (always APPEND, never replace)
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": combined_results}],
            })

        # Max iterations or timeout reached - summarize all accumulated info
        reason = "timeout" if timed_out else f"max_iterations_{iterations}"
        summary = self._summarize(messages, reason, trace=trace)
        final_text = (
            f"抱歉，分析时间超时了，无法完成查询。以下是已收集到的信息：\n{summary}"
            if timed_out
            else f"分析未能在规定步数内完成。以下是已收集到的信息：\n{summary}"
        )
        _trace_debug(trace, "run_finished", iterations=iterations, timed_out=timed_out, reason=reason)
        return AgentAnswer(content=final_text, iterations=iterations, timed_out=timed_out, trace=trace)

    def _execute_one_tool_call(
        self,
        call: dict[str, Any],
        *,
        iteration: int,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        tool_name = call.get("name", "")
        arguments = dict(call.get("arguments", {}) or {})
        if tool_name == "arkham_rules_orient":
            original_query = _clean_text(getattr(self, "_original_query", ""))
            tool_query = _clean_text(arguments.get("query", ""))
            if original_query and original_query not in tool_query:
                arguments["query"] = (
                    f"{tool_query}\n\nOriginal user question: {original_query}"
                    if tool_query
                    else original_query
                )
        table_id = str(runtime_context.get("table_id") or "").strip()
        if table_id:
            arguments.setdefault("_table_id", table_id)
        tool_result = self.tool_registry.execute(tool_name, arguments)
        result_text = (
            f"[{tool_name}] {tool_result.content}"
            if tool_result.ok
            else f"[{tool_name} ERROR: {tool_result.error}]"
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "type": "tool_result",
            "tool_use_id": call.get("id", "") or f"call_{tool_name}_{iteration}",
            "content": result_text,
            "_trace": {
                "tool": tool_name,
                "ok": tool_result.ok,
                "result_chars": len(result_text),
                "result": result_text,
                "duration_ms": duration_ms,
            },
        }

    def _execute_tool_calls_parallel(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        iteration: int,
        trace: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        runtime_context = runtime_context or {}
        max_workers = min(self.max_parallel_tool_calls, len(tool_calls))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    self._execute_one_tool_call,
                    call,
                    iteration=iteration,
                    runtime_context=runtime_context,
                )
                for call in tool_calls
            ]
            results = [future.result() for future in futures]

        for result in results:
            trace_data = dict(result.pop("_trace"))
            print(
                f"  executed tool={trace_data['tool']} ok={trace_data['ok']} "
                f"result_len={trace_data['result_chars']} duration_ms={trace_data['duration_ms']}"
            )
            _trace_debug(
                trace,
                "tool_executed",
                iteration=iteration,
                **trace_data,
            )
        return results

    def _get_native_tool_calls(self, response: dict) -> list[dict] | None:
        """Extract native tool_calls from MiniMax response - return RAW format unchanged."""
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls")
            if tool_calls:
                return tool_calls  # Return exactly as MiniMax returned it
        return None

    def _extract_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool calls from LLM response - prefers native Anthropic tool_calls."""
        # Try native Anthropic tool_calls first
        native_calls = self._get_native_tool_calls(response)
        if native_calls:
            result = []
            for call in native_calls:
                func = call.get("function", {})
                result.append({
                    "name": func.get("name", ""),
                    "arguments": json.loads(func.get("arguments", "{}")),
                    "id": call.get("id", ""),
                })
            return result
        # Fall back to text parsing
        text = _clean_text(self.dialog_client._extract_text(response))
        return _parse_tool_calls(text)

    def _call_llm(self, messages: list[dict], *, tools_enabled: bool = True) -> dict:
        """Make a single LLM API call (non-streaming) using Anthropic API format."""
        headers = {
            "Authorization": f"Bearer {self.dialog_client.api_key}",
            "Content-Type": "application/json",
        }

        # Anthropic API format: system as separate param, NO "name" fields in messages
        payload: dict[str, Any] = {
            "model": self.dialog_client.model,
            "stream": False,
            "max_tokens": 8192,  # Large enough for multi-tool analysis + final answer
            "temperature": 0.1,  # Low temperature for consistent, factual answers
            "top_p": 0.95,
            "system": self._system_prompt,  # Anthropic API uses "system" param
            "messages": messages,  # Already in Anthropic format from run()
        }
        if tools_enabled:
            payload["tools"] = self._anthropic_tools

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        print(f"[DEBUG] payload bytes={len(body)}, messages_count={len(messages)}")

        response_bytes = self.dialog_client._request_sender(
            self.dialog_client.base_url,
            body,
            headers,
            120.0,  # 120s timeout for non-streaming (increased from 60s)
        )
        response = self.dialog_client._parse_text_post_response(response_bytes)

        # Check for API-level errors via base_resp
        base_resp = response.get("base_resp") or {}
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            status_msg = base_resp.get("status_msg", "unknown")
            raise RuntimeError(f"MiniMax API error {status_code}: {status_msg}")

        return response

    def _build_transcript(self, messages: list[dict]) -> str:
        """Build a readable transcript string from messages."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            name = msg.get("name", "")
            content = msg.get("content", "")
            if role == "system":
                lines.append(f"[System]: {content[:200]}")
            elif role == "user":
                lines.append(f"[User{' (' + name + ')' if name else ''}]: {content[:300]}")
            elif role == "assistant":
                lines.append(f"[Assistant]: {content[:500]}")
        return "\n".join(lines)
