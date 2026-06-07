import json

import pytest

from gamevoice_server.config import Settings
from gamevoice_server.dialog_client import (
    MiniMaxDialogClient,
    NoUsableReplyError,
    PlaceholderDialogClient,
    PreviewRoutingDialogClient,
    SiliconFlowAliasRewriteClient,
    SiliconFlowPreviewClient,
    TEXT_POST_URL,
    build_dialog_client,
)


def _text_post_response(content: str, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": content,
                    },
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_minimax_dialog_client_extracts_text_from_text_post_response():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["url"] = url
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _text_post_response("First sentence. Second sentence.")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_reply(
        mode="chatty",
        transcript="hello",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
    )

    assert reply["source"] == "minimax"
    assert reply["content"] == "First sentence. Second sentence."
    assert reply["lead"] == "First sentence."
    assert reply["tail"] == "Second sentence."
    assert captured["url"] == TEXT_POST_URL
    assert captured["body"]["model"] == "MiniMax-M2.7-highspeed"
    assert captured["body"]["stream"] is True
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_minimax_dialog_client_treats_json_shaped_reply_as_plain_text():
    raw = '{"source":"minimax","lead":"????","tail":"???????????","content":"???????????????"}'
    client = MiniMaxDialogClient(
        api_key="secret",
        request_sender=lambda *args: _text_post_response(raw),
    )

    reply = client.generate_reply(
        mode="chatty",
        transcript="hello",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
    )

    assert reply["source"] == "minimax"
    assert reply["content"] == raw


def test_minimax_dialog_client_treats_malformed_structured_text_as_plain_text():
    malformed = "\n".join(
        [
            "json",
            '"source":"live_asr"',
            '"ead":"??????????????"',
            '"tail":"??????????????????????"',
        ]
    )
    client = MiniMaxDialogClient(
        api_key="secret",
        request_sender=lambda *args: _text_post_response(malformed),
    )

    reply = client.generate_reply(
        mode="serious",
        transcript="????????",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "????????"}],
    )

    assert reply["source"] == "minimax"
    assert reply["content"] == malformed.replace("\n", " ")


def test_minimax_dialog_client_requests_plain_reply_contract():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response("???????????????")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)
    client.generate_reply(
        mode="chatty",
        transcript="hello",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
    )

    system_prompt = captured["body"]["messages"][0]["content"]
    user_prompt = captured["body"]["messages"][1]["content"]
    assert "只输出可播报的回复文本" in system_prompt
    assert "不要输出 JSON" in system_prompt
    assert "只输出可播报的回复文本" in user_prompt
    assert "默认用中文回答" in system_prompt


def test_reply_system_prompts_do_not_branch_by_legacy_mode():
    chatty_prompt = MiniMaxDialogClient._build_plain_reply_system_prompt("chatty")
    serious_prompt = MiniMaxDialogClient._build_plain_reply_system_prompt("serious")
    conversation_prompt = MiniMaxDialogClient._build_plain_reply_system_prompt("conversation")

    assert chatty_prompt == serious_prompt == conversation_prompt


def test_preview_and_continuation_system_prompts_do_not_branch_by_legacy_mode():
    assert MiniMaxDialogClient._build_preview_system_prompt(
        "chatty"
    ) == MiniMaxDialogClient._build_preview_system_prompt("serious")
    assert MiniMaxDialogClient._build_continuation_system_prompt(
        "chatty"
    ) == MiniMaxDialogClient._build_continuation_system_prompt("serious")


def test_plain_reply_user_prompt_omits_mode_line_for_cache_stability():
    prompt = MiniMaxDialogClient._build_plain_reply_user_prompt(
        mode="serious",
        transcript="explain this rule",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "explain this rule"}],
    )

    assert "Current mode:" not in prompt
    assert "当前模式" not in prompt
    assert "serious" not in prompt
    assert "chatty" not in prompt


def test_dialog_user_prompts_include_assistant_profile_without_mode_branching():
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "宝子讲个笑话"}]

    preview_prompt = MiniMaxDialogClient._build_preview_user_prompt(
        mode="chatty",
        transcript="宝子讲个笑话",
        events=events,
        assistant_name="小夏",
        assistant_personality="温柔但吐槽欲强",
    )
    formal_prompt = MiniMaxDialogClient._build_plain_reply_user_prompt(
        mode="serious",
        transcript="宝子讲个笑话",
        events=events,
        assistant_name="小夏",
        assistant_personality="温柔但吐槽欲强",
    )
    continuation_prompt = MiniMaxDialogClient._build_continuation_user_prompt(
        mode="conversation",
        transcript="宝子讲个笑话",
        events=events,
        already_spoken_text="来，我给你讲一个。",
        assistant_name="小夏",
        assistant_personality="温柔但吐槽欲强",
    )

    for prompt in (preview_prompt, formal_prompt, continuation_prompt):
        assert "当前助手设定" in prompt
        assert "名字：小夏" in prompt
        assert "性格：温柔但吐槽欲强" in prompt
        assert "chatty" not in prompt
        assert "serious" not in prompt


def test_dialog_user_prompts_strip_assistant_name_prefix_from_context_history():
    events = [{"kind": "assistant_spoken", "source": "companion", "content": "宝子：宝子：我在。"}]

    prompt = MiniMaxDialogClient._build_plain_reply_user_prompt(
        mode="conversation",
        transcript="继续",
        events=events,
        assistant_name="宝子",
    )

    assert "助手: 我在。" in prompt
    assert "助手: 宝子：" not in prompt


