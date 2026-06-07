import gamevoice_server.main as main_module
from gamevoice_server.main import session_manager
import time


def test_public_api_token_protects_http_routes_when_configured(client, monkeypatch):
    monkeypatch.setenv("GAMEVOICE_PUBLIC_API_TOKEN", "server-token")

    health = client.get("/health")
    assert health.status_code == 200

    denied = client.get("/tables")
    assert denied.status_code == 401

    wrong = client.get("/tables", headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401

    allowed = client.get("/tables", headers={"Authorization": "Bearer server-token"})
    assert allowed.status_code == 200


def test_create_table_and_fetch(client):
    created = client.post("/tables", json={"name": "Main Table"})
    assert created.status_code == 200
    table_id = created.json()["id"]
    assert created.json()["assistant_name"] == "宝子"

    fetched = client.get(f"/tables/{table_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Main Table"
    assert fetched.json()["assistant_name"] == "宝子"


def test_create_table_persists_assistant_profile_fields(client):
    created = client.post(
        "/tables",
        json={
            "name": "Custom Assistant Table",
            "assistant_name": "小夏",
            "assistant_personality": "温柔但吐槽欲强",
            "assistant_voice_id": "voice-xia",
        },
    )
    assert created.status_code == 200
    table_id = created.json()["id"]

    fetched = client.get(f"/tables/{table_id}")
    assert fetched.status_code == 200
    assert fetched.json()["assistant_name"] == "小夏"
    assert fetched.json()["assistant_personality"] == "温柔但吐槽欲强"
    assert fetched.json()["assistant_voice_id"] == "voice-xia"

    listed = client.get("/tables").json()["tables"]
    listed_item = next(item for item in listed if item["id"] == table_id)
    assert listed_item["assistant_name"] == "小夏"
    assert listed_item["assistant_personality"] == "温柔但吐槽欲强"
    assert listed_item["assistant_voice_id"] == "voice-xia"


def test_list_tables_orders_most_recently_active_first(client):
    older = client.post("/tables", json={"name": "Older History Table"}).json()
    newer = client.post("/tables", json={"name": "Newer History Table"}).json()
    session_manager.append_context_event(
        newer["id"],
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "player: make newer active",
        },
    )

    listed = client.get("/tables").json()["tables"]
    listed_ids = [item["id"] for item in listed]

    assert listed_ids.index(newer["id"]) < listed_ids.index(older["id"])


def test_list_tables_defaults_to_manual_origin_only(client):
    manual = client.post("/tables", json={"name": "Manual History Table"}).json()
    probe = client.post(
        "/tables",
        json={"name": "Replay Probe Table", "origin": "replay"},
    ).json()

    listed = client.get("/tables").json()["tables"]
    listed_ids = [item["id"] for item in listed]

    assert manual["id"] in listed_ids
    assert probe["id"] not in listed_ids

    all_listed = client.get("/tables", params={"include_non_manual": "true"}).json()["tables"]
    all_listed_ids = [item["id"] for item in all_listed]
    assert manual["id"] in all_listed_ids
    assert probe["id"] in all_listed_ids


def test_list_tables_includes_document_stats(client):
    created = client.post("/tables", json={"name": "Stats Docs Table"}).json()
    table_id = created["id"]

    upload = client.post(
        f"/tables/{table_id}/documents",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert upload.status_code == 200

    listed = client.get("/tables").json()["tables"]
    item = next(table for table in listed if table["id"] == table_id)

    assert item["document_count"] == 1
    assert item["document_total_bytes"] == 5


def test_assistant_profile_endpoint_is_read_only_after_table_creation(client):
    created = client.post(
        "/tables",
        json={
            "name": "Profile Table",
            "assistant_name": "宝子",
            "assistant_personality": "稳定人设",
            "assistant_voice_id": "voice-stable",
        },
    )
    table_id = created.json()["id"]

    profile = client.get(f"/tables/{table_id}/assistant-profile")
    assert profile.status_code == 200
    assert profile.json()["assistant_name"] == "宝子"
    assert profile.json()["assistant_personality"] == "稳定人设"
    assert profile.json()["assistant_voice_id"] == "voice-stable"

    updated = client.put(
        f"/tables/{table_id}/assistant-profile",
        json={"assistant_name": "小夏"},
    )
    assert updated.status_code == 409
    assert updated.json()["detail"] == "assistant profile is fixed at table creation"

    profile_after = client.get(f"/tables/{table_id}/assistant-profile")
    assert profile_after.status_code == 200
    assert profile_after.json()["assistant_name"] == "宝子"
    assert profile_after.json()["assistant_personality"] == "稳定人设"
    assert profile_after.json()["assistant_voice_id"] == "voice-stable"


def test_assistant_profile_freezes_after_conversation_starts(client):
    created = client.post("/tables", json={"name": "Frozen Profile Table"})
    table_id = created.json()["id"]

    updated = client.put(
        f"/tables/{table_id}/assistant-profile",
        json={"assistant_name": "小夏"},
    )
    assert updated.status_code == 409

    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "玩家A：我们开始吧",
        },
    )

    frozen = client.put(
        f"/tables/{table_id}/assistant-profile",
        json={"assistant_name": "阿宝"},
    )

    assert frozen.status_code == 409
    assert frozen.json()["detail"] == "assistant profile is fixed at table creation"


