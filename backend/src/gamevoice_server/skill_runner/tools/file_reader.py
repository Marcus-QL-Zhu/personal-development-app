from __future__ import annotations

import json
import os
import re
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..tool_registry import Tool, ToolResult

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
}
_PDFPLUMBER_VERSION = "0.11.9"
_DOCX_EXTRACTOR_VERSION = "zip-xml-1"
_TEXT_READER_VERSION = "utf8-replace-1"
_DOCUMENT_MAP_VERSION = "document-map-1"


def _upload_base() -> Path:
    return Path(os.getenv("GAMEVOICE_UPLOAD_DIR") or _PROJECT_ROOT / "uploads")


def _safe_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()) or "table"


def _table_dir(table_id: str) -> Path:
    return _upload_base() / _safe_path_segment(table_id)


def _load_records(table_id: str) -> list[dict]:
    manifest = _table_dir(table_id) / "manifest.json"
    if not manifest.exists():
        return []
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [record for record in records if isinstance(record, dict)]


def _record_path(table_id: str, record: dict) -> Path:
    stored_filename = str(record.get("stored_filename") or "")
    return (_table_dir(table_id) / stored_filename).resolve()


def _extracted_dir(table_id: str) -> Path:
    path = _table_dir(table_id) / ".extracted"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_stem(record: dict, path: Path) -> str:
    return str(record.get("stored_filename") or path.name)


def _pdf_cache_paths(table_id: str, record: dict, path: Path) -> tuple[Path, Path]:
    stem = _cache_stem(record, path)
    cache_dir = _extracted_dir(table_id)
    return cache_dir / f"{stem}.txt", cache_dir / f"{stem}.meta.json"


def _inspect_cache_path(table_id: str, record: dict, path: Path) -> Path:
    return _extracted_dir(table_id) / f"{_cache_stem(record, path)}.inspect.json"


