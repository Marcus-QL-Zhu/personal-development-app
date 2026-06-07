import json
from pathlib import Path

from gamevoice_server.config import Settings
from gamevoice_server.tts_adapter import (
    MiniMaxTTSAdapter,
    MiniMaxWebSocketTTSAdapter,
    TTSAdapter,
    build_tts_adapter,
)


def test_tts_adapter_returns_audio_job_with_segment_assets(tmp_path: Path):
    job = TTSAdapter(output_dir=tmp_path).speak("先别急。我先看一眼。")

    assert job["accepted"] is True
    assert job["job_id"]
    assert job["status"] == "ready"
    assert job["segments"] == ["先别急。", "我先看一眼。"]
    assert job["segment_count"] == 2
    assert job["segment_statuses"] == [
        {
            "index": 0,
            "text": "先别急。",
            "status": "queued",
            "format": "mp3",
            "bytes": len("先别急。".encode("utf-8")),
            "output_path": str(tmp_path / f"{job['job_id']}-segment-0.mp3"),
        },
        {
            "index": 1,
            "text": "我先看一眼。",
            "status": "queued",
            "format": "mp3",
            "bytes": len("我先看一眼。".encode("utf-8")),
            "output_path": str(tmp_path / f"{job['job_id']}-segment-1.mp3"),
        },
    ]
    assert Path(job["output_path"]).exists()
    assert Path(job["segment_statuses"][0]["output_path"]).read_bytes() == "先别急。".encode("utf-8")
    assert Path(job["segment_statuses"][1]["output_path"]).read_bytes() == "我先看一眼。".encode("utf-8")


def test_minimax_tts_adapter_writes_audio_file_and_segment_assets(tmp_path: Path):
    captured: list[dict[str, object]] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        captured.append(
            {
                "url": url,
                "body": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        audio_hex = "0102" if len(captured) == 1 else "0304"
        return json.dumps(
            {
                "data": {
                    "audio": audio_hex,
                    "status": 2,
                },
                "extra_info": {
                    "audio_format": "mp3",
                },
                "base_resp": {
                    "status_code": 0,
                    "status_msg": "success",
                },
            }
        ).encode("utf-8")

    adapter = MiniMaxTTSAdapter(
        api_key="secret",
        voice_id="test-voice-placeholder",
        output_dir=tmp_path,
        request_sender=fake_sender,
        job_id_provider=lambda: "job-1",
    )

    job = adapter.speak("规则答案：此时不能触发该效果。下一步再结算。")

    assert job["accepted"] is True
    assert job["job_id"] == "job-1"
    assert job["status"] == "ready"
    assert job["format"] == "mp3"
    assert job["segments"] == ["规则答案：此时不能触发该效果。", "下一步再结算。"]
    assert job["segment_count"] == 2
    assert [item["text"] for item in job["segment_statuses"]] == job["segments"]
    assert [item["status"] for item in job["segment_statuses"]] == ["queued", "queued"]
    assert [Path(item["output_path"]).read_bytes() for item in job["segment_statuses"]] == [
        b"\x01\x02",
        b"\x03\x04",
    ]
    assert Path(job["output_path"]).read_bytes() == b"\x01\x02\x03\x04"
    assert len(captured) == 2
    assert captured[0]["url"] == "https://api.minimaxi.com/v1/t2a_v2"
    assert captured[0]["body"]["text"] == "规则答案：此时不能触发该效果。"
    assert captured[1]["body"]["text"] == "下一步再结算。"
    assert captured[0]["body"]["voice_setting"]["voice_id"] == "test-voice-placeholder"
    assert captured[0]["headers"]["Authorization"] == "Bearer secret"


def test_minimax_tts_adapter_prefers_structured_lead_tail_reply(tmp_path: Path):
    captured: list[dict[str, object]] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        audio_hex = "0102" if len(captured) == 1 else "0304"
        return json.dumps(
            {
                "data": {
                    "audio": audio_hex,
                    "status": 2,
                },
                "extra_info": {
                    "audio_format": "mp3",
                },
                "base_resp": {
                    "status_code": 0,
                    "status_msg": "success",
                },
            }
        ).encode("utf-8")

    adapter = MiniMaxTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        request_sender=fake_sender,
        job_id_provider=lambda: "job-structured",
    )

    job = adapter.speak(
        "我先接一句，我马上补完整。",
        reply={
            "source": "minimax",
            "lead": "我先接一句。",
            "tail": "我马上补完整。",
            "content": "我先接一句。我马上补完整。",
        },
    )

    assert job["segments"] == ["我先接一句。", "我马上补完整。"]
    assert [item["text"] for item in job["segment_statuses"]] == job["segments"]
    assert [payload["text"] for payload in captured] == job["segments"]
    assert Path(job["output_path"]).read_bytes() == b"\x01\x02\x03\x04"


def test_minimax_tts_adapter_falls_back_to_full_content_when_reply_segments_are_incomplete(
    tmp_path: Path,
):
    captured: list[dict[str, object]] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        audio_hex = f"{len(captured):02x}{len(captured):02x}"
        return json.dumps(
            {
                "data": {
                    "audio": audio_hex,
                    "status": 2,
                },
                "extra_info": {
                    "audio_format": "mp3",
                },
                "base_resp": {
                    "status_code": 0,
                    "status_msg": "success",
                },
            }
        ).encode("utf-8")

    adapter = MiniMaxTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        request_sender=fake_sender,
        job_id_provider=lambda: "job-joke",
    )

    job = adapter.speak(
        "先接一句。然后讲完整笑话。最后再收个尾。",
        reply={
            "source": "minimax",
            "lead": "先接一句。",
            "tail": "然后讲完整笑话。",
            "content": "先接一句。然后讲完整笑话。最后再收个尾。",
        },
    )

    assert job["segments"] == ["先接一句。", "然后讲完整笑话。", "最后再收个尾。"]
    assert [payload["text"] for payload in captured] == job["segments"]
    assert [item["text"] for item in job["segment_statuses"]] == job["segments"]


