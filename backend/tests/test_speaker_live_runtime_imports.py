from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_speaker_live_runtime_imports_without_torch_for_placeholder_runtime():
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    code = r'''
import importlib.abc
import sys

class BlockOptionalSpeakerRuntimeDeps(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("torch", "pyannote", "wespeaker")
        if any(fullname == item or fullname.startswith(f"{item}.") for item in blocked):
            raise ModuleNotFoundError(f"blocked {fullname} for placeholder runtime test")
        return None

sys.meta_path.insert(0, BlockOptionalSpeakerRuntimeDeps())

from gamevoice_server.config import Settings
from gamevoice_server.speaker_live_runtime import (
    PlaceholderSpeakerLiveDiarizer,
    PlaceholderSpeakerLiveEmbedder,
    build_speaker_live_runtime,
)

diarizer, embedder = build_speaker_live_runtime(Settings())
assert isinstance(diarizer, PlaceholderSpeakerLiveDiarizer)
assert isinstance(embedder, PlaceholderSpeakerLiveEmbedder)
print("placeholder-runtime-ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "placeholder-runtime-ok" in result.stdout
