import base64
import datetime as dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib import request

from .config import Settings


class SentenceTranscriber(Protocol):
    def transcribe(self, table_id: str, filename: str, clip_bytes: bytes) -> str:
        ...


@dataclass
class PlaceholderSentenceTranscriber:
    def transcribe(self, table_id: str, filename: str, clip_bytes: bytes) -> str:
        return f"Received voice clip {filename}. Placeholder transcript: {len(clip_bytes)} bytes captured."


class TencentSentenceRecognitionClient:
    host = "asr.tencentcloudapi.com"
    endpoint = "https://asr.tencentcloudapi.com/"
    service = "asr"
    action = "SentenceRecognition"
    version = "2019-06-14"

    def __init__(
        self,
        *,
        secret_id: str,
        secret_key: str,
        region: str,
        engine_type: str = "16k_zh",
        timeout_seconds: float = 10.0,
        request_sender: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
        timestamp_provider: Callable[[], int] | None = None,
    ) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.engine_type = engine_type
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or self._send_request
        self._timestamp_provider = timestamp_provider or (lambda: int(dt.datetime.now(dt.timezone.utc).timestamp()))

    def transcribe(self, table_id: str, filename: str, clip_bytes: bytes) -> str:
        voice_format = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
        payload = {
            "EngSerViceType": self.engine_type,
            "SourceType": 1,
            "VoiceFormat": voice_format,
            "Data": base64.b64encode(clip_bytes).decode("utf-8"),
            "DataLen": len(clip_bytes),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = self._timestamp_provider()
        headers = self._build_headers(body, timestamp)
        response_bytes = self._request_sender(self.endpoint, body, headers, self.timeout_seconds)
        response = json.loads(response_bytes.decode("utf-8"))
        response_body = response.get("Response") if isinstance(response, dict) else None
        if not isinstance(response_body, dict):
            raise RuntimeError(f"Unexpected Tencent ASR response: {response!r}")
        result = response_body.get("Result")
        if result is None:
            error = response_body.get("Error")
            if error is not None:
                raise RuntimeError(f"Tencent ASR error response: {response!r}")
            raise RuntimeError(f"Tencent ASR response missing Result: {response!r}")
        return result

    def _build_headers(self, body: bytes, timestamp: int) -> dict[str, str]:
        date = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).strftime("%Y-%m-%d")
        canonical_headers = (
            "content-type:application/json; charset=utf-8\n"
            f"host:{self.host}\n"
            f"x-tc-action:{self.action.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        hashed_request_payload = hashlib.sha256(body).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                "/",
                "",
                canonical_headers,
                signed_headers,
                hashed_request_payload,
            ]
        )
        credential_scope = f"{date}/{self.service}/tc3_request"
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        secret_date = self._sign(("TC3" + self.secret_key).encode("utf-8"), date)
        secret_service = self._sign(secret_date, self.service)
        secret_signing = self._sign(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "TC3-HMAC-SHA256 "
            f"Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.host,
            "X-TC-Action": self.action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self.version,
            "X-TC-Region": self.region,
        }

    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()


def build_sentence_transcriber(settings: Settings) -> SentenceTranscriber:
    if settings.tencent_secret_id and settings.tencent_secret_key:
        return TencentSentenceRecognitionClient(
            secret_id=settings.tencent_secret_id,
            secret_key=settings.tencent_secret_key,
            region=settings.tencent_asr_region,
            engine_type=settings.tencent_asr_engine,
            timeout_seconds=settings.tencent_asr_timeout_seconds,
        )
    return PlaceholderSentenceTranscriber()
