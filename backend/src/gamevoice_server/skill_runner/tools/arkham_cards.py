from __future__ import annotations

import json
import sys
from pathlib import Path

from ..subprocess_worker import run_script
from ..tool_registry import Tool, ToolResult

#: Absolute path to the arkhamdb-cards skill directory
_ARKHAMDB_CARDS_DIR = Path(__file__).resolve().parents[5] / "arkhamdb-cards"
_CARD_QUERY_SCRIPT = str(_ARKHAMDB_CARDS_DIR / "scripts" / "card_query.py")


def _execute(arguments: dict) -> ToolResult:
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult.failure("query cannot be empty")

    # Use the CardQuery class directly via a one-liner subprocess
    # We import the class and call search_cards, returning up to 10 results as JSON
    # Use as_posix() to avoid backslash escaping issues on Windows
    scripts_path = (_ARKHAMDB_CARDS_DIR / "scripts").as_posix()
    escaped_query = query.replace("'", "''")
    code = f"""
import sys
sys.path.insert(0, '{scripts_path}')
from card_query import CardQuery
import json

try:
    cq = CardQuery()
    # If query looks like a card code (all digits), use get_card_by_code
    if '{escaped_query}'.isdigit():
        card = cq.get_card_by_code('{escaped_query}')
        results = [card] if card else []
    else:
        results = cq.search_cards('{escaped_query}', field='all')
    # Return up to 10 results with key fields
    output = []
    for card in results[:10]:
        if not card:
            continue
        output.append({{
            'code': card.get('code', ''),
            'name': card.get('name', ''),
            'type_name': card.get('type_name', ''),
            'faction_name': card.get('faction_name', ''),
            'pack_name': card.get('pack_name', ''),
            'text': card.get('text', '')[:500],
            'traits': card.get('traits', ''),
        }})
    print(json.dumps(output, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{'error': str(e)}}), file=sys.stderr)
    sys.exit(1)
"""
    result = run_script(
        "-c", code,
        timeout=20.0,
    )

    if not result.ok:
        return ToolResult.failure(f"card query failed: {result.stderr or result.stdout}")

    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return ToolResult.failure(f"failed to parse card query output: {result.stdout[:200]}")

    if isinstance(parsed, dict) and "error" in parsed:
        return ToolResult.failure(f"card query error: {parsed['error']}")

    if not parsed:
        return ToolResult.failure("no cards found for query", content="")

    # Format output for the LLM
    lines = []
    for card in parsed:
        lines.append(
            f"[{card['code']}] {card['name']} ({card['faction_name']}, {card['type_name']})\n"
            f"  Pack: {card['pack_name']}\n"
            f"  Traits: {card['traits']}\n"
            f"  Text: {card['text']}"
        )
    return ToolResult.success("\n".join(lines))


TOOL_SCHEMA = {
    "name": "arkham_cards",
    "description": (
        "Search the local ArkhamDB card index for cards by name, effect text, or traits. "
        "Returns card name, code, type, faction, pack, and full card text. "
        "Use this when the user asks about a specific card or card effects."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Card name, keyword in card text, or card code to search for. "
                    "Examples: 'Flashlight', 'weapon', 'Daisy Walker'"
                ),
            },
        },
        "required": ["query"],
    },
}


def build_arkham_cards_tool() -> Tool:
    return Tool(
        name="arkham_cards",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
