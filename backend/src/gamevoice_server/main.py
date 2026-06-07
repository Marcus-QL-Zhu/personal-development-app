import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .archive_store import ArchiveStore
from .auto_interrupt_service import AutoInterruptService
from .audio_gateway import AudioGateway
from .companion_orchestrator import CompanionOrchestrator
from .companion_timing import CompanionTiming
from .config import settings
from .dialog_client import MiniMaxDialogClient, build_dialog_client, normalize_reply_payload
from .dialog_runtime_store import DialogRuntimeStore
from .document_reading_store import DocumentReadingStore
from .document_reader_worker import DocumentReaderWorker
from .document_store import DocumentStore
from .document_summarizer import DocumentSummarizer
from .file_ingest import FileIngestor
from .identity_linker import IdentityLinker
from .interrupt_policy import InterruptPolicy
from .live_diagnostics_store import LiveDiagnosticsStore
from .live_heartbeat import LiveHeartbeatScheduler, reliable_heartbeat_player_names
from .live_silence_gate import SilenceGate, SilenceGateConfig, WebRtcVadFrameClassifier
from .lookup_marker import split_preview_lookup_marker as _split_preview_lookup_marker
from .memory_compaction_service import MemoryCompactionService
from .memory_compaction_store import MemoryCompactionStore
from .memory_compactor import MemoryCompactor
from .mobile_diagnostics_store import MobileDiagnosticsStore
from .personal_development import (
    FeishuOpenApiClient,
    FeishuPersonalDevelopmentBitable,
    InMemoryPersonalDevelopmentStore,
    MiniMaxM3CoachingInsightGenerator,
    PersonalDevelopmentService,
    SQLitePersonalDevelopmentStore,
    TencentFlashFileAsr,
)
from .rule_analysis_service import RuleAnalysisService
from .rule_analysis_store import RuleAnalysisStore
from .rule_analysis_worker import RuleAnalysisWorker
from .skill_runner import SkillAgent, ToolRegistry
from .skill_runner.tools import (
    build_arkham_rules_orient_tool,
    build_arkham_rules_tool,
    build_arkham_cards_tool,
    build_official_faq_tool,
    build_web_faq_tool,
    build_file_reader_tool,
    build_uploaded_file_inspect_tool,
    build_uploaded_file_search_tool,
    build_web_search_tool,
)
from .session_manager import (
    SessionManager,
    _normalize_whitespace as _normalize_session_whitespace,
    normalize_realtime_speaker_id,
)
from .speaker_alias_rewrite_service import SpeakerAliasRewriteService
from .table_store import InMemoryTableStore, SQLiteTableStore, TableStore
from .speaker_live_connector import SpeakerLiveConnector
from .speaker_live_worker import SpeakerLiveWorker
from .speaker_pipeline_adapter import SpeakerPipelineAdapter
from .speaker_live_runtime import build_speaker_live_runtime
from .tencent_asr import build_sentence_transcriber
from .tencent_realtime_asr import build_realtime_session_factory
from .tts_adapter import build_tts_adapter, split_tts_segments
from .tts_stream_bridge import TTSStreamBridge
from .turn_decision import build_turn_decision_engine

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

LIVE_AUDIO_QUEUE_MAX_CHUNKS = 20
PREVIEW_FINAL_WAIT_SECONDS = 2.0
CONVERSATION_MODE = "conversation"
LOOKUP_BUSY_REPLY_TEXT = "我先查完前一个问题再来查这个"
LOOKUP_QUEUE_DELAY_SECONDS = 3.0
PUBLIC_API_TOKEN_ENV = "GAMEVOICE_PUBLIC_API_TOKEN"

_lookup_runtime_lock = threading.RLock()
_active_lookup_by_table: dict[str, dict] = {}
_pending_lookup_by_table: dict[str, dict] = {}
_lookup_job_to_analysis: dict[tuple[str, str], str] = {}

RUNTIME_EVENT_KINDS = {
    "assistant_turn_decision",
    "assistant_preview_ready",
    "assistant_auto_reply_blocked",
    "assistant_ready",
    "assistant_stream_ready",
    "assistant_segments_planned",
    "assistant_speaking",
    "assistant_segment_started",
    "assistant_segment_completed",
    "assistant_reply_cancelled",
    "assistant_interrupted",
    "assistant_barge_in_ignored",
    "assistant_played",
    "assistant_spoken",
    "assistant_priority_reply_ready",
    "assistant_rule_analysis_requested",
    "assistant_rule_analysis_completed",
    "assistant_rule_analysis_failed",
    "assistant_auto_reply_failed",
    "assistant_preview_failed",
    "assistant_formal_generation_started",
    "assistant_formal_first_reply_update",
    "assistant_formal_first_tts_started",
    "assistant_formal_first_tts_completed",
    "assistant_formal_generation_finished",
    "document_upload_ack",
    "speaker_alias_evidence",
    "speaker_alias_rewrite_completed",
    "speaker_alias_rewrite_failed",
    "memory_compaction_queued",
    "memory_compaction_completed",
    "memory_compaction_failed",
}


def _public_api_token() -> str:
    return (os.getenv(PUBLIC_API_TOKEN_ENV) or settings.gamevoice_public_api_token or "").strip()


def _authorization_bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _public_api_request_authorized(authorization: str | None, access_token: str | None = None) -> bool:
    expected = _public_api_token()
    if not expected:
        return True
    provided = _authorization_bearer_token(authorization) or str(access_token or "").strip()
    return bool(provided) and provided == expected

_BARGE_IN_NOISE_TOKENS = {
    "嗯",
    "啊",
    "呃",
    "额",
    "哦",
    "哈",
    "哎",
    "诶",
    "欸",
    "嗯嗯",
    "啊啊",
}

_BARGE_IN_TRIGGER_PHRASES = (
    "等一下",
    "等一等",
    "等等",
    "等会",
    "等会儿",
    "停一下",
    "停一停",
    "停停停",
    "先停",
    "先等等",
    "打住",
    "别说了",
    "先别说",
    "暂停",
    "wait",
    "holdon",
    "stop",
)

MEMORY_COMPACTION_TOKEN_THRESHOLD = settings.memory_compaction_token_threshold


def _build_live_silence_gate() -> SilenceGate:
    config = SilenceGateConfig(
        enabled=settings.live_silence_gate_enabled and not os.getenv("GAMEVOICE_TESTING"),
        sample_rate=settings.speaker_live_sample_rate,
        sample_width_bytes=settings.speaker_live_sample_width_bytes,
        channels=settings.speaker_live_channels,
        frame_ms=settings.live_silence_gate_frame_ms,
        pre_roll_ms=settings.live_silence_gate_preroll_ms,
        speech_start_window_ms=settings.live_silence_gate_speech_start_window_ms,
        speech_start_voiced_ms=settings.live_silence_gate_speech_start_voiced_ms,
        hangover_ms=settings.live_silence_gate_hangover_ms,
    )
    frame_classifier = None
    if config.enabled:
        with contextlib.suppress(Exception):
            frame_classifier = WebRtcVadFrameClassifier(settings.live_silence_gate_vad_mode)
    return SilenceGate(config, frame_classifier=frame_classifier)


def _assistant_lookup_commitment_text(
    *,
    preview_text: str | None,
    formal_text: str | None,
) -> str:
    spoken_formal, has_marker = _split_preview_lookup_marker(formal_text)
    return spoken_formal if has_marker else ""


def _preview_lookup_commitment_text(preview_text: str | None) -> str:
    return _assistant_lookup_commitment_text(preview_text=preview_text, formal_text=None)


def _preview_lookup_reply_id(*, table_id: str, preview_text: str | None, source_text: str | None) -> str:
    seed = "\0".join(
        (
            str(table_id or ""),
            _normalized_turn_text(preview_text),
            _normalized_turn_text(source_text),
        )
    )
    return "preview_lookup:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _skillagent_context_events(events: list[dict], *, limit: int = 10) -> list[dict]:
    allowed_kinds = {"voice_transcript", "assistant_spoken", "document_upload_fact"}
    filtered = [
        item
        for item in events
        if item.get("kind") in allowed_kinds and str(item.get("content") or "").strip()
    ]
    return filtered[-limit:]


def _build_document_upload_context_fact(records: list[dict]) -> str:
    filenames = [
        str(item.get("filename") or "").strip()
        for item in records
        if str(item.get("filename") or "").strip()
    ]
    if not filenames:
        return "你刚刚收到用户上传的文件。之后用户说“这个文件”或“刚刚上传的文件”时，通常指这些文件。"
    joined = "、".join(filenames)
    return (
        f"你刚刚收到用户上传的文件：{joined}。"
        "之后用户说“这个文件”“刚刚上传的文件”或“刚才发的文件”时，通常指这些文件。"
    )


def _latest_user_lookup_query(events: list[dict], *, fallback: str) -> str:
    for item in reversed(events):
        if item.get("kind") != "voice_transcript":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content
    return fallback


_LOOKUP_REQUEST_TOKENS = (
    "联网搜索",
    "联网搜",
    "上网查",
    "网上查",
    "搜索",
    "搜一下",
    "搜一搜",
    "查询",
    "查找",
    "查一查",
    "查一下",
    "查查",
    "浏览",
    "事实核查",
)
_LOOKUP_TARGET_HINT_TOKENS = (
    "新闻",
    "天气",
    "网页",
    "网站",
    "资料",
    "文档",
    "文件",
    "规则",
    "FAQ",
    "faq",
    "官网",
    "官方",
    "最近",
    "今天",
    "昨天",
    "明天",
)
_LOOKUP_COMMITMENT_TOKENS = (
    "查",
    "搜",
    "找",
    "确认",
    "看看",
    "看一下",
    "瞅",
    "联网",
)
_LOOKUP_COMMITMENT_CUE_TOKENS = (
    "我",
    "马上",
    "这就",
    "帮你",
    "给你",
    "去",
    "稍等",
)
_LOOKUP_CLARIFYING_TOKENS = (
    "哪",
    "什么",
    "哪个",
    "具体",
    "方面",
    "你想",
    "要查",
)
_LOOKUP_RESULT_INJECTION_PREFIX = "你刚刚查询得到的结果是："


def _latest_voice_transcript_text(events: list[dict] | None) -> str:
    for item in reversed(events or []):
        if item.get("kind") != "voice_transcript":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content
    return ""


def _context_has_lookup_result(events: list[dict] | None) -> bool:
    for item in events or []:
        content = str(item.get("content") or "")
        if item.get("kind") == "rule_reference" or _LOOKUP_RESULT_INJECTION_PREFIX in content:
            return True
    return False


def _strip_transcript_speaker_prefix(text: str) -> str:
    return re.sub(r"^\s*speaker[_-]?\d+\s*[：:]\s*", "", str(text or "").strip(), flags=re.IGNORECASE)


def _lookup_request_has_target(text: str) -> bool:
    if any(token in text for token in _LOOKUP_TARGET_HINT_TOKENS):
        return True
    for token in _LOOKUP_REQUEST_TOKENS:
        if token not in text:
            continue
        target = text.split(token, 1)[1]
        target = re.sub(r"[，,。.!！?？\s]", "", target)
        target = re.sub(r"^(一下|一下一下|看看|帮我|帮忙|给我|关于|有关|下|吧|呀)+", "", target)
        if len(target) >= 2:
            return True
    return False


def _has_explicit_lookup_intent(text: str) -> bool:
    content = _strip_transcript_speaker_prefix(text)
    if not any(token in content for token in _LOOKUP_REQUEST_TOKENS):
        return False
    return _lookup_request_has_target(content)


def _assistant_formal_looks_like_lookup_commitment(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    if "?" in content or "？" in content:
        return False
    if any(token in content for token in _LOOKUP_CLARIFYING_TOKENS):
        return False
    return any(token in content for token in _LOOKUP_COMMITMENT_TOKENS) and any(
        token in content for token in _LOOKUP_COMMITMENT_CUE_TOKENS
    )


def _should_repair_missing_lookup_marker(dialog_events: list[dict] | None, formal_text: str) -> bool:
    if _context_has_lookup_result(dialog_events):
        return False
    return _has_explicit_lookup_intent(_latest_voice_transcript_text(dialog_events)) and (
        _assistant_formal_looks_like_lookup_commitment(formal_text)
    )


def _record_speaker_alias_evidence_from_realtime_event(
    table_id: str,
    event: dict,
    *,
    seen_keys: set[tuple[str, str, str]] | None = None,
) -> bool:
    if event.get("event") not in {"transcript", "final"}:
        return False

    recorded = False

    def record(*, speaker_id: str | None, text: str, key_parts: tuple[object, ...]) -> None:
        nonlocal recorded
        normalized_speaker_id = normalize_realtime_speaker_id(speaker_id)
        content = _normalize_session_whitespace(text)
        if not normalized_speaker_id or not content:
            return
        key = (
            normalized_speaker_id,
            content,
            "|".join(str(part) for part in key_parts if part is not None),
        )
        if seen_keys is not None:
            if key in seen_keys:
                return
            seen_keys.add(key)
        session_manager.ensure_speaker_identity(
            table_id,
            normalized_speaker_id,
            speaker_label=normalized_speaker_id,
            mark_observed=True,
        )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "speaker_alias_evidence",
                "source": "live_asr",
                "content": f"{normalized_speaker_id}：{content}",
                "speaker_id": normalized_speaker_id,
            },
        )
        recorded = True

    sentence_list = ((event.get("sentences") or {}).get("sentence_list") or [])
    for sentence in sentence_list:
        if sentence.get("sentence_type") not in (1, "1"):
            continue
        record(
            speaker_id=sentence.get("speaker_id"),
            text=str(sentence.get("sentence") or ""),
            key_parts=(
                sentence.get("sentence_id"),
                sentence.get("start_time"),
                sentence.get("end_time"),
            ),
        )

    if not recorded and (
        event.get("event") == "final" or event.get("slice_type") in (2, "2")
    ):
        record(
            speaker_id=event.get("speaker_id"),
            text=str(event.get("text") or ""),
            key_parts=(event.get("index"), event.get("event")),
        )

    return recorded


def _reset_lookup_runtime_for_tests() -> None:
    with _lookup_runtime_lock:
        _active_lookup_by_table.clear()
        _pending_lookup_by_table.clear()
        _lookup_job_to_analysis.clear()


def _lookup_runtime_is_busy(table_id: str) -> bool:
    with _lookup_runtime_lock:
        return table_id in _active_lookup_by_table


