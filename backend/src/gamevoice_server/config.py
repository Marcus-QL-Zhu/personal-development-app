import os
from dataclasses import dataclass, field
from pathlib import Path


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key.startswith("#"):
            continue
        os.environ.setdefault(key, _strip_env_value(value))


def _load_dotenv() -> None:
    if os.getenv("GAMEVOICE_TESTING"):
        return
    backend_root = Path(__file__).resolve().parents[2]
    repo_root = backend_root.parent
    candidates = [
        repo_root / ".env",
        backend_root / ".env",
        Path.cwd() / ".env",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _load_dotenv_file(resolved)


def _env_str(name: str, default: str):
    return field(default_factory=lambda: os.getenv(name) or default)


def _env_optional(name: str):
    return field(default_factory=lambda: os.getenv(name) or None)


def _env_int(name: str, default: str):
    return field(default_factory=lambda: int(os.getenv(name) or default))


def _env_float(name: str, default: str):
    return field(default_factory=lambda: float(os.getenv(name) or default))


def _env_bool(name: str, default: str):
    return field(default_factory=lambda: (os.getenv(name) or default).strip().lower() not in {"0", "false", "no", "off"})


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "GameVoice"
    server_profile: str = "2vcpu-4g"
    gamevoice_public_api_token: str | None = _env_optional("GAMEVOICE_PUBLIC_API_TOKEN")
    gamevoice_db_path: str = _env_str("GAMEVOICE_DB_PATH", ".runtime/gamevoice.db")
    tencent_app_id: str | None = _env_optional("TENCENT_APP_ID")
    tencent_secret_id: str | None = _env_optional("TENCENT_SECRET_ID")
    tencent_secret_key: str | None = _env_optional("TENCENT_SECRET_KEY")
    tencent_asr_region: str = _env_str("TENCENT_ASR_REGION", "ap-shanghai")
    tencent_asr_engine: str = _env_str("TENCENT_ASR_ENGINE", "16k_zh")
    tencent_asr_timeout_seconds: float = _env_float("TENCENT_ASR_TIMEOUT_SECONDS", "10")
    tencent_realtime_engine: str = _env_str(
        "TENCENT_REALTIME_ENGINE", "16k_zh_en_speaker"
    )
    tencent_realtime_need_vad: int = _env_int("TENCENT_REALTIME_NEED_VAD", "1")
    tencent_realtime_speaker_diarization: int = _env_int(
        "TENCENT_REALTIME_SPEAKER_DIARIZATION", "1"
    )
    tencent_realtime_voice_format: int = _env_int("TENCENT_REALTIME_VOICE_FORMAT", "1")
    tencent_realtime_enable_speaker_context: int = _env_int(
        "TENCENT_REALTIME_ENABLE_SPEAKER_CONTEXT", "1"
    )
    tencent_realtime_speaker_context_id: str | None = _env_optional(
        "TENCENT_REALTIME_SPEAKER_CONTEXT_ID"
    )
    tencent_realtime_chunk_bytes: int = _env_int("TENCENT_REALTIME_CHUNK_BYTES", "6400")
    tencent_realtime_keepalive_seconds: float = _env_float(
        "TENCENT_REALTIME_KEEPALIVE_SECONDS", "4"
    )
    tencent_realtime_expired_seconds: int = _env_int(
        "TENCENT_REALTIME_EXPIRED_SECONDS", "3600"
    )
    minimax_api_key: str | None = _env_optional("MINIMAX_API_KEY")
    minimax_text_model: str = _env_str("MINIMAX_TEXT_MODEL", "MiniMax-M2.7-highspeed")
    minimax_text_base_url: str = _env_str(
        "MINIMAX_TEXT_BASE_URL",
        "https://api.minimaxi.com/v1/text/chatcompletion_v2",
    )
    minimax_text_timeout_seconds: float = _env_float("MINIMAX_TEXT_TIMEOUT_SECONDS", "20")
    minimax_reasoning_enabled: bool = _env_bool("MINIMAX_REASONING_ENABLED", "false")
    minimax_reasoning_model: str = _env_str("MINIMAX_REASONING_MODEL", "MiniMax-M3")
    minimax_reasoning_base_url: str = _env_str(
        "MINIMAX_REASONING_BASE_URL",
        "https://api.minimaxi.com/v1/chat/completions",
    )
    minimax_reasoning_thinking_type: str = _env_str(
        "MINIMAX_REASONING_THINKING_TYPE", "adaptive"
    )
    minimax_reasoning_split: bool = _env_bool("MINIMAX_REASONING_SPLIT", "true")
    minimax_reasoning_timeout_seconds: int = _env_int("MINIMAX_REASONING_TIMEOUT_SECONDS", "600")
    siliconflow_api_key: str | None = _env_optional("SILICONFLOW_API_KEY")
    siliconflow_preview_model: str = _env_str(
        "SILICONFLOW_PREVIEW_MODEL", "inclusionAI/Ling-mini-2.0"
    )
    siliconflow_preview_base_url: str = _env_str(
        "SILICONFLOW_PREVIEW_BASE_URL",
        "https://api.siliconflow.cn/v1/chat/completions",
    )
    siliconflow_preview_timeout_seconds: float = _env_float(
        "SILICONFLOW_PREVIEW_TIMEOUT_SECONDS", "8"
    )
    siliconflow_preview_max_tokens: int = _env_int("SILICONFLOW_PREVIEW_MAX_TOKENS", "50")
    siliconflow_preview_temperature: float = _env_float(
        "SILICONFLOW_PREVIEW_TEMPERATURE", "0.45"
    )
    siliconflow_preview_top_p: float = _env_float("SILICONFLOW_PREVIEW_TOP_P", "0.8")
    siliconflow_preview_top_k: int = _env_int("SILICONFLOW_PREVIEW_TOP_K", "40")
    siliconflow_preview_min_p: float = _env_float("SILICONFLOW_PREVIEW_MIN_P", "0")
    siliconflow_preview_frequency_penalty: float = _env_float(
        "SILICONFLOW_PREVIEW_FREQUENCY_PENALTY", "0.2"
    )
    siliconflow_alias_rewrite_model: str = _env_str(
        "SILICONFLOW_ALIAS_REWRITE_MODEL", "deepseek-ai/DeepSeek-V4-Flash"
    )
    siliconflow_alias_rewrite_timeout_seconds: float = _env_float(
        "SILICONFLOW_ALIAS_REWRITE_TIMEOUT_SECONDS", "30"
    )
    metaso_api_key: str | None = _env_optional("METASO_API_KEY")
    feishu_app_id: str | None = _env_optional("FEISHU_APP_ID")
    feishu_app_secret: str | None = _env_optional("FEISHU_APP_SECRET")
    feishu_bitable_app_token: str | None = _env_optional("FEISHU_BITABLE_APP_TOKEN")
    feishu_bitable_table_id: str | None = _env_optional("FEISHU_BITABLE_TABLE_ID")
    feishu_bitable_base_url: str = _env_str("FEISHU_BITABLE_BASE_URL", "https://your-tenant.feishu.cn/base")
    tencent_flash_asr_enabled: bool = _env_bool("TENCENT_FLASH_ASR_ENABLED", "true")
    tencent_flash_asr_engine: str = _env_str("TENCENT_FLASH_ASR_ENGINE", "16k_zh")
    tencent_flash_asr_speaker_diarization: int = _env_int("TENCENT_FLASH_ASR_SPEAKER_DIARIZATION", "1")
    tencent_flash_asr_timeout_seconds: float = _env_float("TENCENT_FLASH_ASR_TIMEOUT_SECONDS", "180")
    personal_development_audio_retention_days: int = _env_int(
        "PERSONAL_DEVELOPMENT_AUDIO_RETENTION_DAYS",
        "90",
    )
    minimax_tts_model: str = _env_str("MINIMAX_TTS_MODEL", "speech-2.8-hd")
    minimax_tts_voice_id: str = _env_str(
        "MINIMAX_TTS_VOICE_ID",
        "",
    )
    minimax_tts_base_url: str = _env_str(
        "MINIMAX_TTS_BASE_URL",
        "wss://api.minimaxi.com/ws/v1/t2a_v2",
    )
    minimax_tts_timeout_seconds: float = _env_float("MINIMAX_TTS_TIMEOUT_SECONDS", "15")
    minimax_tts_output_dir: str = _env_str("MINIMAX_TTS_OUTPUT_DIR", ".runtime/tts")
    speaker_live_sample_rate: int = _env_int("SPEAKER_LIVE_SAMPLE_RATE", "16000")
    speaker_live_channels: int = _env_int("SPEAKER_LIVE_CHANNELS", "1")
    speaker_live_sample_width_bytes: int = _env_int(
        "SPEAKER_LIVE_SAMPLE_WIDTH_BYTES", "2"
    )
    live_silence_gate_enabled: bool = _env_bool("LIVE_SILENCE_GATE_ENABLED", "1")
    live_silence_gate_frame_ms: int = _env_int("LIVE_SILENCE_GATE_FRAME_MS", "20")
    live_silence_gate_vad_mode: int = _env_int("LIVE_SILENCE_GATE_VAD_MODE", "1")
    live_silence_gate_preroll_ms: int = _env_int("LIVE_SILENCE_GATE_PREROLL_MS", "300")
    live_silence_gate_speech_start_window_ms: int = _env_int(
        "LIVE_SILENCE_GATE_SPEECH_START_WINDOW_MS", "200"
    )
    live_silence_gate_speech_start_voiced_ms: int = _env_int(
        "LIVE_SILENCE_GATE_SPEECH_START_VOICED_MS", "60"
    )
    live_silence_gate_hangover_ms: int = _env_int("LIVE_SILENCE_GATE_HANGOVER_MS", "700")
    live_heartbeat_enabled: bool = _env_bool("LIVE_HEARTBEAT_ENABLED", "1")
    live_heartbeat_min_seconds: float = _env_float("LIVE_HEARTBEAT_MIN_SECONDS", "180")
    live_heartbeat_max_seconds: float = _env_float("LIVE_HEARTBEAT_MAX_SECONDS", "300")
    speaker_live_pyannote_model_id: str = _env_str(
        "SPEAKER_LIVE_PYANNOTE_MODEL_ID",
        "pyannote/speaker-diarization-community-1",
    )
    speaker_live_pyannote_token: str | None = _env_optional("PYANNOTEAI_API_KEY")
    speaker_live_wespeaker_model_name: str = _env_str(
        "SPEAKER_LIVE_WESPEAKER_MODEL_NAME", "chinese"
    )
    speaker_live_wespeaker_home: str | None = _env_optional("WESPEAKER_HOME")
    memory_compaction_token_threshold: int = _env_int(
        "MEMORY_COMPACTION_TOKEN_THRESHOLD", "40000"
    )
    arkham_rules_zip_path: str | None = _env_str(
        "ARKHAM_RULES_ZIP_PATH", r"C:\Clawdspace\arkham-rules.zip"
    )
    arkham_cards_zip_path: str | None = _env_str(
        "ARKHAM_CARDS_ZIP_PATH", r"C:\Clawdspace\arkhamdb-cards.zip"
    )
    assistant_auto_reply_cooldown_seconds: float = _env_float(
        "ASSISTANT_AUTO_REPLY_COOLDOWN_SECONDS", "4"
    )
    speaker_alias_rewrite_poll_interval_seconds: float = _env_float(
        "SPEAKER_ALIAS_REWRITE_POLL_INTERVAL_SECONDS", "300"
    )
    speaker_alias_rewrite_active_window_seconds: float = _env_float(
        "SPEAKER_ALIAS_REWRITE_ACTIVE_WINDOW_SECONDS", "300"
    )


settings = Settings()
