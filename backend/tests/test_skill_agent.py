from __future__ import annotations

import builtins
import json
import time
import zipfile
from io import BytesIO

from gamevoice_server.dialog_client import MiniMaxDialogClient
from gamevoice_server.rule_analysis_worker import RuleAnalysisWorker
from gamevoice_server.skill_runner.agent import (
    AgentAnswer,
    SkillAgent,
    _SUMMARIZE_SYSTEM_PROMPT,
    _SYSTEM_PROMPT_TEMPLATE,
    _build_user_prompt,
    _clean_speakable_summary,
)
from gamevoice_server.document_store import DocumentStore
from gamevoice_server.skill_runner.tools import (
    build_arkham_rules_orient_tool,
    build_uploaded_file_inspect_tool,
    build_uploaded_file_search_tool,
    build_web_search_tool,
)
from gamevoice_server.skill_runner.tools import file_reader as file_reader_module
from gamevoice_server.skill_runner.tools import web_search as web_search_module
from gamevoice_server.skill_runner.tool_registry import Tool, ToolRegistry, ToolResult


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(text)
        for text in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _response(*, content: str = "", reasoning: str = "", tool_calls: list[dict] | None = None) -> bytes:
    return json.dumps(
        {
            "base_resp": {"status_code": 0, "status_msg": "ok"},
            "choices": [
                {
                    "message": {
                        "content": content,
                        "reasoning_content": reasoning,
                        "tool_calls": tool_calls or [],
                    },
                    "finish_reason": "stop",
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _client_with_responses(responses: list[bytes]) -> MiniMaxDialogClient:
    pending = list(responses)

    def sender(*_args, **_kwargs) -> bytes:
        if not pending:
            raise AssertionError("unexpected MiniMax request")
        return pending.pop(0)

    return MiniMaxDialogClient(api_key="test", request_sender=sender)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="lookup",
            description="lookup test data",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=lambda args: ToolResult.success(f"Roland result for {args.get('query', '')}"),
        )
    )
    return registry


def _web_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="web_search",
            description="search the web",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=lambda args: ToolResult.success(f"web result for {args.get('query', '')}"),
        )
    )
    return registry


def test_arkham_rules_orient_tool_maps_timing_card_question_to_modules():
    tool = build_arkham_rules_orient_tool()

    result = tool.execute(
        {
            "query": (
                "Can I trigger a reaction ability on Eye of Chaos after revealing "
                "multiple curse tokens during a skill test?"
            )
        }
    )

    assert result.ok
    assert "skill_test_timing" in result.content
    assert "ability_timing" in result.content
    assert "arkham_cards" in result.content
    assert "official_faq" in result.content
    assert "curse token" in result.content


def test_arkham_rules_orient_tool_maps_chinese_eye_rod_scenario_to_skill_test_modules():
    tool = build_arkham_rules_orient_tool()

    result = tool.execute(
        {
            "query": (
                "使用4级eye of chaos开始调查检定，Rod of Carnamagos先抽到3个诅咒标记，"
                "正式检定又抽到1个诅咒标记，检定成功，最终可以获得几个线索？"
            )
        }
    )

    assert result.ok
    assert "skill_test_timing" in result.content
    assert "ability_timing" in result.content
    assert "locations_clues_doom" in result.content
    assert "curse token" in result.content
    assert "Maximum clue answer: 6" in result.content
    assert "1 base clue + 1 Eye of Chaos success clue + 4 curse-token clue choices" in result.content


def test_arkham_rules_orient_tool_schema_is_registered_with_tool_registry():
    registry = ToolRegistry()
    registry.register(build_arkham_rules_orient_tool())

    schema = registry.tool_schemas()[0]

    assert schema["name"] == "arkham_rules_orient"
    assert "orient" in schema["description"].lower()
    assert schema["parameters"]["required"] == ["query"]


def test_skill_agent_prompt_prefers_orientation_before_detailed_arkham_lookup():
    assert "arkham_rules_orient" in _SYSTEM_PROMPT_TEMPLATE
    assert "first" in _SYSTEM_PROMPT_TEMPLATE
    assert "arkham_cards" in _SYSTEM_PROMPT_TEMPLATE
    assert "official_faq" in _SYSTEM_PROMPT_TEMPLATE


