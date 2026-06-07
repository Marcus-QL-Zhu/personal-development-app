from pathlib import Path

from gamevoice_server.config import Settings
from gamevoice_server.tts_probe import run_tts_probe


class FakeProbeAdapter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def prepare_job(self, text: str, *, reply=None, turn_id=None, reply_id=None) -> dict:
        return {
            "accepted": True,
            "job_id": "probe-job-progressive",
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "preparing",
            "text": text,
            "segments": ["Lead", "Tail"],
            "segment_count": 2,
            "segment_statuses": [
                {
                    "index": 0,
                    "text": "Lead",
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 0,
                    "output_path": str(self.output_dir / "probe-job-progressive-segment-0.mp3"),
                },
                {
                    "index": 1,
                    "text": "Tail",
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 0,
                    "output_path": str(self.output_dir / "probe-job-progressive-segment-1.mp3"),
                },
            ],
            "format": "mp3",
            "output_path": str(self.output_dir / "probe-job-progressive.mp3"),
            "bytes": 0,
        }

    def stream_job_audio(self, speech_job: dict, *, on_segment_audio) -> None:
        on_segment_audio(segment_index=0, text="Lead", audio_bytes=b"\x01\x02", format_name="mp3")
        on_segment_audio(segment_index=1, text="Tail", audio_bytes=b"\x03\x04", format_name="mp3")

    def speak(self, text: str, *, reply=None, turn_id=None, reply_id=None) -> dict:
        output_path = self.output_dir / "probe-job-speak.mp3"
        output_path.write_bytes(b"\x05\x06")
        segment_path = self.output_dir / "probe-job-speak-segment-0.mp3"
        segment_path.write_bytes(b"\x05\x06")
        return {
            "accepted": True,
            "job_id": "probe-job-speak",
            "status": "ready",
            "text": text,
            "segments": [text],
            "segment_count": 1,
            "segment_statuses": [
                {
                    "index": 0,
                    "text": text,
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 2,
                    "output_path": str(segment_path),
                }
            ],
            "format": "mp3",
            "output_path": str(output_path),
            "bytes": 2,
        }


def test_run_tts_probe_writes_progressive_and_speak_results(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "gamevoice_server.tts_probe.build_tts_adapter",
        lambda settings_obj: FakeProbeAdapter(tmp_path / "tts"),
    )
    settings = Settings(minimax_api_key="secret", minimax_tts_output_dir=str(tmp_path / "tts"))

    summary = run_tts_probe(
        settings_obj=settings,
        text="宝子，这是测试。",
        output_dir=tmp_path / "probe-output",
    )

    assert summary["adapter"] == "FakeProbeAdapter"
    assert summary["progressive"]["supported"] is True
    assert summary["progressive"]["accepted"] is True
    assert summary["progressive"]["chunk_count"] == 2
    assert summary["progressive"]["output_exists"] is True
    assert summary["progressive"]["output_bytes"] == 4
    assert summary["speak"]["accepted"] is True
    assert summary["speak"]["output_exists"] is True
    assert Path(summary["summary_path"]).exists()