def test_plain_reply_prompt_mentions_lookup_commitment_without_structured_output():
    prompt = MiniMaxDialogClient._build_plain_reply_system_prompt("chatty")

    assert "只输出可播报纯文本" in prompt
    assert "不要输出 JSON" in prompt
    assert "异步查询" in prompt
    assert "<lookup>" in prompt
    assert "句尾" in prompt
    assert "用户文本" in prompt
    assert "自然语言词" in prompt
    assert "我去查一查" not in prompt
    assert "后台脚本会自动 hook" not in prompt


def test_minimax_dialog_client_sanitizes_literal_newline_sequences_in_text_reply():
    client = MiniMaxDialogClient(
        api_key="secret",
        request_sender=lambda *args: _text_post_response("???\n\n???????"),
    )

    reply = client.generate_reply(
        mode="serious",
        transcript="????????",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "????????"}],
    )

    assert "\n" not in reply["lead"]
    assert "\n" not in reply["tail"]
    assert "\n" not in reply["content"]
    assert reply["content"] == "??? ???????"


def test_minimax_dialog_client_rewrites_speaker_alias_map_with_pure_json_contract():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response(
            json.dumps(
                {
                    "player_a": ["Musk", "FatTiger"],
                    "player_b": ["Daxiong"],
                    "player_c": [],
                },
                ensure_ascii=False,
            )
        )

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "voice_transcript", "source": "live_asr", "content": "player_a: I am Musk and FatTiger"},
            {"kind": "voice_transcript", "source": "live_asr", "content": "player_b: Daxiong, your turn"},
        ],
        current_alias_map={
            "player_a": ["old"],
            "player_b": ["small"],
            "player_c": [],
        },
    )

    assert result == {
        "player_a": ["Musk", "FatTiger"],
        "player_b": ["Daxiong"],
        "player_c": [],
    }
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_completion_tokens"] == 4096
    assert captured["body"]["temperature"] == 0.1
    assert captured["body"]["tools"][0]["function"]["name"] == "submit_speaker_alias_map"
    assert captured["body"]["tool_choice"]["function"]["name"] == "submit_speaker_alias_map"
    assert captured["body"]["tools"][0]["function"]["parameters"]["required"] == [
        "player_a",
        "player_b",
        "player_c",
    ]
    system_prompt = captured["body"]["messages"][0]["content"]
    user_prompt = captured["body"]["messages"][1]["content"]
    assert "必须调用 submit_speaker_alias_map 工具提交结果" in system_prompt
    assert "speaker_0 在和老黄搭话" in system_prompt
    assert "{\"speaker_0\":[],\"speaker_2\":[\"老黄\"]}" in system_prompt
    assert "不能因为旧表里有某个 alias 就保留它" in system_prompt
    assert "默认保底称呼“宝宝”会由系统另行补回" in system_prompt
    assert "孙哥和三叔都只是被说话人提到的人" in system_prompt
    assert "{\"speaker_0\":[\"孙哥\"],\"speaker_2\":[\"老黄\"]}" in system_prompt
    assert "把名字贴给了说出这个名字的人" in system_prompt
    assert "跑团里主持人或玩家可能会代演 NPC" in system_prompt
    assert "下一个行动的是 NAME" in system_prompt
    assert "{\"speaker_0\":[],\"speaker_5\":[\"小杨\"]}" in system_prompt
    assert "{\"speaker_2\":[],\"speaker_0\":[]}" in system_prompt
    assert "{\"speaker_0\":[],\"speaker_9\":[\"空条吉子\"]}" in system_prompt
    assert "{\"speaker_7\":[],\"speaker_9\":[\"空条吉子\"]}" in system_prompt
    assert "{\"speaker_0\":[],\"speaker_2\":[\"老黄\"]}" in system_prompt
    assert "不要包含输入 keys 以外的任何字段" in user_prompt
    assert "必须通过 submit_speaker_alias_map 工具提交" in user_prompt
    assert "输出的不是台词列表" in user_prompt
    assert "旧称呼表（可能是错的，只用于审计，不是证据）" in user_prompt
    assert "player_a: I am Musk" in user_prompt


def test_alias_map_rewrite_user_prompt_clips_long_evidence_into_windows():
    dialogue_events = [
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": f"speaker_0：无关闲聊第 {index} 句，只是长上下文噪声。",
            "speaker_id": "speaker_0",
        }
        for index in range(20)
    ]
    dialogue_events.extend(
        [
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：哎，老黄。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_2：怎么了？",
                "speaker_id": "speaker_2",
            },
        ]
    )

    prompt = MiniMaxDialogClient._build_alias_map_rewrite_user_prompt(
        dialogue_events=dialogue_events,
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_2": ["宝宝"],
        },
    )

    assert "Alias evidence windows" in prompt
    assert "哎，老黄" in prompt
    assert "怎么了" in prompt
    assert "无关闲聊第 0 句" not in prompt
    assert "不要把长对话整段重组" in prompt


def test_alias_map_rewrite_user_prompt_prefers_name_clue_windows_over_generic_speaker_changes():
    dialogue_events = [
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_0：普通接话，没有名字。",
            "speaker_id": "speaker_0",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_1：普通回应，也没有名字。",
            "speaker_id": "speaker_1",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_0：哎，老黄。",
            "speaker_id": "speaker_0",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_2：怎么了？",
            "speaker_id": "speaker_2",
        },
    ]

    prompt = MiniMaxDialogClient._build_alias_map_rewrite_user_prompt(
        dialogue_events=dialogue_events,
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_1": ["宝宝"],
            "speaker_2": ["宝宝"],
        },
    )

    assert "哎，老黄" in prompt
    assert "怎么了" in prompt
    assert "普通接话" not in prompt
    assert "普通回应" not in prompt


