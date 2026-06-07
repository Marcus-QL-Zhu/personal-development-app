class DocumentReaderWorker:
    def __init__(self, store, summarizer) -> None:
        self.store = store
        self.summarizer = summarizer

    def read(self, table_id: str, document_id: str, document_text: str, mode: str = "summary") -> dict:
        cached = self.store.load_latest_result(table_id=table_id, document_id=document_id, mode=mode)
        if cached is not None:
            return cached

        if mode == "original":
            result = {
                "kind": "document_original",
                "mode": "original",
                "scope": "whole_file",
                "content": document_text,
            }
        else:
            result = {
                "kind": "document_summary",
                "mode": "summary",
                "scope": "whole_file",
                "content": self.summarizer.summarize(document_text),
            }
        self.store.save_result(table_id=table_id, document_id=document_id, mode=mode, result=result)
        return result

