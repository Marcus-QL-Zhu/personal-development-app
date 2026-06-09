from gamevoice_server.personal_development import MiniMaxM3CoachingInsightGenerator


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
