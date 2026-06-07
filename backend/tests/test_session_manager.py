from pathlib import Path

from gamevoice_server.session_manager import SessionManager


def test_live_transcript_slices_do_not_pollute_main_context(session_manager):
    table = session_manager.start_table(name="Main Table")

    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="hello",
    )
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=1,
        content="hello again",
    )
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-2",
        slice_index=0,
        content="that timing feels wrong",
    )

    assert session_manager.list_context(table.id) == []
    assert session_manager.tables[table.id].latest_live_stable_text == "that timing feels wrong"


def test_latest_live_stable_text_uses_latest_cumulative_slice(session_manager):
    table = session_manager.start_table(name="Stable Table")

    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="what should I do now",
    )
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=1,
        content="what should I do now",
    )

    assert session_manager.tables[table.id].latest_live_stable_text == "what should I do now"


def test_commit_live_transcript_appends_prefixed_user_line(session_manager):
    table = session_manager.start_table(name="Main Table")
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="that timing feels wrong",
    )

    committed = session_manager.commit_live_transcript(
        table.id,
        source="live_asr",
        live_session_id="session-1",
    )

    assert committed is not None
    assert committed["kind"] == "voice_transcript"
    assert committed["content"] == "宝宝：that timing feels wrong"
    assert session_manager.tables[table.id].latest_live_stable_text is None


def test_live_asr_commit_keeps_speaker_id_prefix_when_linked_name_exists(session_manager):
    table = session_manager.start_table(name="Linked Speaker Table")
    session_manager.link_speaker_identity(
        table.id,
        "speaker_1",
        "Alice",
        speaker_label="speaker_1",
    )
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="I will take the next turn",
        speaker_id="speaker_1",
        speaker_label="speaker_1",
    )

    committed = session_manager.commit_live_transcript(
        table.id,
        source="live_asr",
        live_session_id="session-1",
    )

    assert committed is not None
    assert committed["speaker_id"] == "speaker_1"
    assert committed["content"] == "speaker_1：I will take the next turn"
    assert session_manager.tables[table.id].speaker_identities["speaker_1"]["linked_name"] == "Alice"


def test_live_asr_commit_strips_nested_speaker_prefix(session_manager):
    table = session_manager.start_table(name="Nested Prefix Table")
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="speaker_0：I need to check a card",
        speaker_id="speaker_1",
        speaker_label="speaker_1",
    )

    committed = session_manager.commit_live_transcript(
        table.id,
        source="live_asr",
        live_session_id="session-1",
    )

    assert committed is not None
    assert committed["content"] == "speaker_1：I need to check a card"


def test_live_asr_upsert_marks_speaker_bucket_active_for_alias_rewrite(session_manager):
    table = session_manager.start_table(name="Active Speaker Table")

    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="hello from the table",
        speaker_id="1",
        speaker_label="speaker_1",
    )

    assert session_manager.list_active_speaker_ids(table.id) == ["speaker_1"]
    assert session_manager.list_speaker_alias_map(table.id) == {"speaker_1": ["宝宝"]}


def test_dialog_context_excludes_unplayed_assistant_replies(session_manager):
    table = session_manager.start_table(name="Main Table")
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "玩家A：should we engage now",
        },
    )
    session_manager.append_assistant_reply(
        table.id,
        {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": "chatty",
            "content": "planned but not spoken",
            "speech_job": {"job_id": "job-1", "accepted": True, "status": "ready"},
        },
    )

    dialog_events = session_manager.list_dialog_context(table.id)

    assert [item["kind"] for item in dialog_events] == ["voice_transcript"]


def test_commit_spoken_reply_appends_assistant_spoken_once(session_manager):
    table = session_manager.start_table(name="Main Table")
    session_manager.append_assistant_reply(
        table.id,
        {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": "chatty",
            "content": "spoken line",
            "speech_job": {"job_id": "job-1", "accepted": True, "status": "ready"},
        },
    )

    first = session_manager.commit_spoken_reply(table.id, "job-1")
    second = session_manager.commit_spoken_reply(table.id, "job-1")
    spoken_events = [
        item for item in session_manager.list_context(table.id) if item.get("kind") == "assistant_spoken"
    ]

    assert first["content"] == "宝子：spoken line"
    assert second["content"] == "宝子：spoken line"
    assert len(spoken_events) == 1
    assert spoken_events[0]["job_id"] == "job-1"