def test_skill_agent_passes_original_user_question_into_orientation_tool():
    client = _client_with_responses(
        [
            _response(
                tool_calls=[
                    {
                        "id": "call-orient",
                        "function": {
                            "name": "arkham_rules_orient",
                            "arguments": json.dumps({"query": "Eye of Chaos Rod interaction"}),
                        },
                    }
                ],
            ),
            _response(content="enough info"),
            _response(content="summary"),
        ]
    )
    captured: dict[str, str] = {}
    registry = ToolRegistry()

    def inspect_orientation(args: dict) -> ToolResult:
        captured["query"] = args.get("query", "")
        return ToolResult.success("oriented")

    registry.register(
        Tool(
            name="arkham_rules_orient",
            description="orient Arkham rules",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=inspect_orientation,
        )
    )
    agent = SkillAgent(dialog_client=client, tool_registry=registry, max_iterations=4)

    original_query = "Eye of Chaos 调查时 Rod 抽到3个诅咒，正式检定又抽到1个诅咒，成功后几个线索？"
    answer = agent.run(query=original_query, context="")

    assert answer.content == "summary"
    assert original_query in captured["query"]
    assert "Eye of Chaos Rod interaction" in captured["query"]


def test_skill_agent_injects_orientation_call_for_arkham_query_when_llm_skips_it():
    client = _client_with_responses(
        [
            _response(
                tool_calls=[
                    {
                        "id": "call-card",
                        "function": {
                            "name": "arkham_cards",
                            "arguments": json.dumps({"query": "Eye of Chaos"}),
                        },
                    }
                ],
            ),
            _response(content="enough info"),
            _response(content="summary"),
        ]
    )
    calls: list[tuple[str, str]] = []
    registry = ToolRegistry()

    registry.register(
        Tool(
            name="arkham_rules_orient",
            description="orient Arkham rules",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=lambda args: calls.append(("arkham_rules_orient", args.get("query", ""))) or ToolResult.success("oriented"),
        )
    )
    registry.register(
        Tool(
            name="arkham_cards",
            description="card lookup",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=lambda args: calls.append(("arkham_cards", args.get("query", ""))) or ToolResult.success("card"),
        )
    )
    agent = SkillAgent(dialog_client=client, tool_registry=registry, max_iterations=4)

    answer = agent.run(query="arkham rules：Eye of Chaos 和 Rod of Carnamagos 抽到诅咒后几个线索？", context="")

    assert answer.content == "summary"
    assert [name for name, _query in calls] == ["arkham_rules_orient", "arkham_cards"]
    assert "几个线索" in calls[0][1]


def test_skill_agent_summary_prompt_requires_direct_numeric_answer_for_count_questions():
    assert "numeric" in _SUMMARIZE_SYSTEM_PROMPT
    assert "how many" in _SUMMARIZE_SYSTEM_PROMPT
    assert "几个" in _SUMMARIZE_SYSTEM_PROMPT
    assert "Scenario arithmetic hint" in _SUMMARIZE_SYSTEM_PROMPT


def test_skill_agent_executes_same_batch_tool_calls_in_parallel():
    client = _client_with_responses(
        [
            _response(
                tool_calls=[
                    {
                        "id": "call-a",
                        "function": {"name": "slow_lookup", "arguments": json.dumps({"query": "A"})},
                    },
                    {
                        "id": "call-b",
                        "function": {"name": "slow_lookup", "arguments": json.dumps({"query": "B"})},
                    },
                ],
            ),
            _response(content="enough info"),
            _response(content="parallel summary"),
        ]
    )
    registry = ToolRegistry()
    starts: list[float] = []

    def slow_lookup(args: dict) -> ToolResult:
        starts.append(time.perf_counter())
        time.sleep(0.25)
        return ToolResult.success(f"result {args['query']}")

    registry.register(
        Tool(
            name="slow_lookup",
            description="slow test lookup",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=slow_lookup,
        )
    )
    agent = SkillAgent(
        dialog_client=client,
        tool_registry=registry,
        max_iterations=4,
        max_parallel_tool_calls=4,
    )

    start = time.perf_counter()
    answer = agent.run(query="compare A and B", context="")
    elapsed = time.perf_counter() - start

    assert answer.content == "parallel summary"
    assert elapsed < 0.45
    assert len(starts) == 2
    assert abs(starts[1] - starts[0]) < 0.15


