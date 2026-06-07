from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

TEXT = "大家好，我是你们的桌游搭子，今天要玩点什么呢？"
VOICES = [
    voice.strip()
    for voice in os.getenv("GAMEVOICE_PREVIEW_VOICE_IDS", "").split(",")
    if voice.strip()
]
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "voice-previews"
API_KEY = os.getenv("MINIMAX_API_KEY")
BASE_URL = "https://api.minimaxi.com/v1/t2a_v2"


def generate_voice_preview(voice_id: str) -> dict:
    if not API_KEY:
        return {"error": "MINIMAX_API_KEY not set"}

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "speech-2.8-hd",
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "text": TEXT,
        "stream": False,
    }

    started = time.perf_counter()
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            BASE_URL,
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            content = resp.read()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"error": repr(exc)}

    # Detect binary audio vs JSON error response
    is_binary = (
        content[:4] == b"ID3"
        or content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        or b"audio" in content_type.encode()
    )

    sanitized = voice_id.replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
    if is_binary:
        ext = "mp3" if "mp3" in content_type else "wav"
        filename = f"voice-preview-{sanitized}.{ext}"
        path = OUTPUT_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "voice_id": voice_id,
            "filename": filename,
            "path": str(path),
            "bytes": len(content),
            "elapsed_ms": elapsed_ms,
            "content_type": content_type,
        }
    else:
        # Probably an error JSON
        try:
            parsed = json.loads(content.decode("utf-8"))
            return {"voice_id": voice_id, "response": parsed, "elapsed_ms": elapsed_ms}
        except Exception:
            return {"voice_id": voice_id, "raw": content[:500], "elapsed_ms": elapsed_ms}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not VOICES:
        print("Set GAMEVOICE_PREVIEW_VOICE_IDS to a comma-separated voice id list.")
        return
    results = []
    for voice in VOICES:
        print(f"Generating preview for: {voice}...")
        result = generate_voice_preview(voice)
        results.append(result)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  OK: {result.get('filename', '?')} ({result.get('bytes', 0)} bytes, {result.get('elapsed_ms', 0)}ms)")

    summary_path = OUTPUT_DIR / "voice-previews-summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({"text": TEXT, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\nSummary saved to: {summary_path}")
    print(f"Audio files saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
