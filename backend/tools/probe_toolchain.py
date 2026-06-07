#!/usr/bin/env python3
"""
End-to-end probe: parse LLM output + execute tool + return result.
Tests the full tool-call chain without needing an LLM API key.
"""

import sys, os, json, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gamevoice_server.skill_runner import ToolRegistry
from gamevoice_server.skill_runner.tools import (
    build_arkham_cards_tool,
    build_arkham_rules_tool,
    build_official_faq_tool,
    build_web_faq_tool,
    build_file_reader_tool,
    build_web_search_tool,
)

registry = ToolRegistry()
registry.register(build_arkham_cards_tool())
registry.register(build_arkham_rules_tool())
registry.register(build_official_faq_tool())
registry.register(build_web_faq_tool())
registry.register(build_file_reader_tool())
registry.register(build_web_search_tool())

def parse_tool_calls(text: str):
    """Strip XML wrapper tags first."""
    text = re.sub(r'\[TOOL_CALL\]\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\[/TOOL_CALL\]', '', text, flags=re.IGNORECASE)

    dsl_pattern = re.compile(
        r'\{'
        r'\s*tool\s*=>\s*["\']([^"\']+)["\']'
        r'.*?'
        r'\{[^}]*\}'
        r'\}',
        re.DOTALL,
    )
    for match in dsl_pattern.finditer(text):
        block = match.group()
        name_match = re.search(r'tool\s*=>\s*["\']([^"\']+)["\']', block)
        if not name_match:
            continue
        tool_name = name_match.group(1).strip()
        args = {}
        for arg_match in re.finditer(r'--([a-zA-Z_][a-zA-Z0-9_]*)\s+"([^"]*)"', block):
            args[arg_match.group(1)] = arg_match.group(2)
        if args:
            return [{"name": tool_name, "arguments": args}]

    try:
        json_match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, list):
                return [c for c in parsed if isinstance(c, dict) and c.get("name")]
    except (json.JSONDecodeError, Exception):
        pass

    return []

def main():
    test_cases = [
        # Case 1: MiniMax XML-wrapped DSL (card code query)
        ('Card code 60301', '[TOOL_CALL] {tool => "arkham_cards", args => { --query "60301" }} [/TOOL_CALL]'),
        # Case 2: Card name query
        ('Card name "Flashlight"', '[TOOL_CALL] {tool => "arkham_cards", args => { --query "Flashlight" }} [/TOOL_CALL]'),
        # Case 3: Official FAQ query
        ('FAQ "Dark Papermind"', '[TOOL_CALL] {tool => "official_faq", args => { --query "Dark Papermind" }} [/TOOL_CALL]'),
        # Case 4: Arkham rules query
        ('Rules "forced ability"', '[TOOL_CALL] {tool => "arkham_rules", args => { --query "forced ability" }} [/TOOL_CALL]'),
    ]

    all_passed = True
    for name, llm_output in test_cases:
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"LLM output: {llm_output[:100]}")
        print(f"Parsing...")
        tool_calls = parse_tool_calls(llm_output)
        if not tool_calls:
            print("  FAIL: no tool calls parsed")
            all_passed = False
            continue
        print(f"  Parsed: {tool_calls}")

        for call in tool_calls:
            tool_name = call["name"]
            arguments = call["arguments"]
            print(f"  Executing {tool_name}({arguments})...")
            result = registry.execute(tool_name, arguments)
            print(f"  ok={result.ok} error={result.error}")
            if result.content:
                print(f"  content preview: {result.content[:200]}")
            if not result.ok and not result.content:
                all_passed = False

    print(f"\n{'='*60}")
    print(f"OVERALL: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
