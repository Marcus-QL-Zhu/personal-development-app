#!/usr/bin/env python3
"""
Web Search Probe 2: 秘塔AI联网搜索测试.

Question: "联网搜索然后告诉我，数字生命卡兹克的爸爸是谁"

Tests the web_search tool with 秘塔AI API.
"""

import sys
import os

# Add backend/src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("probe2")

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
    print("WEB SEARCH PROBE 2: 秘塔AI 联网搜索测试")
    print("=" * 70)
    print()
    print("Question: 联网搜索然后告诉我，数字生命卡兹克的爸爸是谁")
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

    # Build dialog client
    minimax = MiniMaxDialogClient(
        api_key=settings.minimax_api_key or "",
        model=settings.minimax_text_model,
        base_url=settings.minimax_text_base_url,
        timeout_seconds=settings.minimax_text_timeout_seconds,
    )

    # Build agent
    agent = SkillAgent(dialog_client=minimax, tool_registry=registry)

    print(f"[{time.strftime('%H:%M:%S')}] Agent built, starting search...")
    print()

    # Run search
    query = "联网搜索然后告诉我，数字生命卡兹克的爸爸是谁"
    start = time.time()
    answer = agent.run(query=query, context="暂无")
    elapsed = time.time() - start

    print()
    print("=" * 70)
    print("SEARCH RESULT")
    print("=" * 70)
    print()
    print(answer.content)
    print()
    print("=" * 70)
    print(f"Iterations: {answer.iterations}")
    print(f"Timed out: {answer.timed_out}")
    print(f"Time elapsed: {elapsed:.1f}s")
    print()
    print("[OUTPUT]")
    print("-" * 70)
    print(answer.content)
    print("-" * 70)


if __name__ == "__main__":
    main()
