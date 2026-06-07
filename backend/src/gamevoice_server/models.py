from dataclasses import dataclass, field


@dataclass
class TableSession:
    id: str
    name: str
    assistant_name: str = "宝子"
    assistant_name_locked: bool = False
    assistant_personality: str = ""
    assistant_voice_id: str = ""
    origin: str = "manual"
    status: str = "active"
    active_client_count: int = 1
    messages: list[dict] = field(default_factory=list)
    runtime_events: list[dict] = field(default_factory=list)
    assistant_replies: list[dict] = field(default_factory=list)
    live_transcript_slices: dict[str, dict[int, str]] = field(default_factory=dict)
    latest_live_stable_text: str | None = None
    latest_live_stable_speaker_id: str | None = None
    latest_live_stable_speaker_label: str | None = None
    latest_live_speaker_context_id: str | None = None
    latest_live_session_id: str | None = None
    speaker_identities: dict[str, dict] = field(default_factory=dict)
    speaker_identity_state: dict = field(default_factory=dict)
    compaction_summary_event: dict | None = None
    compaction_checkpoint: int = 0
    compaction_version: int = 0
    created_at: str = ""
    last_active_at: str = ""
