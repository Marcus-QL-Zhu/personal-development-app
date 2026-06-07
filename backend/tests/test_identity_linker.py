from gamevoice_server.identity_linker import IdentityLinker


def test_identity_linker_starts_with_anonymous_speakers():
    speakers = IdentityLinker().bootstrap(["speaker_1", "speaker_2", "speaker_3", "speaker_4"])
    assert speakers[0]["status"] == "anonymous"


def test_identity_linker_assigns_same_diarized_speaker_to_same_player_slot():
    linker = IdentityLinker()
    state = {}

    first = linker.observe(
        state,
        diarized_speaker_id="spk_0",
        embedding=[1.0, 0.0, 0.0],
    )
    second = linker.observe(
        state,
        diarized_speaker_id="spk_0",
        embedding=[0.99, 0.01, 0.0],
    )

    assert first["speaker_id"] == "player_a"
    assert second["speaker_id"] == "player_a"
    assert second["observation_count"] == 2


def test_identity_linker_reuses_player_slot_for_similar_embedding():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    first = linker.observe(
        state,
        diarized_speaker_id="spk_0",
        embedding=[1.0, 0.0, 0.0],
    )
    second = linker.observe(
        state,
        diarized_speaker_id="spk_9",
        embedding=[0.95, 0.05, 0.0],
    )

    assert first["speaker_id"] == "player_a"
    assert second["speaker_id"] == "player_a"
    assert sorted(second["diarized_speaker_ids"]) == ["spk_0", "spk_9"]


def test_identity_linker_ingests_segment_batch_and_records_recent_segments():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_segments(
        state,
        source="pyannote_wespeaker",
        session_id="live-1",
        observations=[
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
            },
        ],
    )

    assert batch["ingested_count"] == 2
    assert batch["observations"][0]["speaker_id"] == "player_a"
    assert batch["observations"][1]["speaker_id"] == "player_a"
    assert batch["observations"][0]["source"] == "pyannote_wespeaker"
    assert batch["observations"][0]["session_id"] == "live-1"
    assert state["recent_segments"][0]["transcript_text"] == "hello there"
    assert state["records"]["player_a"]["observation_count"] == 2


def test_identity_linker_auto_links_recurring_confident_name_hint():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_segments(
        state,
        source="pyannote_wespeaker",
        session_id="live-auto-1",
        observations=[
            {
                "diarized_speaker_id": "spk_0",
                "segment_start_ms": 100,
                "segment_end_ms": 1200,
                "embedding": [1.0, 0.0, 0.0],
                "transcript_text": "I am Musk",
                "candidate_name": "Musk",
                "candidate_confidence": 0.93,
            },
            {
                "diarized_speaker_id": "spk_0",
                "segment_start_ms": 1300,
                "segment_end_ms": 2100,
                "embedding": [0.99, 0.01, 0.0],
                "transcript_text": "still Musk",
                "candidate_name": "Musk",
                "candidate_confidence": 0.95,
            },
        ],
    )

    record = batch["records"][0]
    assert record["linked_name"] == "Musk"
    assert record["status"] == "linked"
    assert record["bridge_active"] is True
    assert record["name_link_source"] == "auto_text_hint"
    assert record["name_link_reason"] == "repeated_confident_hint"
    assert record["name_link_score"] >= 0.88


def test_identity_linker_does_not_auto_link_single_low_confidence_hint():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_segments(
        state,
        source="pyannote_wespeaker",
        session_id="live-auto-2",
        observations=[
            {
                "diarized_speaker_id": "spk_0",
                "segment_start_ms": 100,
                "segment_end_ms": 1200,
                "embedding": [1.0, 0.0, 0.0],
                "transcript_text": "maybe musk",
                "candidate_name": "Musk",
                "candidate_confidence": 0.72,
            }
        ],
    )

    record = batch["records"][0]
    assert record.get("linked_name") is None
    assert record["status"] == "anonymous"
    assert "name_link_source" not in record


def test_identity_linker_auto_links_after_repeated_text_hints():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_pipeline_batch(
        state,
        source="pyannote_wespeaker",
        session_id="live-auto-3",
        diarization_segments=[
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 0,
                "segment_end_ms": 900,
                "transcript_text": "我是马斯克",
            },
            {
                "segment_id": "seg-2",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 950,
                "segment_end_ms": 1800,
                "transcript_text": "我叫马斯克",
            },
        ],
    )

    record = batch["records"][0]
    assert record["linked_name"] == "马斯克"
    assert record["status"] == "linked"
    assert record["bridge_active"] is True
    assert record["name_link_source"] == "auto_text_hint"


