from gamevoice_server.companion_orchestrator import CompanionOrchestrator
from gamevoice_server.companion_timing import CompanionTiming
from gamevoice_server.rules_router import looks_like_arkham_or_card_query
from gamevoice_server.turn_decision import RuleBasedTurnHeuristics, TurnDecisionEngine


class StubDialogClient:
    def __init__(self, content: str, source: str = "minimax") -> None:
        self.content = content
        self.source = source
        self.calls: list[dict] = []
        self.preview_calls: list[dict] = []

    def generate_reply(self, *, mode: str, transcript: str, events: list[dict]) -> dict:
        self.calls.append({"mode": mode, "transcript": transcript, "events": events})
        return {"source": self.source, "content": self.content}

    def generate_lead_preview(self, *, mode: str, transcript: str, events: list[dict]) -> dict | None:
        """Simulate SiliconFlow: return a valid lead preview (pure text path)."""
        self.preview_calls.append({"mode": mode, "transcript": transcript, "events": events})
        # Split at Chinese period (。) or English period (.)
        for sep in ["。", "."]:
            parts = self.content.split(sep, 1)
            if len(parts) > 1:
                lead = parts[0].strip()
                tail = parts[1].strip() if parts[1].strip() else ""
                if lead:
                    return {"source": self.source, "lead": lead, "tail": tail, "content": lead}
        # No separator found
        lead = self.content.strip()
        if not lead:
            return None
        return {"source": self.source, "lead": lead, "tail": "", "content": lead}


class FailingPreviewDialogClient(StubDialogClient):
    def generate_lead_preview(self, *, mode: str, transcript: str, events: list[dict]) -> dict | None:
        self.preview_calls.append({"mode": mode, "transcript": transcript, "events": events})
        raise TimeoutError("preview timed out")


def build_timing():
    return CompanionTiming(
        TurnDecisionEngine(
            heuristics=RuleBasedTurnHeuristics(),
            decision_client=None,
        )
    )


def test_plan_reply_conversation_returns_no_interrupt():
    """When timing decides should_not_interrupt, no reply is generated (pure text path)."""
    dialog_client = StubDialogClient("First line. Second line.")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "let us resolve this"}]
    result = orchestrator.plan_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is False
    assert result["transcript"] == "let us resolve this"
    assert result["reply"] is None
    assert result["analysis_needed"] is False
    assert result["analysis_query"] is None
    # generate_reply should NOT be called when should_interrupt is False
    assert dialog_client.calls == []
    # generate_lead_preview should NOT be called either
    assert dialog_client.preview_calls == []


def test_plan_reply_rule_keyword_no_longer_directly_triggers_analysis():
    dialog_client = StubDialogClient("我先确认一下你问的是哪张牌。")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "阿卡姆规则里 forced ability 怎么处理"}]
    result = orchestrator.plan_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is True
    assert result["analysis_needed"] is False
    assert result["analysis_query"] is None
    assert result["reply"]["content"] == "我先确认一下你问的是哪张牌"
    assert len(dialog_client.preview_calls) == 1


def test_plan_reply_rule_request_delegates_to_lead_preview():
    """Rule-like request: plan_reply delegates to plan_lead_preview (pure text path)."""
    dialog_client = StubDialogClient("我先给你讲基础规则。")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "介绍一下基础规则"}]
    result = orchestrator.plan_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is True
    assert result["analysis_needed"] is False
    assert result["analysis_query"] is None
    # generate_lead_preview is called (not generate_reply directly)
    assert len(dialog_client.preview_calls) == 1
    assert dialog_client.preview_calls[0]["mode"] == "conversation"
    assert dialog_client.preview_calls[0]["transcript"] == "介绍一下基础规则"
    # generate_reply should NOT be called in this path
    assert dialog_client.calls == []
    # Reply should have lead preview format
    assert result["reply"]["lead"] == "我先给你讲基础规则"
    assert result["reply"]["content"] == "我先给你讲基础规则"


def test_plan_progressive_reply_rule_request_defers_formal_generation():
    dialog_client = StubDialogClient("Preview only. Formal rules should stream later.")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "please explain the rule"}]
    result = orchestrator.plan_progressive_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is True
    assert result["deferred_generation"] is True
    assert result["reply"] is None
    assert result["analysis_needed"] is False
    assert result["analysis_query"] is None
    assert dialog_client.preview_calls == []
    assert dialog_client.calls == []


def test_plan_reply_conversation_interrupt_uses_lead_preview():
    """Conversation interrupt: plan_reply delegates to plan_lead_preview (pure text path)."""
    dialog_client = StubDialogClient("Sure. Here is a joke. Then the punchline.")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}]
    result = orchestrator.plan_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is True
    assert result["reply"]["lead"] == "Sure"
    assert result["reply"]["tail"] == ""  # _lead_only_reply always sets tail=""
    assert result["analysis_needed"] is False
    # generate_lead_preview is called (not generate_reply)
    assert len(dialog_client.preview_calls) == 1
    assert dialog_client.preview_calls[0]["mode"] == "conversation"
    assert dialog_client.preview_calls[0]["transcript"] == "tell me a joke"
    assert dialog_client.calls == []


def test_plan_reply_conversation_falls_back_to_formal_reply_when_preview_times_out():
    dialog_client = FailingPreviewDialogClient("我是宝子，桌边陪玩的语音助手。")
    orchestrator = CompanionOrchestrator(
        timing=build_timing(),
        dialog_client=dialog_client,
    )

    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "宝子，介绍你自己"}]
    result = orchestrator.plan_reply(events)

    assert result["mode"] == "conversation"
    assert result["should_interrupt"] is True
    assert result["decision_reason"] == "assistant_name_called"
    assert result["analysis_needed"] is False
    assert result["analysis_query"] is None
    assert result["reply"]["source"] == "minimax"
    assert result["reply"]["content"] == "我是宝子，桌边陪玩的语音助手。"
    assert len(dialog_client.preview_calls) == 1
    assert len(dialog_client.calls) == 1
    assert dialog_client.calls[0]["mode"] == "conversation"
    assert dialog_client.calls[0]["transcript"] == "宝子，介绍你自己"


def test_looks_like_arkham_or_card_query_keywords():
    assert looks_like_arkham_or_card_query("阿卡姆规则里 forced ability 怎么处理") is True
    assert looks_like_arkham_or_card_query("Arkham Horror LCG 里这张牌的效果是什么") is True
    assert looks_like_arkham_or_card_query("what does this card do") is True
    assert looks_like_arkham_or_card_query("卡牌效果是什么") is True
    assert looks_like_arkham_or_card_query("tell me a joke") is False
    assert looks_like_arkham_or_card_query("今天天气怎么样") is False
