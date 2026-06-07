from .models import TableSession


class TableRegistry:
    def __init__(self) -> None:
        self._tables: dict[str, TableSession] = {}

    def register(self, table: TableSession) -> None:
        self._tables[table.id] = table

    def get(self, table_id: str) -> TableSession | None:
        return self._tables.get(table_id)