def _assert_scoped(path: Path, table_id: str) -> None:
    table_dir = _table_dir(table_id).resolve()
    if not str(path).startswith(str(table_dir)):
        raise ValueError("access denied: path outside current table uploads")


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber 0.11.9 is required to search PDF uploads") from exc

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[Page {index}]\n{text}")
    return "\n".join(pages)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_pdf_text(table_id: str, record: dict, path: Path) -> str:
    text_path, meta_path = _pdf_cache_paths(table_id, record, path)
    size_bytes = path.stat().st_size
    sha256 = _file_sha256(path)

    if text_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if (
            meta.get("size_bytes") == size_bytes
            and meta.get("sha256") == sha256
            and meta.get("pdfplumber_version") == _PDFPLUMBER_VERSION
        ):
            status = str(meta.get("status") or "ok")
            if status == "ok":
                return text_path.read_text(encoding="utf-8")
            raise RuntimeError(str(meta.get("error") or "PDF text extraction failed"))

    try:
        text = _extract_pdf_text(path)
    except Exception as exc:  # noqa: BLE001
        meta_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "pdfplumber_version": _PDFPLUMBER_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise

    if not text.strip():
        meta_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": "no extractable text",
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "pdfplumber_version": _PDFPLUMBER_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise RuntimeError("no extractable text")

    text_path.write_text(text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "size_bytes": size_bytes,
                "sha256": sha256,
                "pdfplumber_version": _PDFPLUMBER_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _cached_docx_text(table_id: str, record: dict, path: Path) -> str:
    text_path, meta_path = _pdf_cache_paths(table_id, record, path)
    size_bytes = path.stat().st_size
    sha256 = _file_sha256(path)

    if text_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if (
            meta.get("size_bytes") == size_bytes
            and meta.get("sha256") == sha256
            and meta.get("docx_extractor_version") == _DOCX_EXTRACTOR_VERSION
        ):
            status = str(meta.get("status") or "ok")
            if status == "ok":
                return text_path.read_text(encoding="utf-8")
            raise RuntimeError(str(meta.get("error") or "DOCX text extraction failed"))

    try:
        text = _extract_docx_text(path)
    except Exception as exc:  # noqa: BLE001
        meta_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "docx_extractor_version": _DOCX_EXTRACTOR_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise

    if not text.strip():
        meta_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": "no extractable text",
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "docx_extractor_version": _DOCX_EXTRACTOR_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise RuntimeError("no extractable text")

    text_path.write_text(text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "size_bytes": size_bytes,
                "sha256": sha256,
                "docx_extractor_version": _DOCX_EXTRACTOR_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


def _read_text(path: Path, *, table_id: str, record: dict) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _cached_pdf_text(table_id, record, path)
    if suffix == ".docx":
        return _cached_docx_text(table_id, record, path)
    if suffix in _TEXT_EXTENSIONS:
        return path.read_bytes().decode("utf-8", errors="replace")
    raise ValueError(f"unsupported file format: {suffix}")


def _reader_version_key(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdfplumber_version", _PDFPLUMBER_VERSION
    if suffix == ".docx":
        return "docx_extractor_version", _DOCX_EXTRACTOR_VERSION
    return "text_reader_version", _TEXT_READER_VERSION


def _matches_filename(record: dict, filename: str) -> bool:
    if not filename:
        return True
    return filename.lower() in str(record.get("filename") or "").lower()


def _looks_like_heading(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if len(text) > 90:
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]", text):
        return True
    if re.match(r"^第[一二三四五六七八九十0-9]+[章节部分]", text):
        return True
    if re.match(r"^[0-9]+[.)、．]\s*", text):
        return True
    if re.match(r"^#{1,6}\s+", text):
        return True
    if text.rstrip("：:") in {
        "核心观点",
        "适用场景",
        "关键要点",
        "推荐做法",
        "常见问题",
        "注意事项",
        "一句话总结",
        "参考资料",
        "参考论文与资料来源",
    }:
        return True
    if text.endswith(("：", ":")) and len(text) <= 60:
        return True
    return False


def _compact_line(line: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", line).strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _build_document_map(filename: str, text: str, *, max_sections: int = 12) -> dict:
    raw_lines = text.splitlines()
    nonempty: list[tuple[int, str]] = [
        (index, line.strip()) for index, line in enumerate(raw_lines, start=1) if line.strip()
    ]
    title = nonempty[0][1] if nonempty else filename
    heading_numbers: list[int] = []
    for line_number, line in nonempty:
        if line_number == 1 or _looks_like_heading(line):
            heading_numbers.append(line_number)

    heading_set = set(heading_numbers)
    line_by_number = {line_number: line for line_number, line in nonempty}
    sections: list[dict] = []
    for line_number in heading_numbers[:max_sections]:
        heading = line_by_number.get(line_number, "")
        preview: list[str] = []
        for next_number, next_line in nonempty:
            if next_number <= line_number:
                continue
            if next_number in heading_set:
                break
            preview.append(_compact_line(next_line))
            if len(preview) >= 2:
                break
        sections.append(
            {
                "line": line_number,
                "heading": _compact_line(heading),
                "preview": preview,
            }
        )

    return {
        "filename": filename,
        "title": _compact_line(title),
        "line_count": len(raw_lines),
        "char_count": len(text),
        "opening_lines": [
            {"line": line_number, "text": _compact_line(line)}
            for line_number, line in nonempty[:8]
        ],
        "sections": sections,
    }


def _format_document_map(document_map: dict) -> str:
    lines = [
        f"{document_map.get('filename', '')} 文档地图",
        f"标题：{document_map.get('title', '')}",
        f"规模：{document_map.get('line_count', 0)} 行，约 {document_map.get('char_count', 0)} 字符",
        "文档开头：",
    ]
    for item in document_map.get("opening_lines") or []:
        lines.append(f"{item.get('line')}: {item.get('text')}")
    sections = document_map.get("sections") or []
    if sections:
        lines.append("章节地图：")
        for section in sections:
            lines.append(f"{section.get('line')}: {section.get('heading')}")
            for preview in section.get("preview") or []:
                lines.append(f"  - {preview}")
    return "\n".join(lines)


def _cached_document_map(table_id: str, record: dict, path: Path, text: str | None = None) -> dict:
    cache_path = _inspect_cache_path(table_id, record, path)
    size_bytes = path.stat().st_size
    sha256 = _file_sha256(path)
    version_key, version_value = _reader_version_key(path)

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
        if (
            cached.get("status") == "ok"
            and cached.get("size_bytes") == size_bytes
            and cached.get("sha256") == sha256
            and cached.get("document_map_version") == _DOCUMENT_MAP_VERSION
            and cached.get(version_key) == version_value
            and isinstance(cached.get("map"), dict)
        ):
            return cached["map"]

    if text is None:
        text = _read_text(path, table_id=table_id, record=record)
    document_map = _build_document_map(str(record.get("filename") or path.name), text)
    cache_payload = {
        "status": "ok",
        "size_bytes": size_bytes,
        "sha256": sha256,
        "document_map_version": _DOCUMENT_MAP_VERSION,
        version_key: version_value,
        "map": document_map,
    }
    cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return document_map


def _inspect_execute(arguments: dict) -> ToolResult:
    table_id = str(arguments.get("_table_id") or "").strip()
    if not table_id:
        return ToolResult.failure("current table id is not available")

    filename = str(arguments.get("filename") or "").strip()
    max_files = max(1, min(int(arguments.get("max_files") or 3), 5))
    matches = [record for record in _load_records(table_id) if _matches_filename(record, filename)]
    if not matches:
        suffix = f": {filename}" if filename else ""
        return ToolResult.failure(f"file not found in current table uploads{suffix}")

    formatted: list[str] = []
    errors: list[str] = []
    for record in matches[:max_files]:
        display_name = str(record.get("filename") or "")
        path = _record_path(table_id, record)
        try:
            _assert_scoped(path, table_id)
            document_map = _cached_document_map(table_id, record, path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{display_name}: {exc}")
            continue
        formatted.append(_format_document_map(document_map))

    if formatted:
        return ToolResult.success("\n\n".join(formatted))
    return ToolResult.failure("no uploaded files could be inspected: " + "; ".join(errors[:3]))


def _search_execute(arguments: dict) -> ToolResult:
    table_id = str(arguments.get("_table_id") or "").strip()
    if not table_id:
        return ToolResult.failure("current table id is not available")

    query = str(arguments.get("query") or arguments.get("pattern") or "").strip()
    if not query:
        return ToolResult.failure("query cannot be empty")

    filename = str(arguments.get("filename") or "").strip()
    case_sensitive = bool(arguments.get("case_sensitive") or False)
    max_results = max(1, min(int(arguments.get("max_results") or 10), 30))
    needle = query if case_sensitive else query.lower()
    results: list[str] = []
    errors: list[str] = []

    for record in _load_records(table_id):
        if not _matches_filename(record, filename):
            continue
        display_name = str(record.get("filename") or "")
        path = _record_path(table_id, record)
        try:
            _assert_scoped(path, table_id)
            text = _read_text(path, table_id=table_id, record=record)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{display_name}: {exc}")
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if needle not in haystack:
                continue
            snippet = line.strip()
            results.append(f"{display_name}:{line_number}: {snippet[:240]}")
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

    if results:
        return ToolResult.success("\n".join(results))
    if errors:
        return ToolResult.failure("no matches; some files could not be searched: " + "; ".join(errors[:3]))
    return ToolResult.failure("no matches in current table uploads")


def _excerpt_execute(arguments: dict) -> ToolResult:
    table_id = str(arguments.get("_table_id") or "").strip()
    if not table_id:
        return ToolResult.failure("current table id is not available")

    filename = str(arguments.get("filename") or "").strip()
    if not filename:
        return ToolResult.failure("filename cannot be empty")

    line_number = max(1, int(arguments.get("line_number") or 1))
    around_lines = max(0, min(int(arguments.get("around_lines") or 3), 20))
    matches = [record for record in _load_records(table_id) if _matches_filename(record, filename)]
    if not matches:
        return ToolResult.failure(f"file not found in current table uploads: {filename}")
    if len(matches) > 1:
        names = ", ".join(str(record.get("filename") or "") for record in matches[:8])
        return ToolResult.failure(f"filename is ambiguous: {names}")

    record = matches[0]
    display_name = str(record.get("filename") or "")
    path = _record_path(table_id, record)
    try:
        _assert_scoped(path, table_id)
        lines = _read_text(path, table_id=table_id, record=record).splitlines()
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(f"{display_name}: {exc}")

    if not lines:
        return ToolResult.failure(f"{display_name}: no extractable text")

    start = max(0, line_number - around_lines - 1)
    end = min(len(lines), line_number + around_lines)
    excerpt = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
    return ToolResult.success(f"{display_name} lines {start + 1}-{end}:\n{excerpt}")


SEARCH_TOOL_SCHEMA = {
    "name": "search_uploaded_files",
    "description": (
        "Search current-table uploaded files for a literal text pattern. "
        "Use this before reading excerpts when the user asks about uploaded files, PDFs, "
        "rules documents, scripts, notes, or glossaries. Returns only matching lines/snippets, "
        "not full documents. The current table file list is already provided in the prompt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Literal text to search for, such as a card name, rule term, keyword, or phrase.",
            },
            "filename": {
                "type": "string",
                "description": "Optional partial filename to narrow the search when the prompt lists multiple files.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matching snippets to return. Default 10, maximum 30.",
            },
        },
        "required": ["query"],
    },
}


INSPECT_TOOL_SCHEMA = {
    "name": "inspect_uploaded_file",
    "description": (
        "Build or read a cached document map for current-table uploaded files. "
        "Use this before grep search when the user asks for a broad summary, main content, "
        "overall structure, outline, or 'what is in this uploaded file'. It returns title, "
        "opening lines, section headings, and short local previews without injecting the whole file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Optional partial filename to inspect. Leave empty only when the current table has one obvious file.",
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum files to inspect when filename is omitted or broad. Default 3, maximum 5.",
            },
        },
        "required": [],
    },
}


EXCERPT_TOOL_SCHEMA = {
    "name": "read_uploaded_file_excerpt",
    "description": (
        "Read a small excerpt around a known line from one current-table uploaded file. "
        "Use this only after search_uploaded_files has identified a relevant filename and line. "
        "Do not use it to read whole files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Exact or partial filename from the current table file list or search result.",
            },
            "line_number": {
                "type": "integer",
                "description": "Line number reported by search_uploaded_files.",
            },
            "around_lines": {
                "type": "integer",
                "description": "Number of surrounding lines to include. Default 3, maximum 20.",
            },
        },
        "required": ["filename", "line_number"],
    },
}


def build_uploaded_file_inspect_tool() -> Tool:
    return Tool(
        name="inspect_uploaded_file",
        description=INSPECT_TOOL_SCHEMA["description"],
        parameters=INSPECT_TOOL_SCHEMA["parameters"],
        execute=_inspect_execute,
    )


def build_uploaded_file_search_tool() -> Tool:
    return Tool(
        name="search_uploaded_files",
        description=SEARCH_TOOL_SCHEMA["description"],
        parameters=SEARCH_TOOL_SCHEMA["parameters"],
        execute=_search_execute,
    )


def build_uploaded_file_excerpt_tool() -> Tool:
    return Tool(
        name="read_uploaded_file_excerpt",
        description=EXCERPT_TOOL_SCHEMA["description"],
        parameters=EXCERPT_TOOL_SCHEMA["parameters"],
        execute=_excerpt_execute,
    )


def build_file_reader_tool() -> Tool:
    return build_uploaded_file_excerpt_tool()