def test_speaker_identity_observe_and_link_flow(client):
    created = client.post("/tables", json={"name": "Identity Flow Table"})
    table_id = created.json()["id"]

    observed = client.post(
        f"/tables/{table_id}/speaker-identities/observe",
        json={
            "diarized_speaker_id": "spk_0",
            "embedding": [1.0, 0.0, 0.0],
        },
    )
    assert observed.status_code == 200
    assert observed.json()["speaker_id"] == "player_a"
    assert observed.json()["observation_count"] == 1

    linked = client.post(
        f"/tables/{table_id}/speaker-identities/link",
        json={"speaker_id": "player_a", "linked_name": "马斯克"},
    )
    assert linked.status_code == 200
    assert linked.json()["speaker_id"] == "player_a"
    assert linked.json()["linked_name"] == "马斯克"
    assert linked.json()["bridge_active"] is True

    listed = client.get(f"/tables/{table_id}/speaker-identities")
    assert listed.status_code == 200
    records = listed.json()["speaker_identities"]
    assert records[0]["speaker_id"] == "player_a"
    assert records[0]["linked_name"] == "马斯克"
    assert records[0]["bridge_active"] is True


def test_speaker_identity_ingest_flow(client):
    created = client.post("/tables", json={"name": "Identity Ingest Table"})
    table_id = created.json()["id"]

    ingested = client.post(
        f"/tables/{table_id}/speaker-identities/ingest",
        json={
            "source": "pyannote_wespeaker",
            "session_id": "live-42",
            "observations": [
                {
                    "diarized_speaker_id": "spk_0",
                    "segment_start_ms": 100,
                    "segment_end_ms": 1200,
                    "embedding": [1.0, 0.0, 0.0],
                    "transcript_text": "hello there",
                },
                {
                    "diarized_speaker_id": "spk_9",
                    "segment_start_ms": 1300,
                    "segment_end_ms": 2500,
                    "embedding": [0.95, 0.05, 0.0],
                    "transcript_text": "same person again",
                    "candidate_name": "Musk",
                    "candidate_confidence": 0.93,
                },
            ],
        },
    )

    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["ingested_count"] == 2
    assert payload["observations"][0]["speaker_id"] == "player_a"
    assert payload["observations"][1]["speaker_id"] == "player_a"
    assert payload["observations"][0]["source"] == "pyannote_wespeaker"
    assert payload["observations"][0]["session_id"] == "live-42"
    assert payload["speaker_identities"][0]["speaker_id"] == "player_a"
    assert payload["speaker_identities"][0]["observation_count"] == 2
    assert payload["speaker_identities"][0]["name_hints"][0]["name"] == "Musk"


def test_speaker_identity_ingest_pipeline_flow(client):
    created = client.post("/tables", json={"name": "Identity Pipeline Table"})
    table_id = created.json()["id"]

    ingested = client.post(
        f"/tables/{table_id}/speaker-identities/ingest",
        json={
            "source": "pyannote_wespeaker",
            "session_id": "live-77",
            "diarization_segments": [
                {
                    "segment_id": "seg-1",
                    "diarized_speaker_id": "SPEAKER_00",
                    "segment_start_ms": 0,
                    "segment_end_ms": 1000,
                    "transcript_text": "hello there",
                    "channel": 0,
                },
                {
                    "segment_id": "seg-2",
                    "diarized_speaker_id": "SPEAKER_00",
                    "segment_start_ms": 1010,
                    "segment_end_ms": 1800,
                    "transcript_text": "same speaker",
                    "channel": 0,
                },
            ],
            "speaker_embeddings": [
                {
                    "diarized_speaker_id": "SPEAKER_00",
                    "embedding": [1.0, 0.0, 0.0],
                    "sample_count": 3,
                }
            ],
            "name_candidates": [
                {
                    "diarized_speaker_id": "SPEAKER_00",
                    "candidate_name": "Musk",
                    "candidate_confidence": 0.91,
                }
            ],
        },
    )

    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["ingested_count"] == 2
    assert payload["observations"][0]["segment_id"] == "seg-1"
    assert payload["speaker_identities"][0]["speaker_id"] == "player_a"
    assert payload["speaker_identities"][0]["name_hints"][0]["name"] == "Musk"


def test_speaker_identity_ingest_pipeline_flow_supports_speaker_profile_ids(client):
    created = client.post("/tables", json={"name": "Identity Profile Pipeline Table"})
    table_id = created.json()["id"]

    ingested = client.post(
        f"/tables/{table_id}/speaker-identities/ingest",
        json={
            "source": "pyannote_wespeaker",
            "session_id": "live-88",
            "diarization_segments": [
                {
                    "segment_id": "seg-1",
                    "diarized_speaker_id": "SPEAKER_00",
                    "speaker_profile_id": "profile-1",
                    "segment_start_ms": 0,
                    "segment_end_ms": 1000,
                    "transcript_text": "hello there",
                },
                {
                    "segment_id": "seg-2",
                    "diarized_speaker_id": "SPEAKER_99",
                    "speaker_profile_id": "profile-1",
                    "segment_start_ms": 1010,
                    "segment_end_ms": 1800,
                    "transcript_text": "same speaker",
                },
            ],
            "speaker_embeddings": [
                {
                    "speaker_profile_id": "profile-1",
                    "embedding": [1.0, 0.0, 0.0],
                    "sample_count": 5,
                }
            ],
            "name_candidates": [
                {
                    "speaker_profile_id": "profile-1",
                    "candidate_name": "Musk",
                    "candidate_confidence": 0.92,
                }
            ],
        },
    )

    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["speaker_identities"][0]["speaker_id"] == "player_a"
    assert payload["speaker_identities"][0]["observation_count"] == 2
    assert payload["speaker_identities"][0]["name_hints"][0]["name"] == "Musk"


