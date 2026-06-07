#!/usr/bin/env python3
"""
Rule Analysis Probe 1: Arkham LCG complex rules question.

Question: "我在一個技能檢定中投入了2張牌，然後通過60301的技能抽上一張牌後，能不能直接再投入這個技能檢定"

Tests the full skill-runner agent loop with:
- arkham_cards (lookup card 60301)
- official_faq (search FAQ for card interaction rules)
- arkham_rules (local rules lookup)
- web_faq (ArkhamDB web FAQ if needed)
"""

import sys
import os

# Add backend/src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("probe1")

from gamevoice_server.skill_runner import SkillAgent, ToolRegistry
from gamevoice_server.skill_runner.tools import (
    build_arkham_rules_tool,
    build_arkham_cards_tool,
    build_official_faq_tool,
    build_web_faq_tool,
    build_file_reader_tool,
    build_web_search_tool,
)
from gamevoice_server.dialog_client import MiniMaxDialogClient
from gamevoice_server.config import settings


def main():
    print("=" * 70)
    print("RULE ANALYSIS PROBE 1: Arkham LCG Rules Question")
    print("=" * 70)
    print()
    print("Question: 我在一個技能檢定中投入了2張牌，然後通過60301（Winifred Habbamock）的技能抽上一張牌後，能不能直接再投入這個技能檢定")
    print()

    # Build tool registry
    registry = ToolRegistry()
    registry.register(build_arkham_rules_tool())
    registry.register(build_arkham_cards_tool())
    registry.register(build_official_faq_tool())
    registry.register(build_web_faq_tool())
    registry.register(build_file_reader_tool())
    registry.register(build_web_search_tool())

    print(f"[{time.strftime('%H:%M:%S')}] Tool registry built: {len(registry.tool_schemas())} tools")

    # Build dialog client - use Anthropic-compatible endpoint
    minimax = MiniMaxDialogClient(
        api_key=settings.minimax_api_key or "",
        model=settings.minimax_text_model,
        base_url=settings.minimax_text_base_url,
        timeout_seconds=settings.minimax_text_timeout_seconds,
    )

    # Build agent
    agent = SkillAgent(dialog_client=minimax, tool_registry=registry)

    print(f"[{time.strftime('%H:%M:%S')}] Agent built, starting analysis...")
    print()

    # Run analysis
    query = "我在一個技能檢定中投入了2張牌，然後通過60301的技能抽上一張牌後，能不能直接再投入這個技能檢定"

    start = time.time()
    answer = agent.run(query=query, context="暂无")
    elapsed = time.time() - start

    print()
    print("=" * 70)
    print("ANALYSIS RESULT")
    print("=" * 70)
    print()
    print(answer.content)
    print()
    print("=" * 70)
    print(f"Iterations: {answer.iterations}")
    print(f"Timed out: {answer.timed_out}")
    print(f"Time elapsed: {elapsed:.1f}s")
    print()
    print("[OUTPUT TO INJECT INTO MAIN EVENT STREAM]")
    print("-" * 70)
    print(answer.content)
    print("-" * 70)


if __name__ == "__main__":
    main()
