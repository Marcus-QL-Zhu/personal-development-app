from gamevoice_server.speaker_pipeline_adapter import SpeakerPipelineAdapter


class PyannoteSegmentLike:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_speaker_pipeline_adapter_normalizes_pyannote_style_segments():
    adapter = SpeakerPipelineAdapter()

    batch = adapter.build_batch(
        source="pyannote_wespeaker",
        session_id="live-5",
        pyannote_segments=[
            {
                "segment_id": "seg-1",
                "speaker": "SPEAKER_00",
                "speaker_profile_id": "profile-1",
                "start": 0.25,
                "end": 1.2,
                "text": "hello there",
                "channel": 1,
                "confidence": 0.88,
            },
            {
                "speaker": "SPEAKER_99",
                "speaker_profile_id": "profile-1",
                "start": 1.25,
                "end": 2.0,
                "text": "same speaker again",
            },
        ],
        speaker_embeddings=[
            {
                "speaker_profile_id": "profile-1",
                "embedding": [1.0, 0.0, 0.0],
                "sample_count": 2,
            }
        ],
        name_candidates=[
            {
                "speaker_profile_id": "profile-1",
                "candidate_name": "Musk",
                "candidate_confidence": 0.9,
            }
        ],
    )

    assert batch["source"] == "pyannote_wespeaker"
    assert batch["session_id"] == "live-5"
    assert batch["diarization_segments"][0]["diarized_speaker_id"] == "SPEAKER_00"
    assert batch["diarization_segments"][0]["segment_start_ms"] == 250
    assert batch["diarization_segments"][0]["segment_end_ms"] == 1200
    assert batch["diarization_segments"][0]["transcript_text"] == "hello there"
    assert batch["diarization_segments"][0]["channel"] == 1
    assert batch["speaker_embeddings"][0]["speaker_profile_id"] == "profile-1"
    assert batch["name_candidates"][0]["candidate_name"] == "Musk"
    assert batch["name_candidates"][0]["candidate_confidence"] == 0.9


def test_speaker_pipeline_adapter_accepts_pyannote_and_wespeaker_objects():
    adapter = SpeakerPipelineAdapter()

    batch = adapter.build_batch(
        source="pyannote_wespeaker",
        session_id="live-obj-1",
        pyannote_segments=[
            PyannoteSegmentLike(
                segment_id="seg-obj-1",
                speaker="SPEAKER_07",
                speaker_profile="profile-7",
                start=2.5,
                end=4.0,
                text="call me Nova",
                confidence=0.77,
                channel=2,
            )
        ],
        speaker_embeddings=[
            PyannoteSegmentLike(
                speaker_label="SPEAKER_07",
                speaker_profile="profile-7",
                vector=[0.2, 0.8, 0.0],
                sample_count=4,
            )
        ],
        name_candidates=[
            PyannoteSegmentLike(
                speaker_label="SPEAKER_07",
                speaker_profile="profile-7",
                name="Nova",
                confidence=0.91,
            )
        ],
    )

    assert batch["diarization_segments"][0]["speaker_profile_id"] == "profile-7"
    assert batch["diarization_segments"][0]["segment_start_ms"] == 2500
    assert batch["speaker_embeddings"][0]["speaker_profile_id"] == "profile-7"
    assert batch["speaker_embeddings"][0]["embedding"] == [0.2, 0.8, 0.0]
    assert batch["name_candidates"][0]["candidate_name"] == "Nova"
    assert batch["name_candidates"][0]["candidate_confidence"] == 0.91


def test_speaker_pipeline_adapter_accepts_legacy_diarization_segments_unchanged():
    adapter = SpeakerPipelineAdapter()

    batch = adapter.build_batch(
        source="pyannote_wespeaker",
        session_id="live-6",
        diarization_segments=[
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 100,
                "segment_end_ms": 900,
                "transcript_text": "already normalized",
            }
        ],
    )

    assert batch["diarization_segments"][0]["diarized_speaker_id"] == "SPEAKER_00"
    assert batch["diarization_segments"][0]["segment_start_ms"] == 100
    assert batch["diarization_segments"][0]["segment_end_ms"] == 900


def test_speaker_pipeline_adapter_does_not_extract_text_name_candidate_when_missing():
    adapter = SpeakerPipelineAdapter()

    batch = adapter.build_batch(
        source="pyannote_wespeaker",
        session_id="live-7",
        diarization_segments=[
            {
                "segment_id": "seg-1",
                "diarized_speaker_id": "SPEAKER_00",
                "segment_start_ms": 100,
                "segment_end_ms": 900,
                "transcript_text": "??????",
            }
        ],
    )

    assert batch["name_candidates"] == []
