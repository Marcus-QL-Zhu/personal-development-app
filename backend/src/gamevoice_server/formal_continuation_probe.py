from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import gamevoice_server.main as main_module
from .config import settings
from .dialog_client import build_dialog_client


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _default_events(transcript: str) -> list[dict[str, str]]:
    return [
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": transcript,
        }
    ]


def _replay_current_commit_algorithm(
    *,
    cumulative_texts: list[str],
) -> dict[str, Any]:
    emitted_segments: list[str] = []
    committed_content = ""
    spoken_prefix_for_diff = ""
    updates: list[dict[str, Any]] = []

    for update_index, cumulative_text in enumerate(cumulative_texts):
        cleaned = _clean_text(cumulative_text)
        incremental_text = main_module._extract_incremental_tts_text(  # noqa: SLF001
            cleaned,
            spoken_prefix_for_diff,
        )
        complete_segments, pending_remainder = main_module._split_complete_tts_segments(  # noqa: SLF001
            incremental_text
        )
        emitted_this_update: list[str] = []

        if not emitted_segments and not complete_segments and incremental_text:
            segment_text = main_module._derive_first_provisional_chunk(  # noqa: SLF001
                incremental_text,
                min_content_chars=8,
            )
            if not segment_text:
                updates.append(
                    {
                        "update_index": update_index,
                        "cumulative_text": cleaned,
                        "incremental_text": incremental_text,
                        "complete_segments": complete_segments,
                        "pending_remainder": pending_remainder,
                        "emitted_this_update": emitted_this_update,
                        "committed_content_after_update": committed_content,
                    }
                )
                continue
            emitted_segments.append(segment_text)
            spoken_prefix_for_diff = segment_text
            committed_content = main_module._display_formal_content(  # noqa: SLF001
                emitted_segments,
                main_module._derive_committed_prefix_for_state(  # noqa: SLF001
                    segment_text,
                    min_content_chars=12,
                ),
            )
            emitted_this_update.append(segment_text)
            pending_remainder = ""
        else:
            spoken_prefix_for_diff = main_module._derive_spoken_prefix_for_diff(  # noqa: SLF001
                cleaned,
                pending_remainder,
            )
            committed_prefix_for_state = main_module._derive_committed_prefix_for_state(  # noqa: SLF001
                cleaned,
                min_content_chars=12 if not committed_content else 0,
            )
            for segment_text in complete_segments:
                emitted_segments.append(segment_text)
                committed_content = main_module._display_formal_content(  # noqa: SLF001
                    emitted_segments,
                    committed_prefix_for_state,
                )
                emitted_this_update.append(segment_text)

        updates.append(
            {
                "update_index": update_index,
                "cumulative_text": cleaned,
                "incremental_text": incremental_text,
                "complete_segments": complete_segments,
                "pending_remainder": pending_remainder,
                "emitted_this_update": emitted_this_update,
                "committed_content_after_update": committed_content,
            }
        )

    return {
        "updates": updates,
        "emitted_segments": emitted_segments,
        "committed_content": committed_content,
    }


def run_formal_continuation_probe(
    *,
    transcript: str,
    preview_text: str,
    output_dir: Path,
) -> dict[str, Any]:
    if not settings.minimax_api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set in the current shell environment")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = output_dir / f"formal-continuation-probe-{timestamp}-summary.json"

    dialog_client = build_dialog_client(settings)
    events = _default_events(transcript)

    raw_updates: list[dict[str, Any]] = []
    cumulative_texts: list[str] = []
    started = time.perf_counter()

    for update_index, text in enumerate(
        dialog_client.stream_continuation_text(
            mode="conversation",
            transcript=transcript,
            events=events,
            already_spoken_text=preview_text,
        )
    ):
        cleaned = _clean_text(text)
        cumulative_texts.append(cleaned)
        raw_updates.append(
            {
                "update_index": update_index,
                "since_start_ms": round((time.perf_counter() - started) * 1000, 2),
                "chars": len(cleaned),
                "text": cleaned,
            }
        )

    replay = _replay_current_commit_algorithm(cumulative_texts=cumulative_texts)
    first_update_ms = raw_updates[0]["since_start_ms"] if raw_updates else None

    first_emit_ms = None
    raw_by_index = {item["update_index"]: item for item in raw_updates}
    for item in replay["updates"]:
        if item["emitted_this_update"]:
            first_emit_ms = raw_by_index[item["update_index"]]["since_start_ms"]
            break

    summary = {
        "transcript": transcript,
        "preview_text": preview_text,
        "first_update_ms": first_update_ms,
        "first_emit_ms": first_emit_ms,
        "raw_updates": raw_updates,
        "algorithm_replay": replay,
    }

    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe real MiniMax continuation text updates and replay the current segment commit algorithm."
    )
    parser.add_argument(
        "--transcript",
        default="玩家A：宝子，给我讲解三国杀规则。",
        help="Transcript used as the formal prompt.",
    )
    parser.add_argument(
        "--preview-text",
        default="好嘞，给你来段三国杀规则指南！",
        help="Already spoken preview text passed into continuation generation.",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/formal-continuation-probe",
        help="Directory for probe summaries.",
    )
    args = parser.parse_args()

    summary = run_formal_continuation_probe(
        transcript=args.transcript,
        preview_text=args.preview_text,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