def test_speaker_identity_ingest_pyannote_and_wespeaker_aliases_flow(client):
    created = client.post("/tables", json={"name": "Identity Alias Pipeline Table"})
    table_id = created.json()["id"]

    ingested = client.post(
        f"/tables/{table_id}/speaker-identities/ingest",
        json={
            "source": "pyannote_wespeaker",
            "session_id": "live-alias-1",
            "pyannote_segments": [
                {
                    "segment_id": "seg-1",
                    "speaker": "SPEAKER_00",
                    "speaker_profile_id": "profile-1",
                    "start": 0.25,
                    "end": 1.2,
                    "text": "I am Musk",
                    "confidence": 0.88,
                }
            ],
            "wespeaker_embeddings": [
                {
                    "speaker_profile_id": "profile-1",
                    "embedding": [1.0, 0.0, 0.0],
                    "sample_count": 2,
                }
            ],
            "name_candidates": [
                {
                    "speaker_profile_id": "profile-1",
                    "candidate_name": "Musk",
                    "candidate_confidence": 0.93,
                }
            ],
        },
    )

    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["observations"][0]["diarized_speaker_id"] == "SPEAKER_00"
    assert payload["observations"][0]["segment_start_ms"] == 250
    assert payload["observations"][0]["speaker_profile_id"] == "profile-1"
    assert payload["speaker_identities"][0]["speaker_id"] == "player_a"
    assert payload["speaker_identities"][0]["name_hints"][0]["name"] == "Musk"


def test_speaker_identity_live_ingest_pipeline_flow(client):
    created = client.post("/tables", json={"name": "Identity Live Pipeline Table"})
    table_id = created.json()["id"]

    ingested = client.post(
        f"/tables/{table_id}/speaker-identities/live-ingest",
        json={
            "source": "pyannote_wespeaker",
            "live_session_id": "live-session-1",
            "pyannote_segments": [
                {
                    "segment_id": "seg-1",
                    "speaker": "SPEAKER_01",
                    "speaker_profile": "profile-01",
                    "start": 0.0,
                    "end": 1.1,
                    "text": "I am Nova",
                    "confidence": 0.93,
                    "channel": 0,
                }
            ],
                "wespeaker_embeddings": [
                    {
                        "speaker_profile": "profile-01",
                        "vector": [0.9, 0.1, 0.0],
                        "sample_count": 3,
                    }
                ],
                "name_candidates": [
                    {
                        "speaker_profile_id": "profile-01",
                        "candidate_name": "Nova",
                        "candidate_confidence": 0.94,
                    }
                ],
            },
        )

    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["live_session_id"] == "live-session-1"
    assert payload["live_session_state"]["audio_chunk_count"] == 0
    assert payload["speaker_identity_batch"]["observations"][0]["speaker_id"] == "player_a"
    assert payload["speaker_identity_batch"]["speaker_identities"][0]["name_hints"][0]["name"] == "Nova"

    live_session = client.get(
        f"/tables/{table_id}/speaker-identities/live-sessions/live-session-1"
    )
    assert live_session.status_code == 200
    assert live_session.json()["ingested_batch_count"] == 1


def test_speaker_identity_live_audio_chunk_pull_flow(client):
    created = client.post("/tables", json={"name": "Identity Live Audio Table"})
    table_id = created.json()["id"]

    client.post(
        f"/tables/{table_id}/speaker-identities/live-ingest",
        json={
            "source": "pyannote_wespeaker",
            "live_session_id": "live-session-2",
            "observations": [
                {
                    "diarized_speaker_id": "SPEAKER_01",
                    "segment_start_ms": 0,
                    "segment_end_ms": 120,
                    "transcript_text": "hello",
                }
            ],
        },
    )
    audio = client.get(
        f"/tables/{table_id}/speaker-identities/live-sessions/live-session-2/audio-chunks"
    )
    assert audio.status_code == 200
    assert audio.json()["chunks"] == []
    state = client.get(
        f"/tables/{table_id}/speaker-identities/live-sessions/live-session-2"
    )
    assert state.status_code == 200
    assert state.json()["pending_audio_chunk_count"] == 0


