from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import Settings, settings
from .dialog_client import MiniMaxDialogClient, PlaceholderDialogClient, build_dialog_client
from .memory_compactor import MemoryCompactor


def run_memory_compaction_probe(
    *,
    settings_obj: Settings,
    input_path: Path,
    output_path: Path | None = None,
    previous_summary: str = "",
) -> dict:
    if not settings_obj.minimax_api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set in the current shell environment")

    source_text = input_path.read_text(encoding="utf-8")
    client = build_dialog_client(settings_obj)
    compactor = MemoryCompactor(dialog_client=client)
    result = compactor.compact(
        {
            "previous_summary": previous_summary,
            "active_events": [
                {
                    "kind": "document_test",
                    "source": "probe_file",
                    "content": source_text,
                }
            ],
        }
    )

    summary_text = result["summary_text"]
    resolved_output = output_path or input_path.with_suffix(".compacted.txt")
    resolved_output.write_text(summary_text, encoding="utf-8")

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "client": type(client).__name__,
        "uses_minimax": isinstance(client, MiniMaxDialogClient),
        "uses_placeholder": isinstance(client, PlaceholderDialogClient),
        "input_path": str(input_path),
        "input_chars": len(source_text),
        "previous_summary_chars": len(previous_summary),
        "output_path": str(resolved_output),
        "summary_chars": len(summary_text),
        "status": result.get("status"),
        "metadata": result.get("metadata", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run memory compaction against a local text file.")
    parser.add_argument(
        "--input",
        required=True,
        help="Absolute path to the source .txt file to compact",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional absolute path for the compacted .txt output",
    )
    parser.add_argument(
        "--previous-summary",
        default="",
        help="Optional previous summary to include as compaction context",
    )
    args = parser.parse_args()

    summary = run_memory_compaction_probe(
        settings_obj=settings,
        input_path=Path(args.input),
        output_path=Path(args.output) if args.output else None,
        previous_summary=args.previous_summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
