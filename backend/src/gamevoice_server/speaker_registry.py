class SpeakerRegistry:
    def __init__(self) -> None:
        self._speakers: dict[str, dict] = {}

    def register_anonymous(self, speaker_ids: list[str]) -> list[dict]:
        records = [{"speaker_id": speaker_id, "status": "anonymous"} for speaker_id in speaker_ids]
        for record in records:
            self._speakers[record["speaker_id"]] = record
        return records