def test_commit_spoken_reply_does_not_double_prefix_assistant_name(session_manager):
    table = session_manager.start_table(name="Prefixed Reply Table")
    session_manager.append_assistant_reply(
        table.id,
        {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": "chatty",
            "content": "宝子：我在。",
            "speech_job": {"job_id": "job-prefixed", "accepted": True, "status": "ready"},
        },
    )

    spoken = session_manager.commit_spoken_reply(table.id, "job-prefixed")

    assert spoken["content"] == "宝子：我在。"


def test_strip_assistant_prefix_removes_repeated_inline_name_prefixes(session_manager):
    table = session_manager.start_table(name="Inline Prefixed Reply Table")

    assert (
        session_manager.strip_assistant_prefix(
            table.id,
            "宝子：第一句正式回复。宝子：第二句继续补充。",
        )
        == "第一句正式回复。第二句继续补充。"
    )


def test_commit_interrupted_reply_splits_spoken_and_unspoken(session_manager):
    table = session_manager.start_table(name="Main Table")
    session_manager.append_assistant_reply(
        table.id,
        {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": "chatty",
            "content": "完整内容",
            "speech_job": {
                "job_id": "job-1",
                "accepted": True,
                "status": "interrupted",
                "segment_statuses": [
                    {"index": 0, "text": "前半句", "status": "completed"},
                    {
                        "index": 1,
                        "text": "后半句还有更多解释内容，而且本来还想继续说明为什么这里要这样结算",
                        "status": "interrupted",
                    },
                ],
            },
        },
    )

    result = session_manager.commit_interrupted_reply(table.id, "job-1")

    assert result["spoken"]["content"] == "宝子：前半句"
    assert result["unspoken"]["content"].startswith("宝子（未说）：后半句还有更多解释内容")


def test_assistant_name_locks_after_first_main_context_event(session_manager):
    table = session_manager.start_table(name="Lock Table")

    updated = session_manager.set_assistant_name(table.id, "小夏")
    assert updated == "小夏"

    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "玩家A：我们开始吧",
        },
    )

    try:
        session_manager.set_assistant_name(table.id, "阿宝")
        assert False, "expected assistant name update to be rejected after lock"
    except RuntimeError as exc:
        assert str(exc) == "assistant name is frozen after conversation starts"


def test_linked_speaker_uses_bridge_label_until_compaction_then_real_name(session_manager):
    table = session_manager.start_table(name="Identity Table")

    anonymous = session_manager.format_user_utterance(text="我们先开桌", table_id=table.id)
    assert anonymous == "宝宝：我们先开桌"
    assert session_manager.list_speaker_alias_map(table.id)["player_a"] == ["宝宝"]

    linked = session_manager.link_speaker_identity(table.id, "player_a", "马斯克")
    assert linked["speaker_id"] == "player_a"
    assert linked["display_label"] == "宝宝"
    assert linked["linked_name"] == "马斯克"
    assert linked["bridge_active"] is True
    assert linked["aliases"] == ["宝宝", "马斯克"]

    bridged = session_manager.format_user_utterance(
        text="我们现在开始",
        table_id=table.id,
        speaker_id="player_a",
    )
    assert bridged == "宝宝（马斯克）：我们现在开始"

    session_manager.finish_identity_bridge_after_compaction(table.id)

    renamed = session_manager.format_user_utterance(
        text="继续",
        table_id=table.id,
        speaker_id="player_a",
    )
    assert renamed == "马斯克：继续"