def test_build_tts_adapter_returns_minimax_when_api_key_present(tmp_path: Path):
    settings = Settings(
        minimax_api_key="secret",
        minimax_tts_output_dir=str(tmp_path),
    )

    adapter = build_tts_adapter(settings)

    assert isinstance(adapter, MiniMaxWebSocketTTSAdapter)


def test_minimax_tts_adapter_includes_lead_content_and_tail_without_dropping_edges(
    tmp_path: Path,
):
    captured: list[dict[str, object]] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        audio_hex = f"{len(captured):02x}{len(captured):02x}"
        return json.dumps(
            {
                "data": {
                    "audio": audio_hex,
                    "status": 2,
                },
                "extra_info": {
                    "audio_format": "mp3",
                },
                "base_resp": {
                    "status_code": 0,
                    "status_msg": "success",
                },
            }
        ).encode("utf-8")

    adapter = MiniMaxTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        request_sender=fake_sender,
        job_id_provider=lambda: "job-pacing",
    )

    job = adapter.speak(
        "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 等于 Dec 25。",
        reply={
            "source": "minimax",
            "lead": "哎，正好有个经典老笑话。",
            "content": "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 等于 Dec 25。",
            "tail": "不好笑别打我啊。",
        },
    )

    assert job["segments"] == [
        "哎，正好有个经典老笑话。",
        "为什么程序员分不清万圣节和圣诞节？",
        "因为 Oct 31 等于 Dec 25。",
        "不好笑别打我啊。",
    ]
    assert [payload["text"] for payload in captured] == job["segments"]


def test_minimax_tts_adapter_avoids_repeating_overlap_between_content_and_tail(tmp_path: Path):
    captured: list[dict[str, object]] = []

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        payload = json.loads(body.decode("utf-8"))
        captured.append(payload)
        audio_hex = f"{len(captured):02x}{len(captured):02x}"
        return json.dumps(
            {
                "data": {"audio": audio_hex, "status": 2},
                "extra_info": {"audio_format": "mp3"},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        ).encode("utf-8")

    adapter = MiniMaxTTSAdapter(
        api_key="secret",
        output_dir=tmp_path,
        request_sender=fake_sender,
        job_id_provider=lambda: "job-overlap",
    )

    job = adapter.speak(
        "Arkham Horror 是经典的克苏鲁主题合作桌游，核心是玩家扮演调查员，阻止远古者苏醒。游戏以回合制进行，每回合分为几个阶段。",
        reply={
            "source": "minimax",
            "lead": "Arkham Horror 是经典的克苏鲁主题合作桌游，核心是玩家扮演调查员，阻止远古者苏醒。",
            "content": "Arkham Horror 是经典的克苏鲁主题合作桌游，核心是玩家扮演调查员，阻止远古者苏醒。游戏以回合制进行，每回合分为几个阶段",
            "tail": "游戏以回合制进行，每回合分为几个阶段：神话阶段抽牌刷新威胁、行动阶段推进调查。",
        },
    )

    assert job["segments"] == [
        "Arkham Horror 是经典的克苏鲁主题合作桌游，核心是玩家扮演调查员，阻止远古者苏醒。",
        "游戏以回合制进行，每回合分为几个阶段：神话阶段抽牌刷新威胁、行动阶段推进调查。",
    ]
    assert [payload["text"] for payload in captured] == job["segments"]


def test_tts_adapter_full_output_keeps_reply_edges(tmp_path: Path):
    job = TTSAdapter(output_dir=tmp_path).speak(
        "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 等于 Dec 25。",
        reply={
            "source": "minimax",
            "lead": "哎，正好有个经典老笑话。",
            "content": "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 等于 Dec 25。",
            "tail": "不好笑别打我啊。",
        },
    )

    assert job["segments"] == [
        "哎，正好有个经典老笑话。",
        "为什么程序员分不清万圣节和圣诞节？",
        "因为 Oct 31 等于 Dec 25。",
        "不好笑别打我啊。",
    ]
    assert Path(job["output_path"]).read_bytes() == (
        "哎，正好有个经典老笑话。"
        "为什么程序员分不清万圣节和圣诞节？"
        "因为 Oct 31 等于 Dec 25。"
        "不好笑别打我啊。"
    ).encode("utf-8")
