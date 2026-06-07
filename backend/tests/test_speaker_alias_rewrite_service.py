from gamevoice_server.session_manager import SessionManager
from gamevoice_server.speaker_alias_rewrite_service import SpeakerAliasRewriteService


class StubAliasRewriteClient:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def rewrite_speaker_alias_map(
        self,
        *,
        dialogue_events: list[dict],
        current_alias_map: dict[str, list[str]],
    ) -> dict:
        self.calls.append(
            {
                "dialogue_events": list(dialogue_events),
                "current_alias_map": {key: list(value) for key, value in current_alias_map.items()},
            }
        )
        if not self.outputs:
            raise AssertionError("unexpected alias rewrite request")
        return dict(self.outputs.pop(0))


def _seed_alias_table(session_manager: SessionManager):
    table = session_manager.start_table(name="Alias Rewrite Table")
    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_a",
            "status": "linked",
            "display_label": "Player A",
            "aliases": ["Old Name", "Fat Tiger"],
        },
    )
    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_b",
            "status": "linked",
            "display_label": "Player B",
            "aliases": ["Big Hero", "Zhang San"],
        },
    )
    session_manager.link_speaker_identity(
        table.id,
        "player_c",
        "Old Wang",
        speaker_label="Player C",
    )
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "Player A: I go first",
        },
    )
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "Player B: my turn",
        },
    )
    return table


def test_speaker_alias_rewrite_service_replaces_all_aliases_and_deletes_missing_ones(session_manager):
    table = _seed_alias_table(session_manager)
    client = StubAliasRewriteClient(
        [
            {
                "player_a": ["Musk", "Fat Tiger"],
                "player_b": ["Big Hero"],
                "player_c": [],
            }
        ]
    )
    service = SpeakerAliasRewriteService(session_manager=session_manager, dialog_client=client)

    result = service.rewrite_table_aliases(table.id)

    assert result["status"] == "updated"
    assert result["stopped"] is False
    assert result["consecutive_same_count"] == 1
    assert result["active_speaker_ids"] == ["player_a", "player_b"]
    assert result["speaker_alias_map"] == {
        "player_a": ["宝宝", "Musk", "Fat Tiger"],
        "player_b": ["宝宝", "Big Hero"],
        "player_c": ["宝宝"],
    }

    listed = {item["speaker_id"]: item for item in session_manager.list_speaker_identities(table.id)}
    assert listed["player_a"]["aliases"] == ["宝宝", "Musk", "Fat Tiger"]
    assert listed["player_b"]["aliases"] == ["宝宝", "Big Hero"]
    assert listed["player_c"]["aliases"] == ["宝宝"]
    assert "Old Name" not in listed["player_a"]["aliases"]
    assert "Zhang San" not in listed["player_b"]["aliases"]
    assert "Old Wang" not in listed["player_c"]["aliases"]
    assert client.calls[0]["current_alias_map"] == {
        "player_a": ["宝宝", "Old Name", "Fat Tiger"],
        "player_b": ["宝宝", "Big Hero", "Zhang San"],
        "player_c": ["宝宝", "Old Wang"],
    }


def test_speaker_alias_rewrite_service_stops_after_two_identical_normalized_outputs(session_manager):
    table = _seed_alias_table(session_manager)
    client = StubAliasRewriteClient(
        [
            {
                "player_a": ["Fat Tiger", "Musk"],
                "player_b": ["Big Hero"],
                "player_c": ["Old Wang"],
            },
            {
                "player_a": ["Musk", "Fat Tiger"],
                "player_b": ["Big Hero"],
                "player_c": ["Old Wang"],
            },
        ]
    )
    service = SpeakerAliasRewriteService(session_manager=session_manager, dialog_client=client)

    first = service.rewrite_table_aliases(table.id)
    second = service.rewrite_table_aliases(table.id)

    assert first["consecutive_same_count"] == 1
    assert first["stopped"] is False
    assert second["consecutive_same_count"] == 2
    assert second["stopped"] is True