def test_uploaded_file_search_tool_searches_current_table_text_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    store.save(
        table_id="table-1",
        filename="桌游热词.txt",
        data="先攻玩家可以执行 evade 行动。\n另一个关键词是 doom。".encode("utf-8"),
    )
    store.save(table_id="table-2", filename="other.txt", data="evade should stay hidden".encode("utf-8"))

    tool = build_uploaded_file_search_tool()
    result = tool.execute({"query": "evade", "_table_id": "table-1"})

    assert result.ok
    assert "桌游热词.txt:1" in result.content
    assert "evade" in result.content
    assert "other.txt" not in result.content


def test_uploaded_file_search_tool_searches_pdf_text_with_pdfplumber(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="table-1", filename="rules.pdf", data=b"%PDF")
    monkeypatch.setattr(
        file_reader_module,
        "_extract_pdf_text",
        lambda _path: "第一页\n这里写着 doom 关键词。",
    )

    tool = build_uploaded_file_search_tool()
    result = tool.execute({"query": "doom", "_table_id": "table-1"})

    assert result.ok
    assert "rules.pdf:2" in result.content
    assert "doom" in result.content


def test_uploaded_file_search_tool_reuses_persisted_pdf_text_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="table-1", filename="rules.pdf", data=b"%PDF")
    calls = {"count": 0}

    def fake_extract(_path):
        calls["count"] += 1
        return "第一页\n这里写着 doom 关键词。"

    monkeypatch.setattr(file_reader_module, "_extract_pdf_text", fake_extract)
    tool = build_uploaded_file_search_tool()

    first = tool.execute({"query": "doom", "_table_id": "table-1"})
    second = tool.execute({"query": "doom", "_table_id": "table-1"})

    assert first.ok
    assert second.ok
    assert calls["count"] == 1


def test_uploaded_file_search_tool_searches_docx_text_and_caches_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    saved = store.save(
        table_id="table-1",
        filename="注意力管理.docx",
        data=_docx_bytes(["AI 工具降低生成成本。", "但会提高监督成本和上下文重建成本。"]),
    )

    tool = build_uploaded_file_search_tool()
    result = tool.execute({"query": "监督成本", "_table_id": "table-1"})

    assert result.ok
    assert "注意力管理.docx:2" in result.content
    assert "监督成本" in result.content
    cache_stem = saved["stored_filename"]
    assert (tmp_path / "table-1" / ".extracted" / f"{cache_stem}.txt").exists()
    assert (tmp_path / "table-1" / ".extracted" / f"{cache_stem}.meta.json").exists()


def test_uploaded_file_search_tool_reuses_persisted_docx_text_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="table-1", filename="notes.docx", data=_docx_bytes(["监督成本", "上下文重建"]))
    calls = {"count": 0}

    def fake_extract(_path):
        calls["count"] += 1
        return "监督成本\n上下文重建"

    monkeypatch.setattr(file_reader_module, "_extract_docx_text", fake_extract)
    tool = build_uploaded_file_search_tool()

    first = tool.execute({"query": "监督成本", "_table_id": "table-1"})
    second = tool.execute({"query": "上下文重建", "_table_id": "table-1"})

    assert first.ok
    assert second.ok
    assert calls["count"] == 1


