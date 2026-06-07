def test_document_reader_defaults_to_summary(document_reader):
    result = document_reader.read(table_id="t1", document_id="doc-1", document_text="full scenario text")
    assert result["kind"] == "document_summary"
    assert result["mode"] == "summary"
    assert result["scope"] == "whole_file"


def test_document_reader_returns_original_when_requested(document_reader):
    result = document_reader.read(
        table_id="t1",
        document_id="doc-1",
        document_text="full scenario text",
        mode="original",
    )
    assert result["kind"] == "document_original"
    assert result["mode"] == "original"

