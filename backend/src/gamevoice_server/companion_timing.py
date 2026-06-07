class CompanionTiming:
    def __init__(self, turn_decision_engine) -> None:
        self.turn_decision_engine = turn_decision_engine

    def should_interrupt(
        self,
        transcript: str,
        events: list[dict] | None = None,
        *,
        assistant_name: str = "宝子",
    ) -> dict:
        return self.turn_decision_engine.decide_turn(
            transcript=transcript,
            events=events or [],
            assistant_name=assistant_name,
        )
