def test_document_reading_store_reuses_last_result(reading_store):
    reading_store.save_result(
        table_id="t1",
        document_id="doc-1",
        mode="summary",
        result={"mode": "summary", "content": "cached"},
    )
    cached = reading_store.load_latest_result(table_id="t1", document_id="doc-1", mode="summary")
    assert cached["content"] == "cached"

