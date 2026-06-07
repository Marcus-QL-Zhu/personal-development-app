from pathlib import Path

from gamevoice_server.formal_stream_probe import run_formal_stream_probe


def test_formal_stream_probe_returns_incremental_chunks(tmp_path: Path):
    summary = run_formal_stream_probe(
        output_dir=tmp_path,
        transcript="玩家A：宝子，给我解释三国杀规则",
        preview_text="三国杀是一款以三国时期为背景的身份对战游戏。",
        content_sentences=[
            "核心规则分三块：身份、出牌和胜利条件。",
            "每回合按摸牌、出牌、弃牌推进。",
        ],
        inter_sentence_delay_s=0.0,
    )

    assert summary["result"]["interrupt"] is True
    assert summary["result"]["mode"] == "conversation"
    assert summary["result"]["reply_content"] == "核心规则分三块：身份、出牌和胜利条件。"
    assert [item["text"] for item in summary["stream_chunks"]] == [
        "核心规则分三块：身份、出牌和胜利条件。",
        "每回合按摸牌、出牌、弃牌推进。",
    ]
    assert summary["final_speech_job"]["segments"] == [
        "核心规则分三块：身份、出牌和胜利条件。",
        "每回合按摸牌、出牌、弃牌推进。",
    ]
    assert summary["summary_path"].endswith("-summary.json")