def test_uploaded_file_inspect_tool_builds_cached_document_map_for_broad_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    saved = store.save(
        table_id="table-1",
        filename="注意力管理.docx",
        data=_docx_bytes(
            [
                "用“切换物理空间”提升注意力：",
                "给多任务知识工作者的一套低成本方法",
                "核心观点",
                "把不同任务绑定到不同环境线索上，让大脑更容易进入对应工作模式。",
                "一、为什么物理空间会影响注意力？",
                "同一张桌子承载太多任务线索时，大脑更容易分心。",
                "二、推荐做法：三空间法",
                "社交位用于电话和微信，判断位用于简历筛选，AI 位用于 coding。",
                "三、给同事的 5 分钟落地清单",
                "每次切换位置前写一句 closure note。",
            ]
        ),
    )

    tool = build_uploaded_file_inspect_tool()
    result = tool.execute({"filename": "注意力", "_table_id": "table-1"})

    assert result.ok
    assert "注意力管理.docx" in result.content
    assert "文档地图" in result.content
    assert "标题：用“切换物理空间”提升注意力：" in result.content
    assert "5: 一、为什么物理空间会影响注意力？" in result.content
    assert "7: 二、推荐做法：三空间法" in result.content
    assert "社交位用于电话和微信" in result.content
    cache_stem = saved["stored_filename"]
    assert (tmp_path / "table-1" / ".extracted" / f"{cache_stem}.inspect.json").exists()


def test_uploaded_file_inspect_tool_reuses_persisted_document_map_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_UPLOAD_DIR", str(tmp_path))
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="table-1", filename="notes.docx", data=_docx_bytes(["标题", "核心观点", "监督成本"]))
    calls = {"count": 0}

    def fake_extract(_path):
        calls["count"] += 1
        return "标题\n核心观点\n监督成本"

    monkeypatch.setattr(file_reader_module, "_extract_docx_text", fake_extract)
    tool = build_uploaded_file_inspect_tool()

    first = tool.execute({"filename": "notes", "_table_id": "table-1"})
    second = tool.execute({"filename": "notes", "_table_id": "table-1"})

    assert first.ok
    assert second.ok
    assert calls["count"] == 1


def test_skill_agent_user_prompt_includes_uploaded_file_inventory_without_list_tool():
    prompt = _build_user_prompt(
        "查一下我上传的规则",
        "用户: 查一下文件",
        documents=[
            {
                "filename": "rules.pdf",
                "size_bytes": 2048,
                "uploaded_at": "2026-05-26T12:00:00Z",
            }
        ],
    )

    assert "rules.pdf" in prompt
    assert "2048" in prompt
    assert "list_uploaded_files" not in _SYSTEM_PROMPT_TEMPLATE
    assert "search_uploaded_files" in _SYSTEM_PROMPT_TEMPLATE
    assert "inspect_uploaded_file" in _SYSTEM_PROMPT_TEMPLATE
    assert "主要内容" in _SYSTEM_PROMPT_TEMPLATE
    assert "并行" in _SYSTEM_PROMPT_TEMPLATE


def test_skill_agent_passes_current_table_context_into_tool_calls():
    client = _client_with_responses(
        [
            _response(
                tool_calls=[
                    {
                        "id": "call-search",
                        "function": {"name": "inspect_context", "arguments": json.dumps({"query": "doom"})},
                    }
                ],
            ),
            _response(content="enough info"),
            _response(content="context summary"),
        ]
    )
    captured: dict[str, str] = {}
    registry = ToolRegistry()

    def inspect_context(args: dict) -> ToolResult:
        captured["table_id"] = args.get("_table_id", "")
        return ToolResult.success("ok")

    registry.register(
        Tool(
            name="inspect_context",
            description="inspect runtime context",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=inspect_context,
        )
    )
    agent = SkillAgent(dialog_client=client, tool_registry=registry, max_iterations=4)

    answer = agent.run(
        query="search upload",
        context="",
        table_id="table-ctx",
        documents=[{"filename": "rules.pdf", "size_bytes": 12}],
    )

    assert answer.content == "context summary"
    assert captured["table_id"] == "table-ctx"