def _build_lookup_request(
    *,
    table_id: str,
    reply_id: str,
    query: str,
    events: list[dict],
) -> dict:
    return {
        "table_id": table_id,
        "reply_id": reply_id,
        "query": query,
        "events": list(events),
        "documents": document_store.list_documents(table_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _start_lookup_request(request: dict) -> dict:
    table_id = str(request["table_id"])
    try:
        record = rule_analysis_service.start(
            table_id=table_id,
            query=str(request["query"]),
            events=list(request.get("events") or []),
            documents=list(request.get("documents") or []),
            inject_only=False,
        )
    except TypeError as exc:
        if "documents" not in str(exc):
            raise
        record = rule_analysis_service.start(
            table_id=table_id,
            query=str(request["query"]),
            events=list(request.get("events") or []),
            inject_only=False,
        )
    analysis_id = str(record.get("analysis_id") or "")
    if analysis_id:
        with _lookup_runtime_lock:
            _active_lookup_by_table[table_id] = {
                "analysis_id": analysis_id,
                "query": request.get("query", ""),
                "reply_id": request.get("reply_id", ""),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
    return record


def _start_or_queue_lookup_request(request: dict) -> dict:
    table_id = str(request["table_id"])
    with _lookup_runtime_lock:
        if table_id in _active_lookup_by_table:
            if table_id not in _pending_lookup_by_table:
                _pending_lookup_by_table[table_id] = dict(request)
                queued = True
            else:
                queued = False
            return {
                "status": "queued" if queued else "busy",
                "message": LOOKUP_BUSY_REPLY_TEXT,
            }
    record = _start_lookup_request(request)
    return {"status": "started", "analysis": record}


def _register_lookup_oralized_job(table_id: str, *, analysis_id: str, job_id: str | None) -> None:
    if not job_id:
        return
    with _lookup_runtime_lock:
        _lookup_job_to_analysis[(table_id, job_id)] = analysis_id
        active = _active_lookup_by_table.get(table_id)
        if active and active.get("analysis_id") == analysis_id:
            active["oralized_job_id"] = job_id


def _schedule_pending_lookup_start(
    table_id: str,
    *,
    delay_seconds: float = LOOKUP_QUEUE_DELAY_SECONDS,
) -> None:
    timer = threading.Timer(delay_seconds, _start_pending_lookup_if_idle, args=(table_id,))
    timer.daemon = True
    timer.start()


def _start_pending_lookup_if_idle(table_id: str) -> None:
    with _lookup_runtime_lock:
        if table_id in _active_lookup_by_table:
            return
        request = _pending_lookup_by_table.pop(table_id, None)
    if request is None:
        return
    _start_lookup_request(request)


def _on_lookup_analysis_failed(table_id: str, analysis_id: str) -> None:
    with _lookup_runtime_lock:
        active = _active_lookup_by_table.get(table_id)
        if active and active.get("analysis_id") == analysis_id:
            _active_lookup_by_table.pop(table_id, None)
            _pending_lookup_by_table.pop(table_id, None)


def _on_lookup_oralized_tts_finished(
    table_id: str,
    *,
    job_id: str,
    completed_normally: bool,
) -> None:
    with _lookup_runtime_lock:
        analysis_id = _lookup_job_to_analysis.pop((table_id, job_id), None)
        active = _active_lookup_by_table.get(table_id)
        if not active:
            return
        if analysis_id and active.get("analysis_id") != analysis_id:
            return
        if active.get("oralized_job_id") and active.get("oralized_job_id") != job_id:
            return
        _active_lookup_by_table.pop(table_id, None)
        has_pending = table_id in _pending_lookup_by_table
        if not completed_normally:
            _pending_lookup_by_table.pop(table_id, None)
            return
    if has_pending:
        _schedule_pending_lookup_start(table_id, delay_seconds=LOOKUP_QUEUE_DELAY_SECONDS)


def _queue_lookup_if_table_busy_from_formal(
    *,
    table_id: str,
    formal_text: str | None,
    dialog_events: list[dict],
    reply_id: str,
) -> bool:
    spoken_formal, has_marker = _split_preview_lookup_marker(formal_text)
    if not has_marker or not _lookup_runtime_is_busy(table_id):
        return False
    context_events = _skillagent_context_events(dialog_events)
    lookup_query = _latest_user_lookup_query(context_events, fallback=spoken_formal)
    request = _build_lookup_request(
        table_id=table_id,
        reply_id=reply_id,
        query=lookup_query,
        events=context_events,
    )
    _start_or_queue_lookup_request(request)
    return True


def _spawn_skillagent_for_lookup_commitment(
    *,
    table_id: str,
    result: dict,
    dialog_events: list[dict],
) -> None:
    reply_id = str(result.get("reply_id") or "")
    formal_text = str(result.get("raw_formal_text") or (result.get("reply") or {}).get("content") or "")
    preview_text = str(result.get("preview_handoff_reply_text") or "")
    commitment_text = _assistant_lookup_commitment_text(
        preview_text=preview_text,
        formal_text=formal_text,
    )
    if not commitment_text:
        return

    context_events = _skillagent_context_events(dialog_events)
    lookup_query = _latest_user_lookup_query(context_events, fallback=commitment_text)

    claim = getattr(rule_analysis_service, "try_claim_reply_spawn", None)
    if callable(claim) and not claim(table_id=table_id, reply_id=reply_id):
        return

    try:
        request = _build_lookup_request(
            table_id=table_id,
            query=lookup_query,
            events=context_events,
            reply_id=reply_id,
        )
        outcome = _start_or_queue_lookup_request(request)
    except TypeError:
        raise
    if outcome.get("status") == "started":
        result["analysis"] = outcome.get("analysis")
        return
    result["lookup_deferred"] = outcome.get("status") == "queued"
    result["lookup_busy_reply"] = LOOKUP_BUSY_REPLY_TEXT


class TableCreateRequest(BaseModel):
    name: str = Field(default="Arkham table")
    assistant_name: str = Field(default="宝子")
    assistant_personality: str = Field(default="")
    assistant_voice_id: str = Field(default="")
    origin: str = Field(default="manual")


class TableRenameRequest(BaseModel):
    name: str = Field(min_length=1)


class MemoryCompactTextRequest(BaseModel):
    text: str = Field(min_length=1)
    previous_summary: str = ""


class AssistantProfileUpdateRequest(BaseModel):
    assistant_name: str = Field(min_length=1)



class MobileDiagnosticEntry(BaseModel):
    ts: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    component: str = Field(min_length=1)
    event: str = Field(min_length=1)
    details: dict = Field(default_factory=dict)


class MobileDiagnosticsRequest(BaseModel):
    entries: list[MobileDiagnosticEntry] = Field(default_factory=list)


class SpeakerIdentityObserveRequest(BaseModel):
    diarized_speaker_id: str = Field(min_length=1)
    embedding: list[float] | None = None


class SpeakerIdentityIngestObservation(BaseModel):
    segment_id: str | None = None
    diarized_speaker_id: str = Field(min_length=1)
    speaker_profile_id: str | None = None
    segment_start_ms: int | None = None
    segment_end_ms: int | None = None
    channel: int | None = None
    diarization_confidence: float | None = None
    embedding: list[float] | None = None
    transcript_text: str = ""
    candidate_name: str | None = None
    candidate_confidence: float | None = None


class SpeakerIdentityDiarizationSegment(BaseModel):
    segment_id: str | None = None
    diarized_speaker_id: str = Field(min_length=1)
    speaker_profile_id: str | None = None
    segment_start_ms: int | None = None
    segment_end_ms: int | None = None
    transcript_text: str = ""
    channel: int | None = None
    diarization_confidence: float | None = None


class SpeakerIdentityPyannoteSegment(BaseModel):
    segment_id: str | None = None
    speaker: str | None = None
    speaker_label: str | None = None
    diarized_speaker_id: str | None = None
    speaker_profile_id: str | None = None
    speaker_profile: str | None = None
    start: float | int | str | None = None
    end: float | int | str | None = None
    text: str = ""
    confidence: float | None = None
    channel: int | None = None

    @model_validator(mode="after")
    def validate_identity_reference(self):
        if not (self.diarized_speaker_id or self.speaker or self.speaker_label):
            raise ValueError("pyannote segment requires diarized_speaker_id or speaker")
        return self


class SpeakerIdentitySpeakerEmbedding(BaseModel):
    diarized_speaker_id: str | None = None
    speaker_profile_id: str | None = None
    embedding: list[float] = Field(min_length=1)
    sample_count: int | None = None

    @model_validator(mode="after")
    def validate_identity_reference(self):
        if not (self.diarized_speaker_id or self.speaker_profile_id):
            raise ValueError("speaker embedding requires diarized_speaker_id or speaker_profile_id")
        return self


class SpeakerIdentityWeSpeakerEmbedding(BaseModel):
    diarized_speaker_id: str | None = None
    speaker: str | None = None
    speaker_label: str | None = None
    speaker_profile_id: str | None = None
    speaker_profile: str | None = None
    vector: list[float] = Field(min_length=1)
    sample_count: int | None = None

    @model_validator(mode="after")
    def validate_identity_reference(self):
        if not (self.diarized_speaker_id or self.speaker or self.speaker_label or self.speaker_profile_id or self.speaker_profile):
            raise ValueError("wespeaker embedding requires speaker reference")
        return self


class SpeakerIdentityNameCandidate(BaseModel):
    diarized_speaker_id: str | None = None
    speaker_profile_id: str | None = None
    candidate_name: str = Field(min_length=1)
    candidate_confidence: float | None = None

    @model_validator(mode="after")
    def validate_identity_reference(self):
        if not (self.diarized_speaker_id or self.speaker_profile_id):
            raise ValueError("name candidate requires diarized_speaker_id or speaker_profile_id")
        return self


class SpeakerIdentityIngestRequest(BaseModel):
    source: str = Field(min_length=1)
    session_id: str | None = None
    observations: list[SpeakerIdentityIngestObservation] = Field(default_factory=list)
    pyannote_segments: list[SpeakerIdentityPyannoteSegment] = Field(default_factory=list)
    diarization_segments: list[SpeakerIdentityDiarizationSegment] = Field(default_factory=list)
    wespeaker_embeddings: list[SpeakerIdentitySpeakerEmbedding] = Field(default_factory=list)
    speaker_embeddings: list[SpeakerIdentitySpeakerEmbedding] = Field(default_factory=list)
    name_candidates: list[SpeakerIdentityNameCandidate] = Field(default_factory=list)


class SpeakerIdentityLiveIngestRequest(BaseModel):
    source: str = Field(min_length=1)
    live_session_id: str = Field(min_length=1)
    observations: list[SpeakerIdentityIngestObservation] = Field(default_factory=list)
    pyannote_segments: list[SpeakerIdentityPyannoteSegment] = Field(default_factory=list)
    diarization_segments: list[SpeakerIdentityDiarizationSegment] = Field(default_factory=list)
    wespeaker_embeddings: list[SpeakerIdentityWeSpeakerEmbedding] = Field(default_factory=list)
    speaker_embeddings: list[SpeakerIdentitySpeakerEmbedding] = Field(default_factory=list)
    name_candidates: list[SpeakerIdentityNameCandidate] = Field(default_factory=list)


class SpeakerIdentityLinkRequest(BaseModel):
    speaker_id: str = Field(min_length=1)
    linked_name: str = Field(min_length=1)


class SpeakerIdentityOverrideAcceptRequest(BaseModel):
    speaker_id: str = Field(min_length=1)
    linked_name: str = Field(min_length=1)


def _find_speech_job_event(table_id: str, job_id: str) -> dict | None:
    return session_manager.find_assistant_reply_by_job(table_id, job_id)


def _is_public_speech_job_event(event: dict) -> bool:
    if event.get("kind") == "assistant_preview":
        return False
    if event.get("source") == "runtime_preview":
        return False
    return True


def _start_progressive_tts_stream(table_id: str, speech_job: dict, *, adapter) -> dict:
    stream = tts_stream_bridge.open_stream(
        job_id=speech_job.get("job_id"),
        turn_id=speech_job.get("turn_id"),
        reply_id=speech_job.get("reply_id"),
        segment_count=speech_job.get("segment_count", 0),
    )

    def worker() -> None:
        collected_chunks: list[bytes] = []
        output_format = speech_job.get("format", "mp3")
        try:
            def on_segment_audio(*, segment_index: int, text: str, audio_bytes: bytes, format_name: str) -> None:
                nonlocal output_format
                output_format = format_name or output_format
                collected_chunks.append(audio_bytes)
                segment = _find_segment(speech_job, segment_index)
                if segment is not None:
                    output_path = Path(segment["output_path"])
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(audio_bytes)
                    segment["bytes"] = len(audio_bytes)
                    segment["format"] = output_format
                tts_stream_bridge.append_chunk(
                    stream["stream_id"],
                    segment_index=segment_index,
                    text=text,
                    audio_bytes=audio_bytes,
                )

            adapter.stream_job_audio(speech_job, on_segment_audio=on_segment_audio)
            output_path = Path(speech_job["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"".join(collected_chunks))
            speech_job["bytes"] = output_path.stat().st_size
            speech_job["format"] = output_format
            speech_job["status"] = "ready"
        except Exception as exc:
            speech_job["status"] = "failed"
            session_manager.append_runtime_event(
                table_id,
                {
                    "kind": "assistant_tts_failed",
                    "source": "tts",
                    "content": str(exc),
                    "job_id": speech_job.get("job_id"),
                    "turn_id": speech_job.get("turn_id"),
                    "reply_id": speech_job.get("reply_id"),
                },
            )
        finally:
            tts_stream_bridge.finish_stream(stream["stream_id"])

    threading.Thread(target=worker, daemon=True).start()
    return stream


def _prepare_tts_stream_for_job(table_id: str, speech_job: dict, *, adapter=None) -> dict | None:
    if not speech_job.get("accepted"):
        return None
    if hasattr(adapter, "stream_job_audio") and speech_job.get("status") == "preparing":
        return _start_progressive_tts_stream(table_id, speech_job, adapter=adapter)
    try:
        return tts_stream_bridge.start_stream(speech_job)
    except FileNotFoundError:
        return None


def _append_rule_analysis_requested_event(record: dict) -> None:
    session_manager.append_runtime_event(
        record["table_id"],
        {
            "kind": "assistant_rule_analysis_requested",
            "source": "rule_analysis",
            "content": record["ack_text"],
            "analysis_id": record["analysis_id"],
            "query": record["query"],
            "status": record["status"],
        },
    )


async def _safe_send_ws_json(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if 'close message has been sent' in str(exc):
            return False
        raise


def _append_rule_analysis_completed_event(record: dict) -> None:
    result = record.get("result") or {}
    session_manager.append_runtime_event(
        record["table_id"],
        {
            "kind": "assistant_rule_analysis_completed",
            "source": result.get("source", "rule_analysis"),
            "content": result.get("content", ""),
            "analysis_id": record["analysis_id"],
            "query": record["query"],
            "status": record["status"],
        },
    )
    _ensure_rule_analysis_context_injected(record)
    _materialize_rule_analysis_reply(record)


def _inject_analysis_result_to_stream(record: dict) -> None:
    """Inject rule analysis result into main event stream, then materialize natural reply + TTS."""
    result = record.get("result") or {}
    session_manager.append_runtime_event(
        record["table_id"],
        {
            "kind": "assistant_rule_analysis_injected",
            "source": result.get("source", "rule_analysis"),
            "content": result.get("content", ""),
            "analysis_id": record["analysis_id"],
            "query": record["query"],
            "status": record["status"],
        },
    )
    _ensure_rule_analysis_context_injected(record)
    _materialize_rule_analysis_reply(record)


def _append_rule_analysis_failed_event(record: dict) -> None:
    session_manager.append_runtime_event(
        record["table_id"],
        {
            "kind": "assistant_rule_analysis_failed",
            "source": "rule_analysis",
            "content": record.get("error", ""),
            "analysis_id": record["analysis_id"],
            "query": record["query"],
            "status": record["status"],
        },
    )
    _on_lookup_analysis_failed(record["table_id"], record["analysis_id"])


def _estimate_memory_compaction_tokens(events: list[dict], previous_summary: str = "") -> int:
    total = len(str(previous_summary or ""))
    for item in events:
        total += len(str(item.get("content") or ""))
    return total


def _build_memory_compaction_source_text(previous_summary: str, events: list[dict]) -> str:
    return MiniMaxDialogClient._build_memory_compaction_user_prompt(
        previous_summary=str(previous_summary or ""),
        events=list(events or []),
    )


def _maybe_schedule_memory_compaction(table_id: str) -> dict | None:
    if table_id not in session_manager.tables:
        return None
    if memory_compaction_store.has_active_for_table(table_id):
        return None
    payload = session_manager.build_memory_compaction_payload(table_id)
    estimated_tokens = _estimate_memory_compaction_tokens(
        list(payload.get("active_events") or []),
        str(payload.get("previous_summary") or ""),
    )
    if estimated_tokens <= MEMORY_COMPACTION_TOKEN_THRESHOLD:
        return None
    payload["estimated_tokens"] = estimated_tokens
    record = memory_compaction_service.start(table_id=table_id, payload=payload)
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "memory_compaction_queued",
            "source": "memory_compactor",
            "content": f"auto compaction queued at {estimated_tokens} estimated tokens",
            "compaction_id": record["compaction_id"],
            "checkpoint": record["checkpoint"],
            "estimated_tokens": estimated_tokens,
            "trigger": "threshold_exceeded",
        },
    )
    return record


def _rule_analysis_reference_content(content: str) -> str:
    cleaned = str(content or "").strip()
    if cleaned.startswith("你刚刚查询得到的结果是："):
        return cleaned
    return f"你刚刚查询得到的结果是：{cleaned}"


def _ensure_rule_analysis_context_injected(record: dict) -> dict | None:
    result = record.get("result") or {}
    result_content = str(result.get("content") or "").strip()
    if not result_content:
        return None
    table_id = record["table_id"]
    analysis_id = record["analysis_id"]
    for item in session_manager.list_context(table_id):
        if item.get("kind") == "rule_reference" and item.get("analysis_id") == analysis_id:
            return item
    return session_manager.append_context_event(
        table_id,
        {
            "kind": "rule_reference",
            "source": "rule_analysis",
            "content": _rule_analysis_reference_content(result_content),
            "analysis_id": analysis_id,
            "query": record["query"],
            "status": record.get("status"),
        },
    )


def _build_rule_analysis_return_reply(record: dict) -> dict | None:
    table_id = record["table_id"]
    result = record.get("result") or {}
    result_content = str(result.get("content") or "").strip()
    if not result_content:
        return None

    _ensure_rule_analysis_context_injected(record)
    dialog_events = _dialog_events_with_speaker_alias_map(table_id)
    dialog_client = getattr(getattr(auto_interrupt_service, "orchestrator", None), "dialog_client", None)
    if dialog_client is None:
        return None
    generate_reply = getattr(dialog_client, "generate_reply", None)
    if not callable(generate_reply):
        return None
    try:
        reply = generate_reply(
            mode=CONVERSATION_MODE,
            transcript=record["query"],
            events=dialog_events,
            strict=True,
            assistant_name=session_manager.get_assistant_name(table_id),
            assistant_personality=session_manager.get_assistant_personality(table_id),
        )
    except TypeError as exc:
        if (
            "strict" not in str(exc)
            and "assistant_name" not in str(exc)
            and "assistant_personality" not in str(exc)
        ):
            raise
        reply = generate_reply(
            mode=CONVERSATION_MODE,
            transcript=record["query"],
            events=dialog_events,
        )
    except Exception:
        return None
    content = str(reply.get("content") or reply.get("lead") or reply.get("tail") or "").strip()
    content, _ = _split_preview_lookup_marker(content)
    content = content.strip()
    if not content:
        return None
    return {
        "source": str(reply.get("source") or result.get("source") or "rule_analysis").strip() or "rule_analysis",
        "content": content,
        "lead": "",
        "tail": "",
    }


def _maybe_materialize_pending_rule_analysis_reply(table_id: str) -> None:
    runtime = dialog_runtime_store.snapshot(table_id)
    if runtime["state"] in {"assistant_ready", "agent_speaking"}:
        return
    pending = rule_analysis_store.list_pending_materializations(table_id)
    if not pending:
        return
    _materialize_rule_analysis_reply(pending[0])


def _materialize_rule_analysis_reply(record: dict) -> None:
    result = record.get("result") or {}
    table_id = record["table_id"]
    content = (result.get("content") or "").strip()
    if not content:
        _on_lookup_analysis_failed(table_id, record["analysis_id"])
        return
    runtime = dialog_runtime_store.snapshot(table_id)
    if runtime["state"] in {"assistant_ready", "agent_speaking"}:
        return

    analysis_id = record["analysis_id"]
    existing_reply = [
        item
        for item in session_manager.list_assistant_replies(table_id)
        if item.get("analysis_id") == analysis_id
    ]
    if existing_reply:
        rule_analysis_store.mark_materialized(analysis_id)
        return

    try:
        tts_adapter = getattr(auto_interrupt_service, "tts_adapter", None)
        if tts_adapter is None or not hasattr(tts_adapter, "synthesize_segment"):
            raise RuntimeError("rule analysis materialization requires incremental TTS")
        reply_payload = _build_rule_analysis_return_reply(record)
        if not reply_payload or not str(reply_payload.get("content") or "").strip():
            raise RuntimeError("rule analysis reintegration reply generation failed")
        reply_payload = {
            **reply_payload,
            "content": session_manager.strip_assistant_prefix(
                table_id,
                str(reply_payload.get("content") or ""),
            ).strip(),
            "lead": "",
            "tail": "",
        }
        turn_id = result.get("turn_id") or uuid4().hex
        reply_id = result.get("reply_id") or uuid4().hex
        speech_job = _build_progressive_speech_job(
            text=reply_payload.get("content", content),
            turn_id=turn_id,
            reply_id=reply_id,
            output_dir=getattr(tts_adapter, "output_dir", ".runtime/tts"),
        )
        tts_stream = tts_stream_bridge.open_stream(
            job_id=speech_job["job_id"],
            turn_id=turn_id,
            reply_id=reply_id,
            segment_count=0,
        )
        stream_id = tts_stream.get("stream_id")
        speech_job["stream_id"] = stream_id
        synth = tts_adapter.synthesize_segment(
            reply_payload.get("content", content),
            voice_id=session_manager.get_assistant_voice_id(table_id),
        )
        audio_bytes = synth["audio_bytes"]
        format_name = synth.get("format", "mp3")
        segment = _append_progressive_segment_to_job(
            speech_job,
            segment_text=reply_payload.get("content", content),
            audio_bytes=audio_bytes,
            format_name=format_name,
        )
        speech_job["status"] = "ready"
        tts_stream_bridge.append_chunk(
            stream_id,
            segment_index=segment["index"],
            text=segment["text"],
            audio_bytes=audio_bytes,
        )
        tts_stream_bridge.finish_stream(stream_id)
    except Exception as exc:
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_rule_analysis_failed",
                "source": "tts",
                "content": str(exc),
                "analysis_id": analysis_id,
                "query": record["query"],
                "status": "tts_failed",
            },
        )
        _on_lookup_analysis_failed(table_id, analysis_id)
        return

    speech_job["turn_id"] = turn_id
    speech_job["reply_id"] = reply_id
    reply_event = {
        "kind": "assistant_reply",
        "source": reply_payload.get("source", result.get("source", "rule_analysis")),
        "mode": CONVERSATION_MODE,
        "content": reply_payload.get("content", content),
        "lead": reply_payload.get("lead", ""),
        "tail": reply_payload.get("tail", ""),
        "analysis_id": analysis_id,
        "query": record["query"],
        "turn_id": turn_id,
        "reply_id": reply_id,
        "speech_job": speech_job,
    }
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_ready",
            "source": "rule_analysis",
            "mode": CONVERSATION_MODE,
            "content": reply_payload.get("content", content),
            "lead": reply_payload.get("lead", ""),
            "tail": reply_payload.get("tail", ""),
            "analysis_id": analysis_id,
            "query": record["query"],
            "job_id": speech_job.get("job_id"),
            "stream_id": speech_job.get("stream_id"),
            "turn_id": turn_id,
            "reply_id": reply_id,
        },
    )
    if speech_job.get("stream_id"):
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_stream_ready",
                "source": "runtime",
                "content": reply_payload.get("content", content),
                "lead": reply_payload.get("lead", ""),
                "tail": reply_payload.get("tail", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "stream_id": speech_job.get("stream_id"),
            },
        )
    _register_lookup_oralized_job(
        table_id,
        analysis_id=analysis_id,
        job_id=speech_job.get("job_id"),
    )
    session_manager.append_assistant_reply(table_id, reply_event)
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_segments_planned",
            "source": "rule_analysis",
            "mode": CONVERSATION_MODE,
            "content": reply_payload.get("content", content),
            "lead": reply_payload.get("lead", ""),
            "tail": reply_payload.get("tail", ""),
            "analysis_id": analysis_id,
            "query": record["query"],
            "job_id": speech_job.get("job_id"),
            "stream_id": speech_job.get("stream_id"),
            "turn_id": turn_id,
            "reply_id": reply_id,
            "segment_count": speech_job.get("segment_count", 0),
            "segment_statuses": speech_job.get("segment_statuses", []),
        },
    )
    visible_content = reply_payload.get("content", content)
    dialog_runtime_store.on_priority_agent_reply_ready(
        table_id,
        job_id=speech_job.get("job_id"),
        reply_text=visible_content,
        segment_count=speech_job.get("segment_count", 0),
        barge_in_grace_seconds=2.5,
    )
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_priority_reply_ready",
            "source": "rule_analysis",
            "mode": CONVERSATION_MODE,
            "content": visible_content,
            "analysis_id": analysis_id,
            "query": record["query"],
            "job_id": speech_job.get("job_id"),
            "stream_id": speech_job.get("stream_id"),
            "turn_id": turn_id,
            "reply_id": reply_id,
            "barge_in_grace_seconds": 2.5,
        },
    )
    rule_analysis_store.mark_materialized(analysis_id)


