import pytest

from gamevoice_server.document_store import DocumentStore


def test_documents_are_listed_in_filename_order(document_store):
    document_store.save(table_id="t1", filename="b.txt", data=b"b")
    document_store.save(table_id="t1", filename="a.txt", data=b"a")
    names = [item["filename"] for item in document_store.list_documents(table_id="t1")]
    assert names == ["a.txt", "b.txt"]


def test_partial_filename_match_lists_candidates_on_ambiguity(document_store):
    document_store.save(table_id="t1", filename="arkham-scenario-1.txt", data=b"one")
    document_store.save(table_id="t1", filename="arkham-scenario-2.txt", data=b"two")
    result = document_store.resolve_by_partial_name(table_id="t1", query="arkham-scenario")
    assert result["status"] == "ambiguous"
    assert result["candidates"] == ["arkham-scenario-1.txt", "arkham-scenario-2.txt"]


def test_pdf_upload_is_persisted(document_store):
    document_store.save(table_id="t1", filename="script.pdf", data=b"%PDF")

    documents = document_store.list_documents(table_id="t1")

    assert documents[0]["filename"] == "script.pdf"
    assert documents[0]["size_bytes"] == 4


def test_document_upload_echo_mentions_filename_and_uncertainty(document_store):
    echo = document_store.make_echo(filename="xxx.pdf", guessed_type="某类文件")
    assert "xxx.pdf" in echo["message"]
    assert "不太确定" in echo["message"]


def test_documents_are_persisted_and_reloaded_from_disk(tmp_path):
    first = DocumentStore(root_dir=tmp_path)
    first.save(table_id="t1", filename="rules.txt", data=b"full rules")

    reloaded = DocumentStore(root_dir=tmp_path)
    documents = reloaded.list_documents(table_id="t1")

    assert [item["filename"] for item in documents] == ["rules.txt"]
    assert documents[0]["data"] == b"full rules"
    assert documents[0]["size_bytes"] == len(b"full rules")


def test_same_table_duplicate_filenames_are_renamed_on_save(tmp_path):
    store = DocumentStore(root_dir=tmp_path)

    first = store.save(table_id="t1", filename="rules.txt", data=b"one")
    second = store.save(table_id="t1", filename="rules.txt", data=b"two")

    assert first["filename"] == "rules.txt"
    assert second["filename"] == "rules (1).txt"
    assert [item["filename"] for item in store.list_documents("t1")] == [
        "rules (1).txt",
        "rules.txt",
    ]
    assert store.resolve_by_partial_name("t1", "rules (1)")["document"]["data"] == b"two"


def test_document_stats_and_delete_are_scoped_to_table(tmp_path):
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="t1", filename="a.txt", data=b"aaa")
    store.save(table_id="t1", filename="b.txt", data=b"bb")
    store.save(table_id="t2", filename="a.txt", data=b"x")

    assert store.stats(table_id="t1") == {"document_count": 2, "document_total_bytes": 5}

    deleted = store.delete(table_id="t1", filename="a.txt")

    assert deleted["filename"] == "a.txt"
    assert store.stats(table_id="t1") == {"document_count": 1, "document_total_bytes": 2}
    assert store.stats(table_id="t2") == {"document_count": 1, "document_total_bytes": 1}
    assert [item["filename"] for item in store.list_documents("t2")] == ["a.txt"]


def test_delete_removes_pdf_extracted_text_cache(tmp_path):
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="t1", filename="rules.pdf", data=b"%PDF")
    cache_dir = tmp_path / "t1" / ".extracted"
    cache_dir.mkdir()
    (cache_dir / "rules.pdf.txt").write_text("cached text", encoding="utf-8")
    (cache_dir / "rules.pdf.meta.json").write_text("{}", encoding="utf-8")

    store.delete(table_id="t1", filename="rules.pdf")

    assert not (cache_dir / "rules.pdf.txt").exists()
    assert not (cache_dir / "rules.pdf.meta.json").exists()


def test_delete_removes_uploaded_file_inspection_cache(tmp_path):
    store = DocumentStore(root_dir=tmp_path)
    store.save(table_id="t1", filename="rules.pdf", data=b"%PDF")
    cache_dir = tmp_path / "t1" / ".extracted"
    cache_dir.mkdir()
    (cache_dir / "rules.pdf.inspect.json").write_text("{}", encoding="utf-8")

    store.delete(table_id="t1", filename="rules.pdf")

    assert not (cache_dir / "rules.pdf.inspect.json").exists()
