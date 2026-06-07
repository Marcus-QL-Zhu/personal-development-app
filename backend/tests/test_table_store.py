import pytest
from gamevoice_server.table_store import TableStore, TableStoreError

def test_store_interface_is_abstract():
    with pytest.raises(TypeError, match="abstract"):
        TableStore()

def test_store_list_returns_list():
    # Minimal concrete implementation for interface compliance test
    class MinimalStore(TableStore):
        def __init__(self):
            self._tables = {}
        def list_tables(self) -> list[dict]:
            return list(self._tables.values())
        def create_table(self, metadata: dict) -> dict:
            self._tables[metadata["id"]] = dict(metadata)
            return self._tables[metadata["id"]]
        def get_table(self, table_id: str) -> dict | None:
            return self._tables.get(table_id)
        def update_table_metadata(self, table_id: str, metadata: dict) -> None:
            if table_id in self._tables:
                self._tables[table_id].update(metadata)
        def delete_table(self, table_id: str) -> None:
            self._tables.pop(table_id, None)
        def append_message(self, table_id: str, event: dict) -> int:
            return 0
        def list_messages(self, table_id: str) -> list[dict]:
            return []
        def append_runtime_event(self, table_id: str, event: dict) -> int:
            return 0
        def list_runtime_events(self, table_id: str) -> list[dict]:
            return []
        def append_assistant_reply(self, table_id: str, event: dict) -> int:
            return 0
        def list_assistant_replies(self, table_id: str) -> list[dict]:
            return []
        def get_speaker_identities(self, table_id: str) -> dict | None:
            return None
        def save_speaker_identities(self, table_id: str, identities: dict) -> None:
            pass
        def apply_compaction(self, table_id: str, checkpoint: int, summary_event: dict | None, compaction_version: int) -> None:
            pass
        def close(self) -> None:
            pass

    store = MinimalStore()
    assert isinstance(store.list_tables(), list)


import pytest
import tempfile
import os
from pathlib import Path

from gamevoice_server.table_store import SQLiteTableStore, InMemoryTableStore


class TestSQLiteTableStore:
    def test_creates_db_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = SQLiteTableStore(db_path=str(db_path))
        assert db_path.exists()
        store.close()

    def test_list_tables_empty(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        assert store.list_tables() == []
        store.close()

    def test_create_and_get_table(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        meta = {
            "id": "table-1",
            "name": "测试桌",
            "assistant_name": "宝子",
            "assistant_personality": "温柔体贴",
            "assistant_voice_id": "provider-voice-placeholder-1",
            "status": "active",
            "created_at": now,
            "last_active_at": now,
        }
        store.create_table(meta)
        retrieved = store.get_table("table-1")
        assert retrieved is not None
        assert retrieved["id"] == "table-1"
        assert retrieved["name"] == "测试桌"
        assert retrieved["assistant_voice_id"] == "provider-voice-placeholder-1"
        assert retrieved["compaction_checkpoint"] == 0
        store.close()

    def test_update_table_metadata(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "table-1", "name": "旧名", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        store.update_table_metadata("table-1", {"name": "新名", "last_active_at": "2026-05-16T01:00:00Z"})
        retrieved = store.get_table("table-1")
        assert retrieved["name"] == "新名"
        assert retrieved["last_active_at"] == "2026-05-16T01:00:00Z"
        store.close()

    def test_append_and_list_messages(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "table-1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        store.append_message("table-1", {"kind": "voice_transcript", "content": "玩家A：你好"})
        store.append_message("table-1", {"kind": "assistant_spoken", "content": "宝子：你好"})
        messages = store.list_messages("table-1")
        assert len(messages) == 2
        assert messages[0]["content"] == "玩家A：你好"
        assert messages[1]["content"] == "宝子：你好"
        store.close()

    def test_delete_table(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "table-1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        store.delete_table("table-1")
        assert store.get_table("table-1") is None
        assert store.list_messages("table-1") == []
        store.close()

    def test_speaker_identities_persist(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "table-1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        identities = {"player_a": {"speaker_id": "player_a", "linked_name": "马斯克"}}
        store.save_speaker_identities("table-1", identities)
        retrieved = store.get_speaker_identities("table-1")
        assert retrieved == identities
        store.close()

    def test_apply_compaction(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "table-1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        summary = {"kind": "context_summary", "content": "压缩摘要"}
        store.apply_compaction("table-1", checkpoint=10, summary_event=summary, compaction_version=1)
        retrieved = store.get_table("table-1")
        assert retrieved["compaction_checkpoint"] == 10
        assert retrieved["compaction_version"] == 1
        assert retrieved["compaction_summary"]["content"] == "压缩摘要"
        store.close()

    def test_list_tables_ordered_by_last_active(self, tmp_path):
        store = SQLiteTableStore(db_path=str(tmp_path / "test.db"))
        store.create_table({
            "id": "t1", "name": "t1", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": "2026-05-16T00:00:00Z",
            "last_active_at": "2026-05-16T01:00:00Z",
        })
        store.create_table({
            "id": "t2", "name": "t2", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": "2026-05-16T00:00:00Z",
            "last_active_at": "2026-05-16T02:00:00Z",
        })
        tables = store.list_tables()
        assert tables[0]["id"] == "t2"  # most recent first
        assert tables[1]["id"] == "t1"
        store.close()


class TestInMemoryTableStore:
    def test_create_and_get(self):
        store = InMemoryTableStore()
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "t1", "name": "测试", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "voice1",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        assert store.get_table("t1")["name"] == "测试"
        assert store.get_table("t1")["assistant_voice_id"] == "voice1"
        store.close()

    def test_append_and_list_messages(self):
        store = InMemoryTableStore()
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "t1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        store.append_message("t1", {"kind": "voice_transcript", "content": "hello"})
        messages = store.list_messages("t1")
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"
        store.close()

    def test_delete_table(self):
        store = InMemoryTableStore()
        now = "2026-05-16T00:00:00Z"
        store.create_table({
            "id": "t1", "name": "t", "assistant_name": "宝子",
            "assistant_personality": "", "assistant_voice_id": "",
            "status": "active", "created_at": now, "last_active_at": now,
        })
        store.delete_table("t1")
        assert store.get_table("t1") is None
        store.close()