def _interrupt_active_runtime_job(table_id: str, runtime: dict) -> dict | None:
    job_id = runtime.get("current_job_id")
    if not job_id:
        return None
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        return None
    speech_job = event.get("speech_job")
    if not speech_job:
        return None
    if speech_job.get("status") in {"played", "interrupted", "superseded"}:
        return None
    if "tts_input_chars_total" not in speech_job:
        speech_job["tts_input_chars_total"] = sum(
            _count_content_chars(segment.get("text", "")) for segment in speech_job.get("segment_statuses", [])
        )
    if "tts_input_chunk_count" not in speech_job:
        speech_job["tts_input_chunk_count"] = len(speech_job.get("segment_statuses", []))
    stream_id = _find_stream_id_for_job(table_id, job_id)
    if stream_id is not None:
        try:
            tts_stream_bridge.cancel_stream(stream_id)
        except KeyError:
            pass
    wasted_segments = [
        segment
        for segment in speech_job.get("segment_statuses", [])
        if segment.get("status") != "completed"
    ]
    wasted_chars = sum(_count_content_chars(segment.get("text", "")) for segment in wasted_segments)
    speech_job["tts_wasted_chars_on_interrupt"] = wasted_chars
    speech_job["tts_wasted_chunk_count_on_interrupt"] = len(wasted_segments)
    speech_job["status"] = "interrupted"
    for segment in speech_job.get("segment_statuses", []):
        if segment.get("status") != "completed":
            segment["status"] = "interrupted"
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_reply_cancelled",
            "source": "runtime",
            "content": "assistant reply interrupted by user",
            "job_id": job_id,
            "tts_input_chars_total": speech_job.get("tts_input_chars_total", 0),
            "tts_input_chunk_count": speech_job.get("tts_input_chunk_count", 0),
            "tts_wasted_chars_on_interrupt": wasted_chars,
            "tts_wasted_chunk_count_on_interrupt": len(wasted_segments),
        },
    )
    session_manager.commit_interrupted_reply(table_id, job_id)
    dialog_runtime_store.on_agent_reply_interrupted(table_id, job_id=job_id)
    _on_lookup_oralized_tts_finished(table_id, job_id=job_id, completed_normally=False)
    return speech_job


def _find_stream_id_for_job(table_id: str, job_id: str | None) -> str | None:
    if not job_id:
        return None
    for event in reversed(session_manager.list_runtime_events(table_id)):
        if event.get("kind") != "assistant_stream_ready":
            continue
        if event.get("job_id") != job_id:
            continue
        stream_id = event.get("stream_id")
        if stream_id:
            return str(stream_id)
    return None


def _find_segment(speech_job: dict, segment_index: int) -> dict | None:
    for segment in speech_job.get("segment_statuses", []):
        if segment.get("index") == segment_index:
            return segment
    return None


def _is_terminal_tts_job(speech_job: dict) -> bool:
    return speech_job.get("status") in {"played", "interrupted", "superseded"}


def _mark_tts_job_played(table_id: str, event: dict, job_id: str) -> dict:
    speech_job = event["speech_job"]
    if speech_job.get("status") != "played":
        speech_job["status"] = "played"
    for segment in speech_job.get("segment_statuses", []):
        if segment.get("status") != "completed":
            segment["status"] = "completed"
    session_manager.commit_spoken_reply(table_id, job_id)
    existing_played = [
        item
        for item in session_manager.list_runtime_events(table_id)
        if item.get("kind") == "assistant_played" and item.get("job_id") == job_id
    ]
    if not existing_played:
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_played",
                "source": "runtime",
                "content": event.get("content", ""),
                "job_id": job_id,
            },
        )
    dialog_runtime_store.on_agent_speaking_finished(table_id, job_id=job_id)
    _on_lookup_oralized_tts_finished(table_id, job_id=job_id, completed_normally=True)
    _maybe_materialize_pending_rule_analysis_reply(table_id)
    return speech_job


def _latest_dialog_transcript(events: list[dict]) -> str:
    transcripts = [
        item.get("content", "")
        for item in events
        if item.get("kind") == "voice_transcript" and item.get("content")
    ]
    return transcripts[-1] if transcripts else ""


def _dialog_events_with_speaker_alias_map(
    table_id: str,
    events: list[dict] | None = None,
) -> list[dict]:
    dialog_events = list(events if events is not None else session_manager.list_dialog_context(table_id))
    if any(item.get("kind") == "speaker_alias_map" for item in dialog_events):
        return dialog_events
    alias_map = session_manager.list_speaker_alias_map(table_id)
    if alias_map:
        dialog_events.append(
            {
                "kind": "speaker_alias_map",
                "source": "speaker_identity",
                "speaker_alias_map": alias_map,
            }
        )
    return dialog_events


_EXPLICIT_AUTO_INTERRUPT_REASONS = {
    "assistant_name_called",
    "direct_address",
    "rule_trigger",
    "followup_after_name_call",
    "playful_request",
}


def _is_explicit_auto_interrupt_request(
    transcript: str | None,
    *,
    events: list[dict] | None = None,
    assistant_name: str = "宝子",
) -> bool:
    text = _normalize_session_whitespace(str(transcript or ""))
    if not text:
        return False
    decision = auto_interrupt_service.orchestrator.timing.should_interrupt(
        text,
        events or [],
        assistant_name=assistant_name,
    )
    return bool(decision.get("interrupt")) and decision.get("reason") in _EXPLICIT_AUTO_INTERRUPT_REASONS


def _should_run_auto_interrupt_on_final(
    runtime_snapshot: dict,
    latest_transcript: str | None = None,
    *,
    events: list[dict] | None = None,
    assistant_name: str = "宝子",
) -> bool:
    if runtime_snapshot.get("preview_reply_text"):
        return True
    if runtime_snapshot.get("state") not in {"assistant_ready", "agent_speaking"}:
        return True
    return _is_explicit_auto_interrupt_request(
        latest_transcript,
        events=events,
        assistant_name=assistant_name,
    )


def _normalized_turn_text(text: str | None) -> str:
    return _normalize_session_whitespace(str(text or ""))


def _pending_formal_matches_handoff(
    *,
    pending_source_text: str | None,
    pending_preview_text: str | None,
    pending_preview_job_id: str | None,
    latest_transcript: str | None,
    preview_handoff_reply_text: str | None,
    preview_job_id: str | None,
) -> bool:
    if _normalized_turn_text(pending_source_text) != _normalized_turn_text(latest_transcript):
        return False
    if _normalized_turn_text(pending_preview_text) != _normalized_turn_text(preview_handoff_reply_text):
        return False
    if pending_preview_job_id and preview_job_id and pending_preview_job_id != preview_job_id:
        return False
    return True


def _resolve_final_live_transcript_text(
    runtime_snapshot: dict,
    latest_stable_text: str | None,
) -> str | None:
    pending_source = " ".join(str(runtime_snapshot.get("pending_source_text") or "").split()).strip()
    preview_source = " ".join(str(runtime_snapshot.get("preview_source_text") or "").split()).strip()
    stable_text = " ".join(str(latest_stable_text or "").split()).strip()
    if not stable_text:
        return None

    def source_matches_current_final(source: str) -> bool:
        if not source:
            return False
        if len(stable_text) <= 4:
            return True
        source_folded = source.casefold()
        stable_folded = stable_text.casefold()
        return stable_folded in source_folded or source_folded in stable_folded

    if pending_source and len(pending_source) >= len(stable_text) and source_matches_current_final(pending_source):
        return pending_source
    if preview_source and len(preview_source) >= len(stable_text) and source_matches_current_final(preview_source):
        return preview_source
    if stable_text:
        return stable_text
    return pending_source or preview_source or None


def _build_live_dialog_events_for_transcript(table_id: str, transcript: str) -> list[dict]:
    dialog_events = _dialog_events_with_speaker_alias_map(table_id)
    dialog_events.append(
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": _format_live_dialog_transcript(table_id, transcript),
        }
    )
    return dialog_events


def _format_live_dialog_transcript(table_id: str, transcript: str) -> str:
    cleaned = _normalize_session_whitespace(str(transcript or ""))
    if re.match(r"^speaker_\d+\s*[：:]", cleaned):
        return cleaned
    return session_manager.format_user_utterance(text=cleaned, table_id=table_id)


def _strip_dialogue_prefix(text: str | None) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return ""
    return re.sub(r"^[^：]{1,40}：", "", cleaned, count=1).strip()


def _resolve_preview_handoff_context(
    dialog_events: list[dict],
    runtime_snapshot: dict,
    *,
    current_source_text: str | None = None,
) -> tuple[str | None, str | None]:
    preview_reply_text = " ".join(str(runtime_snapshot.get("preview_reply_text") or "").split()).strip()
    preview_source_text = " ".join(str(runtime_snapshot.get("preview_source_text") or "").split()).strip()
    if preview_reply_text:
        if current_source_text:
            current_source = _normalized_turn_text(_strip_dialogue_prefix(current_source_text))
            preview_source = _normalized_turn_text(_strip_dialogue_prefix(preview_source_text))
            if not preview_source or preview_source != current_source:
                return None, None
        return preview_reply_text, preview_source_text or None

    if current_source_text:
        return None, None

    for item in reversed(dialog_events):
        if item.get("kind") != "assistant_spoken":
            continue
        if item.get("source") != "runtime_preview":
            continue
        preview_reply_text = _strip_dialogue_prefix(item.get("content"))
        if preview_reply_text:
            return preview_reply_text, preview_source_text or None
    return None, None


def _trim_formal_reply_after_preview(reply: dict, preview_reply_text: str | None) -> dict:
    normalized = normalize_reply_payload(reply, default_source=reply.get("source", "companion"))
    preview_text = " ".join(str(preview_reply_text or "").split()).strip()
    if not preview_text:
        return normalized

    content = " ".join(str(normalized.get("content") or "").split()).strip()
    if not content.startswith(preview_text):
        return normalized

    remainder = content[len(preview_text) :].lstrip(" ，。！？、；：)]}\"'")
    remainder = remainder.strip()
    if not remainder:
        return normalized

    return normalize_reply_payload(
        {
            "source": normalized.get("source", "companion"),
            "lead": "",
            "tail": "",
            "content": remainder,
        },
        default_source=normalized.get("source", "companion"),
    )


def _extract_formal_content_after_preview(reply: dict, preview_reply_text: str | None) -> str:
    normalized = normalize_reply_payload(reply, default_source=reply.get("source", "companion"))
    preview_text = " ".join(str(preview_reply_text or "").split()).strip()
    content = " ".join(str(normalized.get("content") or "").split()).strip()
    if not preview_text:
        return content
    if not content.startswith(preview_text):
        return content
    remainder = content[len(preview_text) :].lstrip(" 锛屻€傦紒锛熴€侊紱锛?]}\"'")
    return remainder.strip()


def _split_complete_tts_segments(text: str) -> tuple[list[str], str]:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return [], ""
    segments = split_tts_segments(cleaned)
    if not segments:
        return [], ""
    if cleaned.endswith(("。", "！", "？", ".", "!", "?")):
        return segments, ""
    if len(segments) == 1:
        return [], segments[0]
    return segments[:-1], segments[-1]


def _extract_incremental_tts_text(full_text: str, emitted_text: str) -> str:
    cleaned_full = " ".join(str(full_text or "").split()).strip()
    cleaned_emitted = " ".join(str(emitted_text or "").split()).strip()
    if not cleaned_full:
        return ""
    if not cleaned_emitted:
        return cleaned_full
    if not cleaned_full.startswith(cleaned_emitted):
        return cleaned_full
    remainder = cleaned_full[len(cleaned_emitted) :].lstrip(" ，、:：；")
    if len(remainder) > 1:
        remainder = remainder.lstrip("。！？")
    return remainder.strip()


def _derive_spoken_prefix_for_diff(full_text: str, pending_remainder: str) -> str:
    cleaned_full = " ".join(str(full_text or "").split()).strip()
    cleaned_pending = " ".join(str(pending_remainder or "").split()).strip()
    if not cleaned_full:
        return ""
    if not cleaned_pending:
        return cleaned_full
    if cleaned_full.endswith(cleaned_pending):
        return cleaned_full[: -len(cleaned_pending)].strip()
    if len(cleaned_pending) < len(cleaned_full):
        return cleaned_full[: len(cleaned_full) - len(cleaned_pending)].strip()
    return cleaned_full


def _derive_committed_prefix_for_state(full_text: str, *, min_content_chars: int = 0) -> str:
    cleaned_full = " ".join(str(full_text or "").split()).strip()
    if not cleaned_full:
        return ""
    boundaries = "，。！？,.!?；;：:"
    best_prefix = ""
    for index, char in enumerate(cleaned_full):
        if char not in boundaries:
            continue
        candidate = cleaned_full[: index + 1].strip()
        content_chars = [
            ch for ch in candidate if ch not in boundaries and not ch.isspace()
        ]
        if len(content_chars) < min_content_chars:
            continue
        best_prefix = candidate
    return best_prefix


def _display_formal_content(emitted_segments: list[str], committed_prefix: str) -> str:
    if committed_prefix:
        return committed_prefix
    return " ".join(str(segment or "").strip() for segment in emitted_segments if str(segment or "").strip()).strip()


def _count_content_chars(text: str) -> int:
    boundaries = "，。！？,.!?；;：:"
    cleaned = " ".join(str(text or "").split()).strip()
    return sum(1 for ch in cleaned if ch not in boundaries and not ch.isspace())


def _derive_first_provisional_chunk(text: str, *, min_content_chars: int = 8) -> str:
    return _derive_committed_prefix_for_state(
        text,
        min_content_chars=min_content_chars,
    )


def _build_progressive_speech_job(
    *,
    text: str,
    turn_id: str,
    reply_id: str,
    output_dir: str | Path,
) -> dict:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    job_id = uuid4().hex
    return {
        "accepted": True,
        "job_id": job_id,
        "turn_id": turn_id,
        "reply_id": reply_id,
        "status": "preparing",
        "text": text,
        "segments": [],
        "segment_count": 0,
        "segment_statuses": [],
        "format": "mp3",
        "output_path": str(output_path / f"{job_id}.mp3"),
        "bytes": 0,
        "tts_input_chars_total": 0,
        "tts_input_chunk_count": 0,
        "tts_wasted_chars_on_interrupt": 0,
        "tts_wasted_chunk_count_on_interrupt": 0,
    }


def _append_progressive_segment_to_job(
    speech_job: dict,
    *,
    segment_text: str,
    audio_bytes: bytes,
    format_name: str,
) -> dict:
    output_dir = Path(speech_job["output_path"]).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_index = len(speech_job.get("segment_statuses", []))
    segment_path = output_dir / f"{speech_job['job_id']}-segment-{segment_index}.{format_name}"
    segment_path.write_bytes(audio_bytes)
    with open(speech_job["output_path"], "ab") as stream:
        stream.write(audio_bytes)

    segment = {
        "index": segment_index,
        "text": segment_text,
        "status": "queued",
        "format": format_name,
        "bytes": len(audio_bytes),
        "output_path": str(segment_path),
    }
    speech_job.setdefault("segments", []).append(segment_text)
    speech_job.setdefault("segment_statuses", []).append(segment)
    speech_job["segment_count"] = len(speech_job["segment_statuses"])
    speech_job["format"] = format_name
    speech_job["bytes"] = Path(speech_job["output_path"]).stat().st_size
    speech_job["tts_input_chars_total"] = int(speech_job.get("tts_input_chars_total", 0)) + _count_content_chars(
        segment_text
    )
    speech_job["tts_input_chunk_count"] = int(speech_job.get("tts_input_chunk_count", 0)) + 1
    return segment


def _strip_lookup_marker_from_speech_job(speech_job: dict, spoken_text: str) -> None:
    if not speech_job:
        return
    speech_job["text"] = spoken_text
    segments = speech_job.get("segments", [])
    if len(segments) <= 1:
        speech_job["segments"] = [spoken_text for _ in segments]
        for segment in speech_job.get("segment_statuses", []):
            segment["text"] = spoken_text
        return
    cleaned_segments = []
    for segment_text in segments:
        cleaned_segment, _ = _split_preview_lookup_marker(segment_text)
        cleaned_segments.append(cleaned_segment)
    speech_job["segments"] = cleaned_segments
    for segment in speech_job.get("segment_statuses", []):
        cleaned_segment, _ = _split_preview_lookup_marker(segment.get("text"))
        segment["text"] = cleaned_segment


def _strip_formal_lookup_marker_from_result(result: dict, *, dialog_events: list[dict] | None = None) -> bool:
    reply = result.get("reply") or {}
    raw_formal_text = str(result.get("raw_formal_text") or reply.get("content") or "")
    spoken_formal_text, lookup_marker = _split_preview_lookup_marker(raw_formal_text)
    if not lookup_marker:
        if not _should_repair_missing_lookup_marker(dialog_events, raw_formal_text):
            return bool(result.get("lookup_marker"))
        lookup_marker = True
        spoken_formal_text = raw_formal_text.strip()
        raw_formal_text = f"{spoken_formal_text}<lookup>"

    result["lookup_marker"] = True
    result["raw_formal_text"] = raw_formal_text
    cleaned_reply = {**dict(reply), "content": spoken_formal_text, "lead": spoken_formal_text, "tail": ""}
    result["reply"] = cleaned_reply

    assistant_event = result.get("assistant_event")
    if isinstance(assistant_event, dict):
        assistant_event["content"] = spoken_formal_text
    speech_job = result.get("speech_job") or {}
    _strip_lookup_marker_from_speech_job(speech_job, spoken_formal_text)
    if isinstance(assistant_event, dict) and isinstance(assistant_event.get("speech_job"), dict):
        _strip_lookup_marker_from_speech_job(assistant_event["speech_job"], spoken_formal_text)
    return True


def _looks_like_meaningful_utterance(text: str) -> bool:
    normalized = re.sub(r"[\s\.,!?;:，。！？、；：“”\"'`~\-—\(\)\[\]{}<>《》…]+", "", text).strip()
    if not normalized:
        return False
    if normalized in _BARGE_IN_NOISE_TOKENS:
        return False

    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    latin_digit_count = len(re.findall(r"[A-Za-z0-9]", normalized))
    if cjk_count >= 2:
        return True
    if latin_digit_count >= 4:
        return True
    return len(normalized) >= 3


def _normalize_barge_in_text(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?;:]+", "", text).lower()


def _contains_explicit_barge_in_trigger(text: str, *, assistant_name: str) -> bool:
    normalized = _normalize_barge_in_text(text)
    if not normalized:
        return False
    assistant = _normalize_barge_in_text(assistant_name)
    if assistant and assistant in normalized:
        return True
    return any(trigger in normalized for trigger in _BARGE_IN_TRIGGER_PHRASES)


def _append_turn_decision_event(
    table_id: str,
    *,
    transcript: str,
    automatic: bool,
    interrupt: bool,
    mode: str,
    reason: str,
) -> None:
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_turn_decision",
            "source": "turn_decision",
            "content": transcript,
            "automatic": automatic,
            "interrupt": interrupt,
            "mode": mode,
            "reason": reason,
            },
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_timed_runtime_event(
    table_id: str,
    *,
    kind: str,
    started_monotonic: float,
    **fields,
) -> None:
    elapsed_ms = int(round((time.perf_counter() - started_monotonic) * 1000))
    event = {
        "kind": kind,
        "source": "runtime",
        "at": _utc_now_iso(),
        "elapsed_ms": elapsed_ms,
    }
    event.update(fields)
    session_manager.append_runtime_event(table_id, event)


