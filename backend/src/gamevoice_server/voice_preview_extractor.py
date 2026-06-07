"""Extract and save MP3 files from the voice-previews-summary.json response."""
from __future__ import annotations

import json
from pathlib import Path

INPUT_PATH = Path(__file__).parent.parent.parent.parent / "docs" / "voice-previews" / "voice-previews-summary.json"
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "voice-previews"


def extract():
    with open(INPUT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    text = data["text"]
    results = data["results"]

    saved = []
    errors = []

    for r in results:
        voice_id = r.get("voice_id", "unknown")
        resp = r.get("response", {})
        audio_hex = resp.get("data", {}).get("audio")

        sanitized = voice_id.replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")
        filename = f"voice-preview-{sanitized}.mp3"

        if not audio_hex:
            errors.append((voice_id, "no audio in response"))
            print(f"ERROR: {voice_id} — no audio in response")
            continue

        try:
            audio_bytes = bytes.fromhex(audio_hex)
        except Exception as exc:
            errors.append((voice_id, f"decode error: {exc}"))
            print(f"ERROR: {voice_id} — decode error: {exc}")
            continue

        path = OUTPUT_DIR / filename
        path.write_bytes(audio_bytes)
        saved.append((voice_id, filename, len(audio_bytes)))
        print(f"OK    : {voice_id} -> {filename} ({len(audio_bytes)} bytes)")

    print(f"\n{len(saved)} files saved, {len(errors)} errors")
    if errors:
        print("Errors:", errors)


if __name__ == "__main__":
    extract()
