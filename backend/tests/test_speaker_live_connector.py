from gamevoice_server.identity_linker import IdentityLinker
from gamevoice_server.session_manager import SessionManager
from gamevoice_server.speaker_live_connector import SpeakerLiveConnector
from gamevoice_server.speaker_pipeline_adapter import SpeakerPipelineAdapter


def test_speaker_live_connector_tracks_audio_and_ingests_pipeline_batch():
    session_manager = SessionManager()
    table = session_manager.start_table(name="Live Connector Table")
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=IdentityLinker(),
        pipeline_adapter=SpeakerPipelineAdapter(),
    )

    state = connector.start_session(table.id, "live-1")
    assert state.live_session_id == "live-1"
    assert state.audio_chunk_count == 0

    state = connector.ingest_audio_chunk(table.id, "live-1", b"abc")
    assert state.audio_chunk_count == 1
    assert state.audio_byte_count == 3
    assert state.audio_chunk_enqueued_count == 1

    pulled = connector.pull_audio_chunks(table.id, "live-1")
    assert pulled["chunks"][0]["chunk_index"] == 0
    assert pulled["chunks"][0]["byte_count"] == 3
    assert pulled["live_session_state"]["pending_audio_chunk_count"] == 0

    result = connector.ingest_live_pipeline_batch(
        table.id,
        "live-1",
        source="pyannote_wespeaker",
        pyannote_segments=[
            {
                "segment_id": "seg-1",
                "speaker": "SPEAKER_00",
                "speaker_profile": "profile-1",
                "start": 0.0,
                "end": 1.0,
                "text": "I am Nova",
                "confidence": 0.91,
            }
        ],
        speaker_embeddings=[
            {
                "speaker_profile": "profile-1",
                "vector": [1.0, 0.0, 0.0],
                "sample_count": 2,
            }
        ],
    )

    assert result["live_session_id"] == "live-1"
    assert result["speaker_identity_batch"]["ingested_count"] == 1
    assert result["speaker_identity_batch"]["observations"][0]["speaker_id"] == "player_a"
    assert result["live_session_state"]["ingested_batch_count"] == 1
    assert result["live_session_state"]["ingested_observation_count"] == 1

    state = connector.update_transcript(table.id, "live-1", "I am Nova")
    assert state.last_transcript == "I am Nova"

    finished = connector.finish_session(table.id, "live-1")
    assert finished is not None
    assert finished.ended_at is not None


def test_speaker_live_connector_notifies_auto_process_callback():
    session_manager = SessionManager()
    table = session_manager.start_table(name="Live Connector Callback Table")
    events: list[tuple[str, str]] = []
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=IdentityLinker(),
        pipeline_adapter=SpeakerPipelineAdapter(),
    )
    connector.on_audio_chunk_enqueued = lambda table_id, live_session_id: events.append((table_id, live_session_id))

    connector.ingest_audio_chunk(table.id, "live-2", b"abc")
    assert events == [(table.id, "live-2")]

    connector.finish_session(table.id, "live-2")
    assert events == [(table.id, "live-2"), (table.id, "live-2")]


def test_speaker_live_connector_notifies_identity_listeners():
    session_manager = SessionManager()
    table = session_manager.start_table(name="Live Connector Identity Listener Table")
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=IdentityLinker(),
        pipeline_adapter=SpeakerPipelineAdapter(),
    )

    payloads: list[dict] = []
    connector.add_identity_listener(table.id, payloads.append)

    result = connector.ingest_live_pipeline_batch(
        table.id,
        "live-3",
        source="pyannote_wespeaker",
        diarization_segments=[
            {
                "segment_id": "seg-2",
                "speaker": "SPEAKER_02",
                "speaker_profile": "profile-2",
                "start": 1.2,
                "end": 2.4,
                "text": "I am Nova",
                "confidence": 0.93,
            }
        ],
        speaker_embeddings=[
            {
                "speaker_profile": "profile-2",
                "vector": [0.4, 0.5, 0.6],
                "sample_count": 1,
            }
        ],
    )

    assert payloads
    assert payloads[0]["event"] == "speaker_identity_batch"
    assert payloads[0]["speaker_identity_batch"]["ingested_count"] == 1
    assert result["speaker_identity_batch"]["speaker_identities"][0]["speaker_id"] == "player_a"
    connector.remove_identity_listener(table.id, payloads.append)


def test_speaker_live_connector_notifies_global_identity_batch_callback():
    session_manager = SessionManager()
    table = session_manager.start_table(name="Live Connector Global Identity Callback Table")
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=IdentityLinker(),
        pipeline_adapter=SpeakerPipelineAdapter(),
    )
    payloads: list[dict] = []
    connector.on_identity_batch_ingested = payloads.append

    connector.ingest_live_pipeline_batch(
        table.id,
        "live-4",
        source="pyannote_wespeaker",
        diarization_segments=[
            {
                "segment_id": "seg-3",
                "speaker": "SPEAKER_03",
                "speaker_profile": "profile-3",
                "start": 0.0,
                "end": 1.0,
                "text": "I am Alice",
                "confidence": 0.95,
            }
        ],
        speaker_embeddings=[
            {
                "speaker_profile": "profile-3",
                "vector": [0.2, 0.3, 0.4],
                "sample_count": 1,
            }
        ],
    )

    assert len(payloads) == 1
    assert payloads[0]["table_id"] == table.id
    assert payloads[0]["event"] == "speaker_identity_batch"