def _handle_transcript_barge_in(table_id: str, event: dict) -> bool:
    if event.get("event") != "transcript":
        return False
    text = str(event.get("text", "")).strip()
    if not text:
        return False
    runtime = dialog_runtime_store.snapshot(table_id)
    if runtime["state"] != "agent_speaking":
        return False
    if runtime.get("barge_in_protected"):
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_barge_in_ignored",
                "source": "runtime",
                "content": f"priority reply grace window ignored transcript: {text}",
                "job_id": runtime.get("current_job_id"),
            },
        )
        return False
    if not _contains_explicit_barge_in_trigger(
        text,
        assistant_name=session_manager.get_assistant_name(table_id),
    ):
        return False

    interrupted_job = _interrupt_active_runtime_job(table_id, runtime)
    if interrupted_job is None:
        return False

    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_interrupted",
            "source": "runtime",
            "content": f"user barge-in detected from transcript: {text}",
        },
    )
    return True


def _build_incremental_auto_interrupt_response(
    table_id: str,
    *,
    plan: dict,
    dialog_events: list[dict],
    latest_transcript: str,
    preview_handoff_reply_text: str | None,
    preview_handoff_source_text: str | None,
    preview_stream_id: str | None = None,
    preview_job_id: str | None = None,
    pending_formal_text: str | None = None,
    pending_formal_source_text: str | None = None,
    pending_formal_preview_text: str | None = None,
    pending_formal_preview_job_id: str | None = None,
) -> dict:
    dialog_client = getattr(getattr(auto_interrupt_service, "orchestrator", None), "dialog_client", None)
    tts_adapter = getattr(auto_interrupt_service, "tts_adapter", None)
    if dialog_client is None or tts_adapter is None:
        raise RuntimeError("incremental auto interrupt requires dialog client and tts adapter")
    if not hasattr(tts_adapter, "synthesize_segment"):
        raise RuntimeError("tts adapter does not support incremental segment synthesis")
    assistant_name = session_manager.get_assistant_name(table_id)
    assistant_personality = session_manager.get_assistant_personality(table_id)

    turn_id = uuid4().hex
    reply_id = uuid4().hex
    speech_job = _build_progressive_speech_job(
        text="",
        turn_id=turn_id,
        reply_id=reply_id,
        output_dir=getattr(tts_adapter, "output_dir", ".runtime/tts"),
    )
    tts_stream = tts_stream_bridge.open_stream(
        job_id=speech_job["job_id"],
        turn_id=turn_id,
        reply_id=reply_id,
        segment_count=0,
    )
    assistant_event = {
        "kind": "assistant_reply",
        "source": "companion",
        "mode": plan["mode"],
        "content": "",
        "speech_job": speech_job,
        "turn_id": turn_id,
        "reply_id": reply_id,
    }

    first_ready: queue.Queue[tuple[str, str | Exception]] = queue.Queue(maxsize=1)
    formal_started_monotonic = time.perf_counter()

    def strip_assistant_prefix(text: str) -> str:
        return session_manager.strip_assistant_prefix(table_id, text)

    _append_timed_runtime_event(
        table_id,
        kind="assistant_formal_generation_started",
        started_monotonic=formal_started_monotonic,
        transcript=latest_transcript,
        job_id=speech_job["job_id"],
        turn_id=turn_id,
        reply_id=reply_id,
        preview_handoff=bool(preview_handoff_reply_text),
        preview_reply_text=preview_handoff_reply_text or "",
        preview_source_text=preview_handoff_source_text or "",
    )

    def emit_first_ready(content_text: str) -> None:
        if not first_ready.empty():
            return
        first_ready.put(("content", content_text))

    def emit_error(exc: Exception) -> None:
        if not first_ready.empty():
            return
        first_ready.put(("error", exc))

    def worker() -> None:
        emitted_segments: list[str] = []
        deferred_segments: list[str] = []
        latest_content = ""
        pending_remainder = ""
        audible_prefix_for_diff = ""
        committed_prefix_for_state = ""
        first_reply_update_logged = False
        first_tts_started_logged = False
        first_tts_completed_logged = False
        nonlocal pending_formal_text
        stream_lookup_marker_seen = False
        stream_lookup_raw_text = ""

        def stream_cancelled() -> bool:
            try:
                return tts_stream_bridge.snapshot(tts_stream["stream_id"]).get("state") == "cancelled"
            except Exception:
                return True

        def reply_interrupted() -> bool:
            runtime = dialog_runtime_store.snapshot(table_id)
            return (
                runtime.get("state") == "interrupted"
                and runtime.get("current_job_id") == speech_job["job_id"]
            )

        def current_not_started_chunk_count() -> int:
            runtime = dialog_runtime_store.snapshot(table_id)
            started_segment_count = 0
            if runtime.get("current_job_id") == speech_job["job_id"]:
                started_segment_count = int(runtime.get("started_segment_count") or 0)
            return max(0, len(emitted_segments) - started_segment_count)

        def wait_for_tts_window() -> bool:
            while True:
                if stream_cancelled():
                    return False
                if reply_interrupted():
                    return False
                if len(emitted_segments) < 2:
                    return True
                if current_not_started_chunk_count() < 1:
                    return True
                time.sleep(0.02)

        def synthesize_and_append_segment(segment_text: str) -> bool:
            nonlocal audible_prefix_for_diff
            nonlocal committed_prefix_for_state
            nonlocal first_tts_started_logged
            nonlocal first_tts_completed_logged
            nonlocal stream_lookup_marker_seen
            nonlocal stream_lookup_raw_text
            raw_segment_text = segment_text
            segment_text = strip_assistant_prefix(segment_text)
            segment_text, segment_lookup_marker = _split_preview_lookup_marker(segment_text)
            if segment_lookup_marker:
                stream_lookup_marker_seen = True
                stream_lookup_raw_text = latest_content or raw_segment_text
            if not segment_text:
                return True
            if not wait_for_tts_window():
                return False
            if not first_tts_started_logged:
                _append_timed_runtime_event(
                    table_id,
                    kind="assistant_formal_first_tts_started",
                    started_monotonic=formal_started_monotonic,
                    transcript=latest_transcript,
                    job_id=speech_job["job_id"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    segment_index=len(emitted_segments),
                    segment_text=segment_text,
                )
                first_tts_started_logged = True
            synth = tts_adapter.synthesize_segment(
                segment_text,
                voice_id=session_manager.get_assistant_voice_id(table_id),
            )
            if stream_cancelled() or reply_interrupted():
                return False
            audio_bytes = synth["audio_bytes"]
            format_name = synth.get("format", "mp3")
            _append_progressive_segment_to_job(
                speech_job,
                segment_text=segment_text,
                audio_bytes=audio_bytes,
                format_name=format_name,
            )
            emitted_segments.append(segment_text)
            assistant_event["content"] = _display_formal_content(
                emitted_segments,
                committed_prefix_for_state,
            )
            speech_job["text"] = assistant_event["content"]
            tts_stream_bridge.append_chunk(
                tts_stream["stream_id"],
                segment_index=len(emitted_segments) - 1,
                text=segment_text,
                audio_bytes=audio_bytes,
            )
            if not first_tts_completed_logged:
                _append_timed_runtime_event(
                    table_id,
                    kind="assistant_formal_first_tts_completed",
                    started_monotonic=formal_started_monotonic,
                    transcript=latest_transcript,
                    job_id=speech_job["job_id"],
                    turn_id=turn_id,
                    reply_id=reply_id,
                    segment_index=len(emitted_segments) - 1,
                    segment_text=segment_text,
                    bytes=len(audio_bytes),
                )
                first_tts_completed_logged = True
            emit_first_ready(assistant_event["content"])
            return True

        def queue_segments_for_synthesis(segment_texts: list[str]) -> bool:
            if segment_texts:
                deferred_segments.extend(segment_texts)
            while deferred_segments:
                if not synthesize_and_append_segment(deferred_segments[0]):
                    deferred_segments.clear()
                    return False
                deferred_segments.pop(0)
            return True

        try:
            pending_formal_usable = (
                pending_formal_text
                and preview_handoff_reply_text
                and hasattr(dialog_client, "stream_continuation_text")
                and _pending_formal_matches_handoff(
                    pending_source_text=pending_formal_source_text,
                    pending_preview_text=pending_formal_preview_text,
                    pending_preview_job_id=pending_formal_preview_job_id,
                    latest_transcript=latest_transcript,
                    preview_handoff_reply_text=preview_handoff_reply_text,
                    preview_job_id=preview_job_id,
                )
            )
            if pending_formal_text and not pending_formal_usable:
                dialog_runtime_store.clear_pending_formal_text(table_id)
                pending_formal_text = None
            if pending_formal_usable:
                dialog_runtime_store.clear_pending_formal_text(table_id)
                accumulated = strip_assistant_prefix(pending_formal_text or "")
                if accumulated:
                    incremental_text = _extract_incremental_tts_text(
                        accumulated,
                        audible_prefix_for_diff,
                    )
                    if incremental_text:
                        complete_segments, _ = _split_complete_tts_segments(incremental_text)
                        if complete_segments:
                            queue_segments_for_synthesis(complete_segments)
                            latest_content = accumulated
                            audible_prefix_for_diff = _derive_spoken_prefix_for_diff(
                                accumulated,
                                "",
                            )
                            first_reply_update_logged = True
                            emit_first_ready(accumulated)
                if accumulated:
                    assistant_event["content"] = _display_formal_content(
                        emitted_segments,
                        committed_prefix_for_state,
                    )
                    speech_job["text"] = accumulated

            elif preview_handoff_reply_text and hasattr(dialog_client, "stream_continuation_text"):
                try:
                    text_iter = dialog_client.stream_continuation_text(
                        mode=plan["mode"],
                        transcript=plan["transcript"],
                        events=dialog_events,
                        already_spoken_text=preview_handoff_reply_text,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    )
                except TypeError as exc:
                    if "assistant_name" not in str(exc) and "assistant_personality" not in str(exc):
                        raise
                    text_iter = dialog_client.stream_continuation_text(
                        mode=plan["mode"],
                        transcript=plan["transcript"],
                        events=dialog_events,
                        already_spoken_text=preview_handoff_reply_text,
                    )
                for text in text_iter:
                    trimmed_content = strip_assistant_prefix(" ".join(str(text or "").split()).strip())
                    if not trimmed_content:
                        continue
                    latest_content = trimmed_content
                    if not first_reply_update_logged:
                        logged_content, _ = _split_preview_lookup_marker(trimmed_content)
                        _append_timed_runtime_event(
                            table_id,
                            kind="assistant_formal_first_reply_update",
                            started_monotonic=formal_started_monotonic,
                            transcript=latest_transcript,
                            job_id=speech_job["job_id"],
                            turn_id=turn_id,
                            reply_id=reply_id,
                            content=logged_content,
                        )
                        first_reply_update_logged = True
                    incremental_text = _extract_incremental_tts_text(
                        trimmed_content,
                        audible_prefix_for_diff,
                    )
                    if not incremental_text:
                        continue
                    complete_segments, pending_remainder = _split_complete_tts_segments(incremental_text)
                    if not emitted_segments and not complete_segments:
                        segment_text = _derive_first_provisional_chunk(
                            incremental_text,
                            min_content_chars=8,
                        )
                        if not segment_text:
                            continue
                        pending_remainder = ""
                        committed_prefix_for_state = _derive_committed_prefix_for_state(
                            segment_text,
                            min_content_chars=12 if not committed_prefix_for_state else 0,
                        )
                        if not queue_segments_for_synthesis([segment_text]):
                            return
                        audible_prefix_for_diff = segment_text
                        continue
                    audible_prefix_for_diff = _derive_spoken_prefix_for_diff(
                        trimmed_content,
                        pending_remainder,
                    )
                    committed_prefix_for_state = _derive_committed_prefix_for_state(
                        trimmed_content,
                        min_content_chars=12 if not committed_prefix_for_state else 0,
                    )
                    if not queue_segments_for_synthesis(complete_segments):
                        return
            else:
                try:
                    text_iter = dialog_client.stream_reply_text(
                        mode=plan["mode"],
                        transcript=plan["transcript"],
                        events=dialog_events,
                        already_spoken_text=preview_handoff_reply_text,
                        continue_only=bool(preview_handoff_reply_text),
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    )
                except TypeError as exc:
                    if (
                        "already_spoken_text" not in str(exc)
                        and "continue_only" not in str(exc)
                        and "assistant_name" not in str(exc)
                        and "assistant_personality" not in str(exc)
                    ):
                        raise
                    if "assistant_name" in str(exc) or "assistant_personality" in str(exc):
                        text_iter = dialog_client.stream_reply_text(
                            mode=plan["mode"],
                            transcript=plan["transcript"],
                            events=dialog_events,
                            already_spoken_text=preview_handoff_reply_text,
                            continue_only=bool(preview_handoff_reply_text),
                        )
                    else:
                        text_iter = dialog_client.stream_reply_text(
                            mode=plan["mode"],
                            transcript=plan["transcript"],
                            events=dialog_events,
                        )

                for text in text_iter:
                    trimmed_content = _extract_formal_content_after_preview(
                        {"content": text},
                        preview_handoff_reply_text,
                    )
                    trimmed_content = strip_assistant_prefix(trimmed_content)
                    if not trimmed_content:
                        continue
                    latest_content = trimmed_content
                    if not first_reply_update_logged:
                        logged_content, _ = _split_preview_lookup_marker(trimmed_content)
                        _append_timed_runtime_event(
                            table_id,
                            kind="assistant_formal_first_reply_update",
                            started_monotonic=formal_started_monotonic,
                            transcript=latest_transcript,
                            job_id=speech_job["job_id"],
                            turn_id=turn_id,
                            reply_id=reply_id,
                            content=logged_content,
                        )
                        first_reply_update_logged = True
                    incremental_text = _extract_incremental_tts_text(
                        trimmed_content,
                        audible_prefix_for_diff,
                    )
                    if not incremental_text:
                        continue
                    complete_segments, pending_remainder = _split_complete_tts_segments(incremental_text)
                    if not emitted_segments and not complete_segments:
                        segment_text = _derive_first_provisional_chunk(
                            incremental_text,
                            min_content_chars=8,
                        )
                        if not segment_text:
                            continue
                        pending_remainder = ""
                        committed_prefix_for_state = _derive_committed_prefix_for_state(
                            segment_text,
                            min_content_chars=12 if not committed_prefix_for_state else 0,
                        )
                        if not queue_segments_for_synthesis([segment_text]):
                            return
                        audible_prefix_for_diff = segment_text
                        continue
                    audible_prefix_for_diff = _derive_spoken_prefix_for_diff(
                        trimmed_content,
                        pending_remainder,
                    )
                    committed_prefix_for_state = _derive_committed_prefix_for_state(
                        trimmed_content,
                        min_content_chars=12 if not committed_prefix_for_state else 0,
                    )
                    if not queue_segments_for_synthesis(complete_segments):
                        return

            if pending_remainder:
                deferred_segments.append(pending_remainder)
                audible_prefix_for_diff = " ".join(str(latest_content or "").split()).strip()
                committed_prefix_for_state = audible_prefix_for_diff
                if not queue_segments_for_synthesis([]):
                    return

            if not emitted_segments and latest_content:
                if not queue_segments_for_synthesis([latest_content]):
                    return
                audible_prefix_for_diff = " ".join(str(latest_content or "").split()).strip()
                committed_prefix_for_state = audible_prefix_for_diff

            if not emitted_segments:
                raise RuntimeError("incremental formal reply produced no content")
            raw_final_content = " ".join(str(latest_content or assistant_event["content"] or "").split()).strip()
            cleaned_final_content, final_lookup_marker = _split_preview_lookup_marker(raw_final_content)
            final_lookup_marker = final_lookup_marker or stream_lookup_marker_seen
            if not final_lookup_marker and _should_repair_missing_lookup_marker(dialog_events, raw_final_content):
                final_lookup_marker = True
                cleaned_final_content = raw_final_content.strip()
                raw_final_content = f"{cleaned_final_content}<lookup>"
            if final_lookup_marker:
                if not raw_final_content and stream_lookup_raw_text:
                    raw_final_content = stream_lookup_raw_text
                    cleaned_final_content, _ = _split_preview_lookup_marker(raw_final_content)
                assistant_event["content"] = cleaned_final_content
                speech_job["text"] = cleaned_final_content
                _strip_lookup_marker_from_speech_job(speech_job, cleaned_final_content)
            speech_job["status"] = "ready"
            _append_timed_runtime_event(
                table_id,
                kind="assistant_formal_generation_finished",
                started_monotonic=formal_started_monotonic,
                transcript=latest_transcript,
                job_id=speech_job["job_id"],
                turn_id=turn_id,
                reply_id=reply_id,
                segment_count=len(emitted_segments),
                content=assistant_event["content"],
                tts_input_chars_total=speech_job.get("tts_input_chars_total", 0),
                tts_input_chunk_count=speech_job.get("tts_input_chunk_count", 0),
            )
            if final_lookup_marker:
                _spawn_skillagent_for_lookup_commitment(
                    table_id=table_id,
                    result={
                        "reply_id": reply_id,
                        "turn_id": turn_id,
                        "reply": {"content": cleaned_final_content},
                        "raw_formal_text": raw_final_content,
                        "lookup_marker": True,
                        "preview_handoff_reply_text": preview_handoff_reply_text or "",
                    },
                    dialog_events=dialog_events,
                )
        except Exception as exc:  # pragma: no cover - exercised via tests/real failures
            speech_job["status"] = "failed"
            emit_error(exc)
            session_manager.append_runtime_event(
                table_id,
                {
                    "kind": "assistant_auto_reply_failed",
                    "source": "runtime",
                    "content": str(exc),
                    "transcript": latest_transcript,
                },
            )
        finally:
            tts_stream_bridge.finish_stream(tts_stream["stream_id"])

    threading.Thread(target=worker, daemon=True).start()
    state, payload = first_ready.get(timeout=30.0)
    if state == "error":
        raise payload  # type: ignore[misc]

    content = str(payload)
    speech_job["status"] = "ready"
    return {
        "interrupt": True,
        "mode": plan["mode"],
        "decision_reason": plan.get("decision_reason", "interrupt"),
        "reply": {
            "source": "companion",
            "lead": "",
            "tail": "",
            "content": content,
        },
        "speech_job": speech_job,
        "assistant_event": assistant_event,
        "turn_id": turn_id,
        "reply_id": reply_id,
        "tts_stream": tts_stream,
        "preview_handoff_reply_text": preview_handoff_reply_text,
        "preview_handoff_source_text": preview_handoff_source_text,
        "analysis_needed": plan.get("analysis_needed", False),
        "analysis_query": plan.get("analysis_query"),
    }
    return True


def _run_auto_interrupt_for_table(
    table_id: str,
    *,
    automatic: bool = False,
    dialog_events_override: list[dict] | None = None,
) -> dict:
    dialog_events = _dialog_events_with_speaker_alias_map(table_id, dialog_events_override)
    assistant_name = session_manager.get_assistant_name(table_id)
    assistant_personality = session_manager.get_assistant_personality(table_id)
    latest_transcript = _latest_dialog_transcript(dialog_events)
    preview_handoff_reply_text: str | None = None
    preview_handoff_source_text: str | None = None
    if automatic:
        runtime = dialog_runtime_store.snapshot(table_id)
        busy_with_formal_reply = runtime["state"] in {"assistant_ready", "agent_speaking"} and not runtime.get(
            "preview_reply_text"
        )
        if busy_with_formal_reply and not _is_explicit_auto_interrupt_request(
            latest_transcript,
            events=dialog_events,
            assistant_name=assistant_name,
        ):
            _append_turn_decision_event(
                table_id,
                transcript=latest_transcript,
                automatic=True,
                interrupt=False,
                mode=CONVERSATION_MODE,
                reason="busy_agent",
            )
            session_manager.append_runtime_event(
                table_id,
                {
                    "kind": "assistant_auto_reply_blocked",
                    "source": "policy",
                    "content": "assistant is already speaking",
                    "reason": "busy_agent",
                },
            )
            return {
                "interrupt": False,
                "mode": CONVERSATION_MODE,
                "decision_reason": "busy_agent",
                "reply": {"source": "policy", "content": ""},
                "speech_job": None,
                "assistant_event": None,
                "reason": "busy_agent",
            }
        if not hasattr(auto_interrupt_service, "plan") or not hasattr(auto_interrupt_service, "build_response"):
            try:
                result = auto_interrupt_service.run_once(
                    dialog_events,
                    assistant_name=assistant_name,
                    assistant_personality=assistant_personality,
                    assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
                )
            except TypeError as exc:
                if "assistant_personality" not in str(exc):
                    raise
                result = auto_interrupt_service.run_once(
                    dialog_events,
                    assistant_name=assistant_name,
                    assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
                )
        else:
            preview_handoff_reply_text, preview_handoff_source_text = _resolve_preview_handoff_context(
                dialog_events,
                runtime,
                current_source_text=latest_transcript,
            )
            progressive_plan = None
            if hasattr(auto_interrupt_service, "plan_progressive"):
                try:
                    progressive_plan = auto_interrupt_service.plan_progressive(
                        dialog_events,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    )
                except TypeError as exc:
                    if "assistant_personality" not in str(exc):
                        raise
                    progressive_plan = auto_interrupt_service.plan_progressive(
                        dialog_events,
                        assistant_name=assistant_name,
                    )
            if progressive_plan and progressive_plan.get("deferred_generation") and not _lookup_runtime_is_busy(table_id):
                gate = interrupt_policy.should_allow(
                    table_id,
                    latest_transcript,
                    mode=progressive_plan["mode"],
                    decision_reason=progressive_plan.get("decision_reason", "interrupt"),
                )
                if not gate["allowed"]:
                    _append_turn_decision_event(
                        table_id,
                        transcript=latest_transcript,
                        automatic=True,
                        interrupt=False,
                        mode=progressive_plan["mode"],
                        reason=gate["reason"],
                    )
                    session_manager.append_runtime_event(
                        table_id,
                        {
                            "kind": "assistant_auto_reply_blocked",
                            "source": "policy",
                            "content": f"assistant auto reply blocked: {gate['reason']}",
                            "reason": gate["reason"],
                        },
                    )
                    return {
                        "interrupt": False,
                        "mode": progressive_plan["mode"],
                        "decision_reason": progressive_plan.get("decision_reason", "interrupt"),
                        "reply": {"source": "policy", "content": ""},
                        "speech_job": None,
                        "assistant_event": None,
                        "reason": gate["reason"],
                    }
                result = _build_incremental_auto_interrupt_response(
                    table_id,
                    plan=progressive_plan,
                    dialog_events=dialog_events,
                    latest_transcript=latest_transcript,
                    preview_handoff_reply_text=preview_handoff_reply_text,
                    preview_handoff_source_text=preview_handoff_source_text,
                    preview_stream_id=runtime.get("preview_stream_id"),
                    preview_job_id=runtime.get("preview_job_id"),
                    pending_formal_text=runtime.get("pending_formal_text"),
                    pending_formal_source_text=runtime.get("pending_formal_source_text"),
                    pending_formal_preview_text=runtime.get("pending_formal_preview_text"),
                    pending_formal_preview_job_id=runtime.get("pending_formal_preview_job_id"),
                )
            else:
                try:
                    plan = auto_interrupt_service.plan(
                        dialog_events,
                        assistant_name=assistant_name,
                        assistant_personality=assistant_personality,
                    )
                except TypeError as exc:
                    if "assistant_personality" not in str(exc):
                        raise
                    plan = auto_interrupt_service.plan(
                        dialog_events,
                        assistant_name=assistant_name,
                    )
                if not plan["should_interrupt"]:
                    return auto_interrupt_service.build_response(
                        plan,
                        assistant_name=assistant_name,
                        assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
                    )
                if preview_handoff_reply_text and plan.get("reply"):
                    plan["reply"] = _trim_formal_reply_after_preview(
                        plan["reply"],
                        preview_handoff_reply_text,
                    )
                if _queue_lookup_if_table_busy_from_formal(
                    table_id=table_id,
                    formal_text=(plan.get("reply") or {}).get("content", ""),
                    dialog_events=dialog_events,
                    reply_id=f"lookup_busy:{uuid4().hex}",
                ):
                    plan["reply"] = {
                        **(plan.get("reply") or {}),
                        "content": LOOKUP_BUSY_REPLY_TEXT,
                        "lead": LOOKUP_BUSY_REPLY_TEXT,
                        "tail": "",
                    }
                gate = interrupt_policy.should_allow(
                    table_id,
                    latest_transcript,
                    mode=plan["mode"],
                    decision_reason=plan.get("decision_reason", "interrupt"),
                )
                if not gate["allowed"]:
                    _append_turn_decision_event(
                        table_id,
                        transcript=latest_transcript,
                        automatic=True,
                        interrupt=False,
                        mode=plan["mode"],
                        reason=gate["reason"],
                    )
                    session_manager.append_runtime_event(
                        table_id,
                        {
                            "kind": "assistant_auto_reply_blocked",
                            "source": "policy",
                            "content": f"assistant auto reply blocked: {gate['reason']}",
                            "reason": gate["reason"],
                        },
                    )
                    return {
                        "interrupt": False,
                        "mode": plan["mode"],
                        "decision_reason": plan.get("decision_reason", "interrupt"),
                        "reply": {"source": "policy", "content": ""},
                        "speech_job": None,
                        "assistant_event": None,
                        "reason": gate["reason"],
                    }
                result = auto_interrupt_service.build_response(
                    plan,
                    assistant_name=assistant_name,
                    assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
                )
    else:
        try:
            result = auto_interrupt_service.run_once(
                dialog_events,
                assistant_name=assistant_name,
                assistant_personality=assistant_personality,
                assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
            )
        except TypeError as exc:
            if "assistant_personality" not in str(exc):
                raise
            result = auto_interrupt_service.run_once(
                dialog_events,
                assistant_name=assistant_name,
                assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
            )
    _append_turn_decision_event(
        table_id,
        transcript=latest_transcript or result.get("transcript", ""),
        automatic=automatic,
        interrupt=result["interrupt"],
        mode=result["mode"],
        reason=result.get("decision_reason", result.get("reason", "model")),
    )
    if result["assistant_event"] is not None:
        formal_lookup_marker = _strip_formal_lookup_marker_from_result(result, dialog_events=dialog_events)
        runtime_before_ready = dialog_runtime_store.snapshot(table_id)
        resolved_preview_reply_text, resolved_preview_source_text = _resolve_preview_handoff_context(
            dialog_events,
            runtime_before_ready,
            current_source_text=latest_transcript,
        )
        resolved_preview_reply_text = preview_handoff_reply_text or resolved_preview_reply_text
        resolved_preview_source_text = preview_handoff_source_text or resolved_preview_source_text
        result["preview_handoff"] = bool(resolved_preview_reply_text)
        result["preview_reply_text"] = resolved_preview_reply_text
        result["preview_source_text"] = resolved_preview_source_text
        speech_job = result["speech_job"] or {}
        turn_id = result.get("turn_id") or speech_job.get("turn_id") or uuid4().hex
        reply_id = result.get("reply_id") or speech_job.get("reply_id") or uuid4().hex
        speech_job["turn_id"] = turn_id
        speech_job["reply_id"] = reply_id
        result["turn_id"] = turn_id
        result["reply_id"] = reply_id
        result["assistant_event"]["turn_id"] = turn_id
        result["assistant_event"]["reply_id"] = reply_id
        tts_stream = result.get("tts_stream")
        hook_runs_after_stream_completion = tts_stream is not None
        if tts_stream is None and speech_job.get("accepted"):
            tts_stream = _prepare_tts_stream_for_job(
                table_id,
                speech_job,
                adapter=getattr(auto_interrupt_service, "tts_adapter", None),
            )
        session_manager.append_assistant_reply(table_id, result["assistant_event"])
        dialog_runtime_store.on_agent_reply_ready(
            table_id,
            job_id=speech_job.get("job_id"),
            reply_text=result["reply"].get("content", ""),
            source_text=latest_transcript,
            segment_count=speech_job.get("segment_count", 0),
        )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_ready",
                "source": "runtime",
                "content": result["reply"].get("content", ""),
                "lead": result["reply"].get("lead", ""),
                "tail": result["reply"].get("tail", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "source_transcript": latest_transcript,
                "preview_handoff": result["preview_handoff"],
                "preview_reply_text": result["preview_reply_text"],
                "preview_source_text": result["preview_source_text"],
            },
        )
        if tts_stream is not None:
            session_manager.append_runtime_event(
                table_id,
                {
                    "kind": "assistant_stream_ready",
                    "source": "runtime",
                    "content": result["reply"].get("content", ""),
                    "lead": result["reply"].get("lead", ""),
                    "tail": result["reply"].get("tail", ""),
                    "job_id": speech_job.get("job_id"),
                    "turn_id": turn_id,
                    "reply_id": reply_id,
                    "stream_id": tts_stream.get("stream_id"),
                    "source_transcript": latest_transcript,
                    "preview_handoff": result["preview_handoff"],
                },
            )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_segments_planned",
                "source": "runtime",
                "content": result["reply"].get("content", ""),
                "lead": result["reply"].get("lead", ""),
                "tail": result["reply"].get("tail", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "segment_count": speech_job.get("segment_count", 0),
                "segment_statuses": speech_job.get("segment_statuses", []),
            },
        )
        if result["speech_job"] and result["speech_job"].get("accepted"):
            if automatic and latest_transcript:
                interrupt_policy.record_trigger(
                    table_id,
                    latest_transcript,
                    mode=result["mode"],
                    decision_reason=result.get("decision_reason", "interrupt"),
                )
        if formal_lookup_marker:
            _spawn_skillagent_for_lookup_commitment(
                table_id=table_id,
                result=result,
                dialog_events=dialog_events,
            )
        result["tts_stream"] = tts_stream
    return result


