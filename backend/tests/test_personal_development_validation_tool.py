from pathlib import Path


def test_build_bilibili_audio_command_limits_to_first_ten_minutes(tmp_path):
    from tools.personal_development_validation import build_bilibili_audio_command

    output_path = tmp_path / "clip.wav"

    command = build_bilibili_audio_command(
        "https://www.bilibili.com/video/BV1TEST",
        output_path,
        max_duration_seconds=600,
    )

    assert "-x" in command
    assert "--download-sections" in command
    assert "*00:00-00:10:00" in command
    assert str(output_path.with_suffix("")) in command


def test_build_ffmpeg_trim_command_outputs_16k_mono_wav(tmp_path):
    from tools.personal_development_validation import build_ffmpeg_trim_command

    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "clip.wav"

    command = build_ffmpeg_trim_command(
        input_path,
        output_path,
        max_duration_seconds=600,
    )

    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-t",
        "600",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ]


def test_upload_clip_posts_employee_and_multipart_session(tmp_path, monkeypatch):
    from tools import personal_development_validation as tool

    calls = []
    clip_path = tmp_path / "clip.wav"
    clip_path.write_bytes(b"audio")

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            calls.append(("raise",))

        def json(self):
            return self._payload

    class FakeRequests:
        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            if url.endswith("/development/employees"):
                return FakeResponse({"id": "employee-1", "name": "Alice"})
            return FakeResponse({"id": "session-1", "topic": "Test"})

    monkeypatch.setattr(tool, "requests", FakeRequests())

    result = tool.upload_clip(
        backend_url="http://localhost:8010",
        employee_name="Alice",
        gallup_raw="1 Learner",
        profile_note="New hire",
        clip_path=clip_path,
    )

    assert result["employee"]["id"] == "employee-1"
    assert result["session"]["id"] == "session-1"
    assert calls[0][0] == "post"
    assert calls[0][1] == "http://localhost:8010/development/employees"
    assert calls[2][1] == "http://localhost:8010/development/employees/employee-1/coaching-sessions"
