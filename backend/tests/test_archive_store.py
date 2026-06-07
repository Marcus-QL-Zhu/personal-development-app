from gamevoice_server.archive_store import ArchiveStore


def test_archive_store_persists_full_table_context():
    archive_store = ArchiveStore()
    archive_store.save(table_id="t1", payload={"turns": 3})
    saved = archive_store.load(table_id="t1")
    assert saved["turns"] == 3


def test_archive_store_can_get_compaction_snapshot_by_id():
    archive_store = ArchiveStore()
    archive_store.save_compaction_snapshot(
        "t1",
        {"compaction_id": "cmp-1", "snapshot_name": "20260505_dialog_1", "source_text": "hello"},
    )
    fetched = archive_store.get_compaction_snapshot("t1", "cmp-1")
    assert fetched["snapshot_name"] == "20260505_dialog_1"
    assert fetched["source_text"] == "hello"