def _run_auto_interrupt_for_table_safely(
    table_id: str,
    *,
    automatic: bool = False,
    dialog_events_override: list[dict] | None = None,
) -> dict:
    def _reset_runtime_if_automatic_no_reply(result: dict) -> dict:
        if automatic and not result.get("interrupt") and result.get("assistant_event") is None:
            runtime = dialog_runtime_store.snapshot(table_id)
            if (
                runtime.get("state") == "agent_thinking"
                or runtime.get("preview_reply_text")
                or runtime.get("pending_formal_text")
            ):
                dialog_runtime_store.on_agent_reply_skipped(table_id)
        return result

    try:
        return _reset_runtime_if_automatic_no_reply(
            _run_auto_interrupt_for_table(
                table_id,
                automatic=automatic,
                dialog_events_override=dialog_events_override,
            )
        )
    except Exception as exc:
        logger.exception(
            "auto interrupt failed table=%s automatic=%s error=%s",
            table_id,
            automatic,
            exc,
        )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_auto_reply_failed",
                "source": "runtime",
                "content": str(exc),
                "automatic": automatic,
            },
        )
        return _reset_runtime_if_automatic_no_reply({
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": "auto_interrupt_error",
            "reply": {"source": "runtime", "content": ""},
            "speech_job": None,
            "assistant_event": None,
            "reason": "auto_interrupt_error",
        })


def _run_live_heartbeat_for_table(table_id: str) -> dict:
    dialog_events = _dialog_events_with_speaker_alias_map(table_id)
    assistant_name = session_manager.get_assistant_name(table_id)
    assistant_personality = session_manager.get_assistant_personality(table_id)
    assistant_voice_id = session_manager.get_assistant_voice_id(table_id)
    player_names = reliable_heartbeat_player_names(
        session_manager.list_speaker_alias_map(table_id),
        assistant_name=assistant_name,
    )
    result = auto_interrupt_service.heartbeat(
        dialog_events,
        player_names=player_names,
        assistant_name=assistant_name,
        assistant_personality=assistant_personality,
        assistant_voice_id=assistant_voice_id,
    )
    if result["interrupt"] and result.get("assistant_event"):
        speech_job = result["speech_job"] or {}
        turn_id = result.get("turn_id") or speech_job.get("turn_id") or uuid4().hex
        reply_id = result.get("reply_id") or speech_job.get("reply_id") or uuid4().hex
        speech_job["turn_id"] = turn_id
        speech_job["reply_id"] = reply_id
        result["turn_id"] = turn_id
        result["reply_id"] = reply_id
        result["assistant_event"]["turn_id"] = turn_id
        result["assistant_event"]["reply_id"] = reply_id
        tts_stream = result.get("tts_stream")
        if tts_stream is None and speech_job.get("accepted"):
            tts_stream = _prepare_tts_stream_for_job(
                table_id,
                speech_job,
                adapter=getattr(auto_interrupt_service, "tts_adapter", None),
            )
        session_manager.append_assistant_reply(table_id, result["assistant_event"])
        dialog_runtime_store.on_priority_agent_reply_ready(
            table_id,
            job_id=speech_job.get("job_id"),
            reply_text=result["reply"].get("content", ""),
            source_text="heartbeat",
            segment_count=speech_job.get("segment_count", 0),
            barge_in_grace_seconds=2.0,
        )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_ready",
                "source": "heartbeat",
                "content": result["reply"].get("content", ""),
                "lead": result["reply"].get("lead", ""),
                "tail": result["reply"].get("tail", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "decision_reason": "heartbeat",
            },
        )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_priority_reply_ready",
                "source": "heartbeat",
                "content": result["reply"].get("content", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "decision_reason": "heartbeat",
                "barge_in_grace_seconds": 2.0,
            },
        )
        if tts_stream is not None:
            session_manager.append_runtime_event(
                table_id,
                {
                    "kind": "assistant_stream_ready",
                    "source": "heartbeat",
                    "content": result["reply"].get("content", ""),
                    "lead": result["reply"].get("lead", ""),
                    "tail": result["reply"].get("tail", ""),
                    "job_id": speech_job.get("job_id"),
                    "turn_id": turn_id,
                    "reply_id": reply_id,
                    "stream_id": tts_stream.get("stream_id"),
                    "decision_reason": "heartbeat",
                },
            )
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_segments_planned",
                "source": "heartbeat",
                "content": result["reply"].get("content", ""),
                "lead": result["reply"].get("lead", ""),
                "tail": result["reply"].get("tail", ""),
                "job_id": speech_job.get("job_id"),
                "turn_id": turn_id,
                "reply_id": reply_id,
                "segment_count": speech_job.get("segment_count", 0),
                "segment_statuses": speech_job.get("segment_statuses", []),
                "decision_reason": "heartbeat",
            },
        )
        result["tts_stream"] = tts_stream
        result["player_names"] = player_names
    return result


def _run_live_heartbeat_for_table_safely(table_id: str) -> dict:
    try:
        return _run_live_heartbeat_for_table(table_id)
    except Exception as exc:
        logger.exception("live heartbeat failed table=%s error=%s", table_id, exc)
        return {
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": "heartbeat_error",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
        }


def _run_auto_interrupt_preview_for_table(table_id: str, *, transcript: str) -> dict:
    runtime = dialog_runtime_store.snapshot(table_id)
    if runtime["state"] in {"assistant_ready", "agent_speaking"}:
        return {
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": "busy_agent",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }
    if not hasattr(auto_interrupt_service, "preview"):
        return {
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": "preview_unavailable",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }

    dialog_events = _dialog_events_with_speaker_alias_map(table_id)
    dialog_events.append(
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": _format_live_dialog_transcript(table_id, transcript),
        }
    )
    latest_transcript = dialog_events[-1]["content"]
    gate = interrupt_policy.should_allow(
        table_id,
        latest_transcript,
        mode=CONVERSATION_MODE,
        decision_reason="preview",
    )
    if not gate["allowed"]:
        return {
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": gate["reason"],
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }

    try:
        result = auto_interrupt_service.preview(
            dialog_events,
            assistant_name=session_manager.get_assistant_name(table_id),
            assistant_personality=session_manager.get_assistant_personality(table_id),
            assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
        )
    except TypeError as exc:
        if "assistant_personality" not in str(exc):
            raise
        result = auto_interrupt_service.preview(
            dialog_events,
            assistant_name=session_manager.get_assistant_name(table_id),
            assistant_voice_id=session_manager.get_assistant_voice_id(table_id),
        )
    if not result.get("interrupt") or not result.get("reply") or not result["reply"].get("content"):
        return result

    runtime_after_preview = dialog_runtime_store.snapshot(table_id)
    if runtime_after_preview.get("state") not in {"listening", "user_turn"}:
        return {
            "interrupt": False,
            "mode": result.get("mode", CONVERSATION_MODE),
            "decision_reason": "preview_stale_after_turn_advanced",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }

    raw_preview_text = result["reply"]["content"]
    preview_text, marker_from_text = _split_preview_lookup_marker(raw_preview_text)
    raw_model_preview_text = str(result.get("raw_preview_text") or "")
    raw_model_spoken_text, marker_from_raw_model = _split_preview_lookup_marker(raw_model_preview_text)
    preview_lookup_marker = False
    if marker_from_raw_model:
        preview_text = raw_model_spoken_text
    if preview_text != raw_preview_text:
        result["reply"] = {
            **dict(result.get("reply") or {}),
            "content": preview_text,
            "lead": preview_text,
            "tail": "",
        }
    runtime = dialog_runtime_store.snapshot(table_id)
    if (
        runtime.get("preview_source_text") == transcript
        and runtime.get("preview_reply_text") == preview_text
    ):
        return {
            "interrupt": False,
            "mode": result.get("mode", CONVERSATION_MODE),
            "decision_reason": "preview_unchanged",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }

    speech_job = result.get("speech_job") or {}
    _strip_lookup_marker_from_speech_job(speech_job, preview_text)
    turn_id = result.get("turn_id") or speech_job.get("turn_id") or uuid4().hex
    reply_id = result.get("reply_id") or speech_job.get("reply_id") or uuid4().hex
    tts_stream = None
    if speech_job.get("accepted"):
        speech_job["turn_id"] = turn_id
        speech_job["reply_id"] = reply_id
        result["turn_id"] = turn_id
        result["reply_id"] = reply_id
        assistant_event = result.get("assistant_event") or {
            "kind": "assistant_preview",
            "source": "runtime_preview",
            "mode": result.get("mode", CONVERSATION_MODE),
            "content": preview_text,
        }
        assistant_event["turn_id"] = turn_id
        assistant_event["reply_id"] = reply_id
        assistant_event["content"] = preview_text
        assistant_event["speech_job"] = speech_job
        result["assistant_event"] = assistant_event
        session_manager.append_assistant_reply(table_id, assistant_event)
        tts_stream = _prepare_tts_stream_for_job(
            table_id,
            speech_job,
            adapter=getattr(auto_interrupt_service, "tts_adapter", None),
        )
    # Always set tts_stream so the WebSocket event is sent even when accepted=False.
    # Flutter will fall back to startTtsStream(jobId) if streamId is None.
    result["tts_stream"] = tts_stream

    dialog_runtime_store.on_agent_preview_ready(
        table_id,
        reply_text=preview_text,
        source_text=transcript,
        stream_id=tts_stream.get("stream_id") if tts_stream else None,
        job_id=speech_job.get("job_id") if speech_job else None,
        lookup_marker=False,
    )
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_preview_ready",
            "source": "runtime",
            "content": preview_text,
            "lead": result["reply"].get("lead", preview_text),
            "tail": "",
            "transcript": transcript,
            "mode": result.get("mode", CONVERSATION_MODE),
            "job_id": speech_job.get("job_id"),
            "turn_id": turn_id if speech_job else None,
            "reply_id": reply_id if speech_job else None,
            "stream_id": tts_stream.get("stream_id") if tts_stream else None,
        },
    )
    return result


def _start_formal_generation_after_preview_ready(table_id: str, *, transcript: str) -> None:
    if not hasattr(auto_interrupt_service, "plan_progressive"):
        return

    def worker() -> None:
        dialog_events = _dialog_events_with_speaker_alias_map(table_id)
        dialog_events.append(
            {
                "kind": "voice_transcript",
                "source": "live_asr",
                "content": _format_live_dialog_transcript(table_id, transcript),
            }
        )
        _run_auto_interrupt_for_table_safely(
            table_id,
            automatic=True,
            dialog_events_override=dialog_events,
        )

    threading.Thread(target=worker, daemon=True).start()


