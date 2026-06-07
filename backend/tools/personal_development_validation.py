from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_GALLUP_RAW = "1 Learner\n2 Strategic\n3 Achiever"
DEFAULT_PROFILE_NOTE = "Validation employee created by the personal development validation tool."


def _section_end(max_duration_seconds: int) -> str:
    minutes = max_duration_seconds // 60
    seconds = max_duration_seconds % 60
    return f"00:{minutes:02d}:{seconds:02d}"


def build_bilibili_audio_command(
    source_url: str,
    output_path: Path,
    *,
    max_duration_seconds: int = 600,
) -> list[str]:
    output_base = output_path.with_suffix("")
    launcher = ["yt-dlp"] if shutil.which("yt-dlp") else [sys.executable, "-m", "yt_dlp"]
    return [
        *launcher,
        "--no-playlist",
        "--add-header",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "-x",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--download-sections",
        f"*00:00-{_section_end(max_duration_seconds)}",
        "-o",
        str(output_base),
        source_url,
    ]


def build_ffmpeg_trim_command(
    input_path: Path,
    output_path: Path,
    *,
    max_duration_seconds: int = 600,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-t",
        str(max_duration_seconds),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ]


def prepare_clip(
    *,
    output_dir: Path,
    source_url: str | None = None,
    input_path: Path | None = None,
    max_duration_seconds: int = 600,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / "coach-first-10-minutes.wav"
    if input_path is not None:
        subprocess.run(
            build_ffmpeg_trim_command(
                input_path,
                clip_path,
                max_duration_seconds=max_duration_seconds,
            ),
            check=True,
        )
        return clip_path
    if source_url:
        raw_clip_path = output_dir / "coach-first-10-minutes-source.wav"
        subprocess.run(
            build_bilibili_audio_command(
                source_url,
                raw_clip_path,
                max_duration_seconds=max_duration_seconds,
            ),
            check=True,
        )
        resolved = raw_clip_path if raw_clip_path.exists() else raw_clip_path.with_suffix(".wav")
        if not resolved.exists():
            raise FileNotFoundError(f"yt-dlp did not create {raw_clip_path}")
        subprocess.run(
            build_ffmpeg_trim_command(
                resolved,
                clip_path,
                max_duration_seconds=max_duration_seconds,
            ),
            check=True,
        )
        return clip_path
    raise ValueError("Either source_url or input_path is required.")


def upload_clip(
    *,
    backend_url: str,
    employee_name: str,
    gallup_raw: str,
    profile_note: str,
    clip_path: Path,
    api_token: str = "",
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    backend_url = backend_url.rstrip("/")
    employee_response = requests.post(
        f"{backend_url}/development/employees",
        json={
            "name": employee_name,
            "gallup_raw": gallup_raw,
            "profile_note": profile_note,
        },
        headers=headers,
        timeout=30,
    )
    employee_response.raise_for_status()
    employee = employee_response.json()
    with clip_path.open("rb") as handle:
        session_response = requests.post(
            f"{backend_url}/development/employees/{employee['id']}/coaching-sessions",
            files={"clip": (clip_path.name, handle, "audio/wav")},
            headers=headers,
            timeout=600,
        )
    session_response.raise_for_status()
    return {"employee": employee, "session": session_response.json()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate personal development coaching upload with a 10-minute audio clip.",
    )
    parser.add_argument("--source-url", help="Bilibili URL. Requires yt-dlp.")
    parser.add_argument("--input", type=Path, help="Local audio/video file. Requires ffmpeg.")
    parser.add_argument("--backend-url", default="http://localhost:8010")
    parser.add_argument("--api-token", default="")
    parser.add_argument("--employee-name", required=True)
    parser.add_argument("--gallup-raw", default=DEFAULT_GALLUP_RAW)
    parser.add_argument("--profile-note", default=DEFAULT_PROFILE_NOTE)
    parser.add_argument("--max-duration-seconds", type=int, default=600)
    parser.add_argument("--output-dir", type=Path, default=Path(".runtime/personal-development-validation"))
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / timestamp
    clip_path = prepare_clip(
        output_dir=run_dir,
        source_url=args.source_url,
        input_path=args.input,
        max_duration_seconds=args.max_duration_seconds,
    )
    result = upload_clip(
        backend_url=args.backend_url,
        employee_name=args.employee_name,
        gallup_raw=args.gallup_raw,
        profile_note=args.profile_note,
        clip_path=clip_path,
        api_token=args.api_token,
    )
    result["clip_path"] = str(clip_path)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(summary_path), **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
