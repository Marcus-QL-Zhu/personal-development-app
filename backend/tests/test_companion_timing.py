from gamevoice_server.companion_timing import CompanionTiming
from gamevoice_server.turn_decision import RuleBasedTurnHeuristics, TurnDecisionEngine


def test_companion_timing_interrupts_on_rule_argument():
    timing = CompanionTiming(
        TurnDecisionEngine(
            heuristics=RuleBasedTurnHeuristics(),
            decision_client=None,
        )
    )

    decision = timing.should_interrupt("我觉得这个规则不太对啊")

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"


def test_companion_timing_interrupts_when_user_is_clearly_addressing_assistant():
    timing = CompanionTiming(
        TurnDecisionEngine(
            heuristics=RuleBasedTurnHeuristics(),
            decision_client=None,
        )
    )

    decision = timing.should_interrupt("你觉得我这一回合应该先打谁")

    assert decision["interrupt"] is True
    assert decision["mode"] == "conversation"


def test_companion_timing_stays_quiet_for_normal_table_talk():
    timing = CompanionTiming(
        TurnDecisionEngine(
            heuristics=RuleBasedTurnHeuristics(),
            decision_client=None,
        )
    )

    decision = timing.should_interrupt("先处理这个敌人吧")

    assert decision["interrupt"] is False
    assert decision["mode"] == "conversation"
