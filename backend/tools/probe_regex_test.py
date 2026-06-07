#!/usr/bin/env python3
"""
Debug probe: test the DSL tool-call parsing directly.
"""

import sys, os, re, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gamevoice_server.skill_runner.agent import _parse_tool_calls

# The actual LLM output from MiniMax
test_cases = [
    # Case 1: actual MiniMax output (XML-wrapped DSL)
    '我来帮你查询60301这张卡的信息。 [TOOL_CALL] {tool => "arkham_cards", args => { --query "60301" }} [/TOOL_CALL]',
    # Case 2: plain DSL
    '{tool => "arkham_cards", args => { --query "60301" }}',
    # Case 3: JSON array
    '[{"name": "arkham_cards", "arguments": {"query": "60301"}}]',
    # Case 4: XML-wrapped with different casing
    '[TOOL_CALL] {tool => "arkham_cards", args => { --query "60301" }} [/TOOL_CALL]',
]

for i, text in enumerate(test_cases):
    print(f"=== Case {i+1} ===")
    print(f"Text: {repr(text)}")
    result = _parse_tool_calls(text)
    print(f"Parsed: {result}")
    print()
