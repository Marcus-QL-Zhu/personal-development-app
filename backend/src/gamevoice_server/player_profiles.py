class PlayerProfiles:
    def __init__(self) -> None:
        self._profiles: dict[str, dict] = {}

    def save(self, player_id: str, profile: dict) -> None:
        self._profiles[player_id] = profile

    def load(self, player_id: str) -> dict | None:
        return self._profiles.get(player_id)