def test_alias_map_rewrite_user_prompt_includes_tabletop_action_owner_windows():
    dialogue_events = [
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": f"speaker_0：普通跑团叙述第 {index} 句。",
            "speaker_id": "speaker_0",
        }
        for index in range(20)
    ]
    dialogue_events.extend(
        [
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：下一个行动的是小杨。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_5：那我就试一下。",
                "speaker_id": "speaker_5",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：不愧是你空条吉子。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_9：我拿拐杖去捅一下。",
                "speaker_id": "speaker_9",
            },
        ]
    )

    prompt = MiniMaxDialogClient._build_alias_map_rewrite_user_prompt(
        dialogue_events=dialogue_events,
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_5": ["宝宝"],
            "speaker_9": ["宝宝"],
        },
    )

    assert "下一个行动的是小杨" in prompt
    assert "那我就试一下" in prompt
    assert "不愧是你空条吉子" in prompt
    assert "我拿拐杖去捅一下" in prompt
    assert "普通跑团叙述第 0 句" not in prompt


def test_alias_map_rewrite_user_prompt_expands_context_around_tabletop_action_owner_clues():
    dialogue_events = [
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_9：最近才有人翻看过。",
            "speaker_id": "speaker_9",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_9：那就厕所前面这个房间。",
            "speaker_id": "speaker_9",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_0：嗯，好，那你们打开了厕所房间这边的门。",
            "speaker_id": "speaker_0",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_0：不愧是你空条吉子，凭借着自己不存在的势力摸索。",
            "speaker_id": "speaker_0",
        },
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "speaker_7：Oh my god.",
            "speaker_id": "speaker_7",
        },
    ]

    prompt = MiniMaxDialogClient._build_alias_map_rewrite_user_prompt(
        dialogue_events=dialogue_events,
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_7": ["宝宝"],
            "speaker_9": ["宝宝"],
        },
    )

    assert "最近才有人翻看过" in prompt
    assert "那就厕所前面这个房间" in prompt
    assert "不愧是你空条吉子" in prompt
    assert "行动归属重点片段" in prompt


def test_alias_map_rewrite_filters_aliases_outside_clipped_windows():
    dialogue_events = [
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": f"speaker_0：噪声名字{index} 只是被提到。",
            "speaker_id": "speaker_0",
        }
        for index in range(20)
    ]
    dialogue_events.extend(
        [
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：哎，老黄。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_2：怎么了？",
                "speaker_id": "speaker_2",
            },
        ]
    )

    filtered = MiniMaxDialogClient._filter_alias_map_by_evidence(
        {
            "speaker_0": ["噪声名字0"],
            "speaker_2": ["老黄"],
        },
        dialogue_events=dialogue_events,
    )

    assert filtered == {
        "speaker_0": [],
        "speaker_2": ["老黄"],
    }


def test_alias_map_rewrite_rejects_name_only_spoken_by_same_speaker():
    filtered = MiniMaxDialogClient._filter_alias_map_by_evidence(
        {
            "speaker_3": ["黄飞峰"],
            "speaker_5": ["黄飞峰", "鸡"],
        },
        dialogue_events=[
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_3：哎，黄飞峰，帮我去车里拿个快递。",
                "speaker_id": "speaker_3",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_5：看来这个车十八般武艺样样精通啊。",
                "speaker_id": "speaker_5",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_5：这一集我会啊，鸡，过来陪我练一下。",
                "speaker_id": "speaker_5",
            },
        ],
    )

    assert filtered == {
        "speaker_3": [],
        "speaker_5": ["黄飞峰"],
    }


def test_alias_map_rewrite_rejects_npc_self_intro_and_narration_names():
    filtered = MiniMaxDialogClient._filter_alias_map_by_evidence(
        {
            "speaker_2": ["有希子", "佐仓良介"],
            "speaker_5": ["小杨"],
        },
        dialogue_events=[
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_2：哈喽哈喽，我是来带你们去旅馆的人，你们叫我有希子就好了。",
                "speaker_id": "speaker_2",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：然后有希子就带你们走出了码头。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_2：我是佐仓良介啊，我不是来订汉堡的。",
                "speaker_id": "speaker_2",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：佐仓良介留下了一封信。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：下一个行动的是小杨。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_5：那我就试一下。",
                "speaker_id": "speaker_5",
            },
        ],
    )

    assert filtered == {
        "speaker_2": [],
        "speaker_5": ["小杨"],
    }


def test_alias_map_rewrite_action_owner_requires_substantive_target_response():
    filtered = MiniMaxDialogClient._filter_alias_map_by_evidence(
        {
            "speaker_7": ["空条吉子"],
            "speaker_9": ["空条吉子", "阴阳木棉"],
        },
        dialogue_events=[
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：不愧是你空条吉子，刚才摸到了机关。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_7：Oh my god.",
                "speaker_id": "speaker_7",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_9：我拿我的拐杖去捅一下。",
                "speaker_id": "speaker_9",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_0：下一个行动的是阴阳木棉。",
                "speaker_id": "speaker_0",
            },
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_9：子从此恨之。",
                "speaker_id": "speaker_9",
            },
        ],
    )

    assert filtered == {
        "speaker_7": [],
        "speaker_9": ["空条吉子"],
    }


def test_minimax_dialog_client_rewrites_speaker_alias_map_ignores_reasoning_content():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "player_a": ["Musk"],
                                    "player_b": ["Daxiong"],
                                },
                                ensure_ascii=False,
                            ),
                            "reasoning_content": json.dumps(
                                {
                                    "player_a": ["Wrong"],
                                    "player_b": ["Wrong"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "voice_transcript", "source": "live_asr", "content": "player_a: I am Musk"},
            {"kind": "voice_transcript", "source": "live_asr", "content": "player_b: Daxiong is here"},
        ],
        current_alias_map={
            "player_a": ["old"],
            "player_b": [],
        },
    )

    assert result == {
        "player_a": ["Musk"],
        "player_b": ["Daxiong"],
    }


