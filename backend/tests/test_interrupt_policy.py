from gamevoice_server.interrupt_policy import InterruptPolicy


def test_interrupt_policy_blocks_duplicate_transcript():
    now = 100.0
    policy = InterruptPolicy(cooldown_seconds=5.0, time_provider=lambda: now)

    first = policy.should_allow("table-1", "what should I do", mode="conversation", decision_reason="model")
    policy.record_trigger("table-1", "what should I do", mode="conversation", decision_reason="model")
    second = policy.should_allow("table-1", "what should I do", mode="conversation", decision_reason="model")

    assert first["allowed"] is True
    assert second == {"allowed": False, "reason": "duplicate_transcript"}


def test_interrupt_policy_enforces_cooldown_between_different_transcripts():
    clock = {"now": 100.0}
    policy = InterruptPolicy(
        cooldown_seconds=5.0,
        max_conversation_replies_per_window=2,
        time_provider=lambda: clock["now"],
    )

    policy.record_trigger("table-1", "first question", mode="conversation", decision_reason="model")
    blocked = policy.should_allow("table-1", "second question", mode="conversation", decision_reason="model")
    clock["now"] = 106.0
    allowed = policy.should_allow("table-1", "second question", mode="conversation", decision_reason="model")

    assert blocked == {"allowed": False, "reason": "cooldown"}
    assert allowed == {"allowed": True, "reason": "allowed"}


def test_interrupt_policy_limits_generic_conversation_auto_replies_per_window():
    clock = {"now": 100.0}
    policy = InterruptPolicy(
        cooldown_seconds=0.0,
        conversation_window_seconds=20.0,
        max_conversation_replies_per_window=1,
        time_provider=lambda: clock["now"],
    )

    policy.record_trigger("table-1", "first nudge", mode="conversation", decision_reason="model")
    blocked = policy.should_allow("table-1", "second nudge", mode="conversation", decision_reason="model")
    clock["now"] = 121.0
    allowed = policy.should_allow("table-1", "third nudge", mode="conversation", decision_reason="model")

    assert blocked == {"allowed": False, "reason": "conversation_quota"}
    assert allowed == {"allowed": True, "reason": "allowed"}


def test_interrupt_policy_allows_direct_address_to_bypass_conversation_quota():
    clock = {"now": 100.0}
    policy = InterruptPolicy(
        cooldown_seconds=0.0,
        conversation_window_seconds=20.0,
        max_conversation_replies_per_window=1,
        time_provider=lambda: clock["now"],
    )

    policy.record_trigger("table-1", "first nudge", mode="conversation", decision_reason="model")
    allowed = policy.should_allow(
        "table-1",
        "you tell me",
        mode="conversation",
        decision_reason="direct_address",
    )

    assert allowed == {"allowed": True, "reason": "allowed"}


def test_interrupt_policy_allows_assistant_name_calls_to_bypass_conversation_quota():
    clock = {"now": 100.0}
    policy = InterruptPolicy(
        cooldown_seconds=0.0,
        conversation_window_seconds=20.0,
        max_conversation_replies_per_window=1,
        time_provider=lambda: clock["now"],
    )

    first = policy.should_allow(
        "table-1",
        "baozi, check the weather",
        mode="conversation",
        decision_reason="assistant_name_called",
    )
    policy.record_trigger(
        "table-1",
        "baozi, check the weather",
        mode="conversation",
        decision_reason="assistant_name_called",
    )
    second = policy.should_allow(
        "table-1",
        "baozi, explain the rules",
        mode="conversation",
        decision_reason="assistant_name_called",
    )

    assert first == {"allowed": True, "reason": "allowed"}
    assert second == {"allowed": True, "reason": "allowed"}


def test_interrupt_policy_allows_assistant_name_calls_to_bypass_cooldown():
    clock = {"now": 100.0}
    policy = InterruptPolicy(
        cooldown_seconds=5.0,
        time_provider=lambda: clock["now"],
    )

    policy.record_trigger(
        "table-1",
        "baozi, check the weather",
        mode="conversation",
        decision_reason="assistant_name_called",
    )
    clock["now"] = 101.0
    allowed = policy.should_allow(
        "table-1",
        "baozi, introduce yourself",
        mode="conversation",
        decision_reason="assistant_name_called",
    )

    assert allowed == {"allowed": True, "reason": "allowed"}