def _run_auto_interrupt_preview_for_table_safely(table_id: str, *, transcript: str) -> dict:
    try:
        return _run_auto_interrupt_preview_for_table(table_id, transcript=transcript)
    except Exception as exc:
        logger.exception("auto interrupt preview failed table=%s error=%s", table_id, exc)
        session_manager.append_runtime_event(
            table_id,
            {
                "kind": "assistant_preview_failed",
                "source": "runtime",
                "content": str(exc),
                "transcript": transcript,
            },
        )
        return {
            "interrupt": False,
            "mode": CONVERSATION_MODE,
            "decision_reason": "preview_error",
            "reply": None,
            "speech_job": None,
            "assistant_event": None,
            "transcript": transcript,
        }


def _maybe_start_preview_handoff_formal_pregeneration(
    table_id: str,
    *,
    preview_job_id: str,
) -> None:
    runtime = dialog_runtime_store.snapshot(table_id)
    preview_text = " ".join(str(runtime.get("preview_reply_text") or "").split()).strip()
    preview_source_text = " ".join(str(runtime.get("preview_source_text") or "").split()).strip()
    if not preview_text or not preview_source_text:
        return
    if runtime.get("preview_lookup_marker"):
        return
    if runtime.get("preview_job_id") and runtime.get("preview_job_id") != preview_job_id:
        return
    if not hasattr(auto_interrupt_service, "plan_progressive"):
        return

    def worker() -> None:
        try:
            dialog_events = _dialog_events_with_speaker_alias_map(table_id)
            try:
                progressive_plan = auto_interrupt_service.plan_progressive(
                    dialog_events,
                    assistant_name=session_manager.get_assistant_name(table_id),
                    assistant_personality=session_manager.get_assistant_personality(table_id),
                )
            except TypeError as exc:
                if "assistant_personality" not in str(exc):
                    raise
                progressive_plan = auto_interrupt_service.plan_progressive(
                    dialog_events,
                    assistant_name=session_manager.get_assistant_name(table_id),
                )
            if not progressive_plan.get("deferred_generation"):
                return
            dialog_client = getattr(getattr(auto_interrupt_service, "orchestrator", None), "dialog_client", None)
            if not hasattr(dialog_client, "stream_continuation_text"):
                return
            accumulated = ""
            try:
                text_iter = dialog_client.stream_continuation_text(
                    mode=progressive_plan["mode"],
                    transcript=progressive_plan.get("transcript", ""),
                    events=dialog_events,
                    already_spoken_text=preview_text,
                    assistant_name=session_manager.get_assistant_name(table_id),
                    assistant_personality=session_manager.get_assistant_personality(table_id),
                )
            except TypeError as exc:
                if "assistant_name" not in str(exc) and "assistant_personality" not in str(exc):
                    raise
                text_iter = dialog_client.stream_continuation_text(
                    mode=progressive_plan["mode"],
                    transcript=progressive_plan.get("transcript", ""),
                    events=dialog_events,
                    already_spoken_text=preview_text,
                )
            for chunk in text_iter:
                cleaned = " ".join(str(chunk or "").split()).strip()
                if cleaned:
                    accumulated = cleaned
            runtime_after_generation = dialog_runtime_store.snapshot(table_id)
            if not _pending_formal_matches_handoff(
                pending_source_text=preview_source_text,
                pending_preview_text=preview_text,
                pending_preview_job_id=preview_job_id,
                latest_transcript=runtime_after_generation.get("preview_source_text"),
                preview_handoff_reply_text=runtime_after_generation.get("preview_reply_text"),
                preview_job_id=runtime_after_generation.get("preview_job_id"),
            ):
                return
            dialog_runtime_store.set_pending_formal_text(
                table_id,
                accumulated,
                source_text=preview_source_text,
                preview_text=preview_text,
                preview_job_id=preview_job_id,
            )
        except Exception:
            logger.exception("pregenerate formal text failed table=%s", table_id)

    threading.Thread(target=worker, daemon=True).start()


def _on_memory_compaction_completed(record: dict) -> None:
    payload = dict(record.get("payload") or {})
    table_id = record["table_id"]
    checkpoint = int(record.get("checkpoint", payload.get("checkpoint", 0)))
    source_text = _build_memory_compaction_source_text(
        str(payload.get("previous_summary") or ""),
        list(payload.get("active_events") or []),
    )
    archive_store.save_compaction_snapshot(
        table_id,
        {
            "compaction_id": record["compaction_id"],
            "snapshot_name": record.get("snapshot_name", ""),
            "checkpoint": checkpoint,
            "active_events": list(payload.get("active_events") or []),
            "source_text": source_text,
            "created_at": record.get("created_at"),
        },
    )
    summary_event = session_manager.apply_memory_compaction(
        table_id,
        checkpoint=checkpoint,
        summary_text=record.get("summary_text", ""),
        compaction_id=record["compaction_id"],
        metadata=record.get("metadata"),
    )
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "memory_compaction_completed",
            "source": "memory_compactor",
            "content": summary_event.get("content", ""),
            "compaction_id": record["compaction_id"],
            "checkpoint": checkpoint,
        },
    )


def _on_memory_compaction_failed(record: dict) -> None:
    session_manager.append_runtime_event(
        record["table_id"],
        {
            "kind": "memory_compaction_failed",
            "source": "memory_compactor",
            "content": record.get("error", ""),
            "compaction_id": record["compaction_id"],
            "checkpoint": record.get("checkpoint"),
        },
    )


app = FastAPI(title=settings.app_name)


@app.middleware("http")
async def _public_api_token_middleware(request: Request, call_next):
    if request.url.path != "/health" and not _public_api_request_authorized(
        request.headers.get("authorization"),
        request.query_params.get("access_token"),
    ):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


_table_store: TableStore | None = None

def _get_table_store() -> TableStore:
    global _table_store
    if _table_store is None:
        db_path = getattr(settings, "gamevoice_db_path", None)
        if os.getenv("GAMEVOICE_TESTING") or str(db_path or "").strip().lower() in {
            ":memory:",
            "memory",
            "__memory__",
        }:
            _table_store = InMemoryTableStore()
        elif db_path:
            _table_store = SQLiteTableStore(db_path=db_path)
        else:
            _table_store = InMemoryTableStore()
    return _table_store


def _build_personal_development_service() -> PersonalDevelopmentService:
    db_path = getattr(settings, "gamevoice_db_path", None)
    if os.getenv("GAMEVOICE_TESTING") or str(db_path or "").strip().lower() in {
        ":memory:",
        "memory",
        "__memory__",
    }:
        store = InMemoryPersonalDevelopmentStore()
    else:
        store = SQLitePersonalDevelopmentStore(db_path or ".runtime/gamevoice.db")
    feishu = None
    if settings.feishu_app_id and settings.feishu_app_secret:
        feishu = FeishuPersonalDevelopmentBitable(
            client=FeishuOpenApiClient(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
            ),
            base_url=settings.feishu_bitable_base_url,
        )

    asr = None
    if (
        settings.tencent_flash_asr_enabled
        and settings.tencent_app_id
        and settings.tencent_secret_id
        and settings.tencent_secret_key
    ):
        asr = TencentFlashFileAsr(
            app_id=settings.tencent_app_id,
            secret_id=settings.tencent_secret_id,
            secret_key=settings.tencent_secret_key,
            engine_type=settings.tencent_flash_asr_engine,
            speaker_diarization=settings.tencent_flash_asr_speaker_diarization,
            timeout_seconds=settings.tencent_flash_asr_timeout_seconds,
        )

    generator = None
    if settings.minimax_reasoning_enabled and settings.minimax_api_key:
        generator = MiniMaxM3CoachingInsightGenerator(
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_reasoning_base_url,
            model=settings.minimax_reasoning_model,
            thinking_type=settings.minimax_reasoning_thinking_type,
            reasoning_split=settings.minimax_reasoning_split,
            timeout_seconds=settings.minimax_reasoning_timeout_seconds,
        )

    return PersonalDevelopmentService(
        store=store,
        feishu=feishu,
        asr=asr,
        generator=generator,
        audio_retention_days=settings.personal_development_audio_retention_days,
    )

session_manager = SessionManager(store=_get_table_store())
session_manager.load_from_store(_get_table_store())
personal_development_service = _build_personal_development_service()
dialog_runtime_store = DialogRuntimeStore()
document_store = DocumentStore()
file_ingestor = FileIngestor(document_store)
reading_store = DocumentReadingStore()
document_reader = DocumentReaderWorker(reading_store, DocumentSummarizer())
archive_store = ArchiveStore()
audio_gateway = AudioGateway(build_sentence_transcriber(settings))
speaker_pipeline_adapter = SpeakerPipelineAdapter()
companion_orchestrator = CompanionOrchestrator(
    CompanionTiming(build_turn_decision_engine(settings)),
    build_dialog_client(settings),
)
auto_interrupt_service = AutoInterruptService(companion_orchestrator, build_tts_adapter(settings))
tts_stream_bridge = TTSStreamBridge()
live_heartbeat_scheduler = LiveHeartbeatScheduler(
    min_seconds=settings.live_heartbeat_min_seconds,
    max_seconds=settings.live_heartbeat_max_seconds,
)
interrupt_policy = InterruptPolicy(
    cooldown_seconds=settings.assistant_auto_reply_cooldown_seconds,
)
live_diagnostics_store = LiveDiagnosticsStore()
mobile_diagnostics_store = MobileDiagnosticsStore()
memory_compaction_store = MemoryCompactionStore()
memory_compactor = MemoryCompactor(build_dialog_client(settings))
identity_linker = IdentityLinker()
speaker_live_connector = SpeakerLiveConnector(
    session_manager=session_manager,
    identity_linker=identity_linker,
    pipeline_adapter=speaker_pipeline_adapter,
)
speaker_live_diarizer, speaker_live_embedder = build_speaker_live_runtime(settings)
speaker_live_worker = SpeakerLiveWorker(
    connector=speaker_live_connector,
    diarizer=speaker_live_diarizer,
    embedder=speaker_live_embedder,
)
speaker_alias_rewrite_service = SpeakerAliasRewriteService(
    session_manager=session_manager,
    dialog_client=build_dialog_client(settings),
    poll_interval_seconds=settings.speaker_alias_rewrite_poll_interval_seconds,
    active_window_seconds=settings.speaker_alias_rewrite_active_window_seconds,
)


def _append_speaker_alias_rewrite_completed(result: dict) -> None:
    table_id = str(result.get("table_id") or "")
    if table_id not in session_manager.tables:
        return
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "speaker_alias_rewrite_completed",
            "source": "speaker_alias_rewrite_service",
            "content": str(result.get("status") or "updated"),
            "active_speaker_ids": list(result.get("active_speaker_ids") or []),
            "speaker_alias_map": dict(result.get("speaker_alias_map") or {}),
        },
    )


def _append_speaker_alias_rewrite_failed(result: dict) -> None:
    table_id = str(result.get("table_id") or "")
    if table_id not in session_manager.tables:
        return
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "speaker_alias_rewrite_failed",
            "source": "speaker_alias_rewrite_service",
            "content": str(result.get("error") or "unknown alias rewrite error"),
        },
    )


speaker_alias_rewrite_service.on_updated = _append_speaker_alias_rewrite_completed
speaker_alias_rewrite_service.on_failed = _append_speaker_alias_rewrite_failed
if settings.tencent_realtime_engine != "16k_zh_en_speaker":
    speaker_live_connector.on_audio_chunk_enqueued = lambda table_id, live_session_id: threading.Thread(
        target=speaker_live_worker.process_session,
        kwargs={"table_id": table_id, "live_session_id": live_session_id},
        daemon=True,
    ).start()
else:
    speaker_live_connector.on_audio_chunk_enqueued = None
memory_compaction_service = MemoryCompactionService(
    memory_compaction_store,
    memory_compactor,
)
memory_compaction_service.on_completed = _on_memory_compaction_completed
memory_compaction_service.on_failed = _on_memory_compaction_failed
session_manager.on_context_event_appended = lambda table_id, event: _maybe_schedule_memory_compaction(table_id)
rule_analysis_store = RuleAnalysisStore()

# Build SkillAgent with tool registry
_skill_tool_registry = ToolRegistry()
_skill_tool_registry.register(build_arkham_rules_orient_tool())
_skill_tool_registry.register(build_arkham_rules_tool())
_skill_tool_registry.register(build_arkham_cards_tool())
_skill_tool_registry.register(build_official_faq_tool())
_skill_tool_registry.register(build_web_faq_tool())
_skill_tool_registry.register(build_uploaded_file_inspect_tool())
_skill_tool_registry.register(build_uploaded_file_search_tool())
_skill_tool_registry.register(build_file_reader_tool())
_skill_tool_registry.register(build_web_search_tool())

_dialog_client = build_dialog_client(settings)
# Unwrap MiniMaxDialogClient from PreviewRoutingDialogClient if needed
_minimax_client = getattr(_dialog_client, "reply_client", _dialog_client)
_skill_agent = SkillAgent(dialog_client=_minimax_client, tool_registry=_skill_tool_registry)
rule_analysis_worker = RuleAnalysisWorker(skill_agent=_skill_agent)
rule_analysis_service = RuleAnalysisService(
    rule_analysis_store,
    rule_analysis_worker,
    on_enqueued=_append_rule_analysis_requested_event,
    on_completed=_append_rule_analysis_completed_event,
    on_failed=_append_rule_analysis_failed_event,
)
app.state.realtime_session_factory = build_realtime_session_factory(settings)
app.state.speaker_alias_rewrite_service = speaker_alias_rewrite_service


@app.on_event("startup")
def start_background_workers() -> None:
    speaker_alias_rewrite_service.start_background_polling()


@app.on_event("shutdown")
def stop_background_workers() -> None:
    speaker_alias_rewrite_service.stop_background_polling()
    _get_table_store().close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class DevelopmentEmployeeCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    gallup_raw: str = ""
    profile_note: str = ""


class DevelopmentEmployeeUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    gallup_raw: str = ""
    profile_note: str = ""


@app.post("/development/employees")
def create_development_employee(payload: DevelopmentEmployeeCreateRequest) -> dict:
    return personal_development_service.create_employee(
        name=payload.name,
        gallup_raw=payload.gallup_raw,
        profile_note=payload.profile_note,
    )


@app.get("/development/employees")
def list_development_employees() -> dict:
    return {"employees": personal_development_service.list_employees()}


