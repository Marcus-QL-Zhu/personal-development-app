def test_manual_link_records_source_metadata(session_manager):
    table = session_manager.start_table(name="Manual Link Table")

    linked = session_manager.link_speaker_identity(table.id, "player_a", "Musk")
    assert linked["linked_name"] == "Musk"
    assert linked["bridge_active"] is True
    assert linked["name_link_source"] == "manual"
    assert linked["name_link_reason"] == "manual_override"
    assert linked["name_link_score"] == 1.0


def test_review_candidate_accept_override_updates_link(session_manager):
    table = session_manager.start_table(name="Review Override Table")

    session_manager.observe_speaker_identity(
        table.id,
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

    accepted = session_manager.accept_speaker_identity_name_override(table.id, "player_a", "Elon")

    assert accepted["linked_name"] == "Elon"
    assert accepted["name_link_source"] == "review_override"
    assert accepted["name_link_reason"] == "accepted_override_suggestion"
    assert accepted["name_link_override_suggested"] is False
