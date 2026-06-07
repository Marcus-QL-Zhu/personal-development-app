from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .config import Settings, settings
from .dialog_client import SiliconFlowPreviewClient


def _default_events(transcript: str) -> list[dict]:
    return [
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": transcript,
        }
    ]


def run_siliconflow_preview_probe(
    *,
    settings_obj: Settings,
    transcript: str,
    mode: str,
    output_dir: Path,
    timeout_seconds: float | None = None,
) -> dict:
    if not settings_obj.siliconflow_api_key:
        raise RuntimeError("SILICONFLOW_API_KEY is not set in the current shell environment")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = output_dir / f"siliconflow-preview-probe-{timestamp}-summary.json"

    client = SiliconFlowPreviewClient(
        api_key=settings_obj.siliconflow_api_key,
        model=settings_obj.siliconflow_preview_model,
        base_url=settings_obj.siliconflow_preview_base_url,
        timeout_seconds=timeout_seconds or settings_obj.siliconflow_preview_timeout_seconds,
        max_tokens=settings_obj.siliconflow_preview_max_tokens,
        temperature=settings_obj.siliconflow_preview_temperature,
        top_p=settings_obj.siliconflow_preview_top_p,
        top_k=settings_obj.siliconflow_preview_top_k,
        min_p=settings_obj.siliconflow_preview_min_p,
        frequency_penalty=settings_obj.siliconflow_preview_frequency_penalty,
    )

    started = time.perf_counter()
    preview_text = client.generate_preview_text(
        mode=mode,
        transcript=transcript,
        events=_default_events(transcript),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    summary = {
        "provider": "siliconflow",
        "model": settings_obj.siliconflow_preview_model,
        "base_url": settings_obj.siliconflow_preview_base_url,
        "mode": mode,
        "transcript": transcript,
        "preview_text": preview_text,
        "elapsed_ms": elapsed_ms,
        "request_parameters": {
            "enable_thinking": False,
            "max_tokens": settings_obj.siliconflow_preview_max_tokens,
            "temperature": settings_obj.siliconflow_preview_temperature,
            "top_p": settings_obj.siliconflow_preview_top_p,
            "top_k": settings_obj.siliconflow_preview_top_k,
            "min_p": settings_obj.siliconflow_preview_min_p,
            "frequency_penalty": settings_obj.siliconflow_preview_frequency_penalty,
            "stream": False,
            "n": 1,
            "timeout_seconds": timeout_seconds or settings_obj.siliconflow_preview_timeout_seconds,
        },
    }
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    summary["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a SiliconFlow preview-generation probe.")
    parser.add_argument(
        "--transcript",
        default="宝子，三国杀反贼到底怎么赢？",
        help="Latest user transcript used to generate a short preview lead.",
    )
    parser.add_argument(
        "--mode",
        choices=["conversation"],
        default="conversation",
        help="Preview mode.",
    )
    parser.add_argument(
        "--output-dir",
        default=".runtime/siliconflow-preview-probe",
        help="Where to write the probe summary json.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Override SiliconFlow preview HTTP timeout seconds for this probe.",
    )
    args = parser.parse_args()

    try:
        summary = run_siliconflow_preview_probe(
            settings_obj=settings,
            transcript=args.transcript,
            mode=args.mode,
            output_dir=Path(args.output_dir),
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"SiliconFlow preview probe failed: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
