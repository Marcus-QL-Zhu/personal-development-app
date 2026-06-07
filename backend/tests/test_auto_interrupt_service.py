from gamevoice_server.auto_interrupt_service import AutoInterruptService
from gamevoice_server.companion_orchestrator import CompanionOrchestrator
from gamevoice_server.companion_timing import CompanionTiming
from gamevoice_server.tts_adapter import TTSAdapter
from gamevoice_server.turn_decision import RuleBasedTurnHeuristics, TurnDecisionEngine


class StubDialogClient:
    def generate_reply(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        return {"source": "minimax", "content": f"reply: {transcript}"}

    def generate_lead_preview(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict | None:
        """Simulate SiliconFlow: return lead from generate_reply result."""
        content = f"reply: {transcript}"
        for sep in ["。", "."]:
            parts = content.split(sep, 1)
            if len(parts) > 1:
                lead = parts[0].strip()
                tail = parts[1].strip() if parts[1].strip() else ""
                return {"source": "minimax", "lead": lead, "tail": tail, "content": lead}
        lead = content.strip()
        if not lead:
            return None
        return {"source": "minimax", "lead": lead, "tail": "", "content": lead}

    def generate_heartbeat(
        self,
        *,
        events: list[dict],
        player_names: list[str],
        assistant_name: str | None = None,
        assistant_personality: str | None = None,
    ) -> dict:
        target = player_names[0] if player_names else "宝宝们"
        return {"source": "minimax", "content": f"{target}，别沉默了，出来整点动静。"}


class FailingTtsAdapter:
    def speak(self, text: str, *, reply: dict | None = None, turn_id=None, reply_id=None) -> dict:
        raise RuntimeError("TTS failed")


def build_timing():
    return CompanionTiming(
        TurnDecisionEngine(
            heuristics=RuleBasedTurnHeuristics(),
            decision_client=None,
        )
    )


def test_auto_interrupt_service_speaks_for_rule_reply():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "this rule is wrong"}]

    result = service.run_once(events)

    assert result["interrupt"] is True
    assert result["speech_job"]["accepted"] is True
    assert result["reply"]["content"] == "reply: this rule is wrong"
    assert result["assistant_event"]["source"] == "companion"
    assert result["speech_job"]["turn_id"]
    assert result["speech_job"]["reply_id"]
    assert result["assistant_event"]["turn_id"] == result["speech_job"]["turn_id"]
    assert result["assistant_event"]["reply_id"] == result["speech_job"]["reply_id"]


def test_auto_interrupt_service_stays_quiet_for_table_talk():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "let us handle this enemy first"}]

    result = service.run_once(events)

    assert result["interrupt"] is False
    assert result["speech_job"] is None


def test_auto_interrupt_service_speaks_for_conversation_request():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}]

    result = service.run_once(events)

    assert result["interrupt"] is True
    assert result["mode"] == "conversation"
    assert result["speech_job"]["accepted"] is True
    assert result["speech_job"]["segments"]
    assert result["assistant_event"]["content"] == result["reply"]["content"]


def test_auto_interrupt_service_passes_assistant_personality_to_dialog_client():
    class CapturingDialogClient(StubDialogClient):
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_lead_preview(
            self,
            *,
            mode: str,
            transcript: str,
            events: list[dict],
            assistant_name: str | None = None,
            assistant_personality: str | None = None,
        ) -> dict | None:
            self.calls.append(
                {
                    "assistant_name": assistant_name,
                    "assistant_personality": assistant_personality,
                }
            )
            return super().generate_lead_preview(
                mode=mode,
                transcript=transcript,
                events=events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
            )

    dialog_client = CapturingDialogClient()
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=dialog_client,
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}]

    result = service.run_once(
        events,
        assistant_name="小夏",
        assistant_personality="温柔但吐槽欲强",
    )

    assert result["interrupt"] is True
    assert dialog_client.calls[-1] == {
        "assistant_name": "小夏",
        "assistant_personality": "温柔但吐槽欲强",
    }


def test_auto_interrupt_service_falls_back_when_tts_fails():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=FailingTtsAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "this rule is wrong"}]

    result = service.run_once(events)

    assert result["interrupt"] is False
    assert result["mode"] == "conversation"
    assert result["reply"]["content"] == "reply: this rule is wrong"
    assert result["speech_job"] is None
    assert result["assistant_event"] is None


def test_auto_interrupt_service_returns_lead_preview_with_preview_tts():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "tell me a joke"}]

    result = service.preview(events)

    assert result["interrupt"] is True
    assert result["mode"] == "conversation"
    assert result["reply"]["content"] == "reply: tell me a joke"
    assert result["speech_job"]["accepted"] is True
    assert result["speech_job"]["segments"]
    assert result["speech_job"]["turn_id"]
    assert result["speech_job"]["reply_id"]
    assert result["assistant_event"]["kind"] == "assistant_preview"
    assert result["assistant_event"]["source"] == "runtime_preview"
    assert result["assistant_event"]["speech_job"]["job_id"] == result["speech_job"]["job_id"]


def test_auto_interrupt_service_builds_heartbeat_tts_for_known_players():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )

    result = service.heartbeat(
        [{"kind": "voice_transcript", "source": "live_asr", "content": "蛙爷：我先想想。"}],
        player_names=["蛙爷", "教主"],
        assistant_name="宝子",
    )

    assert result["interrupt"] is True
    assert result["decision_reason"] == "heartbeat"
    assert result["assistant_event"]["kind"] == "assistant_heartbeat"
    assert result["assistant_event"]["source"] == "companion_heartbeat"
    assert result["reply"]["content"] == "蛙爷，别沉默了，出来整点动静。"
    assert result["speech_job"]["accepted"] is True


def test_auto_interrupt_service_heartbeat_falls_back_to_group_call_without_names():
    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=StubDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )

    result = service.heartbeat([], player_names=[], assistant_name="宝子")

    assert result["reply"]["content"].startswith("宝宝们")


def test_auto_interrupt_service_heartbeat_replaces_unplayable_prompt_echo():
    class PromptEchoDialogClient(StubDialogClient):
        def generate_heartbeat(self, **kwargs) -> dict:
            return {
                "source": "minimax",
                "content": "用户要求我作为桌游陪玩语音助手，在桌面安静时主动带气氛。当前信息：玩家名：蛙爷、教主。",
            }

    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=PromptEchoDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )

    result = service.heartbeat([], player_names=["蛙爷", "教主"], assistant_name="宝子")

    assert "用户要求" not in result["reply"]["content"]
    assert "当前信息" not in result["reply"]["content"]
    assert "蛙爷" in result["reply"]["content"]


def test_auto_interrupt_service_strips_assistant_name_prefix_before_preview_tts():
    class PrefixedDialogClient(StubDialogClient):
        def generate_lead_preview(self, **kwargs) -> dict | None:
            return {"source": "minimax", "lead": "宝子：我在。", "tail": "", "content": "宝子：我在。"}

    service = AutoInterruptService(
        orchestrator=CompanionOrchestrator(
            timing=build_timing(),
            dialog_client=PrefixedDialogClient(),
        ),
        tts_adapter=TTSAdapter(),
    )
    events = [{"kind": "voice_transcript", "source": "live_asr", "content": "宝子，介绍你自己"}]

    result = service.preview(events, assistant_name="宝子")

    assert result["reply"]["content"] == "我在。"
    assert result["speech_job"]["segments"] == ["我在。"]
    assert result["assistant_event"]["content"] == "我在。"