def test_identity_linker_keeps_manual_link_but_suggests_override_for_stronger_competing_hint():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    record = linker.observe(
        state,
        diarized_speaker_id="spk_0",
        embedding=[1.0, 0.0, 0.0],
    )
    record["linked_name"] = "Musk"
    record["status"] = "linked"
    record["bridge_active"] = True
    record["name_hints"] = [
        {"name": "Elon", "count": 3, "confidence_max": 0.98},
        {"name": "Musk", "count": 2, "confidence_max": 0.90},
    ]

    updated = linker._maybe_auto_link_name(record)

    assert updated is not None
    assert updated["linked_name"] == "Musk"
    assert updated["name_link_override_suggested"] is True
    assert updated["name_link_override_candidate"] == "Elon"
    assert updated["name_link_override_reason"] == "competing_hint_stronger_than_existing_link"


def test_identity_linker_ingest_tracks_candidate_name_hints():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_segments(
        state,
        source="pyannote_wespeaker",
        session_id="live-2",
        observations=[
            {
                "diarized_speaker_id": "spk_0",
                "segment_start_ms": 100,
                "segment_end_ms": 1200,
                "embedding": [1.0, 0.0, 0.0],
                "transcript_text": "I am Musk",
                "candidate_name": "Musk",
                "candidate_confidence": 0.93,
            }
        ],
    )

    assert batch["records"][0]["name_hints"][0]["name"] == "Musk"
    assert batch["records"][0]["name_hints"][0]["confidence_max"] == 0.93


def test_identity_linker_ingest_pipeline_batch_joins_segments_embeddings_and_name_candidates():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_pipeline_batch(
        state,
        source="pyannote_wespeaker",
        session_id="live-3",
        diarization_segments=[
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 0,
                "segment_end_ms": 900,
                "transcript_text": "hello there",
                "channel": 0,
            },
            {
                "segment_id": "seg-2",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 950,
                "segment_end_ms": 1800,
                "transcript_text": "same speaker again",
                "channel": 0,
            },
        ],
        speaker_embeddings=[
            {
                "diarized_speaker_id": "SPEAKER_00",
                "embedding": [1.0, 0.0, 0.0],
                "sample_count": 3,
            }
        ],
        name_candidates=[
            {
                "diarized_speaker_id": "SPEAKER_00",
                "candidate_name": "Musk",
                "candidate_confidence": 0.91,
            }
        ],
    )

    assert batch["ingested_count"] == 2
    assert batch["observations"][0]["segment_id"] == "seg-1"
    assert batch["observations"][1]["speaker_id"] == "player_a"
    assert batch["observations"][1]["channel"] == 0
    assert batch["records"][0]["speaker_id"] == "player_a"
    assert batch["records"][0]["name_hints"][0]["name"] == "Musk"
    assert state["records"]["player_a"]["embedding_sample_count"] == 2


def test_identity_linker_ingest_pipeline_batch_can_group_multiple_diarized_ids_by_speaker_profile():
    linker = IdentityLinker(similarity_threshold=0.8)
    state = {}

    batch = linker.ingest_pipeline_batch(
        state,
        source="pyannote_wespeaker",
        session_id="live-4",
        diarization_segments=[
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "speaker_profile_id": "profile-1",
                "segment_start_ms": 0,
                "segment_end_ms": 900,
                "transcript_text": "first cut",
            },
            {
                "segment_id": "seg-2",
                "diarized_speaker_id": "SPEAKER_99",
                "speaker_profile_id": "profile-1",
                "segment_start_ms": 950,
                "segment_end_ms": 1800,
                "transcript_text": "same real speaker",
            },
        ],
        speaker_embeddings=[
            {
                "speaker_profile_id": "profile-1",
                "embedding": [1.0, 0.0, 0.0],
                "sample_count": 5,
            }
        ],
        name_candidates=[
            {
                "speaker_profile_id": "profile-1",
                "candidate_name": "Musk",
                "candidate_confidence": 0.92,
            }
        ],
    )

    assert batch["observations"][0]["speaker_id"] == "player_a"
    assert batch["observations"][1]["speaker_id"] == "player_a"
    assert batch["records"][0]["speaker_id"] == "player_a"
    assert batch["records"][0]["name_hints"][0]["name"] == "Musk"
    assert sorted(batch["records"][0]["diarized_speaker_ids"]) == ["SPEAKER_00", "SPEAKER_99"]
