from gamevoice_server.personal_development import MiniMaxM3CoachingInsightGenerator
from gamevoice_server.personal_development import MiniMaxM3Error


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
        "action_plan": "1. task: Register candidate; owner: Crany; deadline: today",
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
