import gamevoice_server.main as main_module


def test_apply_memory_compaction_rotates_active_context_with_summary_block(session_manager):
    table = session_manager.start_table(name="Compaction Table")
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "PlayerA: let's open the table",
        },
    )
    session_manager.append_context_event(
        table.id,
        {
            "kind": "assistant_spoken",
            "source": "companion",
            "content": "Baozi: okay, I will referee this round.",
        },
    )
    session_manager.append_context_event(
        table.id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "PlayerB: then I will play first",
        },
    )

    session_manager.apply_memory_compaction(
        table.id,
        checkpoint=2,
        summary_text="14:00-14:03 The table opened and Baozi agreed to referee.",
        compaction_id="cmp-1",
    )

    visible = session_manager.list_context(table.id)

    assert [item["kind"] for item in visible] == ["context_summary", "voice_transcript"]
    assert visible[0]["content"] == "14:00-14:03 The table opened and Baozi agreed to referee."
    assert visible[1]["content"] == "PlayerB: then I will play first"


class StubMemoryCompactor:
    def compact(self, payload: dict) -> dict:
        assert payload["active_events"]
        return {
            "status": "compacted",
            "summary_text": "14:00-14:05 Players opened the table and confirmed the referee.",
            "metadata": {
                "style": "narrative",
                "input_event_count": len(payload["active_events"]),
            },
        }


class BlockingMemoryCompactor:
    def __init__(self) -> None:
        import threading

        self.started = threading.Event()
        self.release = threading.Event()

    def compact(self, payload: dict) -> dict:
        self.started.set()
        self.release.wait(timeout=2)
        return {
            "status": "compacted",
            "summary_text": "blocked summary",
            "metadata": {"input_event_count": len(payload["active_events"])},
        }


def test_memory_compaction_api_rotates_context_and_preserves_new_tail(client):
    created = client.post("/tables", json={"name": "Memory Table"})
    table_id = created.json()["id"]
    main_module.session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "PlayerA: let's open the table",
        },
    )
    main_module.session_manager.append_context_event(
        table_id,
        {
            "kind": "assistant_spoken",
            "source": "companion",
            "content": "Baozi: okay, I will referee this round.",
        },
    )

    original_worker = main_module.memory_compaction_service.worker
    main_module.memory_compaction_service.worker = StubMemoryCompactor()
    try:
        queued = client.post(f"/tables/{table_id}/memory/compact")
        assert queued.status_code == 200
        compaction_id = queued.json()["compaction_id"]
        main_module.memory_compaction_service.wait(compaction_id, timeout=1)

        main_module.session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "PlayerB: then I will play first",
            },
        )

        fetched = client.get(f"/tables/{table_id}/memory/compactions/{compaction_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "completed"
        assert fetched.json()["snapshot_name"].startswith("20")
        assert fetched.json()["summary_text"] == (
            "14:00-14:05 Players opened the table and confirmed the referee."
        )

        source = client.get(f"/tables/{table_id}/memory/compactions/{compaction_id}/source")
        assert source.status_code == 200
        assert source.json()["snapshot_name"] == fetched.json()["snapshot_name"]
        assert "previous summary" not in source.json()["source_text"].lower()
        assert "PlayerA: let's open the table" in source.json()["source_text"]
        assert "Baozi: okay, I will referee this round." in source.json()["source_text"]

        context = client.get(f"/tables/{table_id}/context")
        assert context.status_code == 200
        assert [item["kind"] for item in context.json()["events"]] == [
            "context_summary",
            "voice_transcript",
        ]
        assert context.json()["events"][0]["content"] == (
            "14:00-14:05 Players opened the table and confirmed the referee."
        )
        assert context.json()["events"][1]["content"] == "PlayerB: then I will play first"
    finally:
        main_module.memory_compaction_service.worker = original_worker


def test_memory_compaction_payload_uses_previous_summary_and_only_new_tail(session_manager):
    table = session_manager.start_table(name="Compaction Payload Table")
    session_manager.append_context_event(
        table.id,
        {"kind": "voice_transcript", "source": "live_asr", "content": "PlayerA: open table"},
    )
    session_manager.append_context_event(
        table.id,
        {"kind": "assistant_spoken", "source": "companion", "content": "Baozi: I will referee."},
    )
    session_manager.apply_memory_compaction(
        table.id,
        checkpoint=2,
        summary_text="The table opened and Baozi became referee.",
        compaction_id="cmp-1",
    )
    session_manager.append_context_event(
        table.id,
        {"kind": "voice_transcript", "source": "live_asr", "content": "PlayerB: I play first"},
    )

    payload = session_manager.build_memory_compaction_payload(table.id)

    assert payload["previous_summary"] == "The table opened and Baozi became referee."
    assert [item["kind"] for item in payload["active_events"]] == ["voice_transcript"]
    assert payload["active_events"][0]["content"] == "PlayerB: I play first"


def test_memory_compaction_auto_triggers_after_context_crosses_threshold(client, monkeypatch):
    created = client.post("/tables", json={"name": "Auto Compact Table"})
    table_id = created.json()["id"]

    original_worker = main_module.memory_compaction_service.worker
    monkeypatch.setattr(main_module, "MEMORY_COMPACTION_TOKEN_THRESHOLD", 20)
    main_module.memory_compaction_service.worker = StubMemoryCompactor()
    try:
        main_module.session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "PlayerA: this line is definitely long enough to cross the threshold",
            },
        )

        records = main_module.memory_compaction_store.list_for_table(table_id)
        assert len(records) == 1
        compaction_id = records[0]["compaction_id"]
        main_module.memory_compaction_service.wait(compaction_id, timeout=1)

        fetched = client.get(f"/tables/{table_id}/memory/compactions/{compaction_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "completed"
        context = client.get(f"/tables/{table_id}/context")
        assert context.json()["events"][0]["kind"] == "context_summary"
    finally:
        main_module.memory_compaction_service.worker = original_worker


def test_memory_compaction_auto_trigger_skips_when_one_is_already_running(client, monkeypatch):
    created = client.post("/tables", json={"name": "Single Running Auto Compact Table"})
    table_id = created.json()["id"]

    worker = BlockingMemoryCompactor()
    original_worker = main_module.memory_compaction_service.worker
    monkeypatch.setattr(main_module, "MEMORY_COMPACTION_TOKEN_THRESHOLD", 20)
    main_module.memory_compaction_service.worker = worker
    try:
        main_module.session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "PlayerA: this line is definitely long enough to cross the threshold",
            },
        )
        assert worker.started.wait(timeout=1)

        main_module.session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": "PlayerB: this second line should not queue another compaction yet",
            },
        )

        records = main_module.memory_compaction_store.list_for_table(table_id)
        assert len(records) == 1
        assert records[0]["status"] in {"queued", "running"}
    finally:
        worker.release.set()
        records = main_module.memory_compaction_store.list_for_table(table_id)
        if records:
            main_module.memory_compaction_service.wait(records[0]["compaction_id"], timeout=1)
        main_module.memory_compaction_service.worker = original_worker