def test_speaker_identity_ingest_pipeline_flow_auto_links_and_persists_name(client):
    created = client.post("/tables", json={"name": "Identity Auto Link Pipeline Table"})
    table_id = created.json()["id"]

    payload = {
        "source": "pyannote_wespeaker",
        "session_id": "live-auto-1",
        "diarization_segments": [
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 0,
                "segment_end_ms": 1000,
                "transcript_text": "I am Musk",
            },
            {
                "segment_id": "seg-2",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 1010,
                "segment_end_ms": 1800,
                "transcript_text": "still Musk",
            },
        ],
        "name_candidates": [
            {
                "diarized_speaker_id": "SPEAKER_00",
                "candidate_name": "Musk",
                "candidate_confidence": 0.99,
            }
        ],
    }

    first = client.post(f"/tables/{table_id}/speaker-identities/ingest", json=payload)
    second = client.post(f"/tables/{table_id}/speaker-identities/ingest", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    payload = second.json()
    assert payload["speaker_identities"][0]["linked_name"] == "Musk"
    assert payload["speaker_identities"][0]["bridge_active"] is True
    assert payload["speaker_identities"][0]["name_link_source"] == "auto_text_hint"


def test_speaker_identity_review_candidates_and_accept_override_flow(client):
    created = client.post("/tables", json={"name": "Identity Review Table"})
    table_id = created.json()["id"]

    main_module.session_manager.observe_speaker_identity(
        table_id,
        {
            "speaker_id": "player_a",
            "status": "linked",
            "display_label": "玩家A",
            "linked_name": "Musk",
            "bridge_active": True,
            "name_hints": [
                {"name": "Elon", "count": 3, "confidence_max": 0.98},
                {"name": "Musk", "count": 2, "confidence_max": 0.90},
            ],
            "name_link_override_suggested": True,
            "name_link_override_candidate": "Elon",
            "name_link_override_reason": "competing_hint_stronger_than_existing_link",
            "name_link_override_confidence": 0.98,
            "name_link_override_count": 3,
            "name_link_override_score": 0.91,
        },
    )

    review = client.get(f"/tables/{table_id}/speaker-identities/review")
    assert review.status_code == 200
    assert review.json()["speaker_identity_review_candidates"][0]["speaker_id"] == "player_a"
    assert review.json()["speaker_identity_review_candidates"][0]["name_link_override_candidate"] == "Elon"

    accepted = client.post(
        f"/tables/{table_id}/speaker-identities/review/accept",
        json={"speaker_id": "player_a", "linked_name": "Elon"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["linked_name"] == "Elon"
    assert accepted.json()["name_link_source"] == "review_override"


def test_speaker_identity_alias_map_query_returns_current_pool_and_state(client):
    created = client.post("/tables", json={"name": "Alias Map Table"})
    table_id = created.json()["id"]

    main_module.session_manager.observe_speaker_identity(
        table_id,
        {
            "speaker_id": "player_a",
            "status": "linked",
            "display_label": "Player A",
            "linked_name": "Musk",
            "aliases": ["Musk", "Fat Tiger"],
        },
    )
    main_module.session_manager.observe_speaker_identity(
        table_id,
        {
            "speaker_id": "player_b",
            "status": "anonymous",
            "display_label": "Player B",
            "aliases": ["Dale"],
        },
    )

    response = client.get(f"/tables/{table_id}/speaker-identities/alias-map")
    assert response.status_code == 200
    payload = response.json()
    assert payload["speaker_alias_map"] == {
        "player_a": ["宝宝", "Musk", "Fat Tiger"],
        "player_b": ["宝宝", "Dale"],
    }
    assert payload["active_speaker_ids"] == ["player_a", "player_b"]
    assert payload["alias_rewrite_state"] is None



def test_companion_interrupt_uses_custom_assistant_name_trigger(client):
    created = client.post(
        "/tables",
        json={
            "name": "Name Trigger Table",
            "assistant_name": "小夏",
        },
    )
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "小夏，给点建议",
        },
    )

    response = client.post(f"/tables/{table_id}/companion/interrupt")

    assert response.status_code == 200
    assert response.json()["interrupt"] is True
    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    decisions = [
        item
        for item in runtime_events.json()["events"]
        if item.get("kind") == "assistant_turn_decision"
    ]
    assert decisions
    assert decisions[-1]["reason"] == "assistant_name_called"


def test_upload_list_and_read_document(client):
    created = client.post("/tables", json={"name": "Docs Table"})
    table_id = created.json()["id"]

    upload = client.post(
        f"/tables/{table_id}/documents",
        files=[
            ("files", ("b-script.txt", b"scene b", "text/plain")),
            ("files", ("a-script.txt", b"scene a", "text/plain")),
        ],
    )
    assert upload.status_code == 200
    assert upload.json()["notifications"] == 1
    assert [item["filename"] for item in upload.json()["records"]] == ["a-script.txt", "b-script.txt"]
    assert "2" in upload.json()["message"]

    listed = client.get(f"/tables/{table_id}/documents")
    assert listed.status_code == 200
    assert [item["filename"] for item in listed.json()["documents"]] == ["a-script.txt", "b-script.txt"]

    read = client.get(f"/tables/{table_id}/documents/a-script/read")
    assert read.status_code == 200
    assert read.json()["kind"] == "document_summary"
    assert read.json()["mode"] == "summary"


def test_upload_document_emits_ack_runtime_event_and_main_context_fact(client):
    created = client.post("/tables", json={"name": "Docs Ack Table"})
    table_id = created.json()["id"]

    upload = client.post(
        f"/tables/{table_id}/documents",
        files=[("files", ("attention.txt", b"focus notes", "text/plain"))],
    )

    assert upload.status_code == 200
    assert "attention.txt" in upload.json()["message"]

    context = client.get(f"/tables/{table_id}/context")
    assert context.status_code == 200
    context_events = context.json()["events"]
    assert len(context_events) == 1
    assert context_events[0]["kind"] == "document_upload_fact"
    assert context_events[0]["source"] == "document_upload"
    assert context_events[0]["filenames"] == ["attention.txt"]
    assert "attention.txt" in context_events[0]["content"]
    assert "这个文件" in context_events[0]["content"]

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    assert runtime_events.status_code == 200
    upload_acks = [
        item
        for item in runtime_events.json()["events"]
        if item.get("kind") == "document_upload_ack"
    ]
    assert upload_acks
    assert upload_acks[-1]["content"] == upload.json()["message"]
    assert upload_acks[-1]["filenames"] == ["attention.txt"]


def test_debug_memory_compact_text_uses_memory_compactor(client):
    original_compactor = main_module.memory_compactor

    class StubCompactor:
        def compact(self, payload: dict) -> dict:
            assert payload["previous_summary"] == "old summary"
            assert payload["active_events"][0]["kind"] == "document_test"
            assert payload["active_events"][0]["source"] == "debug"
            assert payload["active_events"][0]["content"] == "long source text"
            return {
                "status": "compacted",
                "summary_text": "compressed output",
                "metadata": {"input_event_count": 1},
            }

    main_module.memory_compactor = StubCompactor()
    try:
        response = client.post(
            "/debug/memory/compact-text",
            json={"text": "long source text", "previous_summary": "old summary"},
        )
    finally:
        main_module.memory_compactor = original_compactor

    assert response.status_code == 200
    assert response.json()["status"] == "compacted"
    assert response.json()["summary_text"] == "compressed output"
    assert response.json()["metadata"]["input_event_count"] == 1


def test_upload_pdf_persists_for_skillagent_search(client):
    created = client.post("/tables", json={"name": "PDF Table"})
    table_id = created.json()["id"]

    upload = client.post(
        f"/tables/{table_id}/documents",
        files=[("files", ("script.pdf", b"%PDF", "application/pdf"))],
    )
    assert upload.status_code == 200
    assert [item["filename"] for item in upload.json()["records"]] == ["script.pdf"]

    listed = client.get(f"/tables/{table_id}/documents")
    assert listed.status_code == 200
    assert listed.json()["documents"][0]["filename"] == "script.pdf"


def test_original_read_mode_is_supported(client):
    created = client.post("/tables", json={"name": "Read Table"})
    table_id = created.json()["id"]

    upload = client.post(
        f"/tables/{table_id}/documents",
        files=[("files", ("chapter-1.txt", b"full scenario text", "text/plain"))],
    )
    assert upload.status_code == 200

    read = client.get(f"/tables/{table_id}/documents/chapter-1/read?mode=original")
    assert read.status_code == 200
    assert read.json()["kind"] == "document_original"
    assert read.json()["mode"] == "original"


def test_delete_document_removes_only_that_table_file(client):
    first = client.post("/tables", json={"name": "Delete Docs One"}).json()
    second = client.post("/tables", json={"name": "Delete Docs Two"}).json()
    for table in (first, second):
        upload = client.post(
            f"/tables/{table['id']}/documents",
            files=[("files", ("shared.txt", b"payload", "text/plain"))],
        )
        assert upload.status_code == 200

    delete = client.delete(f"/tables/{first['id']}/documents/shared.txt")

    assert delete.status_code == 200
    assert delete.json()["filename"] == "shared.txt"
    assert client.get(f"/tables/{first['id']}/documents").json()["documents"] == []
    assert [
        item["filename"]
        for item in client.get(f"/tables/{second['id']}/documents").json()["documents"]
    ] == ["shared.txt"]


def test_upload_voice_clip_returns_placeholder_transcript(client):
    created = client.post("/tables", json={"name": "Voice Table"})
    table_id = created.json()["id"]

    upload = client.post(
        f"/tables/{table_id}/audio-clips",
        files=[("clip", ("round-1.wav", b"voice-bytes", "audio/wav"))],
    )

    assert upload.status_code == 200
    assert upload.json()["kind"] == "voice_transcript"
    assert upload.json()["filename"] == "round-1.wav"
    assert upload.json()["content"]

    context = client.get(f"/tables/{table_id}/context")
    assert context.status_code == 200
    assert context.json()["events"][0]["kind"] == "voice_transcript"
    assert context.json()["events"][0]["source"] == "voice_clip"
    assert context.json()["events"][0]["content"] == f"宝宝：{upload.json()['content']}"


def test_companion_next_reads_table_context(client):
    created = client.post("/tables", json={"name": "Companion Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "deal with this enemy first",
        },
    )

    response = client.get(f"/tables/{table_id}/companion/next")

    assert response.status_code == 200
    assert response.json()["mode"] == "conversation"
    assert response.json()["transcript"] == "deal with this enemy first"


def test_companion_interrupt_runs_tts_for_rule_context(client):
    created = client.post("/tables", json={"name": "Interrupt Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "the rules feel wrong here",
        },
    )

    response = client.post(f"/tables/{table_id}/companion/interrupt")

    assert response.status_code == 200
    assert response.json()["interrupt"] is True
    assert response.json()["speech_job"]["accepted"] is True
    assert response.json()["tts_stream"]["job_id"] == response.json()["speech_job"]["job_id"]
    assert response.json()["tts_stream"]["stream_id"]

    context = client.get(f"/tables/{table_id}/context")
    assert context.status_code == 200
    assert [item for item in context.json()["events"] if item.get("kind") == "assistant_reply"] == []

    runtime = client.get(f"/tables/{table_id}/runtime/events")
    assert runtime.status_code == 200
    runtime_events = [
        item
        for item in runtime.json()["events"]
        if item.get("kind") in {"assistant_ready", "assistant_segments_planned", "assistant_speaking"}
    ]
    assert [item["kind"] for item in runtime_events[-2:]] == [
        "assistant_ready",
        "assistant_segments_planned",
    ]

    tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
    assert tts_jobs.status_code == 200
    assert len(tts_jobs.json()["jobs"]) >= 1
    assert any(job["job_id"] == response.json()["speech_job"]["job_id"] for job in tts_jobs.json()["jobs"])
    assert all(job["content"] for job in tts_jobs.json()["jobs"])
    assert all(job["segment_count"] >= 1 for job in tts_jobs.json()["jobs"])
    assert all(
        len(job["segment_statuses"]) == job["segment_count"]
        for job in tts_jobs.json()["jobs"]
    )

    latest_audio = client.get(f"/tables/{table_id}/tts-jobs/latest/audio")
    assert latest_audio.status_code == 200
    assert latest_audio.content


class FakeProgressiveTtsAdapter:
    def prepare_job(self, text: str, *, reply: dict | None = None, turn_id: str | None = None, reply_id: str | None = None, voice_id: str | None = None) -> dict:
        return {
            "accepted": True,
            "job_id": "job-progressive-1",
            "turn_id": turn_id,
            "reply_id": reply_id,
            "status": "preparing",
            "text": text,
            "segments": ["先接一句。", "后面补完整。"],
            "segment_count": 2,
            "segment_statuses": [
                {
                    "index": 0,
                    "text": "先接一句。",
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 0,
                    "output_path": ".runtime/tts/job-progressive-1-segment-0.mp3",
                },
                {
                    "index": 1,
                    "text": "后面补完整。",
                    "status": "queued",
                    "format": "mp3",
                    "bytes": 0,
                    "output_path": ".runtime/tts/job-progressive-1-segment-1.mp3",
                },
            ],
            "format": "mp3",
            "output_path": ".runtime/tts/job-progressive-1.mp3",
            "bytes": 0,
        }

    def stream_job_audio(self, speech_job: dict, *, on_segment_audio) -> None:
        on_segment_audio(segment_index=0, text="先接一句。", audio_bytes=b"\x01\x02", format_name="mp3")
        on_segment_audio(segment_index=1, text="后面补完整。", audio_bytes=b"\x03\x04", format_name="mp3")


class DelayedProgressiveTtsAdapter(FakeProgressiveTtsAdapter):
    def stream_job_audio(self, speech_job: dict, *, on_segment_audio) -> None:
        time.sleep(0.05)
        super().stream_job_audio(speech_job, on_segment_audio=on_segment_audio)


def test_companion_interrupt_can_return_progressive_tts_stream_before_full_job_is_materialized(
    client,
    monkeypatch,
):
    created = client.post("/tables", json={"name": "Progressive TTS Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )
    original_adapter = main_module.auto_interrupt_service.tts_adapter
    monkeypatch.setattr(main_module.auto_interrupt_service, "tts_adapter", FakeProgressiveTtsAdapter())
    try:
        response = client.post(f"/tables/{table_id}/companion/interrupt")

        assert response.status_code == 200
        assert response.json()["interrupt"] is True
        assert response.json()["tts_stream"]["job_id"] == "job-progressive-1"
        stream_id = response.json()["tts_stream"]["stream_id"]
        first = client.get(f"/tables/{table_id}/tts-streams/{stream_id}/next")
        second = client.get(f"/tables/{table_id}/tts-streams/{stream_id}/next")

        assert first.status_code == 200
        assert first.json()["segment_index"] == 0
        assert second.status_code == 200
        assert second.json()["segment_index"] == 1
        assert second.json()["is_final"] is True
    finally:
        monkeypatch.setattr(main_module.auto_interrupt_service, "tts_adapter", original_adapter)


def test_progressive_tts_next_chunk_waits_for_first_audio_before_returning(
    client,
    monkeypatch,
):
    created = client.post("/tables", json={"name": "Delayed Progressive TTS Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )
    original_adapter = main_module.auto_interrupt_service.tts_adapter
    monkeypatch.setattr(main_module.auto_interrupt_service, "tts_adapter", DelayedProgressiveTtsAdapter())
    try:
        response = client.post(f"/tables/{table_id}/companion/interrupt")
        assert response.status_code == 200
        stream_id = response.json()["tts_stream"]["stream_id"]

        first = client.get(f"/tables/{table_id}/tts-streams/{stream_id}/next")

        assert first.status_code == 200
        assert first.json()["segment_index"] == 0
    finally:
        monkeypatch.setattr(main_module.auto_interrupt_service, "tts_adapter", original_adapter)


def test_companion_interrupt_answers_remote_rule_context_in_main_dialogue(client):
    created = client.post("/tables", json={"name": "Async Serious Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule seems wrong",
        },
    )

    response = client.post(f"/tables/{table_id}/companion/interrupt")

    assert response.status_code == 200
    assert response.json()["interrupt"] is True
    assert response.json()["mode"] == "conversation"
    assert response.json()["reply"]["source"] == "companion"
    assert response.json()["analysis_needed"] is False
    assert response.json()["analysis_query"] is None

    context = client.get(f"/tables/{table_id}/context")
    kinds = [item["kind"] for item in context.json()["events"]]
    assert "assistant_rule_analysis_requested" not in kinds
    assert "assistant_rule_analysis_completed" not in kinds

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    runtime_kinds = [item["kind"] for item in runtime_events.json()["events"]]
    assert "assistant_rule_analysis_requested" not in runtime_kinds
    assert "assistant_rule_analysis_completed" not in runtime_kinds


def test_runtime_state_tracks_assistant_ready_after_interrupt(client):
    created = client.post("/tables", json={"name": "Runtime Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")

    assert interrupt.status_code == 200
    assert interrupt.json()["interrupt"] is True

    runtime = client.get(f"/tables/{table_id}/runtime/state")
    assert runtime.status_code == 200
    assert runtime.json()["state"] == "assistant_ready"
    assert runtime.json()["is_agent_speaking"] is False


def test_runtime_events_endpoint_returns_only_runtime_trace(client):
    created = client.post("/tables", json={"name": "Runtime Events Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "这个规则不太对啊",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]
    client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
    client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/completed")

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")

    assert runtime_events.status_code == 200
    kinds = [item["kind"] for item in runtime_events.json()["events"]]
    assert "voice_transcript" not in kinds
    assert "assistant_turn_decision" in kinds
    assert "assistant_ready" in kinds
    assert "assistant_segment_started" in kinds
    assert "assistant_segment_completed" in kinds
    assert "assistant_played" in kinds


def test_tts_job_can_be_marked_interrupted(client):
    created = client.post("/tables", json={"name": "BargeIn Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    stopped = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/interrupt")

    assert stopped.status_code == 200
    assert stopped.json()["job"]["status"] == "interrupted"
    assert stopped.json()["job"]["tts_input_chars_total"] >= 0
    assert stopped.json()["job"]["tts_wasted_chars_on_interrupt"] >= 0
    assert stopped.json()["job"]["tts_wasted_chunk_count_on_interrupt"] >= 0

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    assert runtime_events.status_code == 200
    cancelled_events = [
        item for item in runtime_events.json()["events"] if item.get("kind") == "assistant_reply_cancelled"
    ]
    assert cancelled_events
    assert cancelled_events[-1]["tts_input_chars_total"] == stopped.json()["job"]["tts_input_chars_total"]
    assert (
        cancelled_events[-1]["tts_wasted_chars_on_interrupt"]
        == stopped.json()["job"]["tts_wasted_chars_on_interrupt"]
    )


def test_tts_job_can_be_marked_played(client):
    created = client.post("/tables", json={"name": "Playback Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    played = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/played")

    assert played.status_code == 200
    assert played.json()["job"]["status"] == "played"

    runtime = client.get(f"/tables/{table_id}/runtime/state")
    assert runtime.status_code == 200
    assert runtime.json()["is_agent_speaking"] is False
    assert runtime.json()["last_completed_job_id"] == job_id
    assert runtime.json()["state"] in {"listening", "assistant_ready"}

    context = client.get(f"/tables/{table_id}/context")
    spoken_events = [
        item for item in context.json()["events"] if item.get("kind") == "assistant_spoken"
    ]
    assert len(spoken_events) == 1
    assert spoken_events[0]["job_id"] == job_id

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    played_events = [
        item for item in runtime_events.json()["events"] if item.get("kind") == "assistant_played"
    ]
    assert len(played_events) == 1
    assert played_events[0]["job_id"] == job_id


def test_tts_job_segment_status_can_be_marked_started_and_completed(client):
    created = client.post("/tables", json={"name": "Segment Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this rule feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
    completed = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/completed")

    assert started.status_code == 200
    assert started.json()["segment"]["status"] == "playing"
    assert completed.status_code == 200
    assert completed.json()["segment"]["status"] == "completed"

    tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
    segment_statuses = tts_jobs.json()["jobs"][0]["segment_statuses"]
    assert segment_statuses[0]["status"] == "completed"


def test_late_segment_update_after_played_does_not_revive_runtime(client):
    created = client.post("/tables", json={"name": "Late Segment Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    speech_job = interrupt.json()["speech_job"]
    job_id = speech_job["job_id"]

    played = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/played")
    late_started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
    late_completed = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/completed")

    assert played.status_code == 200
    assert late_started.status_code == 200
    assert late_completed.status_code == 200
    assert late_started.json()["segment"]["status"] == "completed"
    assert late_completed.json()["segment"]["status"] == "completed"

    runtime = client.get(f"/tables/{table_id}/runtime/state")
    assert runtime.status_code == 200
    assert runtime.json()["is_agent_speaking"] is False
    assert runtime.json()["state"] in {"listening", "assistant_ready"}
    assert runtime.json()["last_completed_job_id"] == job_id


def test_final_segment_completion_auto_marks_job_played(client):
    created = client.post("/tables", json={"name": "Segment Auto Finish Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    speech_job = interrupt.json()["speech_job"]
    job_id = speech_job["job_id"]

    for segment in speech_job["segment_statuses"]:
        started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/{segment['index']}/started")
        completed = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/{segment['index']}/completed")
        assert started.status_code == 200
        assert completed.status_code == 200

    tts_jobs = client.get(f"/tables/{table_id}/tts-jobs")
    assert tts_jobs.status_code == 200
    assert tts_jobs.json()["jobs"][0]["status"] == "played"
    assert all(
        item["status"] == "completed" for item in tts_jobs.json()["jobs"][0]["segment_statuses"]
    )

    runtime = client.get(f"/tables/{table_id}/runtime/state")
    assert runtime.status_code == 200
    assert runtime.json()["is_agent_speaking"] is False
    assert runtime.json()["last_completed_job_id"] == job_id
    assert runtime.json()["state"] in {"listening", "assistant_ready"}

    context = client.get(f"/tables/{table_id}/context")
    spoken_events = [item for item in context.json()["events"] if item.get("kind") == "assistant_spoken"]
    assert len(spoken_events) == 1
    assert spoken_events[0]["job_id"] == job_id

    runtime_events = client.get(f"/tables/{table_id}/runtime/events")
    played_events = [item for item in runtime_events.json()["events"] if item.get("kind") == "assistant_played"]
    assert len(played_events) == 1
    assert played_events[0]["job_id"] == job_id


def test_segment_completion_and_explicit_played_do_not_duplicate_played_event(client):
    created = client.post("/tables", json={"name": "Played Dedup Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )
    reply = client.post(f"/tables/{table_id}/companion/interrupt")
    assert reply.status_code == 200
    job_id = reply.json()["speech_job"]["job_id"]

    completed = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/completed")
    assert completed.status_code == 200
    played = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/played")
    assert played.status_code == 200

    runtime_events = client.get(f"/tables/{table_id}/runtime/events").json()["events"]
    played_events = [
        item for item in runtime_events if item.get("kind") == "assistant_played" and item.get("job_id") == job_id
    ]
    assert len(played_events) == 1


def test_tts_job_segments_can_be_listed_independently(client):
    created = client.post("/tables", json={"name": "Segment Queue Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    segments = client.get(f"/tables/{table_id}/tts-jobs/{job_id}/segments")

    assert segments.status_code == 200
    assert segments.json()["job_id"] == job_id
    assert segments.json()["segment_count"] >= 1
    assert len(segments.json()["segments"]) == segments.json()["segment_count"]
    assert all(item["status"] == "queued" for item in segments.json()["segments"])
    assert all(item["output_path"] for item in segments.json()["segments"])


def test_tts_job_segment_audio_can_be_fetched_independently(client):
    created = client.post("/tables", json={"name": "Segment Audio Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    audio = client.get(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/audio")

    assert audio.status_code == 200
    assert audio.content


def test_tts_job_next_segment_endpoint_tracks_queue_progress(client):
    created = client.post("/tables", json={"name": "Next Segment Table"})
    table_id = created.json()["id"]
    session_manager.append_assistant_reply(
        table_id,
        {
            "kind": "assistant_reply",
            "source": "companion",
            "mode": "chatty",
            "content": "第一句。第二句。",
            "speech_job": {
                "accepted": True,
                "job_id": "job-next-1",
                "status": "ready",
                "segment_count": 2,
                "segment_statuses": [
                    {
                        "index": 0,
                        "text": "第一句。",
                        "status": "queued",
                        "format": "mp3",
                        "bytes": 3,
                        "output_path": "fake-0.mp3",
                    },
                    {
                        "index": 1,
                        "text": "第二句。",
                        "status": "queued",
                        "format": "mp3",
                        "bytes": 3,
                        "output_path": "fake-1.mp3",
                    },
                ],
            },
        },
    )
    job_id = "job-next-1"

    first = client.get(f"/tables/{table_id}/tts-jobs/{job_id}/segments/next")
    assert first.status_code == 200
    assert first.json()["segment"]["index"] == 0

    client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/completed")

    second = client.get(f"/tables/{table_id}/tts-jobs/{job_id}/segments/next")
    assert second.status_code == 200
    assert second.json()["segment"]["index"] == 1


def test_tts_job_interrupted_does_not_commit_spoken_context(client):
    created = client.post("/tables", json={"name": "Interrupted Playback Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    stopped = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/interrupt")

    assert stopped.status_code == 200
    context = client.get(f"/tables/{table_id}/context")
    spoken_events = [
        item for item in context.json()["events"] if item.get("kind") == "assistant_spoken"
    ]
    assert spoken_events == []


def test_tts_job_interrupt_marks_active_segment_interrupted(client):
    created = client.post("/tables", json={"name": "Interrupted Segment Table"})
    table_id = created.json()["id"]
    session_manager.append_context_event(
        table_id,
        {
            "kind": "voice_transcript",
            "source": "live_asr",
            "content": "this timing feels wrong",
        },
    )

    interrupt = client.post(f"/tables/{table_id}/companion/interrupt")
    job_id = interrupt.json()["speech_job"]["job_id"]

    started = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/segments/0/started")
    assert started.status_code == 200

    stopped = client.post(f"/tables/{table_id}/tts-jobs/{job_id}/interrupt")

    assert stopped.status_code == 200
    assert stopped.json()["job"]["status"] == "interrupted"
    assert stopped.json()["job"]["segment_statuses"][0]["status"] == "interrupted"


def test_mobile_diagnostics_endpoint_appends_and_lists_entries(client):
    created = client.post("/tables", json={"name": "Mobile Diagnostics Table"})
    table_id = created.json()["id"]

    posted = client.post(
        f"/tables/{table_id}/mobile-diagnostics",
        json={
            "entries": [
                {
                    "ts": "2026-05-10T10:40:00.000Z",
                    "session_id": "live-session-1",
                    "component": "table_shell",
                    "event": "live_start_requested",
                    "details": {"route": "table_shell"},
                },
                {
                    "ts": "2026-05-10T10:40:00.120Z",
                    "session_id": "live-session-1",
                    "component": "ws",
                    "event": "audio_chunk_sent",
                    "details": {"bytes": 1280, "chunk": 1},
                },
            ]
        },
    )

    assert posted.status_code == 200
    assert posted.json()["accepted"] == 2

    fetched = client.get(f"/tables/{table_id}/mobile-diagnostics")
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["count"] == 2
    assert payload["entries"][0]["component"] == "table_shell"
    assert payload["entries"][1]["details"]["bytes"] == 1280