@app.get("/development/employees/{employee_id}")
def get_development_employee(employee_id: str) -> dict:
    employee = personal_development_service.get_employee(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


@app.put("/development/employees/{employee_id}")
def update_development_employee(employee_id: str, payload: DevelopmentEmployeeUpdateRequest) -> dict:
    try:
        return personal_development_service.update_employee(
            employee_id,
            name=payload.name,
            gallup_raw=payload.gallup_raw,
            profile_note=payload.profile_note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="employee not found") from exc


@app.post("/development/employees/{employee_id}/coaching-sessions")
async def create_development_coaching_session(employee_id: str, clip: UploadFile = File(...)) -> dict:
    try:
        return personal_development_service.create_coaching_session(
            employee_id=employee_id,
            filename=clip.filename or "coach.wav",
            audio_bytes=await clip.read(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="employee not found") from exc


@app.get("/development/employees/{employee_id}/coaching-sessions")
def list_development_coaching_sessions(employee_id: str) -> dict:
    if personal_development_service.get_employee(employee_id) is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return {"sessions": personal_development_service.list_sessions(employee_id)}


@app.post("/tables")
def create_table(payload: TableCreateRequest) -> dict:
    table = session_manager.start_table(
        name=payload.name,
        assistant_name=payload.assistant_name,
        assistant_personality=payload.assistant_personality,
        assistant_voice_id=payload.assistant_voice_id,
        origin=payload.origin,
    )
    dialog_runtime_store.ensure_table(table.id)
    archive_store.save(table.id, {"table_id": table.id, "name": table.name, "status": table.status})
    return {
        "id": table.id,
        "name": table.name,
        "status": table.status,
        "assistant_name": table.assistant_name,
        "assistant_personality": table.assistant_personality,
        "assistant_voice_id": table.assistant_voice_id,
        "origin": table.origin,
    }


@app.get("/tables")
def list_tables(include_non_manual: bool = False) -> dict:
    tables = []
    ordered_tables = sorted(
        session_manager.tables.values(),
        key=lambda item: item.last_active_at or item.created_at or "",
        reverse=True,
    )
    for table in ordered_tables:
        if not include_non_manual and table.origin != "manual":
            continue
        tables.append({
            "id": table.id,
            "name": table.name,
            "assistant_name": table.assistant_name,
            "assistant_personality": table.assistant_personality,
            "assistant_voice_id": table.assistant_voice_id,
            "origin": table.origin,
            "status": table.status,
            "created_at": table.created_at,
            "last_active_at": table.last_active_at,
            "personality_preview": "",
            **document_store.stats(table.id),
        })
    return {"tables": tables}


@app.delete("/tables/{table_id}")
def delete_table(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    session_manager.stop_table(table_id)
    return {"ok": True}


@app.patch("/tables/{table_id}")
def rename_table(table_id: str, payload: TableRenameRequest) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    table = session_manager.rename_table(table_id, payload.name)
    return {
        "id": table.id,
        "name": table.name,
        "status": table.status,
        "assistant_name": table.assistant_name,
    }


@app.get("/tables/{table_id}")
def get_table(table_id: str) -> dict:
    table = session_manager.tables.get(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    return {
        "id": table.id,
        "name": table.name,
        "status": table.status,
        "assistant_name": table.assistant_name,
        "assistant_personality": table.assistant_personality,
        "assistant_voice_id": table.assistant_voice_id,
        "origin": table.origin,
    }


@app.get("/tables/{table_id}/assistant-profile")
def get_assistant_profile(table_id: str) -> dict:
    table = session_manager.tables.get(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    return {
        "assistant_name": table.assistant_name,
        "assistant_personality": table.assistant_personality,
        "assistant_voice_id": table.assistant_voice_id,
    }


@app.put("/tables/{table_id}/assistant-profile")
def update_assistant_profile(table_id: str, payload: AssistantProfileUpdateRequest) -> dict:
    table = session_manager.tables.get(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    raise HTTPException(status_code=409, detail="assistant profile is fixed at table creation")


@app.get("/tables/{table_id}/speaker-identities")
def get_speaker_identities(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return {"speaker_identities": session_manager.list_speaker_identities(table_id)}


@app.get("/tables/{table_id}/speaker-identities/alias-map")
def get_speaker_identity_alias_map(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    alias_rewrite_state = speaker_alias_rewrite_service.describe_table_state(table_id)
    return {
        "speaker_alias_map": session_manager.list_speaker_alias_map(table_id),
        "active_speaker_ids": session_manager.list_active_speaker_ids(table_id),
        "alias_rewrite_state": alias_rewrite_state,
    }


@app.get("/tables/{table_id}/speaker-identities/review")
def list_speaker_identity_review_candidates(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return {
        "speaker_identity_review_candidates": session_manager.list_speaker_identity_review_candidates(table_id)
    }


@app.post("/tables/{table_id}/speaker-identities/observe")
def observe_speaker_identity(table_id: str, payload: SpeakerIdentityObserveRequest) -> dict:
    table = session_manager.tables.get(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    observed = identity_linker.observe(
        table.speaker_identity_state,
        diarized_speaker_id=payload.diarized_speaker_id,
        embedding=payload.embedding,
    )
    return session_manager.observe_speaker_identity(table_id, observed)


@app.post("/tables/{table_id}/speaker-identities/ingest")
def ingest_speaker_identities(table_id: str, payload: SpeakerIdentityIngestRequest) -> dict:
    table = session_manager.tables.get(table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    if payload.observations:
        batch = identity_linker.ingest_segments(
            table.speaker_identity_state,
            source=payload.source,
            session_id=payload.session_id,
            observations=[item.model_dump() for item in payload.observations],
        )
    elif payload.pyannote_segments or payload.diarization_segments:
        diarization_segments = payload.pyannote_segments or payload.diarization_segments
        speaker_embeddings = payload.wespeaker_embeddings or payload.speaker_embeddings
        pipeline_batch = speaker_pipeline_adapter.build_batch(
            source=payload.source,
            session_id=payload.session_id,
            pyannote_segments=[item.model_dump() for item in payload.pyannote_segments],
            diarization_segments=[item.model_dump() for item in payload.diarization_segments],
            speaker_embeddings=[item.model_dump() for item in speaker_embeddings],
            name_candidates=[item.model_dump() for item in payload.name_candidates],
        )
        batch = identity_linker.ingest_pipeline_batch(
            table.speaker_identity_state,
            source=pipeline_batch["source"],
            session_id=pipeline_batch["session_id"],
            diarization_segments=pipeline_batch["diarization_segments"],
            speaker_embeddings=pipeline_batch["speaker_embeddings"],
            name_candidates=pipeline_batch["name_candidates"],
        )
    else:
        raise HTTPException(
            status_code=400,
        detail="ingest requires observations or diarization_segments",
    )
    return session_manager.ingest_speaker_identity_batch(table_id, batch)


@app.post("/tables/{table_id}/speaker-identities/live-ingest")
def live_ingest_speaker_identities(table_id: str, payload: SpeakerIdentityLiveIngestRequest) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    if payload.observations:
        return speaker_live_connector.ingest_observations(
            table_id,
            payload.live_session_id,
            source=payload.source,
            observations=[item.model_dump() for item in payload.observations],
        )
    if payload.pyannote_segments or payload.diarization_segments:
        return speaker_live_connector.ingest_live_pipeline_batch(
            table_id,
            payload.live_session_id,
            source=payload.source,
            pyannote_segments=[item.model_dump() for item in payload.pyannote_segments],
            diarization_segments=[item.model_dump() for item in payload.diarization_segments],
            speaker_embeddings=[item.model_dump() for item in (payload.wespeaker_embeddings or payload.speaker_embeddings)],
            name_candidates=[item.model_dump() for item in payload.name_candidates],
        )
    raise HTTPException(status_code=400, detail="live ingest requires observations or diarization_segments")


@app.get("/tables/{table_id}/speaker-identities/live-sessions/{live_session_id}/audio-chunks")
def get_live_speaker_identity_audio_chunks(
    table_id: str,
    live_session_id: str,
    after_chunk_index: int = -1,
    limit: int = 32,
) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return speaker_live_connector.pull_audio_chunks(
        table_id,
        live_session_id,
        after_chunk_index=after_chunk_index,
        limit=limit,
    )


@app.get("/tables/{table_id}/speaker-identities/live-sessions/{live_session_id}")
def get_live_speaker_identity_session(table_id: str, live_session_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    state = speaker_live_connector.describe_session(table_id, live_session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="live session not found")
    return state


@app.post("/tables/{table_id}/speaker-identities/live-sessions/{live_session_id}/finish")
def finish_live_speaker_identity_session(table_id: str, live_session_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    state = speaker_live_connector.finish_session(table_id, live_session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="live session not found")
    return speaker_live_connector.describe_session(table_id, live_session_id) or {}


@app.post("/tables/{table_id}/speaker-identities/live-sessions/{live_session_id}/worker/process")
def process_live_speaker_identity_worker(
    table_id: str,
    live_session_id: str,
    after_chunk_index: int = -1,
    limit: int = 32,
) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return speaker_live_worker.process_session(
        table_id,
        live_session_id,
        after_chunk_index=after_chunk_index,
        limit=limit,
    )


@app.get("/tables/{table_id}/speaker-identities/live-sessions/{live_session_id}/worker")
def get_live_speaker_identity_worker_session(table_id: str, live_session_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    state = speaker_live_worker.describe_worker_session(table_id, live_session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="worker session not found")
    return state


@app.post("/tables/{table_id}/speaker-identities/link")
def link_speaker_identity(table_id: str, payload: SpeakerIdentityLinkRequest) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return session_manager.link_speaker_identity(
        table_id,
        payload.speaker_id,
        payload.linked_name,
    )


@app.post("/tables/{table_id}/speaker-identities/review/accept")
def accept_speaker_identity_override(table_id: str, payload: SpeakerIdentityOverrideAcceptRequest) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return session_manager.accept_speaker_identity_name_override(
        table_id,
        payload.speaker_id,
        payload.linked_name,
    )


@app.get("/tables/{table_id}/context")
def get_table_context(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return {"events": session_manager.list_context(table_id)}


@app.post("/tables/{table_id}/memory/compact")
def start_memory_compaction(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    payload = session_manager.build_memory_compaction_payload(table_id)
    record = memory_compaction_service.start(table_id=table_id, payload=payload)
    return {
        "compaction_id": record["compaction_id"],
        "table_id": table_id,
        "status": record["status"],
        "checkpoint": record["checkpoint"],
    }


@app.get("/tables/{table_id}/memory/compactions")
def list_memory_compactions(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return {"compactions": memory_compaction_store.list_for_table(table_id)}


@app.get("/tables/{table_id}/memory/compactions/{compaction_id}")
def get_memory_compaction(table_id: str, compaction_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    record = memory_compaction_store.get(compaction_id)
    if record is None or record.get("table_id") != table_id:
        raise HTTPException(status_code=404, detail="memory compaction not found")
    return record


@app.get("/tables/{table_id}/memory/compactions/{compaction_id}/source")
def get_memory_compaction_source(table_id: str, compaction_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    snapshot = archive_store.get_compaction_snapshot(table_id, compaction_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="memory compaction source not found")
    return snapshot


@app.post("/debug/memory/compact-text")
def debug_memory_compact_text(payload: MemoryCompactTextRequest) -> dict:
    return memory_compactor.compact(
        {
            "previous_summary": payload.previous_summary,
            "active_events": [
                {
                    "kind": "document_test",
                    "source": "debug",
                    "content": payload.text,
                }
            ],
        }
    )


@app.get("/tables/{table_id}/runtime/state")
def get_table_runtime_state(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return dialog_runtime_store.snapshot(table_id)


@app.get("/tables/{table_id}/runtime/events")
def get_table_runtime_events(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    events = [
        item
        for item in session_manager.list_runtime_events(table_id)
        if item.get("kind") in RUNTIME_EVENT_KINDS
    ]
    return {"events": events}


@app.get("/tables/{table_id}/live-diagnostics")
def get_live_diagnostics(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return live_diagnostics_store.snapshot(table_id)


@app.post("/tables/{table_id}/mobile-diagnostics")
def append_mobile_diagnostics(table_id: str, payload: MobileDiagnosticsRequest) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    entries = [entry.model_dump() for entry in payload.entries]
    accepted = mobile_diagnostics_store.append(table_id, entries)
    snapshot = mobile_diagnostics_store.snapshot(table_id)
    return {"accepted": accepted, "total": snapshot["count"]}


@app.get("/tables/{table_id}/mobile-diagnostics")
def get_mobile_diagnostics(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return mobile_diagnostics_store.snapshot(table_id)


@app.get("/tables/{table_id}/tts-jobs")
def list_table_tts_jobs(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")

    jobs = []
    for event in session_manager.list_assistant_replies(table_id):
        if not _is_public_speech_job_event(event):
            continue
        speech_job = event.get("speech_job")
        if not speech_job:
            continue
        jobs.append(
            {
                "job_id": speech_job.get("job_id"),
                "content": event.get("content", ""),
                "mode": event.get("mode", CONVERSATION_MODE),
                "output_path": speech_job.get("output_path"),
                "format": speech_job.get("format"),
                "accepted": speech_job.get("accepted", False),
                "status": speech_job.get("status", "ready"),
                "segments": speech_job.get("segments", []),
                "segment_count": speech_job.get("segment_count", 0),
                "segment_statuses": speech_job.get("segment_statuses", []),
                "stream_id": speech_job.get("stream_id"),
                "turn_id": speech_job.get("turn_id"),
                "reply_id": speech_job.get("reply_id"),
            }
        )
    return {"jobs": jobs}


@app.get("/tables/{table_id}/tts-jobs/{job_id}/segments")
def list_tts_job_segments(table_id: str, job_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    speech_job = event["speech_job"]
    return {
        "job_id": job_id,
        "status": speech_job.get("status", "ready"),
        "segment_count": speech_job.get("segment_count", 0),
        "segments": speech_job.get("segment_statuses", []),
    }


@app.get("/tables/{table_id}/tts-jobs/{job_id}/segments/next")
def get_next_tts_job_segment(table_id: str, job_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    for segment in event["speech_job"].get("segment_statuses", []):
        if segment.get("status") in {"queued", "playing"}:
            return {
                "job_id": job_id,
                "segment": segment,
            }
    raise HTTPException(status_code=404, detail="no pending tts segment")


@app.get("/tables/{table_id}/tts-jobs/{job_id}/segments/{segment_index}/audio")
def get_tts_job_segment_audio(table_id: str, job_id: str, segment_index: int):
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    segment = _find_segment(event["speech_job"], segment_index)
    if segment is None:
        raise HTTPException(status_code=404, detail="tts segment not found")
    output_path = segment.get("output_path")
    if not output_path:
        raise HTTPException(status_code=404, detail="tts segment audio not found")
    path = Path(output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tts segment audio not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


_VOICE_PREVIEW_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "voice-previews"


@app.get("/voice-previews/{filename}")
def get_voice_preview(filename: str) -> FileResponse:
    path = _VOICE_PREVIEW_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="voice preview not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@app.post("/tables/{table_id}/tts-jobs/{job_id}/stream")
def start_tts_job_stream(table_id: str, job_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    try:
        return tts_stream_bridge.start_stream(event["speech_job"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="tts audio not found") from exc


@app.get("/tables/{table_id}/tts-streams/{stream_id}/next")
def read_next_tts_stream_chunk(table_id: str, stream_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    try:
        chunk = tts_stream_bridge.next_chunk(stream_id, wait_timeout=1.0)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tts stream not found") from exc
    if chunk is None:
        raise HTTPException(status_code=404, detail="tts stream exhausted")
    return {
        "stream_id": chunk["stream_id"],
        "job_id": chunk["job_id"],
        "chunk_index": chunk["chunk_index"],
        "segment_index": chunk["segment_index"],
        "text": chunk["text"],
        "is_final": chunk["is_final"],
        "turn_id": chunk.get("turn_id"),
        "reply_id": chunk.get("reply_id"),
        "audio_base64": base64.b64encode(chunk["audio_bytes"]).decode("ascii"),
    }


@app.post("/tables/{table_id}/tts-streams/{stream_id}/cancel")
def cancel_tts_stream(table_id: str, stream_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    try:
        return tts_stream_bridge.cancel_stream(stream_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tts stream not found") from exc


@app.post("/tables/{table_id}/tts-jobs/{job_id}/interrupt")
def interrupt_tts_job(table_id: str, job_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    speech_job = _interrupt_active_runtime_job(
        table_id,
        {"current_job_id": job_id},
    )
    if speech_job is None:
        speech_job = event["speech_job"]
    return {"job": speech_job}


@app.post("/tables/{table_id}/tts-jobs/{job_id}/played")
def mark_tts_job_played(table_id: str, job_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    speech_job = _mark_tts_job_played(table_id, event, job_id)
    return {"job": speech_job}


@app.post("/tables/{table_id}/tts-jobs/{job_id}/segments/{segment_index}/started")
def mark_tts_segment_started(table_id: str, job_id: str, segment_index: int) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    segment = _find_segment(event["speech_job"], segment_index)
    if segment is None:
        raise HTTPException(status_code=404, detail="tts segment not found")
    if _is_terminal_tts_job(event["speech_job"]):
        return {"segment": segment, "ignored": True, "reason": "terminal_job"}
    segment["status"] = "playing"
    if segment_index == 0:
        live_heartbeat_scheduler.on_agent_speech_started(table_id)
    dialog_runtime_store.on_agent_speaking_started(table_id, job_id=job_id, segment_index=segment_index)
    if (
        segment_index == 0
        and event.get("kind") == "assistant_preview"
        and event.get("source") == "runtime_preview"
    ):
        session_manager.commit_spoken_reply(table_id, job_id)
        _maybe_start_preview_handoff_formal_pregeneration(table_id, preview_job_id=job_id)
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_speaking",
            "source": "runtime",
            "content": event.get("content", ""),
            "job_id": job_id,
        },
    )
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_segment_started",
            "source": "runtime",
            "content": segment.get("text", ""),
            "job_id": job_id,
            "segment_index": segment_index,
        },
    )
    return {"segment": segment}


@app.post("/tables/{table_id}/tts-jobs/{job_id}/segments/{segment_index}/completed")
def mark_tts_segment_completed(table_id: str, job_id: str, segment_index: int) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    event = _find_speech_job_event(table_id, job_id)
    if event is None:
        raise HTTPException(status_code=404, detail="tts job not found")
    segment = _find_segment(event["speech_job"], segment_index)
    if segment is None:
        raise HTTPException(status_code=404, detail="tts segment not found")
    if _is_terminal_tts_job(event["speech_job"]):
        return {"segment": segment, "ignored": True, "reason": "terminal_job"}
    segment["status"] = "completed"
    dialog_runtime_store.on_agent_segment_completed(table_id, job_id=job_id, segment_index=segment_index)
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "assistant_segment_completed",
            "source": "runtime",
            "content": segment.get("text", ""),
            "job_id": job_id,
            "segment_index": segment_index,
        },
    )
    speech_job = event["speech_job"]
    all_completed = all(
        item.get("status") == "completed" for item in speech_job.get("segment_statuses", [])
    )
    if all_completed:
        _mark_tts_job_played(table_id, event, job_id)
    return {"segment": segment}


@app.get("/tables/{table_id}/tts-jobs/latest/audio")
def get_latest_tts_audio(table_id: str):
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")

    latest_path: str | None = None
    for event in session_manager.list_assistant_replies(table_id):
        if not _is_public_speech_job_event(event):
            continue
        speech_job = event.get("speech_job")
        if speech_job and speech_job.get("output_path"):
            latest_path = speech_job["output_path"]

    if latest_path is None:
        raise HTTPException(status_code=404, detail="tts audio not found")

    path = Path(latest_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tts audio not found")

    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@app.get("/tables/{table_id}/companion/next")
def get_companion_next(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return companion_orchestrator.plan_reply(session_manager.list_dialog_context(table_id))


@app.post("/tables/{table_id}/companion/interrupt")
def run_companion_interrupt(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return _run_auto_interrupt_for_table_safely(table_id, automatic=False)

@app.get("/tables/{table_id}/rules/analyses")
def list_rule_analyses(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    return {"analyses": rule_analysis_store.list_for_table(table_id)}


@app.get("/tables/{table_id}/rules/analyses/{analysis_id}")
def get_rule_analysis(table_id: str, analysis_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    record = rule_analysis_store.get(analysis_id)
    if record is None or record.get("table_id") != table_id:
        raise HTTPException(status_code=404, detail="rule analysis not found")
    return record


@app.post("/tables/{table_id}/audio-clips")
async def upload_audio_clip(table_id: str, clip: UploadFile = File(...)) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")

    transcript = audio_gateway.ingest_clip(
        table_id=table_id,
        filename=clip.filename or "voice-clip.bin",
        clip_bytes=await clip.read(),
    )
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "voice_clip",
            "content": session_manager.format_user_utterance(text=transcript["content"]),
            "filename": transcript["filename"],
        },
    )
    return transcript


@app.websocket("/ws/tables/{table_id}/listen")
async def listen_table(table_id: str, websocket: WebSocket) -> None:
    if not _public_api_request_authorized(
        websocket.headers.get("authorization"),
        websocket.query_params.get("access_token"),
    ):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    logger.info("listen_table accepted websocket for table=%s", table_id)
    if table_id not in session_manager.tables:
        await _safe_send_ws_json(websocket, {"event": "error", "message": "table not found"})
        await websocket.close()
        return
    live_diagnostics_store.mark_websocket_connected(table_id)

    def _build_realtime_session() -> object:
        factory = app.state.realtime_session_factory
        speaker_context_id = session_manager.tables[table_id].latest_live_speaker_context_id
        try:
            return factory(speaker_context_id=speaker_context_id)
        except TypeError:
            return factory()

    realtime_session = _build_realtime_session()
    live_session_id = uuid4().hex
    seen_stable_transcript = False
    speaker_live_connector.start_session(table_id, live_session_id)
    identity_event_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    preview_tasks: set[asyncio.Task] = set()
    preview_inflight_transcript: str | None = None
    alias_evidence_seen: set[tuple[str, str, str]] = set()
    last_voice_activity_monotonic = 0.0
    if settings.live_heartbeat_enabled:
        live_heartbeat_scheduler.on_listening_started(table_id)

    def _enqueue_identity_event(payload: dict) -> None:
        try:
            loop.call_soon_threadsafe(identity_event_queue.put_nowait, dict(payload))
        except RuntimeError:
            return

    speaker_live_connector.add_identity_listener(table_id, _enqueue_identity_event)

    try:
        await realtime_session.connect()
        logger.info("listen_table connected realtime session for table=%s", table_id)
    except Exception as exc:
        logger.exception("listen_table failed to connect realtime session for table=%s", table_id)
        await _safe_send_ws_json(websocket, {"event": "error", "message": str(exc)})
        await websocket.close()
        return

    silence_gate = _build_live_silence_gate()

    def _attach_speaker_identity_snapshot(event: dict) -> dict:
        forwarded = dict(event)
        if forwarded.get("event") in {"transcript", "final"}:
            forwarded["speaker_identities"] = session_manager.list_speaker_identities(table_id)
            forwarded["speaker_identity_review_candidates"] = session_manager.list_speaker_identity_review_candidates(table_id)
        return forwarded

    def _build_auto_preview_payload(auto_preview: dict) -> dict:
        return {
            "event": "assistant_preview",
            "mode": auto_preview["mode"],
            "source": auto_preview["reply"]["source"],
            "content": auto_preview["reply"]["content"],
            "lead": auto_preview["reply"].get("lead", ""),
            "turn_id": auto_preview.get("turn_id"),
            "reply_id": auto_preview.get("reply_id"),
            "speech_job": auto_preview.get("speech_job"),
            "tts_stream": auto_preview.get("tts_stream"),
            "transcript": auto_preview.get("transcript", ""),
            "runtime": dialog_runtime_store.snapshot(table_id),
        }

    def _build_heartbeat_payload(heartbeat: dict) -> dict:
        return {
            "event": "assistant_ready",
            "mode": heartbeat["mode"],
            "source": heartbeat["reply"]["source"],
            "content": heartbeat["reply"]["content"],
            "lead": heartbeat["reply"].get("lead", ""),
            "tail": heartbeat["reply"].get("tail", ""),
            "turn_id": heartbeat.get("turn_id"),
            "reply_id": heartbeat.get("reply_id"),
            "speech_job": heartbeat.get("speech_job"),
            "tts_stream": heartbeat.get("tts_stream"),
            "heartbeat": True,
            "decision_reason": "heartbeat",
            "runtime": dialog_runtime_store.snapshot(table_id),
        }

    def _schedule_auto_preview(transcript: str) -> None:
        nonlocal preview_inflight_transcript
        preview_inflight_transcript = transcript

        async def run_preview() -> None:
            nonlocal preview_inflight_transcript
            try:
                auto_preview = await asyncio.to_thread(
                    _run_auto_interrupt_preview_for_table_safely,
                    table_id,
                    transcript=transcript,
                )
                if auto_preview and auto_preview.get("interrupt") and auto_preview.get("reply"):
                    await _safe_send_ws_json(websocket, _build_auto_preview_payload(auto_preview))
            finally:
                if preview_inflight_transcript == transcript:
                    preview_inflight_transcript = None

        task = asyncio.create_task(run_preview())
        preview_tasks.add(task)
        task.add_done_callback(preview_tasks.discard)

    async def _ensure_preview_before_final(final_text: str) -> None:
        if not final_text:
            return
        runtime_snapshot = dialog_runtime_store.snapshot(table_id)
        if runtime_snapshot.get("preview_reply_text"):
            return
        if preview_inflight_transcript is None:
            _schedule_auto_preview(final_text)
        if preview_tasks:
            await asyncio.wait(list(preview_tasks), timeout=PREVIEW_FINAL_WAIT_SECONDS)

    async def forward_events() -> None:
        nonlocal seen_stable_transcript, last_voice_activity_monotonic
        while True:
            try:
                event = await realtime_session.receive_event()
            except Exception:
                logger.exception("listen_table failed while receiving realtime event table=%s", table_id)
                break
            if event is None:
                logger.info("listen_table realtime session ended event stream for table=%s", table_id)
                break
            _record_speaker_alias_evidence_from_realtime_event(
                table_id,
                event,
                seen_keys=alias_evidence_seen,
            )
            auto_interrupt: dict | None = None
            auto_preview: dict | None = None
            forwarded_events: list[dict] = []
            forwarding_failed = False
            wait_for_preview_after_forward = False
            if _handle_transcript_barge_in(table_id, event):
                session_manager.commit_live_transcript(
                    table_id,
                    source="live_asr",
                    text=str(event.get("text", "")),
                    interrupted=True,
                    live_session_id=live_session_id,
                )
                if not await _safe_send_ws_json(
                    websocket,
                    {
                        "event": "barge_in",
                        "runtime": dialog_runtime_store.snapshot(table_id),
                    },
                    ):
                        break
            if event.get("event") == "transcript" and event.get("slice_type") == 2 and event.get("text"):
                last_voice_activity_monotonic = time.monotonic()
                seen_stable_transcript = True
                speaker_id = event.get("speaker_id")
                speaker_label = event.get("speaker_label")
                stable_update = session_manager.upsert_live_transcript(
                    table_id=table_id,
                    live_session_id=live_session_id,
                    slice_index=event.get("index", 0),
                    content=event["text"],
                    speaker_id=str(speaker_id).strip() if speaker_id not in (None, "", -1, "-1") else None,
                    speaker_label=speaker_label,
                )
                speaker_live_connector.update_transcript(
                    table_id,
                    live_session_id,
                    stable_update["stable_text"],
                    speaker_id=stable_update.get("speaker_id"),
                    speaker_label=stable_update.get("speaker_label"),
                )
                runtime_snapshot = dialog_runtime_store.snapshot(table_id)
                if (
                    runtime_snapshot["state"] not in {"assistant_ready", "agent_speaking"}
                    and not runtime_snapshot.get("preview_reply_text")
                    and preview_inflight_transcript is None
                ):
                    _schedule_auto_preview(stable_update["stable_text"])
                    wait_for_preview_after_forward = True
            if event.get("event") == "transcript" and event.get("slice_type") == 1:
                live_diagnostics_store.mark_draft_forwarded(table_id)
            elif event.get("event") == "transcript" and event.get("slice_type") == 2:
                live_diagnostics_store.mark_stable_forwarded(table_id)
            elif event.get("event") == "final":
                if event.get("text"):
                    last_voice_activity_monotonic = time.monotonic()
                live_diagnostics_store.mark_final_forwarded(table_id)
                final_text = _normalize_session_whitespace(str(event.get("text") or ""))
                if final_text:
                    session_manager.upsert_live_transcript(
                        table_id=table_id,
                        live_session_id=live_session_id,
                        slice_index=int(event.get("index", 0) or 0),
                        content=final_text,
                        speaker_id=(
                            str(event.get("speaker_id")).strip()
                            if event.get("speaker_id") not in (None, "", -1, "-1")
                            else None
                        ),
                        speaker_label=str(event.get("speaker_label") or "").strip() or None,
                        speaker_context_id=str(event.get("speaker_context_id") or "").strip() or None,
                    )
                    await _ensure_preview_before_final(final_text)
                try:
                    runtime_before_commit = dialog_runtime_store.snapshot(table_id)
                    table_state = session_manager.tables.get(table_id)
                    latest_stable_text = getattr(table_state, "latest_live_stable_text", None)
                    resolved_final_text = _resolve_final_live_transcript_text(
                        runtime_before_commit,
                        latest_stable_text,
                    )
                    await _ensure_preview_before_final(resolved_final_text)
                    runtime_before_commit = dialog_runtime_store.snapshot(table_id)
                    committed = session_manager.commit_live_transcript(
                        table_id,
                        source="live_asr",
                        text=resolved_final_text,
                        live_session_id=live_session_id,
                        speaker_context_id=str(event.get("speaker_context_id") or "").strip() or None,
                    )
                    if committed is not None:
                        if _should_run_auto_interrupt_on_final(
                            runtime_before_commit,
                            committed.get("content") or resolved_final_text,
                            events=session_manager.list_dialog_context(table_id),
                            assistant_name=session_manager.get_assistant_name(table_id),
                        ):
                            dialog_runtime_store.on_user_turn_committed(table_id)
                            auto_interrupt = await asyncio.to_thread(
                                _run_auto_interrupt_for_table_safely,
                                table_id,
                                automatic=True,
                            )
                except Exception:
                    logger.exception("listen_table failed to commit final transcript table=%s", table_id)
            elif event.get("event") == "error":
                live_diagnostics_store.mark_error(
                    table_id,
                    str(event.get("message", "live websocket error")),
                )
            logger.info("listen_table forwarding event for table=%s event=%s", table_id, event)
            for extra_event in forwarded_events:
                if not await _safe_send_ws_json(websocket, _attach_speaker_identity_snapshot(extra_event)):
                    forwarding_failed = True
                    break
            if forwarding_failed:
                break
            if not await _safe_send_ws_json(websocket, _attach_speaker_identity_snapshot(event)):
                break
            if auto_preview and auto_preview.get("interrupt") and auto_preview.get("reply"):
                if not await _safe_send_ws_json(
                    websocket,
                    {
                        "event": "assistant_preview",
                        "mode": auto_preview["mode"],
                        "source": auto_preview["reply"]["source"],
                        "content": auto_preview["reply"]["content"],
                        "lead": auto_preview["reply"].get("lead", ""),
                        "turn_id": auto_preview.get("turn_id"),
                        "reply_id": auto_preview.get("reply_id"),
                        "speech_job": auto_preview.get("speech_job"),
                        "tts_stream": auto_preview.get("tts_stream"),
                        "transcript": auto_preview.get("transcript", ""),
                        "runtime": dialog_runtime_store.snapshot(table_id),
                    },
                ):
                    break
            if auto_interrupt and auto_interrupt["interrupt"]:
                if not await _safe_send_ws_json(
                    websocket,
                    {
                        "event": "assistant_ready",
                        "mode": auto_interrupt["mode"],
                        "source": auto_interrupt["reply"]["source"],
                        "content": auto_interrupt["reply"]["content"],
                        "lead": auto_interrupt["reply"].get("lead", ""),
                        "tail": auto_interrupt["reply"].get("tail", ""),
                        "turn_id": auto_interrupt.get("turn_id"),
                        "reply_id": auto_interrupt.get("reply_id"),
                        "speech_job": auto_interrupt["speech_job"],
                        "tts_stream": auto_interrupt.get("tts_stream"),
                        "preview_handoff": bool(auto_interrupt.get("preview_handoff")),
                        "preview_reply_text": auto_interrupt.get("preview_reply_text"),
                        "preview_source_text": auto_interrupt.get("preview_source_text"),
                    },
                ):
                    break
            if wait_for_preview_after_forward and preview_tasks:
                await asyncio.wait(list(preview_tasks), timeout=0.05)
            if event.get("event") == "error" or (
                event.get("event") == "final" and event.get("stream_final", True)
            ):
                break

    forward_task = asyncio.create_task(forward_events())

    async def forward_identity_events() -> None:
        while True:
            payload = await identity_event_queue.get()
            if payload is None:
                break
            outbound = dict(payload)
            outbound["runtime"] = dialog_runtime_store.snapshot(table_id)
            if not await _safe_send_ws_json(websocket, outbound):
                break

    identity_task = asyncio.create_task(forward_identity_events())

    async def reconnect_realtime_session(reason: str) -> None:
        nonlocal realtime_session, forward_task, live_session_id, seen_stable_transcript
        logger.warning("listen_table reconnecting realtime session table=%s reason=%s", table_id, reason)
        if not forward_task.done():
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task
        else:
            with contextlib.suppress(Exception):
                await forward_task
        with contextlib.suppress(Exception):
            await realtime_session.close()
        speaker_live_connector.finish_session(table_id, live_session_id)
        realtime_session = _build_realtime_session()
        live_session_id = uuid4().hex
        seen_stable_transcript = False
        speaker_live_connector.start_session(table_id, live_session_id)
        await realtime_session.connect()
        logger.info("listen_table reconnected realtime session table=%s", table_id)
        live_diagnostics_store.mark_realtime_reconnected(table_id)
        if not await _safe_send_ws_json(websocket, {"event": "realtime_reconnected", "reason": reason}):
            return
        forward_task = asyncio.create_task(forward_events())

    audio_queue: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(
        maxsize=LIVE_AUDIO_QUEUE_MAX_CHUNKS
    )
    audio_accepting = True
    audio_sender_stopping = False

    def enqueue_audio_chunk(chunk: bytes, *, received_monotonic_ms: float) -> None:
        if not audio_accepting:
            return
        item = (chunk, received_monotonic_ms)
        try:
            audio_queue.put_nowait(item)
            live_diagnostics_store.mark_audio_enqueue(
                table_id,
                queue_depth=audio_queue.qsize(),
            )
            return
        except asyncio.QueueFull:
            pass
        with contextlib.suppress(asyncio.QueueEmpty):
            audio_queue.get_nowait()
            audio_queue.task_done()
        live_diagnostics_store.mark_error(
            table_id,
            "live audio backlog exceeded; dropped stale audio chunk",
        )
        logger.warning(
            "listen_table dropped stale audio chunk for table=%s queue_size=%s",
            table_id,
            audio_queue.qsize(),
        )
        with contextlib.suppress(asyncio.QueueFull):
            audio_queue.put_nowait(item)
            live_diagnostics_store.mark_audio_enqueue(
                table_id,
                queue_depth=audio_queue.qsize(),
            )

    async def send_audio_with_diagnostics(chunk: bytes) -> None:
        send_started = time.monotonic()
        await realtime_session.send_audio(chunk)
        send_elapsed_ms = round((time.monotonic() - send_started) * 1000, 3)
        live_diagnostics_store.mark_audio_send_complete(
            table_id,
            send_audio_elapsed_ms=send_elapsed_ms,
            tencent_payload_send_elapsed_ms=getattr(
                realtime_session,
                "last_payload_send_elapsed_ms",
                None,
            ),
            send_audio_pacing_requested_ms=getattr(
                realtime_session,
                "last_pacing_requested_ms",
                None,
            ),
            send_audio_pacing_actual_ms=getattr(
                realtime_session,
                "last_pacing_actual_ms",
                None,
            ),
        )

    async def send_audio_worker() -> None:
        while True:
            item = await audio_queue.get()
            try:
                if item is None:
                    return
                chunk, received_monotonic_ms = item
                dequeued_monotonic_ms = time.monotonic() * 1000
                live_diagnostics_store.mark_audio_dequeue(
                    table_id,
                    queue_depth=audio_queue.qsize(),
                    send_worker_lag_ms=round(
                        dequeued_monotonic_ms - received_monotonic_ms,
                        3,
                    ),
                )
                try:
                    await send_audio_with_diagnostics(chunk)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if audio_sender_stopping:
                        return
                    logger.exception("listen_table send_audio failed table=%s", table_id)
                    try:
                        await reconnect_realtime_session("send_audio_failed")
                        await send_audio_with_diagnostics(chunk)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("listen_table realtime reconnect failed table=%s", table_id)
                        live_diagnostics_store.mark_error(
                            table_id,
                            "live audio bridge dropped; please restart live listening",
                        )
                        await _safe_send_ws_json(
                            websocket,
                            {
                                "event": "error",
                                "message": "live audio bridge dropped; please restart live listening",
                            },
                        )
                        return
            finally:
                audio_queue.task_done()

    audio_sender_task = asyncio.create_task(send_audio_worker())

    async def monitor_event_loop_lag() -> None:
        interval_seconds = 0.05
        next_wake = time.monotonic() + interval_seconds
        while True:
            await asyncio.sleep(interval_seconds)
            now = time.monotonic()
            lag_ms = max(0.0, (now - next_wake) * 1000)
            live_diagnostics_store.mark_event_loop_lag(
                table_id,
                lag_ms=round(lag_ms, 3),
            )
            next_wake = now + interval_seconds

    event_loop_lag_task = asyncio.create_task(monitor_event_loop_lag())

    def _heartbeat_has_pending_work(runtime: dict) -> bool:
        if runtime.get("state") in {"assistant_ready", "agent_thinking", "agent_speaking"}:
            return True
        if runtime.get("preview_reply_text") or runtime.get("pending_formal_text"):
            return True
        if preview_inflight_transcript is not None or preview_tasks:
            return True
        with _lookup_runtime_lock:
            return table_id in _active_lookup_by_table or table_id in _pending_lookup_by_table

    async def monitor_live_heartbeat() -> None:
        if not settings.live_heartbeat_enabled:
            return
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            runtime = dialog_runtime_store.snapshot(table_id)
            user_voice_active = (
                last_voice_activity_monotonic > 0
                and now - last_voice_activity_monotonic < 2.0
            )
            has_pending_work = _heartbeat_has_pending_work(runtime)
            if not live_heartbeat_scheduler.should_fire(
                table_id,
                now_monotonic=now,
                is_listening=audio_accepting,
                is_agent_speaking=bool(runtime.get("is_agent_speaking")),
                has_pending_assistant_audio=has_pending_work,
                user_voice_active=user_voice_active,
            ):
                continue
            live_heartbeat_scheduler.mark_inflight(table_id)
            heartbeat = await asyncio.to_thread(_run_live_heartbeat_for_table_safely, table_id)
            if heartbeat.get("interrupt") and heartbeat.get("reply"):
                if not await _safe_send_ws_json(websocket, _build_heartbeat_payload(heartbeat)):
                    break
            else:
                live_heartbeat_scheduler.mark_finished(table_id, now_monotonic=time.monotonic())

    heartbeat_task = asyncio.create_task(monitor_live_heartbeat())

    def drain_audio_queue() -> None:
        while True:
            try:
                audio_queue.get_nowait()
                audio_queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def stop_audio_sender() -> None:
        nonlocal audio_accepting, audio_sender_stopping
        audio_accepting = False
        audio_sender_stopping = True
        drain_audio_queue()
        if not audio_sender_task.done():
            audio_sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audio_sender_task
        else:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await audio_sender_task

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                logger.info("listen_table websocket disconnect for table=%s", table_id)
                break
            if message.get("bytes") is not None:
                runtime = dialog_runtime_store.snapshot(table_id)
                if runtime["state"] not in {"assistant_ready", "agent_speaking"}:
                    dialog_runtime_store.on_user_audio(table_id)
                speaker_live_connector.ingest_audio_chunk(table_id, live_session_id, message["bytes"])
                logger.info(
                    "listen_table received audio chunk for table=%s bytes=%s",
                    table_id,
                    len(message["bytes"]),
                )
                receive_monotonic_ms = time.monotonic() * 1000
                live_diagnostics_store.mark_audio_chunk_received(
                    table_id,
                    len(message["bytes"]),
                    monotonic_ms=receive_monotonic_ms,
                )
                if forward_task.done():
                    try:
                        await reconnect_realtime_session("event_stream_ended")
                    except Exception:
                        logger.exception("listen_table realtime reconnect failed table=%s", table_id)
                        await _safe_send_ws_json(
                            websocket,
                            {
                                "event": "error",
                                "message": "live audio bridge dropped; please restart live listening",
                            },
                        )
                        break
                gate_decision = silence_gate.process_chunk(message["bytes"])
                live_diagnostics_store.mark_silence_gate_decision(
                    table_id,
                    state=gate_decision.state,
                    input_bytes=gate_decision.input_bytes,
                    forwarded_bytes=gate_decision.forwarded_bytes,
                    suppressed_bytes=gate_decision.suppressed_bytes,
                    voiced_frames=gate_decision.voiced_frames,
                    total_frames=gate_decision.total_frames,
                    preroll_flushed=gate_decision.preroll_flushed,
                    error=gate_decision.error,
                )
                if gate_decision.voiced_frames > 0:
                    last_voice_activity_monotonic = time.monotonic()
                for forward_chunk in gate_decision.forward_chunks:
                    enqueue_audio_chunk(
                        forward_chunk,
                        received_monotonic_ms=receive_monotonic_ms,
                    )
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("listen_table ignored malformed control payload table=%s payload=%s", table_id, text)
                continue
            logger.info("listen_table received control message for table=%s payload=%s", table_id, payload)
            if payload.get("type") == "end":
                await stop_audio_sender()
                await realtime_session.end()
                with contextlib.suppress(Exception):
                    await realtime_session.close()
                await forward_task
                break
    except WebSocketDisconnect:
        logger.info("listen_table caught websocket disconnect for table=%s", table_id)
        live_diagnostics_store.mark_websocket_disconnected(table_id)
        pass
    finally:
        speaker_live_connector.finish_session(table_id, live_session_id)
        speaker_live_connector.remove_identity_listener(table_id, _enqueue_identity_event)
        live_diagnostics_store.mark_websocket_disconnected(table_id)
        if not identity_task.done():
            identity_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await identity_task
        else:
            with contextlib.suppress(Exception):
                await identity_task
        if not forward_task.done():
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task
        else:
            with contextlib.suppress(Exception):
                await forward_task
        for task in list(preview_tasks):
            if not task.done():
                task.cancel()
        if preview_tasks:
            await asyncio.gather(*list(preview_tasks), return_exceptions=True)
        await stop_audio_sender()
        if not event_loop_lag_task.done():
            event_loop_lag_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_loop_lag_task
        if not heartbeat_task.done():
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        else:
            with contextlib.suppress(Exception):
                await heartbeat_task
        live_heartbeat_scheduler.on_listening_stopped(table_id)
        await realtime_session.close()
        logger.info("listen_table closed realtime session for table=%s", table_id)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


@app.get("/tables/{table_id}/documents")
def list_documents(table_id: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    documents = document_store.list_documents(table_id)
    return {
        "documents": [
            {
                "filename": item["filename"],
                "status": item["status"],
                "size_bytes": item.get("size_bytes", 0),
            }
            for item in documents
        ]
    }


@app.post("/tables/{table_id}/documents")
async def upload_documents(table_id: str, files: list[UploadFile] = File(...)) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")

    payload_files: list[dict] = []
    for item in files:
        payload_files.append({"filename": item.filename, "data": await item.read()})

    existing_filenames = {
        str(item.get("filename") or "")
        for item in document_store.list_documents(table_id)
    }
    try:
        result = file_ingestor.ingest_files(table_id=table_id, files=payload_files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    uploaded_records = [
        item
        for item in list(result.get("records") or [])
        if str(item.get("filename") or "") not in existing_filenames
    ]
    uploaded_filenames = [
        str(item.get("filename") or "").strip()
        for item in uploaded_records
        if str(item.get("filename") or "").strip()
    ]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "document_upload_fact",
            "source": "document_upload",
            "content": _build_document_upload_context_fact(uploaded_records),
            "filenames": uploaded_filenames,
            "document_count": len(uploaded_records),
        },
    )
    session_manager.append_runtime_event(
        table_id,
        {
            "kind": "document_upload_ack",
            "source": "document_upload",
            "content": result.get("message", ""),
            "filenames": uploaded_filenames,
            "document_count": len(uploaded_records),
        },
    )
    return result


@app.get("/tables/{table_id}/documents/{query}/read")
def read_document(table_id: str, query: str, mode: str = "summary") -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    if mode not in {"summary", "original"}:
        raise HTTPException(status_code=400, detail="mode must be summary or original")

    resolved = document_store.resolve_by_partial_name(table_id, query)
    if resolved["status"] == "ambiguous":
        raise HTTPException(status_code=409, detail={"candidates": resolved["candidates"]})

    document = resolved["document"]
    text = document["data"].decode("utf-8", errors="replace")
    return document_reader.read(
        table_id=table_id,
        document_id=document["filename"],
        document_text=text,
        mode=mode,
    )


@app.delete("/tables/{table_id}/documents/{filename}")
def delete_document(table_id: str, filename: str) -> dict:
    if table_id not in session_manager.tables:
        raise HTTPException(status_code=404, detail="table not found")
    try:
        deleted = document_store.delete(table_id=table_id, filename=filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc
    return {
        "filename": deleted["filename"],
        "status": "deleted",
        **document_store.stats(table_id),
    }
