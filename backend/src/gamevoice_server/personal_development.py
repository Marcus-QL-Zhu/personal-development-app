from __future__ import annotations

import base64
import hashlib
import hmac
import ast
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib import parse, request
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_gallup_strengths(raw_text: str) -> list[dict[str, object]]:
    strengths: list[dict[str, object]] = []
    seen_ranks: set[int] = set()
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(\d{1,2})\s*[\.\)）:：、-]?\s*(.+?)\s*$", line)
        if not match:
            continue
        rank = int(match.group(1))
        name = re.sub(r"\s+", " ", match.group(2)).strip()
        if rank < 1 or rank > 34 or not name or rank in seen_ranks:
            continue
        seen_ranks.add(rank)
        strengths.append({"rank": rank, "name": name})
    strengths.sort(key=lambda item: int(item["rank"]))
    return strengths


class PersonalDevelopmentStore(Protocol):
    def create_employee(self, employee: dict[str, Any]) -> dict[str, Any]: ...
    def update_employee(self, employee_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...
    def get_employee(self, employee_id: str) -> dict[str, Any] | None: ...
    def list_employees(self) -> list[dict[str, Any]]: ...
    def create_session(self, session: dict[str, Any]) -> dict[str, Any]: ...
    def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...
    def list_sessions(self, employee_id: str) -> list[dict[str, Any]]: ...


class InMemoryPersonalDevelopmentStore:
    def __init__(self) -> None:
        self.employees: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

    def create_employee(self, employee: dict[str, Any]) -> dict[str, Any]:
        self.employees[employee["id"]] = dict(employee)
        return dict(employee)

    def update_employee(self, employee_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        employee = self.employees[employee_id]
        employee.update(updates)
        return dict(employee)

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        employee = self.employees.get(employee_id)
        return dict(employee) if employee is not None else None

    def list_employees(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(item) for item in self.employees.values()),
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

    def create_session(self, session: dict[str, Any]) -> dict[str, Any]:
        self.sessions[session["id"]] = dict(session)
        return dict(session)

    def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        session = self.sessions[session_id]
        session.update(updates)
        return dict(session)

    def list_sessions(self, employee_id: str) -> list[dict[str, Any]]:
        return sorted(
            (
                dict(item)
                for item in self.sessions.values()
                if item.get("employee_id") == employee_id
            ),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )


class SQLitePersonalDevelopmentStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS development_employees (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS development_sessions (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES development_employees(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_development_sessions_employee_id
                ON development_sessions(employee_id);
            """
        )
        self._conn.commit()

    def create_employee(self, employee: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO development_employees (id, data) VALUES (?, ?)",
                (employee["id"], json.dumps(employee, ensure_ascii=False)),
            )
            self._conn.commit()
        return dict(employee)

    def update_employee(self, employee_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        employee = self.get_employee(employee_id)
        if employee is None:
            raise KeyError(employee_id)
        employee.update(updates)
        with self._lock:
            self._conn.execute(
                "UPDATE development_employees SET data = ? WHERE id = ?",
                (json.dumps(employee, ensure_ascii=False), employee_id),
            )
            self._conn.commit()
        return dict(employee)

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM development_employees WHERE id = ?",
                (employee_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_employees(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM development_employees").fetchall()
        employees = [json.loads(row[0]) for row in rows]
        return sorted(
            employees,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

    def create_session(self, session: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO development_sessions (id, employee_id, data) VALUES (?, ?, ?)",
                (
                    session["id"],
                    session["employee_id"],
                    json.dumps(session, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        return dict(session)

    def update_session(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM development_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            session = json.loads(row[0])
            session.update(updates)
            self._conn.execute(
                "UPDATE development_sessions SET data = ? WHERE id = ?",
                (json.dumps(session, ensure_ascii=False), session_id),
            )
            self._conn.commit()
        return dict(session)

    def list_sessions(self, employee_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM development_sessions WHERE employee_id = ?",
                (employee_id,),
            ).fetchall()
        sessions = [json.loads(row[0]) for row in rows]
        return sorted(sessions, key=lambda item: str(item.get("created_at") or ""), reverse=True)


class FeishuBitablePort(Protocol):
    def create_employee_table(self, employee_name: str) -> dict[str, str]: ...
    def append_coaching_record(self, employee: dict[str, Any], session: dict[str, Any]) -> str: ...


class NoopFeishuBitable:
    def create_employee_table(self, employee_name: str) -> dict[str, str]:
        return {"app_token": "", "table_id": "", "url": ""}

    def append_coaching_record(self, employee: dict[str, Any], session: dict[str, Any]) -> str:
        return ""


class FeishuApiError(RuntimeError):
    pass


class FeishuOpenApiClient:
    base_url = "https://open.feishu.cn/open-apis"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        request_sender: Any | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or self._send_request
        self._token = ""
        self._token_acquired_at = 0.0

    def _ensure_token(self) -> str:
        import time

        if self._token and time.time() - self._token_acquired_at < 2 * 3600 - 60:
            return self._token
        data = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
            include_auth=False,
            unwrap_data=False,
        )
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuApiError("Feishu token response missing tenant_access_token")
        self._token = token
        self._token_acquired_at = time.time()
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        include_auth: bool = True,
        unwrap_data: bool = True,
    ) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if include_auth:
            headers["Authorization"] = f"Bearer {self._ensure_token()}"
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        raw = self._request_sender(
            f"{self.base_url}{path}",
            body,
            headers,
            self.timeout_seconds,
            method,
        )
        result = json.loads(raw.decode("utf-8"))
        if result.get("code") != 0:
            raise FeishuApiError(f"Feishu API error {result.get('code')}: {result.get('msg')}")
        return dict(result.get("data") or {}) if unwrap_data else dict(result)

    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any]) -> str:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            {"fields": fields},
        )
        return str((data.get("record") or {}).get("record_id") or "")

    def create_app(self, name: str) -> str:
        data = self._request("POST", "/bitable/v1/apps", {"name": name})
        return str(data.get("app_token") or (data.get("app") or {}).get("app_token") or "")

    def create_table(self, app_token: str, name: str) -> str:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            {"table": {"name": name}},
        )
        return str(data.get("table_id") or (data.get("table") or {}).get("table_id") or "")

    def create_field(self, app_token: str, table_id: str, field_name: str, field_type: int) -> None:
        self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            {"field_name": field_name, "type": field_type},
        )

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float, method: str) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method=method)
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()


class FeishuPersonalDevelopmentBitable:
    """Append-only Feishu Bitable writer for employee-visible coaching records."""

    FIELD_TYPES = {
        "日期": 5,
        "主题": 1,
        "内容总结": 1,
        "Action Plan": 1,
        "质量状态": 1,
        "本地记录ID": 1,
    }

    def __init__(self, *, client: Any, base_url: str = "https://your-tenant.feishu.cn/base") -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    def create_employee_table(self, employee_name: str) -> dict[str, str]:
        app_token = self.client.create_app(f"{employee_name} Coach Records")
        table_id = self.client.create_table(app_token, "Coach Records")
        for field_name, field_type in self.FIELD_TYPES.items():
            self.client.create_field(app_token, table_id, field_name, field_type)
        return {
            "app_token": app_token,
            "table_id": table_id,
            "url": f"{self.base_url}/{app_token}" if app_token else "",
        }

    def append_coaching_record(self, employee: dict[str, Any], session: dict[str, Any]) -> str:
        feishu = dict(employee.get("feishu") or {})
        app_token = str(feishu.get("app_token") or "")
        table_id = str(feishu.get("table_id") or "")
        if not app_token or not table_id:
            raise FeishuApiError("employee is missing Feishu app_token/table_id binding")
        return self.client.create_record(
            app_token,
            table_id,
            {
                "日期": _date_to_feishu_timestamp(session.get("coach_date")),
                "主题": str(session.get("topic") or ""),
                "内容总结": str(session.get("content_summary") or ""),
                "Action Plan": str(session.get("action_plan") or ""),
                "质量状态": str(session.get("quality_status") or ""),
                "本地记录ID": str(session.get("id") or ""),
            },
        )


def _date_to_feishu_timestamp(value: Any) -> int | str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text).date()
    except ValueError:
        return text
    return int(datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).timestamp() * 1000)


class PostCoachAsrPort(Protocol):
    def transcribe(self, *, filename: str, audio_bytes: bytes) -> dict[str, Any]: ...


class TencentFlashFileAsr:
    endpoint_host = "asr.cloud.tencent.com"
    default_engine_type = "16k_zh"
    max_body_bytes = 100 * 1024 * 1024

    def __init__(
        self,
        *,
        app_id: str,
        secret_id: str,
        secret_key: str,
        engine_type: str = default_engine_type,
        speaker_diarization: int = 1,
        timeout_seconds: float = 30.0,
        request_sender: Any | None = None,
        timestamp_provider: Any | None = None,
        nonce_provider: Any | None = None,
    ) -> None:
        self.app_id = app_id
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.engine_type = engine_type
        self.speaker_diarization = speaker_diarization
        self.timeout_seconds = timeout_seconds
        self._request_sender = request_sender or self._send_request
        self._timestamp_provider = timestamp_provider or (lambda: int(datetime.now(timezone.utc).timestamp()))
        self._nonce_provider = nonce_provider or (lambda: int(datetime.now(timezone.utc).timestamp() * 1000) % 1000000)

    def build_upload_request(self, *, filename: str, content_length: int) -> dict[str, Any]:
        if content_length <= 0:
            raise ValueError("content_length must be positive")
        if content_length > self.max_body_bytes:
            raise ValueError("content_length exceeds Tencent Flash ASR 100MB limit")
        voice_format = Path(filename or "coach.m4a").suffix.lstrip(".").lower() or "m4a"
        timestamp = int(self._timestamp_provider())
        expired = timestamp + 3600
        params = self._build_params(voice_format=voice_format, timestamp=timestamp, expired=expired)
        path = f"/asr/flash/v1/{self.app_id}"
        signature = self._signature(path, params)
        return {
            "method": "POST",
            "url": f"https://{self.endpoint_host}{path}?{parse.urlencode(params)}",
            "headers": {
                "Authorization": signature,
                "Content-Type": "application/octet-stream",
            },
            "expires_at": expired,
            "max_body_bytes": self.max_body_bytes,
        }

    def transcribe(self, *, filename: str, audio_bytes: bytes) -> dict[str, Any]:
        voice_format = Path(filename or "coach.wav").suffix.lstrip(".").lower() or "wav"
        timestamp = int(self._timestamp_provider())
        expired = timestamp + 3600
        params = self._build_params(voice_format=voice_format, timestamp=timestamp, expired=expired)
        path = f"/asr/flash/v1/{self.app_id}"
        signature = self._signature(path, params)
        url = f"https://{self.endpoint_host}{path}?{parse.urlencode(params)}"
        response = self._request_sender(
            url,
            audio_bytes,
            {
                "Authorization": signature,
                "Content-Type": "application/octet-stream",
            },
            self.timeout_seconds,
        )
        payload = json.loads(response.decode("utf-8"))
        if int(payload.get("code", 0) or 0) != 0:
            raise RuntimeError(f"Tencent flash ASR error: {payload!r}")
        return self._parse_payload(payload)

    def _build_params(self, *, voice_format: str, timestamp: int, expired: int) -> dict[str, str]:
        return {
            "engine_type": self.engine_type,
            "expired": str(expired),
            "filter_dirty": "0",
            "filter_modal": "0",
            "filter_punc": "0",
            "nonce": str(self._nonce_provider()),
            "secretid": self.secret_id,
            "speaker_diarization": str(self.speaker_diarization),
            "timestamp": str(timestamp),
            "voice_format": voice_format,
            "word_info": "0",
        }

    def _signature(self, path: str, params: dict[str, str]) -> str:
        query = "&".join(f"{key}={params[key]}" for key in sorted(params))
        source = f"POST{self.endpoint_host}{path}?{query}"
        digest = hmac.new(self.secret_key.encode("utf-8"), source.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("utf-8")

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> dict[str, Any]:
        chunks = list(payload.get("flash_result") or [])
        texts: list[str] = []
        segments: list[dict[str, Any]] = []
        for chunk in chunks:
            if chunk.get("text"):
                texts.append(str(chunk.get("text") or ""))
            for sentence in list(chunk.get("sentence_list") or []):
                text = str(sentence.get("text") or "").strip()
                if not text:
                    continue
                segments.append(
                    {
                        "speaker_id": str(sentence.get("speaker_id", "")),
                        "role": "",
                        "text": text,
                        "start_time": sentence.get("start_time"),
                        "end_time": sentence.get("end_time"),
                    }
                )
        return {
            "text": "".join(texts).strip(),
            "segments": segments,
            "quality_status": "ok" if texts or segments else "quality_pending",
            "provider": "tencent_flash_asr",
        }

    @staticmethod
    def _send_request(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()


class PlaceholderPostCoachAsr:
    def transcribe(self, *, filename: str, audio_bytes: bytes) -> dict[str, Any]:
        return {
            "text": f"Placeholder transcript for {filename}: {len(audio_bytes)} bytes captured.",
            "segments": [],
            "quality_status": "quality_pending",
            "provider": "placeholder",
        }


class CoachingInsightGeneratorPort(Protocol):
    def generate(self, *, employee: dict[str, Any], transcript: dict[str, Any]) -> dict[str, str]: ...


class MiniMaxM3Error(RuntimeError):
    pass


class MiniMaxM3CoachingInsightGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "MiniMax-M3",
        thinking_type: str = "adaptive",
        reasoning_split: bool = True,
        post: Any | None = None,
        timeout_seconds: int = 180,
        retry_delay_seconds: float = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.thinking_type = thinking_type
        self.reasoning_split = reasoning_split
        self.post = post or self._post
        self.timeout_seconds = timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds

    def generate(self, *, employee: dict[str, Any], transcript: dict[str, Any]) -> dict[str, str]:
        prompt = self._build_prompt(employee=employee, transcript=transcript)
        payload = self._build_payload(prompt)
        body = self._send_payload(payload)
        content = self._extract_message_content(body)
        parsed = self._parse_or_repair_content(content)
        validation_errors = _coaching_generation_validation_errors(parsed)
        if validation_errors:
            regenerated_content = self._regenerate_complete_json(
                original_prompt=prompt,
                invalid_content=content,
                validation_errors=validation_errors,
            )
            parsed = self._parse_or_repair_content(regenerated_content)
            validation_errors = _coaching_generation_validation_errors(parsed)
        if validation_errors:
            raise MiniMaxM3Error(f"MiniMax M3 returned incomplete coaching summary: {validation_errors}")
        return _format_coaching_generation(parsed)

    def _build_payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": self.thinking_type},
            "reasoning_split": self.reasoning_split,
        }

    def _send_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                break
            except MiniMaxM3Error as exc:
                last_error = exc
                if attempt >= 3 or not _is_retryable_minimax_error(str(exc)):
                    raise
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds * attempt)
        else:
            raise MiniMaxM3Error(str(last_error or "MiniMax M3 request failed"))
        if getattr(response, "status_code", 200) >= 400:
            raise MiniMaxM3Error(f"MiniMax M3 HTTP error {getattr(response, 'status_code', '?')}")
        body = response.json()
        base_resp = body.get("base_resp") or {}
        if base_resp.get("status_code") not in (None, 0):
            raise MiniMaxM3Error(str(base_resp))
        return body

    @staticmethod
    def _extract_message_content(body: dict[str, Any]) -> str:
        return str(body["choices"][0]["message"].get("content") or "")

    def _parse_or_repair_content(self, content: str) -> dict[str, Any]:
        try:
            return _parse_json_object(content)
        except json.JSONDecodeError as exc:
            repaired_content = self._repair_json_content(content=content, parse_error=str(exc))
            return _parse_json_object(repaired_content)

    def _repair_json_content(self, *, content: str, parse_error: str) -> str:
        repair_prompt = json.dumps(
            {
                "task": "repair invalid JSON from a MiniMax coaching summary response",
                "instructions": [
                    "Return only one valid JSON object.",
                    "Do not use markdown fences.",
                    "Do not add or remove substantive coaching content.",
                    "The JSON object must contain topic, content_summary, action_plan, manager_feedback.",
                ],
                "parse_error": parse_error,
                "source_text": content,
            },
            ensure_ascii=False,
        )
        body = self._send_payload(self._build_payload(repair_prompt))
        return self._extract_message_content(body)

    def _regenerate_complete_json(
        self,
        *,
        original_prompt: str,
        invalid_content: str,
        validation_errors: list[str],
    ) -> str:
        regenerate_prompt = json.dumps(
            {
                "task": "regenerate a complete coaching summary JSON",
                "instructions": [
                    "Return only one valid JSON object.",
                    "Do not use markdown fences.",
                    "Use the original transcript and employee context.",
                    "content_summary and manager_feedback must be non-empty and evidence-based.",
                    "topic must be specific, not 待提炼, Pending review, or a placeholder.",
                    "action_plan may say 本次未形成明确 Action Plan。 only when the transcript contains no concrete action plan.",
                ],
                "validation_errors": validation_errors,
                "original_request_json": original_prompt,
                "previous_invalid_response": invalid_content,
            },
            ensure_ascii=False,
        )
        body = self._send_payload(self._build_payload(regenerate_prompt))
        return self._extract_message_content(body)

    @staticmethod
    def _build_prompt(*, employee: dict[str, Any], transcript: dict[str, Any]) -> str:
        payload = {
            "task": "为 manager 的员工 coach 录音生成结构化输出。默认输出中文。",
            "employee": {
                "name": employee.get("name", ""),
                "profile_note": employee.get("profile_note", ""),
                "gallup_strengths_for_manager_feedback_only": employee.get("gallup_strengths", []),
            },
            "transcript": {
                "text": transcript.get("text", ""),
                "segments": transcript.get("segments", []),
            },
            "requirements": [
                "content_summary 面向员工本人复习，必须覆盖知识点、反馈点、关键例子或易错点，不要过度概括。",
                "Action Plan 只记录录音里明确提到的行动项、交付物、截止时间、验收标准；不要编造 Action Plan。",
                "manager_feedback 只给 manager 在 app 内查看，不进入飞书；要评价讲解清晰度、Gallup 沟通适配、行动项清晰度、节奏、互动质量，并用证据链推测员工感受。",
                "Gallup 不进入员工可见总结或飞书内容，只用于 manager_feedback。",
                "如果 transcript 涉及政治、地缘、公共事件或争议性社会议题，只做中立的沟通结构、论证框架、证据类型和表达方式总结；不要输出立场判断、动员性措辞或超出录音的政治结论。",
                "所有字段都面向人阅读，使用 plain text；不要输出 Markdown 粗体、Markdown 标题、JSON、Python dict/list、代码块或 HTML。",
                "输出 JSON，字段为 topic, content_summary, action_plan, manager_feedback。",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: int) -> Any:
        body = __import__("json").dumps(json, ensure_ascii=False).encode("utf-8")
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            if hasattr(exc, "read"):
                error_body = exc.read().decode("utf-8", errors="replace")
                raise MiniMaxM3Error(f"{exc}: {error_body[:1000]}") from exc
            raise

        class Response:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return __import__("json").loads(text)

        return Response()


def _parse_json_object(content: str) -> dict[str, Any]:
    content = _strip_markdown_json_fence(str(content or "").strip())
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("Expected JSON object", content, 0)
        return parsed
    except json.JSONDecodeError as direct_error:
        decoder = json.JSONDecoder()
        last_error = direct_error
        for match in re.finditer(r"\{", content):
            try:
                parsed, _ = decoder.raw_decode(content[match.start() :])
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed
        raise last_error


def _strip_markdown_json_fence(content: str) -> str:
    match = re.match(r"^```(?:json|JSON)?\s*(.*?)\s*```$", content, re.DOTALL)
    return match.group(1).strip() if match else content


def _format_action_plan(value: Any) -> str:
    parsed_value = _parse_structured_text(value)
    if parsed_value is not value:
        return _format_action_plan(parsed_value)
    parsed_lines = _parse_numbered_structured_lines(value)
    if parsed_lines is not value:
        return _format_action_plan(parsed_lines)
    if isinstance(value, list):
        items = [_format_action_plan_item(item) for item in value]
        items = [item for item in items if item]
        if not items:
            return "本次未形成明确 Action Plan。"
        return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))
    if isinstance(value, dict):
        return _format_action_plan_item(value) or "本次未形成明确 Action Plan。"
    return _plain_text(value) or "本次未形成明确 Action Plan。"


def _format_action_plan_item(value: Any) -> str:
    if isinstance(value, dict):
        primary = ""
        parts: list[str] = []
        labels = {
            "owner": "负责人",
            "deadline": "截止时间",
            "deliverable": "交付物",
            "acceptance": "验收标准",
            "acceptance_criteria": "验收标准",
            "criteria": "验收标准",
            "detail": "说明",
            "details": "说明",
            "source": "依据",
            "evidence": "依据",
        }
        for key, item_value in value.items():
            text = _plain_text(item_value)
            if not text:
                continue
            normalized_key = str(key).strip().lower()
            if normalized_key in {"task", "item", "action", "todo"} and not primary:
                primary = text
                continue
            label = labels.get(normalized_key, str(key).strip())
            parts.append(f"{label}：{text}")
        if primary and parts:
            return f"{primary}；" + "；".join(parts)
        return primary or "；".join(parts)
    return _plain_text(value)


def _format_coaching_generation(parsed: dict[str, Any]) -> dict[str, str]:
    action_plan = _format_action_plan(parsed.get("action_plan"))
    return {
        "topic": _plain_text(parsed.get("topic")) or "待提炼",
        "content_summary": _plain_text(parsed.get("content_summary")),
        "action_plan": action_plan,
        "manager_feedback": _plain_text(parsed.get("manager_feedback")),
    }


def _parse_structured_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, (list, dict)):
            return parsed
    loose_dict = _parse_loose_python_dict(text)
    if loose_dict is not None:
        return loose_dict
    return value


def _parse_loose_python_dict(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    pairs = re.findall(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]*)['\"]", stripped)
    if not pairs:
        return None
    return {key: value for key, value in pairs}


def _parse_numbered_structured_lines(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    parsed_items = []
    saw_structured_line = False
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\d+[\.\)、)]\s*(\{.*\}|\[.*\])\s*$", line)
        if not match:
            return value
        parsed = _parse_structured_text(match.group(1))
        if parsed is match.group(1):
            return value
        if isinstance(parsed, list):
            parsed_items.extend(parsed)
        else:
            parsed_items.append(parsed)
        saw_structured_line = True
    return parsed_items if saw_structured_line else value


def _plain_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(
            f"{index}. {_format_action_plan_item(item)}"
            for index, item in enumerate(value, start=1)
            if _format_action_plan_item(item)
        )
    if isinstance(value, dict):
        return _format_action_plan_item(value)
    parsed = _parse_structured_text(value)
    if parsed is not value:
        if isinstance(parsed, list):
            return "\n".join(
                f"{index}. {_format_action_plan_item(item)}"
                for index, item in enumerate(parsed, start=1)
                if _format_action_plan_item(item)
            )
        if isinstance(parsed, dict):
            return _format_action_plan_item(parsed)
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    whole_bold = re.fullmatch(r"\*\*(.+?)\*\*", text, flags=re.DOTALL)
    if whole_bold:
        return whole_bold.group(1).strip()
    text = re.sub(r"```(?:\w+)?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"^\s*#{1,6}\s*(.+?)\s*$", r"【\1】", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*\s*[:：]?", r"【\1】", text)
    text = re.sub(r"__([^_]+)__\s*[:：]?", r"【\1】", text)
    text = re.sub(r"】[ \t]+", "】", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)
    text = _sectionize_labeled_plain_text(text)
    text = re.sub(r"(?m)^(【[^】\n]{1,40}】)([^\n])", r"\1\n\2", text)
    text = _format_heading_structured_blocks(text)
    text = _format_standalone_structured_lines(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sectionize_labeled_plain_text(text: str) -> str:
    labels = {
        "explanation_clarity": "讲解清晰度",
        "gallup_alignment": "Gallup 沟通适配",
        "gallup_communication_fit": "Gallup 沟通适配",
        "action_item_clarity": "行动项清晰度",
        "interaction_quality": "互动质量",
        "pace": "节奏",
        "employee_feeling": "员工感受推测",
        "employee_feelings": "员工感受推测",
        "improvement": "改进建议",
        "improvement_suggestions": "改进建议",
        "整体观察": "整体观察",
    }
    key_pattern = r"[A-Za-z_][A-Za-z0-9_ ]{2,40}|[\u4e00-\u9fffA-Za-z /]{2,24}"
    matches = list(re.finditer(rf"(?:^|；)\s*({key_pattern})[:：]", text, flags=re.MULTILINE))
    if len(matches) < 2:
        return text
    sections = []
    for index, match in enumerate(matches):
        raw_label = match.group(1).strip()
        content_start = match.end()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[content_start:content_end].lstrip("； \t").strip()
        if not content:
            continue
        content = _plain_text(content)
        normalized = raw_label.lower().replace(" ", "_")
        label = labels.get(normalized, raw_label)
        sections.append(f"【{label}】\n{content}")
    return "\n\n".join(sections) if sections else text


def _format_heading_structured_blocks(text: str) -> str:
    lines = text.splitlines()
    formatted: list[str] = []
    index = 0
    while index < len(lines):
        formatted.append(lines[index])
        if index + 1 < len(lines):
            next_line = lines[index + 1].strip()
            parsed = _parse_structured_text(next_line)
            if isinstance(parsed, (list, dict)):
                formatted.append(_plain_text(parsed))
                index += 2
                continue
        index += 1
    return "\n".join(formatted)


def _format_standalone_structured_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        parsed = _parse_structured_text(stripped)
        if isinstance(parsed, (list, dict)):
            lines.append(_plain_text(parsed))
        else:
            lines.append(line)
    return "\n".join(lines)


def _coaching_generation_validation_errors(parsed: dict[str, Any]) -> list[str]:
    formatted = _format_coaching_generation(parsed)
    errors: list[str] = []
    if formatted["topic"].strip().lower() in {"", "待提炼", "pending review", "未提供录音内容"}:
        errors.append("topic is placeholder")
    if not formatted["content_summary"].strip():
        errors.append("content_summary is empty")
    if not formatted["manager_feedback"].strip():
        errors.append("manager_feedback is empty")
    return errors


def _is_retryable_minimax_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "502",
            "503",
            "504",
            "429",
            "bad gateway",
            "timeout",
            "temporarily",
            "new_sensitive",
        )
    )


class PlaceholderCoachingInsightGenerator:
    def generate(self, *, employee: dict[str, Any], transcript: dict[str, Any]) -> dict[str, str]:
        text = str(transcript.get("text") or "").strip()
        return {
            "topic": "待提炼",
            "content_summary": text or "本次录音未生成可用转写，内容总结待检查。",
            "action_plan": "本次未形成明确 Action Plan。",
            "manager_feedback": "当前使用占位生成器，需配置 MiniMax M3 后生成 manager feedback。",
        }


@dataclass
class PersonalDevelopmentService:
    store: PersonalDevelopmentStore
    feishu: FeishuBitablePort | None = None
    asr: PostCoachAsrPort | None = None
    generator: CoachingInsightGeneratorPort | None = None
    audio_root: Path = Path(".runtime/development-audio")
    audio_retention_days: int = 90

    def __post_init__(self) -> None:
        self.feishu = self.feishu or NoopFeishuBitable()
        self.asr = self.asr or PlaceholderPostCoachAsr()
        self.generator = self.generator or PlaceholderCoachingInsightGenerator()
        self.audio_root = Path(self.audio_root)
        self.audio_root.mkdir(parents=True, exist_ok=True)

    def create_employee(self, *, name: str, gallup_raw: str = "", profile_note: str = "") -> dict[str, Any]:
        now = utc_now_iso()
        feishu_binding = self.feishu.create_employee_table(name) if self.feishu else {}
        employee = {
            "id": str(uuid4()),
            "name": name.strip(),
            "gallup_raw": gallup_raw,
            "gallup_strengths": parse_gallup_strengths(gallup_raw),
            "profile_note": profile_note,
            "feishu": dict(feishu_binding or {}),
            "created_at": now,
            "updated_at": now,
        }
        return self.store.create_employee(employee)

    def update_employee(self, employee_id: str, *, name: str, gallup_raw: str, profile_note: str) -> dict[str, Any]:
        updates = {
            "name": name.strip(),
            "gallup_raw": gallup_raw,
            "gallup_strengths": parse_gallup_strengths(gallup_raw),
            "profile_note": profile_note,
            "updated_at": utc_now_iso(),
        }
        return self.store.update_employee(employee_id, updates)

    def list_employees(self) -> list[dict[str, Any]]:
        return self.store.list_employees()

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        return self.store.get_employee(employee_id)

    def create_coaching_session(
        self,
        *,
        employee_id: str,
        filename: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        employee = self.store.get_employee(employee_id)
        if employee is None:
            raise KeyError(employee_id)

        now = datetime.now(timezone.utc)
        session_id = str(uuid4())
        audio_path = self._save_audio(employee_id, session_id, filename, audio_bytes)
        try:
            transcript = self.asr.transcribe(filename=filename, audio_bytes=audio_bytes) if self.asr else {}
        except Exception as exc:  # noqa: BLE001
            transcript = {
                "text": "",
                "segments": [],
                "quality_status": "asr_failed",
                "provider": "asr_failed",
                "error": str(exc),
            }
        generation_error = ""
        try:
            generated = self.generator.generate(employee=employee, transcript=transcript) if self.generator else {}
        except Exception as exc:  # noqa: BLE001
            generation_error = str(exc)
            generated = {
                "topic": "待复盘",
                "content_summary": "本次录音未能自动生成可用总结，需要人工检查转写或音频质量。",
                "action_plan": "本次未形成明确 Action Plan。",
                "manager_feedback": f"自动生成失败：{exc}",
            }
        quality_status = str(transcript.get("quality_status") or "ok")
        if generation_error:
            quality_status = "generation_failed" if quality_status == "ok" else f"{quality_status}+generation_failed"
        session = {
            "id": session_id,
            "employee_id": employee_id,
            "coach_date": now.date().isoformat(),
            "created_at": now.isoformat(),
            "audio_filename": filename,
            "audio_path": str(audio_path),
            "audio_expires_at": (now + timedelta(days=self.audio_retention_days)).isoformat(),
            "transcript_text": str(transcript.get("text") or ""),
            "speaker_segments": list(transcript.get("segments") or []),
            "asr_provider": str(transcript.get("provider") or ""),
            "asr_error": str(transcript.get("error") or ""),
            "generation_error": generation_error,
            "topic": str(generated.get("topic") or "待提炼"),
            "content_summary": str(generated.get("content_summary") or ""),
            "action_plan": str(generated.get("action_plan") or "本次未形成明确 Action Plan。"),
            "manager_feedback": str(generated.get("manager_feedback") or ""),
            "quality_status": quality_status,
            "sync_status": "pending",
            "sync_error": "",
            "feishu_record_id": "",
        }
        session = self.store.create_session(session)
        try:
            record_id = self.feishu.append_coaching_record(employee, session) if self.feishu else ""
            session = self.store.update_session(
                session_id,
                {"sync_status": "synced", "feishu_record_id": record_id, "sync_error": ""},
            )
        except Exception as exc:  # noqa: BLE001
            session = self.store.update_session(
                session_id,
                {"sync_status": "failed", "sync_error": str(exc), "feishu_record_id": ""},
            )
        return session

    def create_coaching_session_from_transcript(
        self,
        *,
        employee_id: str,
        recording_id: str,
        audio_filename: str,
        transcript: dict[str, Any],
    ) -> dict[str, Any]:
        employee = self.store.get_employee(employee_id)
        if employee is None:
            raise KeyError(employee_id)
        if recording_id:
            for existing in self.store.list_sessions(employee_id):
                if str(existing.get("recording_id") or "") == recording_id:
                    return existing

        now = datetime.now(timezone.utc)
        session_id = str(uuid4())
        generation_error = ""
        try:
            generated = self.generator.generate(employee=employee, transcript=transcript) if self.generator else {}
        except Exception as exc:  # noqa: BLE001
            generation_error = str(exc)
            generated = {
                "topic": "Pending review",
                "content_summary": "Automatic summary generation failed. Please review the transcript manually.",
                "action_plan": "No clear action plan was generated.",
                "manager_feedback": f"Automatic generation failed: {exc}",
            }
        quality_status = str(transcript.get("quality_status") or "ok")
        if generation_error:
            quality_status = "generation_failed" if quality_status == "ok" else f"{quality_status}+generation_failed"
        session = {
            "id": session_id,
            "employee_id": employee_id,
            "coach_date": now.date().isoformat(),
            "created_at": now.isoformat(),
            "recording_id": recording_id,
            "audio_filename": audio_filename,
            "audio_path": "",
            "audio_expires_at": "",
            "transcript_text": str(transcript.get("text") or ""),
            "speaker_segments": list(transcript.get("segments") or []),
            "asr_provider": str(transcript.get("provider") or ""),
            "asr_error": str(transcript.get("error") or ""),
            "generation_error": generation_error,
            "topic": str(generated.get("topic") or "Pending review"),
            "content_summary": str(generated.get("content_summary") or ""),
            "action_plan": str(generated.get("action_plan") or "No clear action plan was generated."),
            "manager_feedback": str(generated.get("manager_feedback") or ""),
            "quality_status": quality_status,
            "sync_status": "pending",
            "sync_error": "",
            "feishu_record_id": "",
        }
        session = self.store.create_session(session)
        try:
            record_id = self.feishu.append_coaching_record(employee, session) if self.feishu else ""
            session = self.store.update_session(
                session_id,
                {"sync_status": "synced", "feishu_record_id": record_id, "sync_error": ""},
            )
        except Exception as exc:  # noqa: BLE001
            session = self.store.update_session(
                session_id,
                {"sync_status": "failed", "sync_error": str(exc), "feishu_record_id": ""},
            )
        return session

    def list_sessions(self, employee_id: str) -> list[dict[str, Any]]:
        return self.store.list_sessions(employee_id)

    def _save_audio(self, employee_id: str, session_id: str, filename: str, audio_bytes: bytes) -> Path:
        extension = Path(filename or "coach.wav").suffix or ".bin"
        employee_dir = self.audio_root / employee_id
        employee_dir.mkdir(parents=True, exist_ok=True)
        path = employee_dir / f"{session_id}{extension}"
        path.write_bytes(audio_bytes)
        return path