def test_speaker_alias_rewrite_service_resumes_on_next_poll_when_new_active_bucket_appears(session_manager):
    table = _seed_alias_table(session_manager)
    client = StubAliasRewriteClient(
        [
            {
                "player_a": ["Fat Tiger", "Musk"],
                "player_b": ["Big Hero"],
                "player_c": ["Old Wang"],
            },
            {
                "player_a": ["Musk", "Fat Tiger"],
                "player_b": ["Big Hero"],
                "player_c": ["Old Wang"],
            },
            {
                "player_a": ["Musk", "Fat Tiger"],
                "player_b": ["Big Hero"],
                "player_c": ["Old Wang"],
                "player_d": ["Zhao Six"],
            },
        ]
    )
    service = SpeakerAliasRewriteService(session_manager=session_manager, dialog_client=client)

    service.rewrite_table_aliases(table.id)
    service.rewrite_table_aliases(table.id)

    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_d",
            "status": "anonymous",
            "display_label": "Player D",
            "aliases": [],
        },
    )
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "Player D: I just joined",
        },
    )

    resumed = service.rewrite_table_aliases(table.id)

    assert resumed["stopped"] is False
    assert resumed["consecutive_same_count"] == 1
    assert resumed["active_speaker_ids"] == ["player_a", "player_b", "player_d"]
    assert resumed["speaker_alias_map"]["player_d"] == ["宝宝", "Zhao Six"]
    assert len(client.calls) == 3


def test_speaker_alias_rewrite_service_includes_runtime_alias_evidence(session_manager):
    table = _seed_alias_table(session_manager)
    session_manager.append_runtime_event(
        table.id,
        {
            "kind": "speaker_alias_evidence",
            "source": "live_asr",
            "content": "player_a：孙哥说可以",
            "speaker_id": "player_a",
        },
    )
    client = StubAliasRewriteClient(
        [
            {
                "player_a": ["孙哥"],
                "player_b": [],
                "player_c": [],
            }
        ]
    )
    service = SpeakerAliasRewriteService(session_manager=session_manager, dialog_client=client)

    result = service.rewrite_table_aliases(table.id)

    assert result["speaker_alias_map"]["player_a"] == ["宝宝", "孙哥"]
    assert "player_a：孙哥说可以" in [
        item["content"] for item in client.calls[0]["dialogue_events"]
    ]


def test_speaker_alias_rewrite_poll_only_runs_recent_tables_with_new_context(session_manager):
    old_table = _seed_alias_table(session_manager)
    old_table.last_active_at = "2026-05-29T00:00:00+00:00"
    recent_table = _seed_alias_table(session_manager)
    client = StubAliasRewriteClient(
        [
            {
                "player_a": ["Musk"],
                "player_b": ["Big Hero"],
                "player_c": [],
            },
            {
                "player_a": ["Musk", "New Hint"],
                "player_b": ["Big Hero"],
                "player_c": [],
            },
        ]
    )
    service = SpeakerAliasRewriteService(
        session_manager=session_manager,
        dialog_client=client,
        active_window_seconds=300.0,
    )

    first = service.poll_once()
    second = service.poll_once()
    session_manager.append_context_event(
        recent_table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "Player A: New Hint joins the table",
        },
    )
    third = service.poll_once()

    assert [item["table_id"] for item in first] == [recent_table.id]
    assert second == []
    assert [item["table_id"] for item in third] == [recent_table.id]
    assert len(client.calls) == 2
    assert old_table.id not in service._states


def test_speaker_alias_rewrite_poll_skips_tables_without_active_speakers(session_manager):
    table = session_manager.start_table(name="No Speakers")
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "hello",
        },
    )
    client = StubAliasRewriteClient([])
    service = SpeakerAliasRewriteService(
        session_manager=session_manager,
        dialog_client=client,
        active_window_seconds=300.0,
    )

    assert service.poll_once() == []
    assert client.calls == []
