class VoiceprintStore:
    def __init__(self) -> None:
        self._voiceprints: dict[str, dict] = {}

    def save(self, speaker_id: str, payload: dict) -> None:
        self._voiceprints[speaker_id] = payload

    def load(self, speaker_id: str) -> dict | None:
        return self._voiceprints.get(speaker_id)

