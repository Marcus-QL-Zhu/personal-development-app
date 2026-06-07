from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RuleAnalysisWorker:
    """
    Delegates rule analysis to the SkillAgent (LLM + tool-call loop).

    The SkillAgent handles multi-step tool execution for complex rule
    and card queries. This worker is a thin wrapper that:
    1. Builds the conversation context from recent events
    2. Runs the SkillAgent
    3. Returns a normalized reply payload
    """

    def __init__(
        self,
        skill_agent,
        rules_router=None,  # kept for backwards compatibility
    ) -> None:
        self._skill_agent = skill_agent
        self._rules_router = rules_router

    def analyze(
        self,
        *,
        query: str,
        events: list[dict],
        recent_results: list[dict] | None = None,
        table_id: str | None = None,
        documents: list[dict] | None = None,
    ) -> dict:
        """
        Run the SkillAgent to analyze the rule/card query.

        The agent will iteratively call tools (local rules, card DB, FAQ, web search)
        until it has enough information to produce a final answer.
        """
        # Build context string from recent events and cached results
        context_lines = []
        for item in events[-10:]:
            kind = item.get("kind", "")
            content = item.get("content", "").strip()
            if not content:
                continue
            if kind == "voice_transcript":
                source = item.get("source", "unknown")
                context_lines.append(f"用户({source}): {content}")
            elif kind == "assistant_spoken":
                context_lines.append(f"助手: {content}")
            elif kind == "rule_reference":
                context_lines.append(f"参考: {content}")
            elif kind == "document_upload_fact":
                context_lines.append(f"系统事实: {content}")

        for cached in recent_results or []:
            cached_query = cached.get("query", "")
            result = cached.get("result") or {}
            content = str(result.get("content", "")).strip()
            if content:
                context_lines.append(f"最近查过: {cached_query} → {content[:100]}")

        if documents:
            context_lines.append("当前桌已上传文件：")
            for item in documents:
                filename = str(item.get("filename") or "").strip()
                if not filename:
                    continue
                size = int(item.get("size_bytes") or 0)
                uploaded_at = str(item.get("uploaded_at") or "").strip()
                suffix = f"，上传时间 {uploaded_at}" if uploaded_at else ""
                context_lines.append(f"- {filename}，{size} bytes{suffix}")

        context = "\n".join(context_lines) if context_lines else "暂无"

        try:
            try:
                answer = self._skill_agent.run(
                    query=query,
                    context=context,
                    table_id=table_id,
                    documents=documents,
                )
            except TypeError as exc:
                if "table_id" not in str(exc) and "documents" not in str(exc):
                    raise
                answer = self._skill_agent.run(query=query, context=context)
            logger.info(
                "SkillAgent finished: iterations=%d timed_out=%s content_len=%d",
                answer.iterations,
                answer.timed_out,
                len(answer.content),
            )
        except Exception as exc:
            logger.exception("SkillAgent.run() raised")
            return {
                "source": "companion",
                "content": "抱歉，分析过程中出了点问题，请稍后重试。",
            }

        payload = {"source": "skill_agent", "content": answer.content}
        payload["iterations"] = answer.iterations
        payload["timed_out"] = answer.timed_out
        payload["trace"] = list(getattr(answer, "trace", []) or [])
        return payload
