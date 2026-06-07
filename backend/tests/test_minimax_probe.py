import json

from gamevoice_server.minimax_probe import (
    ProbeVariant,
    _parse_streaming_response,
    build_probe_payload,
    classify_probe_attempt,
    default_variants,
    summarize_probe_attempts,
)


def test_default_variants_cover_anthropic_and_text_post_routes():
    variants = default_variants()

    assert [variant.name for variant in variants] == [
        "anthropic_current",
        "anthropic_900_balanced",
        "anthropic_1200_balanced",
        "text_post_900_stream",
        "text_post_1200_stream",
    ]
    assert {variant.endpoint_mode for variant in variants} == {"anthropic", "text_post"}


def test_build_probe_payload_uses_anthropic_compatible_shape():
    payload = build_probe_payload(
        variant=ProbeVariant(
            name="anthropic_test",
            endpoint_mode="anthropic",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=900,
            temperature=0.4,
            top_p=0.95,
            stream=False,
        ),
        mode="conversation",
        transcript="宝子，给我讲个笑话",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "宝子，给我讲个笑话"}],
    )

    assert payload["model"] == "MiniMax-M2.7-highspeed"
    assert payload["max_tokens"] == 900
    assert payload["temperature"] == 0.4
    assert payload["top_p"] == 0.95
    assert payload["stream"] is False
    assert "system" in payload
    assert payload["messages"][0]["content"][0]["type"] == "text"


def test_build_probe_payload_uses_text_post_shape():
    payload = build_probe_payload(
        variant=ProbeVariant(
            name="text_post_test",
            endpoint_mode="text_post",
            model="MiniMax-M2.7-highspeed",
            max_output_tokens=1200,
            temperature=0.35,
            top_p=0.95,
            stream=True,
        ),
        mode="conversation",
        transcript="解释一下这条规则",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "解释一下这条规则"}],
    )

    assert payload["model"] == "MiniMax-M2.7-highspeed"
    assert payload["max_completion_tokens"] == 1200
    assert "max_tokens" not in payload
    assert payload["temperature"] == 0.35
    assert payload["top_p"] == 0.95
    assert payload["stream"] is True
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert isinstance(payload["messages"][1]["content"], str)


def test_classify_probe_attempt_marks_no_text_when_anthropic_response_has_no_text_block():
    attempt = classify_probe_attempt(
        endpoint_mode="anthropic",
        mode="conversation",
        transcript="解释一下这条规则",
        response={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "stop_reason": "max_tokens",
            "content": [{"type": "thinking", "thinking": "no visible text"}],
        },
    )

    assert attempt["status"] == "no_text"
    assert attempt["stop_reason"] == "max_tokens"
    assert attempt["reply"] is None


def test_classify_probe_attempt_marks_no_text_when_text_post_response_has_empty_content():
    attempt = classify_probe_attempt(
        endpoint_mode="text_post",
        mode="conversation",
        transcript="宝子，给我讲个笑话",
        response={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "thinking only",
                    },
                }
            ]
        },
    )

    assert attempt["status"] == "no_text"
    assert attempt["stop_reason"] == "length"


def test_classify_probe_attempt_marks_truncated_conversation_reply_separately():
    attempt = classify_probe_attempt(
        endpoint_mode="text_post",
        mode="conversation",
        transcript="讲个笑话",
        response={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": '{"source":"minimax","lead":"给你讲一个","tail":"","content":"有个人去算命"}',
                        "reasoning_content": "omitted",
                    },
                }
            ]
        },
    )

    assert attempt["status"] == "conversation_truncated"
    assert attempt["stop_reason"] == "length"


def test_parse_text_post_streaming_response_uses_latest_cumulative_content_only():
    stream_payload = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"{\\"source\\":\\"minimax\\",\\"content\\":\\"第一段"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"{\\"source\\":\\"minimax\\",\\"content\\":\\"第一段第二段\\"}"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    ).encode("utf-8")

    parsed = _parse_streaming_response("text_post", stream_payload)

    assert parsed["choices"][0]["message"]["content"] == '{"source":"minimax","content":"第一段第二段"}'
    assert parsed["choices"][0]["finish_reason"] == "stop"


def test_summarize_probe_attempts_counts_by_variant_and_scenario():
    summary = summarize_probe_attempts(
        [
            {
                "status": "structured_success",
                "mode": "conversation",
                "scenario": "conversation_rule_explain",
                "variant": "anthropic_current",
                "reply": {"content": "A"},
            },
            {
                "status": "plain_text_success",
                "mode": "conversation",
                "scenario": "conversation_joke",
                "variant": "text_post_900_stream",
                "reply": {"content": "B"},
            },
            {
                "status": "no_text",
                "mode": "conversation",
                "scenario": "conversation_rule_explain",
                "variant": "anthropic_current",
                "reply": None,
            },
            {
                "status": "request_error",
                "mode": "conversation",
                "scenario": "conversation_joke",
                "variant": "text_post_900_stream",
                "reply": None,
            },
        ]
    )

    assert summary["total"] == 4
    assert summary["by_status"]["structured_success"] == 1
    assert summary["by_mode"]["conversation"]["total"] == 4
    assert summary["by_scenario"]["conversation_joke"]["total"] == 2
    assert summary["by_variant"]["anthropic_current"]["total"] == 2
    assert summary["by_variant"]["text_post_900_stream"]["by_status"]["request_error"] == 1