def test_pick_speaker_alias_returns_none_when_pool_is_empty_and_can_choose_from_aliases(session_manager):
    import random

    table = session_manager.start_table(name="Alias Pool Table")
    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_a",
            "status": "linked",
            "display_label": "Player A",
            "aliases": ["Musk", "Fat Tiger"],
        },
    )
    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_b",
            "status": "anonymous",
            "display_label": "Player B",
            "aliases": [],
        },
    )

    chosen = session_manager.choose_speaker_alias(table.id, "player_a", rng=random.Random(7))

    assert chosen in {"宝宝", "Musk", "Fat Tiger"}
    assert session_manager.choose_speaker_alias(table.id, "player_b", rng=random.Random(7)) == "宝宝"


def test_speaker_alias_updates_preserve_default_baby_alias(session_manager):
    table = session_manager.start_table(name="Alias Default Table")
    session_manager.observe_speaker_identity(
        table.id,
        {
            "speaker_id": "player_a",
            "status": "linked",
            "aliases": ["大雄"],
        },
    )

    assert session_manager.list_speaker_alias_map(table.id)["player_a"] == ["宝宝", "大雄"]

    session_manager.apply_speaker_alias_map(table.id, {"player_a": ["胖虎"]})

    assert session_manager.list_speaker_alias_map(table.id)["player_a"] == ["宝宝", "胖虎"]


def test_commit_live_transcript_prefers_latest_live_speaker_identity(session_manager):
    table = session_manager.start_table(name="Live Speaker Table")
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="we should move",
        speaker_id="player_c",
        speaker_label="玩家C",
    )

    committed = session_manager.commit_live_transcript(
        table.id,
        source="live_asr",
        live_session_id="session-1",
    )

    assert committed is not None
    assert committed["content"] == "玩家C：we should move"
    assert session_manager.tables[table.id].latest_live_stable_speaker_id is None
    assert session_manager.tables[table.id].latest_live_stable_speaker_label is None


def test_latest_live_speaker_context_id_is_preserved_for_reconnect(session_manager):
    table = session_manager.start_table(name="Context Table")
    session_manager.upsert_live_transcript(
        table_id=table.id,
        live_session_id="session-1",
        slice_index=0,
        content="hello there",
        speaker_id="speaker_7",
        speaker_label="speaker_7",
        speaker_context_id="ctx-777",
    )

    committed = session_manager.commit_live_transcript(
        table.id,
        source="live_asr",
        live_session_id="session-1",
    )

    assert committed is not None
    assert session_manager.tables[table.id].latest_live_speaker_context_id == "ctx-777"


def test_load_from_store_hydrates_tables():
    from gamevoice_server.table_store import InMemoryTableStore
    store = InMemoryTableStore()
    now = "2026-05-16T00:00:00Z"
    store.create_table({
        "id": "table-1",
        "name": "测试桌",
        "assistant_name": "宝子",
        "assistant_personality": "温柔体贴",
        "assistant_voice_id": "provider-voice-placeholder-1",
        "status": "active",
        "created_at": now,
        "last_active_at": now,
    })
    store.append_message("table-1", {"kind": "voice_transcript", "content": "玩家A：你好"})
    manager = SessionManager()
    manager.load_from_store(store)
    assert "table-1" in manager.tables
    assert manager.tables["table-1"].name == "测试桌"
    assert manager.tables["table-1"].assistant_voice_id == "provider-voice-placeholder-1"
    messages = manager.list_raw_context("table-1")
    assert len(messages) == 1
    assert messages[0]["content"] == "玩家A：你好"
    store.close()


def test_store_write_through_append_message(tmp_path):
    from gamevoice_server.table_store import SQLiteTableStore
    store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
    manager = SessionManager(store=store)
    table = manager.start_table(name="persist_test", assistant_name="宝子", assistant_voice_id="sweet_girl")
    table_id = table.id
    manager.append_context_event(table_id, {"kind": "voice_transcript", "content": "玩家A：你好"})

    manager2 = SessionManager(store=store)
    manager2.load_from_store(store)
    assert table_id in manager2.tables
    messages = manager2.list_raw_context(table_id)
    assert len(messages) == 1
    assert messages[0]["content"] == "玩家A：你好"
    assert manager2.tables[table_id].assistant_voice_id == "sweet_girl"
    store.close()
