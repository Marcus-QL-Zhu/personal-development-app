from gamevoice_server.identity_linker import IdentityLinker
from gamevoice_server.session_manager import SessionManager
from gamevoice_server.speaker_live_connector import SpeakerLiveConnector
from gamevoice_server.speaker_live_worker import SpeakerLiveWorker
from gamevoice_server.speaker_pipeline_adapter import SpeakerPipelineAdapter


class StubDiarizer:
    def diarize(self, *, table_id, live_session_id, audio_chunks, audio_bytes):
        return [
            {
                "segment_id": f"seg-{audio_chunks[0]['chunk_index']}",
                "speaker": "SPEAKER_00",
                "speaker_profile": "profile-1",
                "start": 0.0,
                "end": 1.0,
                "text": "I am Nova",
                "confidence": 0.95,
                "channel": 0,
            }
        ]


class StubEmbedder:
    def embed(self, *, table_id, live_session_id, audio_chunks, audio_bytes, diarization_segments):
        return [
            {
                "speaker_profile": "profile-1",
                "vector": [1.0, 0.0, 0.0],
                "sample_count": len(audio_chunks),
            }
        ]


def test_speaker_live_worker_processes_pending_audio_into_pipeline_batch():
    session_manager = SessionManager()
    table = session_manager.start_table(name="Live Worker Table")
    connector = SpeakerLiveConnector(
        session_manager=session_manager,
        identity_linker=IdentityLinker(),
        pipeline_adapter=SpeakerPipelineAdapter(),
    )
    worker = SpeakerLiveWorker(
        connector=connector,
        diarizer=StubDiarizer(),
        embedder=StubEmbedder(),
    )

    connector.start_session(table.id, "live-1")
    connector.ingest_audio_chunk(table.id, "live-1", b"aaa")
    connector.ingest_audio_chunk(table.id, "live-1", b"bbb")

    result = worker.process_session(table.id, "live-1")

    assert result["status"] == "processed"
    assert result["speaker_identity_batch"]["ingested_count"] == 1
    assert result["speaker_identity_batch"]["speaker_identities"][0]["speaker_id"] == "player_a"
    assert result["worker_state"]["processed_chunk_count"] == 2
    assert result["worker_state"]["last_processed_chunk_index"] == 1

    worker_state = worker.describe_worker_session(table.id, "live-1")
    assert worker_state["last_status"] == "processed"
    assert worker_state["last_result_source"] == "pyannote_wespeaker"
