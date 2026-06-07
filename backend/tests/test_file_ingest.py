def test_batch_file_upload_returns_one_notification(file_ingest):
    result = file_ingest.ingest_files(
        table_id="t1",
        files=[
            {"filename": "b-script.txt", "data": b"scene b"},
            {"filename": "a-script.txt", "data": b"scene a"},
        ],
    )
    assert result["notifications"] == 1
    assert [item["filename"] for item in result["records"]] == ["a-script.txt", "b-script.txt"]