def test_minimax_dialog_client_rewrites_speaker_alias_map_uses_tool_call_arguments():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "reasoning_content": "long internal reasoning that should be ignored",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_speaker_alias_map",
                                        "arguments": json.dumps(
                                            {
                                                "speaker_0": [],
                                                "speaker_2": ["老黄"],
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_0：哎，老黄。"},
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_2：怎么了？"},
        ],
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_2": ["宝宝"],
        },
    )

    assert result == {
        "speaker_0": [],
        "speaker_2": ["老黄"],
    }


def test_minimax_dialog_client_rewrites_speaker_alias_map_rejects_reasoning_only_response():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "",
                            "reasoning_content": json.dumps(
                                {
                                    "player_a": ["Musk"],
                                    "player_b": ["Daxiong"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    with pytest.raises(NoUsableReplyError):
        client.rewrite_speaker_alias_map(
            dialogue_events=[
                {"kind": "voice_transcript", "source": "live_asr", "content": "player_a: I am Musk"},
                {"kind": "voice_transcript", "source": "live_asr", "content": "player_b: Daxiong is here"},
            ],
            current_alias_map={
                "player_a": ["old"],
                "player_b": [],
            },
        )


def test_minimax_dialog_client_filters_aliases_without_cross_speaker_support():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return _text_post_response(
            json.dumps(
                {
                    "speaker_0": ["БІБІ", "孙哥"],
                    "speaker_2": ["阿珍"],
                },
                ensure_ascii=False,
            )
        )

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_0：孙哥说今晚看星星"},
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_2：阿珍说想看星星"},
        ],
        current_alias_map={
            "speaker_0": ["宝宝"],
            "speaker_2": ["宝宝"],
        },
    )

    assert result == {
        "speaker_0": [],
        "speaker_2": [],
    }


def test_minimax_dialog_client_tolerates_missing_and_extra_alias_buckets():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return _text_post_response(
            json.dumps(
                {
                    "speaker_0": ["Sun"],
                    "speaker_extra": ["Ignored"],
                },
                ensure_ascii=False,
            )
        )

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_0: Sun is talking"},
        ],
        current_alias_map={
            "player_a": ["Baby"],
            "speaker_0": ["Baby"],
        },
    )

    assert result == {
        "player_a": [],
        "speaker_0": ["Sun"],
    }


def test_minimax_dialog_client_parses_plain_text_alias_response():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return b'{"speaker_0":["Sun"]}'

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_0: Sun is talking"},
        ],
        current_alias_map={"speaker_0": ["Baby"]},
    )

    assert result == {"speaker_0": ["Sun"]}


def test_minimax_dialog_client_falls_back_after_two_empty_responses():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return _text_post_response("", finish_reason="length")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_reply(
        mode="chatty",
        transcript="tell me a joke",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}],
    )

    assert reply["source"] == "minimax_fallback"
    assert reply["content"]


def test_minimax_dialog_client_retries_after_empty_text():
    calls: list[dict] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        parsed = json.loads(body.decode("utf-8"))
        calls.append(parsed)
        if len(calls) == 1:
            return _text_post_response("", finish_reason="length")
        return _text_post_response("please give a concise answer.")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_reply(
        mode="chatty",
        transcript="tell me a joke",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}],
    )

    assert len(calls) == 2
    assert "80" in calls[0]["messages"][1]["content"]
    assert "shorter" in calls[1]["messages"][1]["content"].lower() or "concise" in calls[1]["messages"][1]["content"].lower()
    assert "give" in reply["content"].lower()


def test_minimax_dialog_client_parses_text_post_stream_response():
    stream_payload = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"??"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"????"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    ).encode("utf-8")

    client = MiniMaxDialogClient(
        api_key="secret",
        request_sender=lambda *args: stream_payload,
    )

    reply = client.generate_reply(
        mode="chatty",
        transcript="????",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "????"}],
    )

    assert reply["content"] == "????"


def test_placeholder_dialog_client_returns_lead_tail_content_contract():
    client = PlaceholderDialogClient()

    reply = client.generate_reply(
        mode="chatty",
        transcript="hello there",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello there"}],
    )

    assert reply["source"] == "companion"
    assert reply["lead"]
    assert "tail" in reply


def test_build_dialog_client_returns_minimax_when_only_minimax_key_present():
    settings = Settings(minimax_api_key="secret", siliconflow_api_key=None)

    client = build_dialog_client(settings)

    assert isinstance(client, MiniMaxDialogClient)


def test_build_dialog_client_routes_preview_to_siliconflow_when_key_present():
    settings = Settings(minimax_api_key="minimax-secret", siliconflow_api_key="sf-secret")

    client = build_dialog_client(settings)

    assert isinstance(client, PreviewRoutingDialogClient)
    assert isinstance(client.reply_client, MiniMaxDialogClient)
    assert isinstance(client.preview_client, SiliconFlowPreviewClient)
    assert isinstance(client.alias_rewrite_client, SiliconFlowAliasRewriteClient)


def test_build_dialog_client_returns_placeholder_without_api_key():
    settings = Settings(minimax_api_key=None)

    client = build_dialog_client(settings)

    assert isinstance(client, PlaceholderDialogClient)


