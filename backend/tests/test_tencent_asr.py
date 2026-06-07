import base64
import json
import pytest

from gamevoice_server.tencent_asr import TencentSentenceRecognitionClient


def test_tencent_sentence_client_sends_sentence_recognition_request():
    captured: dict[str, object] = {}

    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        captured["timeout"] = timeout
        return json.dumps({"Response": {"Result": "transcribed text"}}).encode("utf-8")

    client = TencentSentenceRecognitionClient(
        secret_id="secret-id",
        secret_key="secret-key",
        region="ap-shanghai",
        request_sender=fake_sender,
        timestamp_provider=lambda: 1_700_000_000,
    )

    result = client.transcribe(
        table_id="t1",
        filename="round-1.wav",
        clip_bytes=b"abc",
    )

    assert result == "transcribed text"
    assert captured["url"] == "https://asr.tencentcloudapi.com/"
    assert captured["timeout"] == 10.0

    body = json.loads(captured["body"].decode("utf-8"))
    assert body["EngSerViceType"] == "16k_zh"
    assert body["SourceType"] == 1
    assert body["VoiceFormat"] == "wav"
    assert body["Data"] == base64.b64encode(b"abc").decode("utf-8")
    assert body["DataLen"] == 3

    headers = captured["headers"]
    assert headers["X-TC-Action"] == "SentenceRecognition"
    assert headers["X-TC-Version"] == "2019-06-14"
    assert headers["X-TC-Region"] == "ap-shanghai"
    assert headers["Authorization"].startswith(
        "TC3-HMAC-SHA256 Credential=secret-id/2023-11-14/asr/tc3_request"
    )


def test_tencent_sentence_client_raises_helpful_error_when_result_missing():
    def fake_sender(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        return json.dumps({"Response": {"Error": {"Code": "InvalidParameter", "Message": "bad audio"}}}).encode("utf-8")

    client = TencentSentenceRecognitionClient(
        secret_id="secret-id",
        secret_key="secret-key",
        region="ap-shanghai",
        request_sender=fake_sender,
        timestamp_provider=lambda: 1_700_000_000,
    )

    with pytest.raises(RuntimeError, match="Tencent ASR error response"):
        client.transcribe(
            table_id="t1",
            filename="round-1.wav",
            clip_bytes=b"abc",
        )