def test_skill_agent_unicode_logging_failure_does_not_abort(monkeypatch):
    def broken_print(*_args, **_kwargs):
        raise UnicodeEncodeError("gbk", "🔍", 0, 1, "illegal multibyte sequence")

    monkeypatch.setattr(builtins, "print", broken_print)

    client = _client_with_responses(
        [
            _response(
                content="我会查一下🔍",
                reasoning="计划🕵️",
                tool_calls=[
                    {
                        "id": "call-1",
                        "function": {"name": "lookup", "arguments": json.dumps({"query": "Roland"})},
                    }
                ],
            ),
            _response(content="查到了📋", reasoning="可以回答"),
            _response(content="Roland 的效果是测试结果。"),
        ]
    )
    agent = SkillAgent(dialog_client=client, tool_registry=_registry(), max_iterations=4)

    answer = agent.run(query="查 Roland", context="玩家: 查 Roland")

    assert "Roland" in answer.content
    assert answer.trace


def test_skill_agent_retries_malformed_tool_call_from_checkpoint():
    client = _client_with_responses(
        [
            _response(
                content="准备查",
                tool_calls=[{"id": "bad-1", "function": {"name": "lookup", "arguments": "{"}}],
            ),
            _response(content="已经能回答了"),
            _response(content="最终答案。"),
        ]
    )
    agent = SkillAgent(dialog_client=client, tool_registry=_registry(), max_iterations=4)

    answer = agent.run(query="查 Roland", context="玩家: 查 Roland")

    assert answer.content == "最终答案。"
    assert answer.iterations == 2
    assert any(event.get("stage") == "parse_tool_calls_failed" for event in answer.trace)


def test_skill_agent_retries_transient_llm_failure_from_checkpoint():
    responses = [
        RuntimeError("temporary network failure"),
        _response(content="已经能回答了"),
        _response(content="最终答案。"),
    ]

    def sender(*_args, **_kwargs) -> bytes:
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = MiniMaxDialogClient(api_key="test", request_sender=sender)
    agent = SkillAgent(dialog_client=client, tool_registry=_registry(), max_iterations=4)

    answer = agent.run(query="查 Roland", context="玩家: 查 Roland")

    assert answer.content == "最终答案。"
    assert answer.iterations == 2
    assert any(event.get("stage") == "llm_call_failed" for event in answer.trace)


def test_rule_analysis_worker_preserves_skill_agent_trace():
    class StubSkillAgent:
        def run(self, *, query: str, context: str) -> AgentAnswer:
            return AgentAnswer(
                content="查到结果。",
                iterations=2,
                timed_out=False,
                trace=[{"stage": "tool_executed", "tool": "lookup"}],
            )

    worker = RuleAnalysisWorker(skill_agent=StubSkillAgent())

    result = worker.analyze(query="查一下", events=[{"kind": "voice_transcript", "content": "查一下"}])

    assert result["content"] == "查到结果。"
    assert result["iterations"] == 2
    assert result["timed_out"] is False
    assert result["trace"] == [{"stage": "tool_executed", "tool": "lookup"}]


def test_rule_analysis_worker_keeps_skill_agent_answer_plain_text():
    class StubSkillAgent:
        def run(self, *, query: str, context: str) -> AgentAnswer:
            return AgentAnswer(
                content="第一句。第二句。第三句。",
                iterations=1,
                timed_out=False,
                trace=[],
            )

    worker = RuleAnalysisWorker(skill_agent=StubSkillAgent())

    result = worker.analyze(query="news", events=[])

    assert result["source"] == "skill_agent"
    assert result["content"] == "第一句。第二句。第三句。"
    assert "lead" not in result
    assert "tail" not in result


def test_rule_analysis_worker_passes_uploaded_documents_to_skill_agent():
    captured: dict[str, object] = {}

    class StubSkillAgent:
        def run(self, *, query: str, context: str, table_id: str | None = None, documents: list[dict] | None = None) -> AgentAnswer:
            captured["table_id"] = table_id
            captured["documents"] = documents
            captured["context"] = context
            return AgentAnswer(
                content="查到了。",
                iterations=1,
                timed_out=False,
                trace=[],
            )

    worker = RuleAnalysisWorker(skill_agent=StubSkillAgent())

    result = worker.analyze(
        query="查文件",
        events=[{"kind": "voice_transcript", "content": "查文件"}],
        table_id="table-docs",
        documents=[{"filename": "rules.pdf", "size_bytes": 2048}],
    )

    assert result["content"] == "查到了。"
    assert captured["table_id"] == "table-docs"
    assert captured["documents"] == [{"filename": "rules.pdf", "size_bytes": 2048}]
    assert "rules.pdf" in captured["context"]