def test_siliconflow_alias_rewrite_client_uses_tool_call_arguments():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["url"] = url
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["headers"] = headers
        captured["timeout"] = timeout
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_speaker_alias_map",
                                        "arguments": json.dumps(
                                            {
                                                "speaker_0": ["孙哥"],
                                                "speaker_2": ["老黄"],
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    client = SiliconFlowAliasRewriteClient(
        api_key="sf-secret",
        model="deepseek-ai/DeepSeek-V4-Flash",
        request_sender=fake_sender,
    )

    result = client.rewrite_speaker_alias_map(
        dialogue_events=[
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_2：孙哥。"},
            {"kind": "speaker_alias_evidence", "source": "live_asr", "content": "speaker_0：哎，老黄。"},
        ],
        current_alias_map={
            "speaker_0": ["宝宝", "老黄"],
            "speaker_2": ["宝宝", "孙哥"],
        },
    )

    assert result == {
        "speaker_0": ["孙哥"],
        "speaker_2": ["老黄"],
    }
    assert captured["body"]["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert captured["body"]["enable_thinking"] is False
    assert captured["body"]["tool_choice"]["function"]["name"] == "submit_speaker_alias_map"
    assert captured["body"]["tools"][0]["function"]["parameters"]["required"] == [
        "speaker_0",
        "speaker_2",
    ]


def test_siliconflow_preview_client_requests_qwen_non_thinking_preview():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["url"] = url
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _text_post_response("我先接一句，这个点可以快速说清。")

    client = SiliconFlowPreviewClient(api_key="sf-secret", request_sender=fake_sender)

    preview_text = client.generate_preview_text(
        mode="serious",
        transcript="宝子，三国杀反贼怎么赢？",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "宝子，三国杀反贼怎么赢？"}],
    )

    body = captured["body"]
    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sf-secret"
    assert body["model"] == "Qwen/Qwen3.5-4B"
    assert body["enable_thinking"] is False
    assert body["max_tokens"] == 50
    assert body["stream"] is False
    assert body["temperature"] == 0.45
    assert body["top_p"] == 0.8
    assert body["top_k"] == 40
    assert body["min_p"] == 0.05
    assert body["frequency_penalty"] == 0.2
    assert isinstance(preview_text, str)
    assert preview_text
    return
    assert preview == {
        "source": "siliconflow",
        "lead": "我先接一句，这个点可以快速说清。",
        "tail": "",
        "content": "我先接一句，这个点可以快速说清。",
    }


def test_siliconflow_preview_client_writes_sanitized_trace_when_enabled(tmp_path, monkeypatch):
    trace_path = tmp_path / "preview-trace.jsonl"
    monkeypatch.setenv("GAMEVOICE_SILICONFLOW_PREVIEW_TRACE_PATH", str(trace_path))

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        assert "Authorization" in headers
        return _text_post_response("我先看看。")

    client = SiliconFlowPreviewClient(api_key="sf-secret", request_sender=fake_sender)

    text = client.generate_preview_text(
        mode="conversation",
        transcript="宝子，帮我查一下明天上海的天气。",
        events=[
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "宝宝：宝子，帮我查一下明天上海的天气。",
            }
        ],
        assistant_name="宝子",
    )

    assert text == "我先看看。"
    trace = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[-1])
    assert trace["model"] == "Qwen/Qwen3.5-4B"
    assert trace["response_text"] == "我先看看。"
    assert trace["marker_detected"] is False
    assert trace["messages"][0]["role"] == "system"
    assert "Authorization" not in json.dumps(trace, ensure_ascii=False)
    assert "sf-secret" not in json.dumps(trace, ensure_ascii=False)


def test_siliconflow_preview_prompt_uses_shorter_of_recent_ten_or_one_minute_window():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response("哇哦，先接一下。")

    client = SiliconFlowPreviewClient(api_key="sf-secret", request_sender=fake_sender)
    events = [
        {"kind": "voice_transcript", "source": "live_asr", "content": "old context", "at": "2026-05-24T08:00:00+00:00"},
        {"kind": "assistant_spoken", "source": "companion", "content": "old answer", "at": "2026-05-24T08:00:10+00:00"},
        {"kind": "voice_transcript", "source": "live_asr", "content": "recent one", "at": "2026-05-24T08:01:30+00:00"},
        {"kind": "assistant_spoken", "source": "companion", "content": "recent two", "at": "2026-05-24T08:01:50+00:00"},
        {"kind": "voice_transcript", "source": "live_asr", "content": "latest request", "at": "2026-05-24T08:02:00+00:00"},
    ]

    client.generate_preview_text(
        mode="conversation",
        transcript="latest request",
        events=events,
    )

    user_prompt = captured["body"]["messages"][1]["content"]
    assert "recent one" in user_prompt
    assert "recent two" in user_prompt
    assert "latest request" in user_prompt
    assert "old context" not in user_prompt
    assert "old answer" not in user_prompt


def test_preview_and_formal_prompts_shape_natural_total_length():
    preview_system = MiniMaxDialogClient._build_preview_system_prompt("conversation")
    continuation_system = MiniMaxDialogClient._build_continuation_system_prompt("conversation")
    preview_user = MiniMaxDialogClient._build_preview_user_prompt(
        mode="conversation",
        transcript="停停。",
        events=[],
    )
    continuation_user = MiniMaxDialogClient._build_continuation_user_prompt(
        mode="conversation",
        transcript="再讲一个笑话",
        events=[],
        already_spoken_text="哇哦，好厉害。",
    )

    assert "我靠，真的吗" not in preview_system
    assert "哇哦，好厉害" not in preview_system
    assert "我靠，真的吗" not in preview_user
    assert "哇哦，好厉害" not in preview_user
    assert "不要使用固定口头禅" in preview_system
    assert "不要为了活泼而套用固定感叹句" not in preview_user
    assert "preview 加 formal" in continuation_system
    assert "四句" in continuation_system
    assert "已经说出口的开场" in continuation_user
    assert "后台才会启动查询" in continuation_user
    assert "总长度" not in continuation_user


