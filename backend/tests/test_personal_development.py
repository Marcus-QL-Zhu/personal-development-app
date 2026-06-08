from fastapi.testclient import TestClient


def test_gallup_parser_accepts_numbered_text():
    from gamevoice_server.personal_development import parse_gallup_strengths

    parsed = parse_gallup_strengths(
        """
        1. Learner
        2 Strategic
        3） Achiever
        4 Individualization
        """
    )

    assert parsed == [
        {"rank": 1, "name": "Learner"},
        {"rank": 2, "name": "Strategic"},
        {"rank": 3, "name": "Achiever"},
        {"rank": 4, "name": "Individualization"},
    ]


def test_create_employee_initializes_feishu_table(monkeypatch):
    import gamevoice_server.main as main_module
    from gamevoice_server.personal_development import (
        InMemoryPersonalDevelopmentStore,
        PersonalDevelopmentService,
    )

    class FakeFeishu:
        def __init__(self):
            self.created = []

        def create_employee_table(self, employee_name: str) -> dict:
            self.created.append(employee_name)
            return {
                "app_token": f"base-{employee_name}",
                "table_id": f"table-{employee_name}",
                "url": f"https://feishu.example/base-{employee_name}",
            }

        def append_coaching_record(self, employee: dict, session: dict) -> str:
            raise AssertionError("append should not run during employee creation")

    fake_feishu = FakeFeishu()
    service = PersonalDevelopmentService(
        store=InMemoryPersonalDevelopmentStore(),
        feishu=fake_feishu,
    )
    monkeypatch.setattr(main_module, "personal_development_service", service)

    client = TestClient(main_module.app)
    response = client.post(
        "/development/employees",
        json={
            "name": "Alice",
            "gallup_raw": "1 Learner\n2 Strategic",
            "profile_note": "New consultant with strong curiosity.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Alice"
    assert payload["gallup_strengths"][0] == {"rank": 1, "name": "Learner"}
    assert payload["feishu"]["app_token"] == "base-Alice"
    assert fake_feishu.created == ["Alice"]

    listed = client.get("/development/employees").json()["employees"]
    assert [item["name"] for item in listed] == ["Alice"]


def test_upload_coaching_audio_processes_and_appends_public_feishu_record(monkeypatch):
    import gamevoice_server.main as main_module
    from gamevoice_server.personal_development import (
        InMemoryPersonalDevelopmentStore,
        PersonalDevelopmentService,
    )

    class FakeFeishu:
        def __init__(self):
            self.records = []

        def create_employee_table(self, employee_name: str) -> dict:
            return {
                "app_token": "base-token",
                "table_id": "table-id",
                "url": "https://feishu.example/base-token",
            }

        def append_coaching_record(self, employee: dict, session: dict) -> str:
            fields = {
                "日期": session["coach_date"],
                "主题": session["topic"],
                "内容总结": session["content_summary"],
                "Action Plan": session["action_plan"],
                "质量状态": session["quality_status"],
                "本地记录ID": session["id"],
            }
            self.records.append(fields)
            assert "完整转写" not in fields
            assert "manager_feedback" not in fields
            assert "Gallup" not in fields
            return "rec-1"

    class FakeAsr:
        def transcribe(self, *, filename: str, audio_bytes: bytes) -> dict:
            return {
                "text": "manager: 今天讲客户需求澄清。employee: 我会先复述客户目标。",
                "segments": [
                    {"speaker_id": "0", "role": "manager", "text": "今天讲客户需求澄清。"},
                    {"speaker_id": "1", "role": "employee", "text": "我会先复述客户目标。"},
                ],
                "quality_status": "ok",
                "provider": "fake-asr",
            }

    class FakeGenerator:
        def generate(self, *, employee: dict, transcript: dict) -> dict:
            return {
                "topic": "客户需求澄清",
                "content_summary": "知识点：先复述客户目标，再确认成功标准。反馈点：回应直接。",
                "action_plan": "下次客户沟通前先写三句复述。",
                "manager_feedback": "讲解清楚；可增加员工复述练习。员工可能感到任务明确。",
            }

    service = PersonalDevelopmentService(
        store=InMemoryPersonalDevelopmentStore(),
        feishu=FakeFeishu(),
        asr=FakeAsr(),
        generator=FakeGenerator(),
    )
    monkeypatch.setattr(main_module, "personal_development_service", service)

    client = TestClient(main_module.app)
    employee = client.post(
        "/development/employees",
        json={
            "name": "Alice",
            "gallup_raw": "1 Learner\n2 Strategic",
            "profile_note": "New consultant.",
        },
    ).json()

    response = client.post(
        f"/development/employees/{employee['id']}/coaching-sessions",
        files=[("clip", ("coach.wav", b"voice-bytes", "audio/wav"))],
    )

    assert response.status_code == 200
    session = response.json()
    assert session["employee_id"] == employee["id"]
    assert session["transcript_text"].startswith("manager:")
    assert session["content_summary"].startswith("知识点")
    assert session["manager_feedback"].startswith("讲解清楚")
    assert session["sync_status"] == "synced"
    assert session["feishu_record_id"] == "rec-1"
    assert service.feishu.records == [
        {
            "日期": session["coach_date"],
            "主题": "客户需求澄清",
            "内容总结": "知识点：先复述客户目标，再确认成功标准。反馈点：回应直接。",
            "Action Plan": "下次客户沟通前先写三句复述。",
            "质量状态": "ok",
            "本地记录ID": session["id"],
        }
    ]

    history = client.get(f"/development/employees/{employee['id']}/coaching-sessions").json()
    assert history["sessions"][0]["id"] == session["id"]


def test_flash_asr_signing_endpoint_returns_short_lived_url(monkeypatch):
    import gamevoice_server.main as main_module
    from gamevoice_server.personal_development import (
        InMemoryPersonalDevelopmentStore,
        PersonalDevelopmentService,
        TencentFlashFileAsr,
    )

    service = PersonalDevelopmentService(
        store=InMemoryPersonalDevelopmentStore(),
        asr=TencentFlashFileAsr(
            app_id="123456",
            secret_id="sid",
            secret_key="skey",
            timestamp_provider=lambda: 1770000000,
            nonce_provider=lambda: 42,
        ),
    )
    monkeypatch.setattr(main_module, "personal_development_service", service)

    response = TestClient(main_module.app).post(
        "/development/asr/flash-signatures",
        json={"filename": "coach.m4a", "content_length": 12345},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["method"] == "POST"
    assert payload["url"].startswith("https://asr.cloud.tencent.com/asr/flash/v1/123456?")
    assert "voice_format=m4a" in payload["url"]
    assert "engine_type=16k_zh" in payload["url"]
    assert payload["headers"]["Authorization"]
    assert payload["headers"]["Content-Type"] == "application/octet-stream"
    assert payload["expires_at"] == 1770003600
    assert payload["max_body_bytes"] == 100 * 1024 * 1024


def test_create_coaching_session_from_mobile_transcript(monkeypatch):
    import gamevoice_server.main as main_module
    from gamevoice_server.personal_development import (
        InMemoryPersonalDevelopmentStore,
        PersonalDevelopmentService,
    )

    class FakeFeishu:
        def create_employee_table(self, employee_name: str) -> dict:
            return {"app_token": "base-token", "table_id": "table-id", "url": ""}

        def append_coaching_record(self, employee: dict, session: dict) -> str:
            return "rec-transcript"

    class FakeGenerator:
        def generate(self, *, employee: dict, transcript: dict) -> dict:
            assert transcript["text"] == "manager: clear next step"
            assert transcript["provider"] == "tencent_flash_asr_mobile"
            return {
                "topic": "Pipeline review",
                "content_summary": "Covered qualification criteria and next action.",
                "action_plan": "Send written recap after the call.",
                "manager_feedback": "Good structure; ask the employee to restate the action.",
            }

    service = PersonalDevelopmentService(
        store=InMemoryPersonalDevelopmentStore(),
        feishu=FakeFeishu(),
        generator=FakeGenerator(),
    )
    monkeypatch.setattr(main_module, "personal_development_service", service)
    client = TestClient(main_module.app)
    employee = client.post("/development/employees", json={"name": "Alice"}).json()

    response = client.post(
        f"/development/employees/{employee['id']}/coaching-sessions/from-transcript",
        json={
            "recording_id": "local-recording-1",
            "audio_filename": "coach.m4a",
            "transcript_text": "manager: clear next step",
            "segments": [{"speaker_id": "0", "text": "clear next step"}],
            "asr_provider": "tencent_flash_asr_mobile",
            "quality_status": "ok",
        },
    )

    assert response.status_code == 200
    session = response.json()
    assert session["audio_filename"] == "coach.m4a"
    assert session["audio_path"] == ""
    assert session["transcript_text"] == "manager: clear next step"
    assert session["sync_status"] == "synced"
    assert session["feishu_record_id"] == "rec-transcript"

    duplicate = client.post(
        f"/development/employees/{employee['id']}/coaching-sessions/from-transcript",
        json={
            "recording_id": "local-recording-1",
            "audio_filename": "coach.m4a",
            "transcript_text": "manager: clear next step",
            "segments": [],
            "asr_provider": "tencent_flash_asr_mobile",
            "quality_status": "ok",
        },
    ).json()
    assert duplicate["id"] == session["id"]


def test_feishu_appender_writes_only_public_coaching_fields():
    from gamevoice_server.personal_development import FeishuPersonalDevelopmentBitable

    calls = []

    class FakeClient:
        def create_record(self, app_token, table_id, fields):
            calls.append((app_token, table_id, fields))
            return "rec-public"

    feishu = FeishuPersonalDevelopmentBitable(client=FakeClient())
    employee = {
        "name": "Alice",
        "feishu": {"app_token": "base-token", "table_id": "table-id"},
        "gallup_raw": "1 Learner",
    }
    session = {
        "id": "session-1",
        "coach_date": "2026-06-07",
        "topic": "客户需求澄清",
        "content_summary": "知识点完整总结",
        "action_plan": "本次未形成明确 Action Plan。",
        "quality_status": "quality_pending",
        "transcript_text": "完整转写不能进入飞书",
        "manager_feedback": "manager 私密反馈不能进入飞书",
    }

    assert feishu.append_coaching_record(employee, session) == "rec-public"
    assert calls == [
        (
            "base-token",
            "table-id",
            {
                "日期": 1780790400000,
                "主题": "客户需求澄清",
                "内容总结": "知识点完整总结",
                "Action Plan": "本次未形成明确 Action Plan。",
                "质量状态": "quality_pending",
                "本地记录ID": "session-1",
            },
        )
    ]


def test_feishu_client_reads_top_level_tenant_access_token():
    from gamevoice_server.personal_development import FeishuOpenApiClient

    calls = []

    def fake_sender(url, body, headers, timeout, method):
        calls.append((url, body, headers, method))
        return b'{"code":0,"tenant_access_token":"tenant-token"}'

    client = FeishuOpenApiClient(
        app_id="app-id",
        app_secret="secret",
        request_sender=fake_sender,
    )

    assert client._ensure_token() == "tenant-token"


def test_upload_coaching_audio_continues_when_asr_fails():
    from gamevoice_server.personal_development import (
        InMemoryPersonalDevelopmentStore,
        PersonalDevelopmentService,
    )

    class FakeFeishu:
        def __init__(self):
            self.records = []

        def create_employee_table(self, employee_name: str) -> dict:
            return {"app_token": "base-token", "table_id": "table-id", "url": ""}

        def append_coaching_record(self, employee: dict, session: dict) -> str:
            self.records.append(session)
            return "rec-asr-failed"

    class FailingAsr:
        def transcribe(self, *, filename: str, audio_bytes: bytes) -> dict:
            raise RuntimeError("Tencent flash ASR error")

    class FakeGenerator:
        def generate(self, *, employee: dict, transcript: dict) -> dict:
            assert transcript["quality_status"] == "asr_failed"
            return {
                "topic": "待复盘",
                "content_summary": "ASR 失败，内容总结待补充。",
                "action_plan": "本次未形成明确 Action Plan。",
                "manager_feedback": "ASR 失败，无法评价本轮 coach。",
            }

    feishu = FakeFeishu()
    service = PersonalDevelopmentService(
        store=InMemoryPersonalDevelopmentStore(),
        feishu=feishu,
        asr=FailingAsr(),
        generator=FakeGenerator(),
    )
    employee = service.create_employee(
        name="Alice",
        gallup_raw="1 Learner",
        profile_note="New hire.",
    )

    session = service.create_coaching_session(
        employee_id=employee["id"],
        filename="coach.wav",
        audio_bytes=b"audio",
    )

    assert session["quality_status"] == "asr_failed"
    assert session["transcript_text"] == ""
    assert session["sync_status"] == "synced"
    assert session["feishu_record_id"] == "rec-asr-failed"
    assert feishu.records[0]["quality_status"] == "asr_failed"


def test_tencent_flash_asr_uses_speaker_diarization_and_parses_segments():
    from urllib.parse import parse_qs, urlparse

    from gamevoice_server.personal_development import TencentFlashFileAsr

    captured = {}

    def fake_sender(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return """
        {
          "code": 0,
          "flash_result": [
            {
              "text": "今天讲客户需求澄清。我会先复述。",
              "sentence_list": [
                {"text": "今天讲客户需求澄清。", "speaker_id": 0, "start_time": 0, "end_time": 1200},
                {"text": "我会先复述。", "speaker_id": 1, "start_time": 1300, "end_time": 2400}
              ]
            }
          ]
        }
        """.encode("utf-8")

    asr = TencentFlashFileAsr(
        app_id="123456",
        secret_id="sid",
        secret_key="skey",
        request_sender=fake_sender,
        timestamp_provider=lambda: 1770000000,
        nonce_provider=lambda: 42,
    )
    result = asr.transcribe(filename="coach.wav", audio_bytes=b"audio-bytes")

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["speaker_diarization"] == ["1"]
    assert query["voice_format"] == ["wav"]
    assert query["engine_type"] == ["16k_zh"]
    assert "signature" not in query
    assert captured["headers"]["Authorization"]
    assert captured["body"] == b"audio-bytes"
    assert result["text"] == "今天讲客户需求澄清。我会先复述。"
    assert result["segments"] == [
        {
            "speaker_id": "0",
            "role": "",
            "text": "今天讲客户需求澄清。",
            "start_time": 0,
            "end_time": 1200,
        },
        {
            "speaker_id": "1",
            "role": "",
            "text": "我会先复述。",
            "start_time": 1300,
            "end_time": 2400,
        },
    ]


def test_minimax_m3_generator_sends_reasoning_payload_and_parses_json():
    from gamevoice_server.personal_development import MiniMaxM3CoachingInsightGenerator

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": """
                            {
                              "topic": "客户需求澄清",
                              "content_summary": "知识点：先复述目标。",
                              "action_plan": "",
                              "manager_feedback": "节奏评分：4。"
                            }
                            """
                        }
                    }
                ]
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse()

    generator = MiniMaxM3CoachingInsightGenerator(
        api_key="key",
        base_url="https://api.minimaxi.com/v1/chat/completions",
        model="MiniMax-M3",
        post=fake_post,
    )
    result = generator.generate(
        employee={
            "name": "Alice",
            "profile_note": "轻量背景",
            "gallup_strengths": [{"rank": 1, "name": "Learner"}],
        },
        transcript={"text": "manager: 先复述目标。employee: 明白。", "segments": []},
    )

    assert captured["json"]["model"] == "MiniMax-M3"
    assert captured["json"]["thinking"] == {"type": "adaptive"}
    assert captured["json"]["reasoning_split"] is True
    prompt = captured["json"]["messages"][0]["content"]
    assert "默认输出中文" in prompt
    assert "不要编造 Action Plan" in prompt
    assert result == {
        "topic": "客户需求澄清",
        "content_summary": "知识点：先复述目标。",
        "action_plan": "本次未形成明确 Action Plan。",
        "manager_feedback": "节奏评分：4。",
    }
