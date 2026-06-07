from gamevoice_server.rule_analysis_store import RuleAnalysisStore


def test_rule_analysis_store_tracks_status_lifecycle():
    store = RuleAnalysisStore()

    created = store.create(table_id="table-1", query="how does this rule work", ack_text="我去查一下")
    assert created["status"] == "queued"
    assert created["table_id"] == "table-1"

    running = store.mark_running(created["analysis_id"])
    assert running["status"] == "running"

    completed = store.mark_completed(
        created["analysis_id"],
        {
            "source": "local",
            "content": "Final answer.",
            "lead": "我查到了。",
            "tail": "Final answer.",
        },
    )
    assert completed["status"] == "completed"
    assert completed["result"]["content"] == "Final answer."


def test_rule_analysis_store_lists_records_by_table():
    store = RuleAnalysisStore()

    first = store.create(table_id="table-1", query="first", ack_text="ack")
    second = store.create(table_id="table-1", query="second", ack_text="ack")
    other = store.create(table_id="table-2", query="other", ack_text="ack")

    listed = store.list_for_table("table-1")

    assert [item["analysis_id"] for item in listed] == [second["analysis_id"], first["analysis_id"]]
    assert other["analysis_id"] not in [item["analysis_id"] for item in listed]


def test_rule_analysis_store_keeps_only_latest_five_recent_results():
    store = RuleAnalysisStore(max_recent_results_per_table=5)

    for index in range(6):
        record = store.create(table_id="table-1", query=f"q{index}", ack_text="ack")
        store.mark_completed(
            record["analysis_id"],
            {
                "source": "local",
                "content": f"answer-{index}",
            },
        )

    cached = store.list_recent_results("table-1")

    assert [item["query"] for item in cached] == ["q5", "q4", "q3", "q2", "q1"]
    assert [item["result"]["content"] for item in cached] == [
        "answer-5",
        "answer-4",
        "answer-3",
        "answer-2",
        "answer-1",
    ]


def test_rule_analysis_store_lists_pending_materializations_and_marks_materialized():
    store = RuleAnalysisStore()
    first = store.create(table_id="table-1", query="first", ack_text="ack")
    second = store.create(table_id="table-1", query="second", ack_text="ack")

    store.mark_completed(first["analysis_id"], {"source": "local", "content": "first-answer"})
    store.mark_completed(second["analysis_id"], {"source": "local", "content": "second-answer"})

    pending = store.list_pending_materializations("table-1")
    assert [item["analysis_id"] for item in pending] == [first["analysis_id"], second["analysis_id"]]

    store.mark_materialized(first["analysis_id"])
    pending_after = store.list_pending_materializations("table-1")
    assert [item["analysis_id"] for item in pending_after] == [second["analysis_id"]]


def test_rule_analysis_store_detects_active_duplicate_query_by_table():
    store = RuleAnalysisStore()
    first = store.create(table_id="table-1", query="  Check   Weather  ", ack_text="ack")

    assert store.has_active_query(table_id="table-1", query="check weather") is True
    assert store.has_active_query(table_id="table-1", query="other query") is False
    assert store.has_active_query(table_id="table-2", query="check weather") is False

    store.mark_completed(first["analysis_id"], {"source": "local", "content": "done"})
    assert store.has_active_query(table_id="table-1", query="check weather") is False
