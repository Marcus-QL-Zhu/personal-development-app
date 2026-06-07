class FileIngestor:
    def __init__(self, store) -> None:
        self.store = store

    def ingest_files(self, table_id: str, files: list[dict]) -> dict:
        for item in files:
            self.store.save(table_id, item["filename"], item["data"])
        records = [
            {
                "kind": item["kind"],
                "table_id": item["table_id"],
                "filename": item["filename"],
                "size_bytes": item.get("size_bytes", 0),
                "visibility": item["visibility"],
                "status": item["status"],
                "origin": item["origin"],
            }
            for item in self.store.list_documents(table_id)
        ]
        return {
            "notifications": 1,
            "records": records,
            "message": self.store.make_echo_batch([item["filename"] for item in files])["message"],
        }
