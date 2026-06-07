import json
import zipfile

from gamevoice_server.card_index import CardIndex


def test_card_index_loads_cards_from_zip_and_matches_name_queries(tmp_path):
    zip_path = tmp_path / "arkhamdb-cards.zip"
    cards = [
        {
            "code": "01001",
            "name": "Roland Banks",
            "faction_name": "Guardian",
            "type_name": "Investigator",
            "traits": "Agency. Detective.",
            "text": "After you defeat an enemy: Discover 1 clue at your location.",
            "xp": 0,
        }
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "arkhamdb-cards/data/indexes/master_index_with_tags.json",
            json.dumps(cards, ensure_ascii=False),
        )

    index = CardIndex(arkham_cards_zip_path=str(zip_path))

    result = index.search("what does Roland Banks do")

    assert result is not None
    assert "Roland Banks" in result
    assert "Guardian" in result
    assert "Discover 1 clue" in result


def test_card_index_matches_card_text_queries(tmp_path):
    zip_path = tmp_path / "arkhamdb-cards.zip"
    cards = [
        {
            "code": "01001",
            "name": "Flashlight",
            "faction_name": "Neutral",
            "type_name": "Asset",
            "traits": "Item. Tool.",
            "text": "Spend 1 supply: Investigate. This investigation gets -2 shroud.",
            "xp": 0,
        }
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "arkhamdb-cards/data/indexes/master_index_with_tags.json",
            json.dumps(cards, ensure_ascii=False),
        )

    index = CardIndex(arkham_cards_zip_path=str(zip_path))

    result = index.search("which card gets -2 shroud")

    assert result is not None
    assert "Flashlight" in result
    assert "-2 shroud" in result


def test_card_index_prefers_exact_card_name_over_related_card(tmp_path):
    zip_path = tmp_path / "arkhamdb-cards.zip"
    cards = [
        {
            "code": "01001",
            "name": "Roland Banks",
            "faction_name": "Guardian",
            "type_name": "Investigator",
            "traits": "Agency. Detective.",
            "text": "After you defeat an enemy: Discover 1 clue at your location.",
            "xp": 0,
        },
        {
            "code": "90030",
            "name": "Roland's .38 Special",
            "faction_name": "Neutral",
            "type_name": "Asset",
            "traits": "Item. Weapon. Firearm.",
            "text": "Roland Banks deck only.",
            "xp": 0,
        },
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "arkhamdb-cards/data/indexes/master_index_with_tags.json",
            json.dumps(cards, ensure_ascii=False),
        )

    index = CardIndex(arkham_cards_zip_path=str(zip_path))

    result = index.search("Roland Banks")

    assert result is not None
    assert result.startswith("Roland Banks (01001)")


def test_card_index_prefers_stronger_text_match_when_multiple_cards_share_keyword(tmp_path):
    zip_path = tmp_path / "arkhamdb-cards.zip"
    cards = [
        {
            "code": "01087",
            "name": "Flashlight",
            "faction_name": "Neutral",
            "type_name": "Asset",
            "traits": "Item. Tool.",
            "text": "Spend 1 supply: Investigate. This investigation gets -2 shroud.",
            "xp": 0,
        },
        {
            "code": "04185",
            "name": "Sacred Woods",
            "faction_name": "Mythos",
            "type_name": "Location",
            "traits": "Ancient.",
            "text": "While you are investigating Sacred Woods, it gets -1 shroud for each asset you control.",
            "xp": 0,
        },
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "arkhamdb-cards/data/indexes/master_index_with_tags.json",
            json.dumps(cards, ensure_ascii=False),
        )

    index = CardIndex(arkham_cards_zip_path=str(zip_path))

    result = index.search("which card gets -2 shroud")

    assert result is not None
    assert result.startswith("Flashlight (01087)")


def test_card_index_prefers_name_hit_over_generic_text_overlap(tmp_path):
    zip_path = tmp_path / "arkhamdb-cards.zip"
    cards = [
        {
            "code": "01087",
            "name": "Flashlight",
            "faction_name": "Neutral",
            "type_name": "Asset",
            "traits": "Item. Tool.",
            "text": "Spend 1 supply: Investigate. This investigation gets -2 shroud.",
            "xp": 0,
        },
        {
            "code": "99999",
            "name": "Decoy Card",
            "faction_name": "Neutral",
            "type_name": "Event",
            "traits": "Trick.",
            "text": "What does this card do? Do what the card says.",
            "xp": 0,
        },
    ]
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "arkhamdb-cards/data/indexes/master_index_with_tags.json",
            json.dumps(cards, ensure_ascii=False),
        )

    index = CardIndex(arkham_cards_zip_path=str(zip_path))

    result = index.search("what does Flashlight do")

    assert result is not None
    assert result.startswith("Flashlight (01087)")