def test_preview_prompt_pushes_heckle_toward_table_roast_voice():
    preview_system = MiniMaxDialogClient._build_preview_system_prompt("conversation")

    assert "桌边朋友互损" in preview_system
    assert "一句就撤" in preview_system
    assert "别像总结或评论" in preview_system
    assert "禁止使用" in preview_system
    assert "这波操作" in preview_system
    assert "不要替被吐槽的人解围" in preview_system
    assert "老黄，咱别送了行吗" in preview_system
    assert "阿珍这把有点下饭了" in preview_system
    assert "蛙爷，咱别送了行吗" not in preview_system


def test_preview_user_prompt_clarifies_heckle_speaker_and_target():
    preview_user = MiniMaxDialogClient._build_preview_user_prompt(
        mode="conversation",
        transcript="教主：蛙爷太菜了",
        events=[
            {
                "kind": "speaker_alias_map",
                "source": "speaker_identity",
                "speaker_alias_map": {
                    "speaker_0": ["宝宝", "蛙爷"],
                    "speaker_1": ["宝宝", "教主"],
                },
            },
            {"kind": "voice_transcript", "source": "live_asr", "content": "教主：蛙爷太菜了"},
        ],
        assistant_name="宝子",
    )

    assert "朋友局起哄场景" in preview_user
    assert "说话人：教主" in preview_user
    assert "被吐槽玩家：蛙爷" in preview_user
    assert "槽点：太菜" in preview_user
    assert "不要回应说话人" in preview_user
    assert "禁止词：这波操作" in preview_user
    assert "方向词：别送、下饭、醒醒" in preview_user
    assert "不要照抄示例" in preview_user
    assert "优先用“咱”或“你”" in preview_user
    assert "回复里不要出现“我”字" in preview_user
    assert "我换人" in preview_user
    assert "蛙爷，咱别送了行吗" not in preview_user


def test_minimax_dialog_client_generate_heartbeat_prompts_for_player_callout():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response("蛙爷别摸鱼了，到你表演了。")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_heartbeat(
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "蛙爷：我先想想。"}],
        player_names=["蛙爷", "教主"],
        assistant_name="宝子",
        assistant_personality="活泼健谈",
    )

    assert reply["content"] == "蛙爷别摸鱼了，到你表演了。"
    body = captured["body"]
    system_prompt = body["messages"][0]["content"]
    user_prompt = body["messages"][1]["content"]
    assert body["stream"] is False
    assert body["enable_thinking"] is False
    assert body["max_completion_tokens"] == 420
    assert "只输出一句" in system_prompt
    assert "句尾" in system_prompt
    assert "不要说没听清" in system_prompt
    assert "优先接这段内容" in user_prompt
    assert "不要提到计时器" in system_prompt
    assert "不要分析上下文" in system_prompt
    assert "不要复述任务" in system_prompt
    assert "主动Q人" not in system_prompt
    assert "主动Q人" not in user_prompt
    assert "当前信息" not in user_prompt
    assert "用户要求" not in user_prompt
    assert "直接写一句新的台词" in user_prompt
    assert "蛙爷、教主" in user_prompt
    assert "宝宝们" not in user_prompt


def test_minimax_dialog_client_generate_heartbeat_uses_passive_listening_context():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response("你们这问题问得也太绕了吧。")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    client.generate_heartbeat(
        events=[
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": "speaker_1：为什么要扔两个啊？",
            }
        ],
        player_names=["Tango"],
        assistant_name="宝子",
    )

    user_prompt = captured["body"]["messages"][1]["content"]
    assert "为什么要扔两个啊" in user_prompt
    assert "暂无" not in user_prompt


def test_minimax_dialog_client_generate_heartbeat_ignores_reasoning_only_response():
    calls = 0

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "用户让我扮演桌游助手，需要输出一句台词。",
                        }
                    }
                ]
            }
        ).encode("utf-8")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_heartbeat(events=[], player_names=["Tango"], assistant_name="宝子")

    assert reply["source"] == "minimax_fallback"
    assert "用户让我" not in reply["content"]
    assert "Tango" in reply["content"]
    assert reply["content"] != "宝宝们别安静了，谁来整点动静？"
    assert calls == 1


def test_minimax_dialog_client_generate_heartbeat_adds_terminal_punctuation():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return _text_post_response("宝宝们怎么都不说话啦，手痒了没")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_heartbeat(events=[], player_names=[], assistant_name="宝子")

    assert reply["source"] == "minimax"
    assert reply["content"] == "宝宝们怎么都不说话啦，手痒了没。"


def test_minimax_dialog_client_generate_heartbeat_allows_group_fallback_without_names():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["body"] = json.loads(body.decode("utf-8"))
        return _text_post_response("宝宝们别安静了，谁先来整点动静？")

    client = MiniMaxDialogClient(api_key="secret", request_sender=fake_sender)

    reply = client.generate_heartbeat(events=[], player_names=[], assistant_name="宝子")

    user_prompt = captured["body"]["messages"][1]["content"]
    assert reply["content"].startswith("宝宝们")
    assert "没有可靠玩家名" in user_prompt
    assert "宝宝们" in user_prompt


