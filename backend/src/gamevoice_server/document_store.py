from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class DocumentStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        configured_root = root_dir or os.getenv("GAMEVOICE_UPLOAD_DIR")
        self.root_dir = Path(configured_root) if configured_root else _PROJECT_ROOT / "uploads"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.documents: list[dict] = []
        self._load_from_disk()

    def _table_dir(self, table_id: str) -> Path:
        safe_table = self._safe_path_segment(table_id)
        path = self.root_dir / safe_table
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _manifest_path(self, table_id: str) -> Path:
        return self._table_dir(table_id) / "manifest.json"

    @staticmethod
    def _safe_path_segment(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()) or "table"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        candidate = Path(str(filename or "uploaded-file").replace("\\", "/")).name.strip()
        return candidate or "uploaded-file"

    @staticmethod
    def _display_filename(base: str, index: int) -> str:
        path = Path(base)
        stem = path.stem or path.name
        suffix = path.suffix
        return base if index == 0 else f"{stem} ({index}){suffix}"

    @staticmethod
    def _storage_filename(display_filename: str) -> str:
        return DocumentStore._safe_path_segment(display_filename)

    def _next_available_filename(self, table_id: str, filename: str) -> str:
        base = self._safe_filename(filename)
        existing = {item["filename"].lower() for item in self.list_documents(table_id)}
        index = 0
        while True:
            candidate = self._display_filename(base, index)
            if candidate.lower() not in existing:
                return candidate
            index += 1

    def _write_manifest(self, table_id: str) -> None:
        records = []
        for item in self.list_documents(table_id):
            record = {key: value for key, value in item.items() if key != "data"}
            records.append(record)
        self._manifest_path(table_id).write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        for manifest in self.root_dir.glob("*/manifest.json"):
            table_id = manifest.parent.name
            try:
                records = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                stored_filename = str(record.get("stored_filename") or "")
                filename = str(record.get("filename") or "")
                file_path = manifest.parent / stored_filename
                if not stored_filename or not filename or not file_path.exists():
                    continue
                data = file_path.read_bytes()
                self.documents.append(
                    {
                        "kind": "document",
                        "table_id": str(record.get("table_id") or table_id),
                        "filename": filename,
                        "stored_filename": stored_filename,
                        "data": data,
                        "size_bytes": int(record.get("size_bytes") or len(data)),
                        "uploaded_at": record.get("uploaded_at") or "",
                        "visibility": record.get("visibility") or "document_space",
                        "status": record.get("status") or "stored",
                        "origin": record.get("origin") or "file",
                    }
                )

    def save(self, table_id: str, filename: str, data: bytes) -> dict:
        display_filename = self._next_available_filename(table_id, filename)
        stored_filename = self._storage_filename(display_filename)
        table_dir = self._table_dir(table_id)
        file_path = table_dir / stored_filename
        file_path.write_bytes(data)
        record = {
            "kind": "document",
            "table_id": table_id,
            "filename": display_filename,
            "stored_filename": stored_filename,
            "data": data,
            "size_bytes": len(data),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "visibility": "document_space",
            "status": "stored",
            "origin": "file",
        }
        self.documents.append(record)
        self._write_manifest(table_id)
        return record

    def list_documents(self, table_id: str) -> list[dict]:
        return sorted(
            [item for item in self.documents if item["table_id"] == table_id],
            key=lambda item: item["filename"].lower(),
        )

    def resolve_by_partial_name(self, table_id: str, query: str) -> dict:
        needle = query.lower()
        matches = [item for item in self.list_documents(table_id) if needle in item["filename"].lower()]
        if len(matches) == 1:
            return {"status": "resolved", "document": matches[0]}
        return {"status": "ambiguous", "candidates": [item["filename"] for item in matches]}

    def stats(self, table_id: str) -> dict:
        documents = self.list_documents(table_id)
        return {
            "document_count": len(documents),
            "document_total_bytes": sum(int(item.get("size_bytes") or len(item.get("data") or b"")) for item in documents),
        }

    def delete(self, table_id: str, filename: str) -> dict:
        for index, item in enumerate(self.documents):
            if item["table_id"] != table_id or item["filename"] != filename:
                continue
            removed = self.documents.pop(index)
            stored_filename = str(removed.get("stored_filename") or self._storage_filename(filename))
            path = self._table_dir(table_id) / stored_filename
            if path.exists():
                path.unlink()
            self._delete_extracted_cache(table_id, filename, stored_filename)
            self._write_manifest(table_id)
            return removed
        raise FileNotFoundError(filename)

    def _delete_extracted_cache(self, table_id: str, filename: str, stored_filename: str) -> None:
        cache_dir = self._table_dir(table_id) / ".extracted"
        for stem in {stored_filename, self._storage_filename(filename), filename}:
            for suffix in (".txt", ".meta.json", ".inspect.json"):
                path = cache_dir / f"{stem}{suffix}"
                if path.exists():
                    path.unlink()
        if cache_dir.exists() and not any(cache_dir.iterdir()):
            shutil.rmtree(cache_dir, ignore_errors=True)

    def make_echo(self, filename: str, guessed_type: str) -> dict:
        return {
            "message": f"我看到你刚刚传给我一个文件 `{filename}`，看起来像{guessed_type}，但我不太确定。要我现在读一下吗？"
        }

    def make_echo_batch(self, filenames: list[str]) -> dict:
        joined = "、".join(filenames)
        return {"message": f"我看到你刚刚传了 {len(filenames)} 个文件：{joined}。要看详情的话，点开一个文件名就行。"}