def test_skill_agent_retries_out_of_scope_refusal_for_external_web_query():
    client = _client_with_responses(
        [
            _response(content="无法执行超出范围的网络搜索请求。"),
            _response(
                content="现在查询。",
                tool_calls=[
                    {
                        "id": "call-web",
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps({"query": "最近特朗普相关新闻"}),
                        },
                    }
                ],
            ),
            _response(content="查到了特朗普新闻摘要。"),
            _response(content="特朗普新闻摘要。"),
        ]
    )
    agent = SkillAgent(dialog_client=client, tool_registry=_web_registry(), max_iterations=5)

    answer = agent.run(query="帮我联网查一下最近特朗普相关的新闻。", context="")

    assert answer.content == "特朗普新闻摘要。"
    assert any(event.get("stage") == "out_of_scope_refusal_retried" for event in answer.trace)
    assert any(event.get("stage") == "tool_executed" and event.get("tool") == "web_search" for event in answer.trace)


def test_skill_agent_summarizer_allows_non_game_web_queries():
    assert "天气" in _SUMMARIZE_SYSTEM_PROMPT
    assert "新闻" in _SUMMARIZE_SYSTEM_PROMPT
    assert "不要把非游戏问题改写成规则分析" in _SUMMARIZE_SYSTEM_PROMPT
    assert "不要使用 Markdown" in _SUMMARIZE_SYSTEM_PROMPT


def test_skill_agent_summary_user_prompt_uses_generic_answer_language():
    captured: dict[str, str] = {}

    class CapturingClient:
        model = "test-model"
        base_url = "http://example.invalid"
        api_key = "test"

        def _request_sender(self, _url, body, _headers, _timeout):
            payload = json.loads(body.decode("utf-8"))
            captured["prompt"] = payload["messages"][0]["content"][0]["text"]
            return _response(content="总结完成。")

        def _parse_text_post_response(self, response_bytes):
            return json.loads(response_bytes.decode("utf-8"))

        def _extract_text(self, response):
            return response["choices"][0]["message"]["content"]

    agent = SkillAgent(dialog_client=CapturingClient(), tool_registry=_registry())
    agent._original_query = "Donald Trump latest news"

    summary = agent._summarize(
        [{"role": "assistant", "content": [{"type": "text", "text": "web search result"}]}],
        "test",
    )

    assert summary == "总结完成。"
    assert "完整的回答结论" in captured["prompt"]
    assert "规则分析结论" not in captured["prompt"]


def test_skill_agent_summary_includes_plain_text_tool_results():
    captured: dict[str, str] = {}

    class CapturingClient:
        model = "test-model"
        base_url = "http://example.invalid"
        api_key = "test"

        def _request_sender(self, _url, body, _headers, _timeout):
            payload = json.loads(body.decode("utf-8"))
            captured["prompt"] = payload["messages"][0]["content"][0]["text"]
            return _response(content="summary complete")

        def _parse_text_post_response(self, response_bytes):
            return json.loads(response_bytes.decode("utf-8"))

        def _extract_text(self, response):
            return response["choices"][0]["message"]["content"]

    agent = SkillAgent(dialog_client=CapturingClient(), tool_registry=_registry())
    agent._original_query = "attention management methods"

    summary = agent._summarize(
        [
            {"role": "assistant", "content": [{"type": "text", "text": "I found a matching file."}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Tool call-read returned:\n"
                            "[read_uploaded_file_excerpt] attention.docx lines 1-2:\n"
                            "1: Focus recovery means sleep, pauses, and fewer context switches."
                        ),
                    }
                ],
            },
        ],
        "test",
    )

    assert summary == "summary complete"
    assert "Focus recovery means sleep" in captured["prompt"]


