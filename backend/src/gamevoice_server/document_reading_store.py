class DocumentReadingStore:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], dict] = {}

    def save_result(self, table_id: str, document_id: str, mode: str, result: dict) -> None:
        self._cache[(table_id, document_id, mode)] = result

    def load_latest_result(self, table_id: str, document_id: str, mode: str) -> dict | None:
        return self._cache.get((table_id, document_id, mode))

