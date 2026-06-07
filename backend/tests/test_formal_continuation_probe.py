from gamevoice_server.formal_continuation_probe import _replay_current_commit_algorithm


def test_formal_continuation_probe_replay_handles_rewritten_prefix_without_duplication():
    replay = _replay_current_commit_algorithm(
        cumulative_texts=[
            "三国杀是一款以",
            "三国杀是一款以三国时期为背景的卡牌对战游戏。玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。",
            "三国杀是一款以三国时期为背景的卡牌对战游戏。玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。游戏开始前，玩家需要根据身份制定策略。",
        ]
    )

    assert replay["emitted_segments"] == [
        "三国杀是一款以三国时期为背景的卡牌对战游戏。",
        "玩家扮演不同势力的人物，通过出牌和发动技能来击败对手。",
        "游戏开始前，玩家需要根据身份制定策略。",
    ]


def test_formal_continuation_probe_replay_waits_for_safe_boundary_before_first_emit():
    replay = _replay_current_commit_algorithm(
        cumulative_texts=[
            "The game has 4 roles: one Lord (主",
            "The game has 4 roles: one Lord (主公), two Loyalists and one Rebel.",
        ]
    )

    assert replay["updates"][0]["emitted_this_update"] == ["The game has 4 roles:"]
    assert replay["emitted_segments"][0] == "The game has 4 roles:"
