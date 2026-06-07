from gamevoice_server.player_profiles import PlayerProfiles


def test_profile_remembers_only_name_and_reminder_style():
    player_profiles = PlayerProfiles()
    player_profiles.save("p1", {"name": "阿杰", "reminder_style": "light"})
    loaded = player_profiles.load("p1")
    assert loaded["name"] == "阿杰"
    assert loaded["reminder_style"] == "light"