def test_web_search_tool_schema_allows_non_game_queries():
    registry = ToolRegistry()
    registry.register(build_web_search_tool())
    tool = registry.tool_schemas()[0]

    assert "Use this for web-connected lookup" in tool["description"]
    assert "Prefer Arkham-specific tools first" in tool["description"]
    assert "Donald Trump latest news" in tool["parameters"]["properties"]["query"]["description"]
    assert web_search_module._HTTP_TIMEOUT_SECONDS >= 20
    assert web_search_module._SUBPROCESS_TIMEOUT_SECONDS >= 30
    assert web_search_module._MAX_ATTEMPTS >= 3
    assert web_search_module._METASO_API_URL == "https://metaso.cn/api/v1/chat/completions"


def test_web_search_parses_metaso_chat_sse_stream():
    stream = "\n\n".join(
        [
            'data:{"choices":[{"delta":{"citations":[{"title":"上海天气","link":"https://sh.weather.com.cn/","date":"2026-05-30"}]},"index":0}]}',
            'data:{"choices":[{"delta":{"role":"assistant","content":"明天上海晴到多云，"},"index":0}]}',
            'data:{"choices":[{"delta":{"content":"20℃到31℃。[[1]]"},"index":0}]}',
            'data:{"choices":[{"delta":{"highlights":["<mark>明天 晴到多云 温度:最低20℃ 最高31℃</mark>"]},"index":0}]}',
            'data:{"choices":[{"finish_reason":"stop","delta":{},"index":0}]}',
        ]
    )

    parsed = web_search_module._parse_metaso_chat_sse(stream)

    assert parsed["answer"] == "明天上海晴到多云，20℃到31℃。"
    assert parsed["citations"] == [
        {"title": "上海天气", "link": "https://sh.weather.com.cn/", "date": "2026-05-30"}
    ]
    assert parsed["highlights"] == ["明天 晴到多云 温度:最低20℃ 最高31℃"]
    assert parsed["finish_reason"] == "stop"


def test_web_search_formats_metaso_chat_answer_for_skill_agent():
    formatted = web_search_module._format_metaso_chat_answer(
        {
            "answer": "明天上海晴到多云，20℃到31℃。",
            "citations": [
                {"title": "上海天气", "link": "https://sh.weather.com.cn/", "date": "2026-05-30"},
                {"title": "上海市天气预报", "link": "https://example.com/weather", "date": "2026-05-30"},
            ],
            "highlights": [
                "明天 晴到多云 温度:最低20℃ 最高31℃",
                "明天最高气温30度，最低气温20度。",
            ],
        },
        max_citations=1,
        max_highlights=1,
    )

    assert formatted == (
        "Answer:\n明天上海晴到多云，20℃到31℃。\n\n"
        "Citations:\n"
        "1. 上海天气 (2026-05-30) https://sh.weather.com.cn/\n\n"
        "Highlights:\n"
        "1. 明天 晴到多云 温度:最低20℃ 最高31℃"
    )


def test_web_search_normalizes_spoken_trump_news_query():
    query = "speaker_0：宝子，帮我联网查询一下关于特朗普的最新新闻。"

    assert web_search_module._normalize_query(query) == "特朗普 最新新闻"


def test_web_search_normalizes_relative_weather_query_with_resolved_date(monkeypatch):
    monkeypatch.setattr(web_search_module, "_tomorrow_iso", lambda: "2026-05-25")

    normalized = web_search_module._normalize_query("上海明天天气")

    assert normalized == "上海明天天气"


def test_skill_agent_user_prompt_anchors_relative_dates():
    prompt = _build_user_prompt("明天上海天气", "暂无")

    assert "当前日期：" in prompt
    assert "用户说今天、明天、最近、最新时" in prompt


def test_web_search_repairs_common_utf8_mojibake():
    assert web_search_module._repair_mojibake("ç‰¹æœ—æ™®") == "特朗普"


def test_clean_speakable_summary_strips_markdown_and_emoji():
    cleaned = _clean_speakable_summary("## 📰 标题 **特朗普新闻** - `web_search` 结果 ✅ 🌤️ 2026-05-20")

    assert cleaned == "标题 特朗普新闻 web_search 结果 2026-05-20"
