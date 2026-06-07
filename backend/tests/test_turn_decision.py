import json

from gamevoice_server.turn_decision import (
    MiniMaxTurnDecisionClient,
    RuleBasedTurnHeuristics,
    TurnDecisionEngine,
)


class StubDecisionClient:
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def decide_turn(self, *, transcript: str, events: list[dict], assistant_name: str) -> dict:
        self.calls.append(
            {"transcript": transcript, "events": events, "assistant_name": assistant_name}
        )
        return self.decision


def test_turn_decision_engine_interrupts_for_rule_argument():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="this rule sounds wrong",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "rule_trigger"


def test_turn_decision_engine_interrupts_for_direct_address_to_assistant():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="can you help me with this turn",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "direct_address"


def test_turn_decision_engine_interrupts_for_playful_banter_hook():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="that is insane",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "that is insane"}],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "banter_hook"


def test_turn_decision_engine_interrupts_for_playful_request():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="tell me a joke",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "playful_request"


def test_turn_decision_engine_interrupts_for_known_player_heckle():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="教主：蛙爷太菜了",
        events=[
            {
                "kind": "speaker_alias_map",
                "speaker_alias_map": {
                    "speaker_0": ["蛙爷"],
                    "speaker_1": ["教主"],
                },
            },
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "教主：蛙爷太菜了",
            },
        ],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "heckle_hook"


def test_turn_decision_engine_does_not_heckle_without_known_player_name():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="路人太菜了",
        events=[
            {
                "kind": "speaker_alias_map",
                "speaker_alias_map": {
                    "speaker_0": ["蛙爷"],
                    "speaker_1": ["教主"],
                },
            }
        ],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is False
    assert decision["reason"] == "table_talk"


def test_turn_decision_engine_stays_quiet_for_normal_table_talk():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="go first",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is False
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "table_talk"


def test_turn_decision_engine_uses_model_for_ambiguous_question_when_enabled():
    client = StubDecisionClient(
        {"interrupt": True, "mode": "conversation", "reason": "model_addressed"}
    )
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=client,
        use_model_fallback=True,
    )

    decision = engine.decide_turn(
        transcript="what is our best option now",
        events=[{"kind": "voice_transcript", "source": "live_asr", "content": "what is our best option now"}],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "model_addressed"
    assert client.calls == [
        {
            "transcript": "what is our best option now",
            "events": [
                {"kind": "voice_transcript", "source": "live_asr", "content": "what is our best option now"}
            ],
            "assistant_name": "宝子",
        }
    ]


def test_turn_decision_engine_interrupts_when_assistant_name_is_called():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="宝子你怎么看",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "assistant_name_called"


def test_turn_decision_engine_prefers_assistant_name_when_named_rule_request():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="宝子，给我介绍三国杀的规则",
        events=[],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "assistant_name_called"


def test_turn_decision_engine_interrupts_followup_after_recent_name_call():
    engine = TurnDecisionEngine(
        heuristics=RuleBasedTurnHeuristics(),
        decision_client=None,
        use_model_fallback=False,
    )

    decision = engine.decide_turn(
        transcript="那现在要先打谁？",
        events=[
            {"kind": "voice_transcript", "source": "live_asr", "content": "宝子"},
            {"kind": "voice_transcript", "source": "live_asr", "content": "我刚刚没听清"},
        ],
        assistant_name="宝子",
    )

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"
    assert decision["reason"] == "followup_after_name_call"


def test_minimax_turn_decision_client_accepts_json_wrapped_in_text():
    def fake_sender(_url: str, _body: bytes, _headers: dict[str, str], _timeout: float) -> bytes:
        return json.dumps(
            {
                "content": [
                    {
                        "type": "text",
                        "text": 'Decision: {"interrupt": true, "mode": "serious", "reason": "rule_conflict"}',
                    }
                ]
            }
        ).encode("utf-8")

    client = MiniMaxTurnDecisionClient(api_key="test-key", request_sender=fake_sender)
    decision = client.decide_turn(
        transcript="this rule is wrong",
        events=[],
        assistant_name="宝子",
    )

    assert decision == {"interrupt": True, "mode": "conversation", "reason": "rule_conflict"}


def test_minimax_turn_decision_client_falls_back_on_invalid_json_response():
    def fake_sender(_url: str, _body: bytes, _headers: dict[str, str], _timeout: float) -> bytes:
        return json.dumps({"content": [{"type": "text", "text": "okay let me think"}]}).encode("utf-8")

    client = MiniMaxTurnDecisionClient(api_key="test-key", request_sender=fake_sender)
    decision = client.decide_turn(
        transcript="hello",
        events=[],
        assistant_name="宝子",
    )

    assert decision == {"interrupt": False, "mode": "conversation", "reason": "model_parse_error"}
