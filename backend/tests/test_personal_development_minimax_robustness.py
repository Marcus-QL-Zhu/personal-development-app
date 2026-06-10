from gamevoice_server.personal_development import MiniMaxM3CoachingInsightGenerator
from gamevoice_server.personal_development import MiniMaxM3Error
from gamevoice_server.personal_development import _format_action_plan
from gamevoice_server.personal_development import _format_coaching_generation


def test_minimax_m3_generator_parses_fenced_json_and_list_action_plan():
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": """```json
{
  "topic": "Database search",
  "content_summary": "Covered boolean search and example mistakes.",
  "action_plan": [
    "Practice one AND OR NOT query.",
    "Confirm the exact system component names."
  ],
  "manager_feedback": "Clear topic, but examples were too thin."
}
```""",
                            "reasoning_content": "hidden reasoning should be ignored",
                        }
                    }
                ],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=lambda *args, **kwargs: FakeResponse(),
    )

    result = generator.generate(employee={"name": "Eva"}, transcript={"text": "short", "segments": []})

    assert result == {
        "topic": "Database search",
        "content_summary": "Covered boolean search and example mistakes.",
        "action_plan": "1. Practice one AND OR NOT query.\n2. Confirm the exact system component names.",
        "manager_feedback": "Clear topic, but examples were too thin.",
    }


def test_minimax_m3_generator_repairs_invalid_json_response_once():
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(
                '{"topic":"Database search","content_summary":"Bad "quoted" JSON",'
                '"action_plan":"","manager_feedback":"Needs examples."}'
            )
        return FakeResponse(
            '{"topic":"Database search","content_summary":"Bad quoted JSON",'
            '"action_plan":"","manager_feedback":"Needs examples."}'
        )

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
    )

    result = generator.generate(employee={"name": "Eva"}, transcript={"text": "short", "segments": []})

    assert len(calls) == 2
    assert "repair" in calls[1]["messages"][0]["content"].lower()
    assert result == {
        "topic": "Database search",
        "content_summary": "Bad quoted JSON",
        "action_plan": "本次未形成明确 Action Plan。",
        "manager_feedback": "Needs examples.",
    }


def test_minimax_m3_generator_regenerates_when_required_fields_are_empty():
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self._content = content

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(
                '{"topic":"待提炼","content_summary":"","action_plan":"","manager_feedback":""}'
            )
        return FakeResponse(
            '{"topic":"Candidate registration flow",'
            '"content_summary":"Explained authorization email, resume handoff, and privacy-law caveats.",'
            '"action_plan":[{"task":"Register candidate","owner":"Crany","deadline":"today"}],'
            '"manager_feedback":"The coach used concrete steps but should slow down near compliance details."}'
        )

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
    )

    result = generator.generate(
        employee={"name": "Crany"},
        transcript={"text": "Candidate registration authorization flow.", "segments": []},
    )

    assert len(calls) == 2
    assert "validation_errors" in calls[1]["messages"][0]["content"]
    assert result == {
        "topic": "Candidate registration flow",
        "content_summary": "Explained authorization email, resume handoff, and privacy-law caveats.",
        "action_plan": "1. Register candidate；负责人：Crany；截止时间：today",
        "manager_feedback": "The coach used concrete steps but should slow down near compliance details.",
    }


def test_minimax_m3_generator_retries_transient_sensitive_or_gateway_errors():
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"topic":"Call confidence coaching",'
                                '"content_summary":"Covered confidence, industry knowledge, and redirecting candidates.",'
                                '"action_plan":"Practice one phone call.",'
                                '"manager_feedback":"Clear diagnosis with practical next steps."}'
                            )
                        }
                    }
                ],
                "base_resp": {"status_code": 0, "status_msg": ""},
            }

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            raise MiniMaxM3Error(
                'HTTP Error 422: Unprocessable Entity: {"message":"input new_sensitive (1026)"}'
            )
        return FakeResponse()

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        post=fake_post,
        retry_delay_seconds=0,
    )

    result = generator.generate(employee={"name": "Crany"}, transcript={"text": "phone call coach", "segments": []})

    assert len(calls) == 2
    assert result["topic"] == "Call confidence coaching"
    assert result["content_summary"]
    assert result["manager_feedback"]


def test_coaching_generation_formats_structured_output_as_plain_text():
    result = _format_coaching_generation(
        {
            "topic": "**ATS registration**",
            "content_summary": "**Flow**: register candidate\n### Risk\nUse authorization.",
            "action_plan": [
                {
                    "task": "Register candidate",
                    "owner": "Crany",
                    "deadline": "today",
                    "acceptance_criteria": "candidate appears in CC",
                },
                {"item": "Send authorization email", "detail": "use secretary flow"},
            ],
            "manager_feedback": "**Good**: used examples.\n- Slow down around compliance.",
        }
    )

    assert result["topic"] == "ATS registration"
    assert result["content_summary"] == "【Flow】\nregister candidate\n【Risk】\nUse authorization."
    assert result["action_plan"] == (
        "1. Register candidate；负责人：Crany；截止时间：today；验收标准：candidate appears in CC\n"
        "2. Send authorization email；说明：use secretary flow"
    )
    assert result["manager_feedback"] == "【Good】\nused examples.\nSlow down around compliance."
    combined = "\n".join(result.values())
    assert "**" not in combined
    assert "{'task'" not in combined
    assert "###" not in combined


def test_action_plan_formats_python_literal_strings_as_plain_text():
    value = (
        "[{'task': 'Practice phone call', 'owner': 'Crany', 'deadline': 'tomorrow'}, "
        "{'task': 'Review industry notes', 'acceptance': 'can explain three facts'}]"
    )

    assert _format_action_plan(value) == (
        "1. Practice phone call；负责人：Crany；截止时间：tomorrow\n"
        "2. Review industry notes；验收标准：can explain three facts"
    )


def test_action_plan_formats_numbered_python_literal_lines_as_plain_text():
    value = (
        "1. {'task': 'Register Siyun', 'detail': 'use English name', 'deadline': 'today'}\n"
        "2. {'task': 'Move candidate to ATS', 'owner': 'Crany', 'acceptance': 'appears in shortlist'}"
    )

    assert _format_action_plan(value) == (
        "1. Register Siyun；说明：use English name；截止时间：today\n"
        "2. Move candidate to ATS；负责人：Crany；验收标准：appears in shortlist"
    )


def test_manager_feedback_splits_semicolon_labeled_sections():
    result = _format_coaching_generation(
        {
            "topic": "Review",
            "content_summary": "Summary",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": (
                "explanation_clarity：The structure was clear but too fast.；"
                "gallup_alignment：Input was supported with examples.；"
                "action_item_clarity：Next steps need owners."
            ),
        }
    )

    assert result["manager_feedback"] == (
        "【讲解清晰度】\nThe structure was clear but too fast.\n\n"
        "【Gallup 沟通适配】\nInput was supported with examples.\n\n"
        "【行动项清晰度】\nNext steps need owners."
    )


def test_manager_feedback_adds_newline_after_inline_section_heading():
    result = _format_coaching_generation(
        {
            "topic": "Review",
            "content_summary": "Summary",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": "【讲解清晰度】优点：框架清楚。\n\n【互动质量】员工有回应。",
        }
    )

    assert result["manager_feedback"] == "【讲解清晰度】\n优点：框架清楚。\n\n【互动质量】\n员工有回应。"


def test_manager_feedback_formats_nested_dict_section_content():
    result = _format_coaching_generation(
        {
            "topic": "Review",
            "content_summary": "Summary",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": (
                "讲解清晰度：{'评分': '中等', '证据': '流程清楚', '不足': '跳跃较多'}；"
                "互动质量：{'证据': '有提问', '建议': '让员工复述'}"
            ),
        }
    )

    assert "{'评分'" not in result["manager_feedback"]
    assert result["manager_feedback"] == (
        "【讲解清晰度】\n评分：中等；证据：流程清楚；不足：跳跃较多\n\n"
        "【互动质量】\n证据：有提问；建议：让员工复述"
    )


def test_manager_feedback_formats_heading_followed_by_dict_content():
    result = _format_coaching_generation(
        {
            "topic": "Review",
            "content_summary": "Summary",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": "【讲解清晰度】\n{'评分': '中等', '证据': '流程清楚'}",
        }
    )

    assert result["manager_feedback"] == "【讲解清晰度】\n评分：中等；证据：流程清楚"


def test_manager_feedback_formats_line_start_chinese_labels_as_sections():
    result = _format_coaching_generation(
        {
            "topic": "Review",
            "content_summary": "Summary",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": (
                "讲解清晰度：经理讲得清楚。\n\n"
                "Gallup 沟通适配：适合搜集优势。\n\n"
                "行动项清晰度：有验收标准。"
            ),
        }
    )

    assert result["manager_feedback"] == (
        "【讲解清晰度】\n经理讲得清楚。\n\n"
        "【Gallup 沟通适配】\n适合搜集优势。\n\n"
        "【行动项清晰度】\n有验收标准。"
    )
