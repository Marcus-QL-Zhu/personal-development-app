from __future__ import annotations

from gamevoice_server.live_heartbeat import LiveHeartbeatScheduler, reliable_heartbeat_player_names


class SequenceRandom:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def uniform(self, minimum: float, maximum: float) -> float:
        if not self.values:
            raise AssertionError("no random values left")
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def test_live_heartbeat_schedules_when_listening_and_fires_once_when_due():
    scheduler = LiveHeartbeatScheduler(
        min_seconds=180,
        max_seconds=300,
        rng=SequenceRandom([240, 210]),
    )

    snapshot = scheduler.on_listening_started("table-1", now_monotonic=1000)

    assert snapshot["deadline_monotonic"] == 1240
    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1239,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=False,
        user_voice_active=False,
    ) is False
    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1240,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=False,
        user_voice_active=False,
    ) is True

    scheduler.mark_inflight("table-1")

    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1300,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=False,
        user_voice_active=False,
    ) is False


def test_live_heartbeat_resets_after_any_agent_speech_started():
    scheduler = LiveHeartbeatScheduler(
        min_seconds=180,
        max_seconds=300,
        rng=SequenceRandom([240, 210]),
    )
    scheduler.on_listening_started("table-1", now_monotonic=1000)

    snapshot = scheduler.on_agent_speech_started("table-1", now_monotonic=1100)

    assert snapshot["deadline_monotonic"] == 1310
    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1240,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=False,
        user_voice_active=False,
    ) is False


def test_live_heartbeat_blocks_while_agent_is_active():
    scheduler = LiveHeartbeatScheduler(
        min_seconds=180,
        max_seconds=300,
        rng=SequenceRandom([180]),
    )
    scheduler.on_listening_started("table-1", now_monotonic=1000)

    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1180,
        is_listening=True,
        is_agent_speaking=True,
        has_pending_assistant_audio=False,
        user_voice_active=False,
    ) is False
    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1180,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=True,
        user_voice_active=False,
    ) is False


def test_live_heartbeat_can_fire_while_user_voice_is_active_after_deadline():
    scheduler = LiveHeartbeatScheduler(
        min_seconds=180,
        max_seconds=300,
        rng=SequenceRandom([180]),
    )
    scheduler.on_listening_started("table-1", now_monotonic=1000)

    assert scheduler.should_fire(
        "table-1",
        now_monotonic=1180,
        is_listening=True,
        is_agent_speaking=False,
        has_pending_assistant_audio=False,
        user_voice_active=True,
    ) is True


def test_reliable_heartbeat_player_names_filters_default_fallback_aliases():
    names = reliable_heartbeat_player_names(
        {
            "speaker_0": ["宝宝", "蛙爷"],
            "speaker_1": ["宝宝", "教主"],
            "speaker_2": ["speaker_2", "宝子"],
        },
        assistant_name="宝子",
    )

    assert names == ["蛙爷", "教主"]