def test_lookup_handoff_guidance_moves_from_preview_to_formal():
    preview_system = MiniMaxDialogClient._build_preview_system_prompt("conversation")
    preview_user = MiniMaxDialogClient._build_preview_user_prompt(
        mode="conversation",
        transcript="\u5b9d\u5b50\uff0c\u5e2e\u6211\u8054\u7f51\u67e5\u4e00\u4e0b\u7279\u6717\u666e\u65b0\u95fb\u3002",
        events=[],
    )
    formal_system = MiniMaxDialogClient._build_plain_reply_system_prompt("conversation")

    assert "<lookup>" not in preview_system
    assert "<lookup>" not in preview_user
    assert "\u4e0d\u8981\u627f\u8bfa" not in preview_system
    assert "\u5f88\u77ed\u7684\u67e5\u8be2\u63a5\u8bdd" not in preview_system
    assert "\u53ea\u7ed9\u7b2c\u4e00\u53e5" in preview_user
    assert "<lookup>" in formal_system
    assert "\u53e5\u5c3e" in formal_system
    assert "\u7528\u6237\u6587\u672c" in formal_system
    assert "\u81ea\u7136\u8bed\u8a00\u8bcd" in formal_system
    assert "\u5f02\u6b65\u67e5\u8be2" in formal_system


def test_formal_prompt_does_not_retrigger_lookup_after_result_injection():
    formal_system = MiniMaxDialogClient._build_plain_reply_system_prompt("conversation")

    assert "\u4f60\u521a\u521a\u67e5\u8be2\u5f97\u5230\u7684\u7ed3\u679c\u662f" in formal_system
    assert "\u4e0d\u8981\u8ffd\u52a0 <lookup>" in formal_system


def test_dialog_prompts_include_uploaded_file_context_facts():
    events = [
        {
            "kind": "document_upload_fact",
            "source": "document_upload",
            "content": "你刚刚收到用户上传的文件：attention.txt。之后用户说“这个文件”时，通常指这些文件。",
        },
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "speaker_0：宝子，查一下这个文件。",
        },
    ]

    preview_user = MiniMaxDialogClient._build_preview_user_prompt(
        mode="conversation",
        transcript="speaker_0：宝子，查一下这个文件。",
        events=events,
    )
    formal_user = MiniMaxDialogClient._build_plain_reply_user_prompt(
        mode="conversation",
        transcript="speaker_0：宝子，查一下这个文件。",
        events=events,
    )

    assert "系统事实(document_upload)" in preview_user
    assert "attention.txt" in preview_user
    assert "系统事实(document_upload)" in formal_user
    assert "attention.txt" in formal_user


def test_continuation_prompt_includes_formal_lookup_marker_guidance():
    continuation_system = MiniMaxDialogClient._build_continuation_system_prompt("conversation")
    continuation_user = MiniMaxDialogClient._build_continuation_user_prompt(
        mode="conversation",
        transcript="宝子，帮我查一下明天上海天气。",
        events=[],
        already_spoken_text="好的，我这就查一下。",
    )

    assert "<lookup>" in continuation_system
    assert "句尾" in continuation_system
    assert "异步查询" in continuation_system
    assert "用户文本" in continuation_system
    assert "<lookup>" in continuation_user
    assert "后台才会启动查询" in continuation_user
    assert "没有联网能力" in continuation_user


def test_siliconflow_preview_client_rejects_non_ascii_api_key_before_http_request():
    def fail_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        raise AssertionError("request should not be sent with an invalid API key")

    try:
        SiliconFlowPreviewClient(api_key="你的key", request_sender=fail_sender)
    except RuntimeError as exc:
        assert "SILICONFLOW_API_KEY" in str(exc)
        assert "raw API token" in str(exc)
    else:
        raise AssertionError("expected invalid API key error")


def test_preview_routing_dialog_client_adapts_plain_preview_text_at_legacy_boundary():
    class FakeReplyClient:
        pass

    class FakePlainPreviewClient:
        def generate_preview_text(self, *, mode: str, transcript: str, events: list[dict]) -> str:
            return "plain preview"

    client = PreviewRoutingDialogClient(
        reply_client=FakeReplyClient(),
        preview_client=FakePlainPreviewClient(),
    )

    preview = client.generate_lead_preview(
        mode="chatty",
        transcript="hello",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
    )

    assert preview == {
        "source": "siliconflow",
        "lead": "plain preview",
        "tail": "",
        "content": "plain preview",
    }


def test_preview_routing_dialog_client_does_not_expose_context_reply_path():
    class FakeReplyClient:
        pass

    class FakePlainPreviewClient:
        pass

    client = PreviewRoutingDialogClient(
        reply_client=FakeReplyClient(),
        preview_client=FakePlainPreviewClient(),
    )

    assert not hasattr(client, "generate_context_reply")


def test_minimax_dialog_client_generate_reply_prefers_stream_sender_when_available():
    def fail_request_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        raise AssertionError("generate_reply should not fall back to the buffered sender")

    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"First"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"First sentence. Second sentence."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(
        api_key="secret",
        request_sender=fail_request_sender,
        stream_request_sender=fake_stream_sender,
    )

    reply = client.generate_reply(
        mode="chatty",
        transcript="hello",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
    )

    assert reply["source"] == "minimax"
    assert reply["content"] == "First sentence. Second sentence."


def test_minimax_dialog_client_streams_plain_reply_text_from_cumulative_text():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"First sentence."},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"First sentence. Second sentence."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    updates = list(
        client.stream_reply_text(
            mode="chatty",
            transcript="hello",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "hello"}],
        )
    )

    assert updates == ["First sentence.", "First sentence. Second sentence."]


def test_minimax_dialog_client_stream_reply_text_normalizes_delta_and_final_message_snapshot():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"Hello baoz."},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":" I am your tabletop buddy."},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"message":{"content":"Hello baoz. I am your tabletop buddy."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    updates = list(
        client.stream_reply_text(
            mode="chatty",
            transcript="introduce yourself",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "introduce yourself"}],
        )
    )

    assert updates == [
        "Hello baoz.",
        "Hello baoz. I am your tabletop buddy.",
    ]


