from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import gamevoice_server.main as main_module


class _FakeProgressiveDialogClient:
    def __init__(
        self,
        *,
        preview_text: str,
        content_sentences: list[str],
        inter_sentence_delay_s: float,
    ) -> None:
        self._preview_text = preview_text
        self._content_sentences = content_sentences
        self._inter_sentence_delay_s = max(inter_sentence_delay_s, 0.0)

    def generate_reply(self, *args, **kwargs):
        raise AssertionError("generate_reply should not be used in formal stream probe")

    def stream_reply_text(
        self,
        *,
        mode: str,
        transcript: str,
        events: list[dict],
        already_spoken_text: str | None = None,
        continue_only: bool = False,
    ):
        if mode != "conversation":
            raise AssertionError(f"expected conversation mode, got {mode!r}")
        if not continue_only:
            raise AssertionError("formal stream probe expects continuation-only reply text")
        cumulative = ""
        for index, sentence in enumerate(self._content_sentences):
            if index > 0 and self._inter_sentence_delay_s:
                time.sleep(self._inter_sentence_delay_s)
            cumulative = f"{cumulative}{sentence}"
            yield cumulative


class _FakeProgressiveTtsAdapter:
    def __init__(self, *, output_dir: Path) -> None:
        self._output_dir = output_dir

    def synthesize_segment(self, text: str, *, voice_id: str | None = None) -> dict:
        return {
            "audio_bytes": text.encode("utf-8"),
            "format": "mp3",
        }


class _FakeProgressiveAutoInterruptService:
    def __init__(
        self,
        *,
        preview_text: str,
        content_sentences: list[str],
        inter_sentence_delay_s: float,
        output_dir: Path,
    ) -> None:
        self.orchestrator = type(
            "FakeOrchestrator",
            (),
            {
                "dialog_client": _FakeProgressiveDialogClient(
                    preview_text=preview_text,
                    content_sentences=content_sentences,
                    inter_sentence_delay_s=inter_sentence_delay_s,
                )
            },
        )()
        self.tts_adapter = _FakeProgressiveTtsAdapter(output_dir=output_dir)

    def plan_progressive(self, events: list[dict], assistant_name: str = "宝子") -> dict:
        return {
            "should_interrupt": True,
            "mode": "conversation",
            "decision_reason": "assistant_name_called",
            "transcript": events[-1]["content"] if events else "",
            "deferred_generation": True,
        }

    def plan(self, events: list[dict], assistant_name: str = "宝子") -> dict:
        raise AssertionError("plan() should not run in formal stream probe")

    def build_response(self, plan: dict) -> dict:
        raise AssertionError("build_response() should not run in formal stream probe")


def _json_safe_runtime(snapshot: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


def run_formal_stream_probe(
    *,
    output_dir: Path,
    transcript: str,
    preview_text: str,
    content_sentences: list[str],
    inter_sentence_delay_s: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = output_dir / f"formal-stream-probe-{timestamp}-summary.json"

    table = main_module.session_manager.start_table("Formal Stream Probe", origin="probe")
    table_id = table.id
    main_module.dialog_runtime_store.ensure_table(table_id)

    original_service = main_module.auto_interrupt_service
    main_module.auto_interrupt_service = _FakeProgressiveAutoInterruptService(
        preview_text=preview_text,
        content_sentences=content_sentences,
        inter_sentence_delay_s=inter_sentence_delay_s,
        output_dir=output_dir,
    )

    try:
        main_module.session_manager.append_context_event(
            table_id,
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": transcript,
            },
        )
        main_module.dialog_runtime_store.on_agent_preview_ready(
            table_id,
            reply_text=preview_text,
            source_text=transcript.split("：", 1)[-1] if "：" in transcript else transcript,
        )
        main_module.dialog_runtime_store.on_agent_speaking_started(
            table_id,
            job_id="probe-preview-job",
            segment_index=0,
        )

        started = time.perf_counter()
        result = main_module._run_auto_interrupt_for_table(table_id, automatic=True)
        returned_ms = round((time.perf_counter() - started) * 1000, 2)

        stream_id = result["tts_stream"]["stream_id"]
        chunks: list[dict[str, Any]] = []
        while True:
            before = time.perf_counter()
            chunk = main_module.tts_stream_bridge.next_chunk(stream_id, wait_timeout=2.0)
            waited_ms = round((time.perf_counter() - before) * 1000, 2)
            if chunk is None:
                break
            chunks.append(
                {
                    "chunk_index": chunk["chunk_index"],
                    "segment_index": chunk["segment_index"],
                    "text": chunk["text"],
                    "is_final": chunk["is_final"],
                    "bytes": len(chunk["audio_bytes"]),
                    "waited_ms": waited_ms,
                    "since_start_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
            if chunk["is_final"]:
                break

        runtime_snapshot = main_module.dialog_runtime_store.snapshot(table_id)
        runtime_events_tail = main_module.session_manager.list_runtime_events(table_id)[-8:]
        assistant_replies = main_module.session_manager.list_assistant_replies(table_id)
        speech_job = result["speech_job"]

        summary = {
            "table_id": table_id,
            "transcript": transcript,
            "preview_text": preview_text,
            "content_sentences": content_sentences,
            "result": {
                "interrupt": result.get("interrupt"),
                "mode": result.get("mode"),
                "decision_reason": result.get("decision_reason"),
                "reply_content": result.get("reply", {}).get("content", ""),
                "speech_job_id": speech_job.get("job_id"),
                "stream_id": stream_id,
                "returned_ms": returned_ms,
                "initial_segment_count": speech_job.get("segment_count", 0),
            },
            "stream_chunks": chunks,
            "final_speech_job": {
                "status": speech_job.get("status"),
                "segment_count": speech_job.get("segment_count", 0),
                "segments": speech_job.get("segments", []),
            },
            "runtime": _json_safe_runtime(runtime_snapshot),
            "runtime_events_tail": runtime_events_tail,
            "assistant_replies_count": len(assistant_replies),
        }
    finally:
        main_module.auto_interrupt_service = original_service

    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe incremental formal-content streaming after preview handoff."
    )
    parser.add_argument(
        "--transcript",
        default="玩家A：宝子，给我解释三国杀规则",
        help="Committed transcript inserted before running automatic interrupt.",
    )
    parser.add_argument(
        "--preview-text",
        default="三国杀是一款以三国时期为背景的身份对战游戏。",
        help="Preview line treated as already spoken.",
    )
    parser.add_argument(
        "--sentence",
        action="append",
        dest="sentences",
        help="Formal content sentence to stream after preview. Repeat this flag for multiple sentences.",
    )
    parser.add_argument(
        "--inter-sentence-delay-ms",
        type=float,
        default=250.0,
        help="Delay between streamed formal sentences, in milliseconds.",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/formal-stream-probe",
        help="Directory for summary output.",
    )
    args = parser.parse_args()

    sentences = args.sentences or [
        "核心规则分三块：身份、出牌和胜利条件。",
        "每回合按摸牌、出牌、弃牌推进。",
        "不同身份有各自的胜利目标。",
    ]

    summary = run_formal_stream_probe(
        output_dir=Path(args.output_dir),
        transcript=args.transcript,
        preview_text=args.preview_text,
        content_sentences=sentences,
        inter_sentence_delay_s=max(args.inter_sentence_delay_ms, 0.0) / 1000.0,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