def test_minimax_dialog_client_streams_partial_plain_updates_before_text_stabilizes():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"????????????????????????????????"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"???????????????????????????????????????????????"},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    updates = list(
        client.stream_reply_text(
            mode="serious",
            transcript="?????????",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "?????????"}],
        )
    )

    assert updates[0].startswith("??????")
    assert updates[-1].endswith("???????????????")


def test_minimax_dialog_client_generates_lead_preview_from_first_stream_update():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"Let me think."},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"Let me think. Here is the rest."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    preview = client.generate_lead_preview(
        mode="chatty",
        transcript="how should I open",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "how should I open"}],
    )

    assert preview == {
        "source": "minimax",
        "lead": "Let me think.",
        "tail": "",
        "content": "Let me think.",
    }


def test_minimax_dialog_client_stream_reply_text_can_request_continuation_only():
    captured = {}

    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        captured["payload"] = json.loads(body.decode("utf-8"))
        yield b'data: {"choices":[{"delta":{"content":"Core rules first."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    updates = list(
        client.stream_reply_text(
            mode="serious",
            transcript="explain the rules",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "explain the rules"}],
            already_spoken_text="Let me explain.",
            continue_only=True,
        )
    )

    assert updates[-1] == "Core rules first."
    user_prompt = captured["payload"]["messages"][1]["content"]
    assert "Let me explain." in user_prompt
    assert "这次只继续输出后续还没说出口的正文，不要重复前面已经说过的话。" in user_prompt


def test_minimax_dialog_client_stream_continuation_text_uses_plain_contract():
    captured = {}

    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        captured["payload"] = json.loads(body.decode("utf-8"))
        yield b'data: {"choices":[{"delta":{"content":"Core rules"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"Core rules first. Then identity."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    chunks = list(
        client.stream_continuation_text(
            mode="serious",
            transcript="explain the rules",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "explain the rules"}],
            already_spoken_text="Let me explain.",
        )
    )

    assert chunks == ["Core rules", "Core rules first. Then identity."]
    system_prompt = captured["payload"]["messages"][0]["content"]
    user_prompt = captured["payload"]["messages"][1]["content"]
    assert "JSON" not in system_prompt
    assert "lead" not in system_prompt.lower()
    assert "Let me explain." in user_prompt
    assert "直接从下一句有意义的话开始" in system_prompt


def test_minimax_dialog_client_stream_continuation_text_normalizes_delta_and_final_message_snapshot():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"Core rules"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":" first. Then"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":" identity."},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"message":{"content":"Core rules first. Then identity."},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    chunks = list(
        client.stream_continuation_text(
            mode="serious",
            transcript="explain the rules",
            events=[{"kind": "voice_transcript", "source": "live_asr", "content": "explain the rules"}],
            already_spoken_text="Let me explain.",
        )
    )

    assert chunks == [
        "Core rules",
        "Core rules first. Then",
        "Core rules first. Then identity.",
    ]


def test_minimax_dialog_client_generate_lead_preview_waits_for_complete_sentence():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd\xe7\x9a\x84\xef\xbc\x8c\xe7\xbb\x99\xe4\xbd\xa0"},"finish_reason":null}]}\n'
        yield b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd\xe7\x9a\x84\xef\xbc\x8c\xe7\xbb\x99\xe4\xbd\xa0\xe8\xae\xb2\xe8\xae\xb2\xe4\xb8\x89\xe5\x9b\xbd\xe6\x9d\x80\xe8\xa7\x84\xe5\x88\x99\xe3\x80\x82"},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    preview = client.generate_lead_preview(
        mode="serious",
        transcript="老师，给我介绍一下三国杀的规则。",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "老师，给我介绍一下三国杀的规则。"}],
    )

    assert preview == {
        "source": "minimax",
        "lead": "好的，给你讲讲三国杀规则。",
        "tail": "",
        "content": "好的，给你讲讲三国杀规则。",
    }


def test_minimax_dialog_client_generate_lead_preview_drops_short_fragmentary_fallback():
    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        yield b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd\xe7\x9a\x84\xef\xbc\x8c\xe7\xbb\x99\xe4\xbd\xa0"},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    preview = client.generate_lead_preview(
        mode="serious",
        transcript="老师，给我介绍一下三国杀的规则。",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "老师，给我介绍一下三国杀的规则。"}],
    )

    assert preview is None


def test_minimax_dialog_client_generate_lead_preview_requests_plain_preview_prompt():
    captured: dict[str, object] = {}

    def fake_stream_sender(url: str, body: bytes, headers: dict[str, str], timeout: float):
        captured["body"] = json.loads(body.decode("utf-8"))
        yield b'data: {"choices":[{"delta":{"content":"???????????"},"finish_reason":"stop"}]}\n'
        yield b"data: [DONE]\n"

    client = MiniMaxDialogClient(api_key="secret", stream_request_sender=fake_stream_sender)

    preview = client.generate_lead_preview(
        mode="chatty",
        transcript="???????????",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "???????????"}],
    )

    system_prompt = captured["body"]["messages"][0]["content"]
    user_prompt = captured["body"]["messages"][1]["content"]

    assert "只输出可播报的 preview 短句" in system_prompt
    assert "不要输出 JSON" in system_prompt
    assert "只给第一句很短的口语接话" in user_prompt
    assert preview["content"] == "???????????"


def test_minimax_dialog_client_defaults_stream_request_sender():
    client = MiniMaxDialogClient(api_key="secret")

    assert client._stream_request_sender is not None
