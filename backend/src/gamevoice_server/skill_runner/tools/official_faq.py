from __future__ import annotations

import json
from pathlib import Path

from ..subprocess_worker import run_script
from ..tool_registry import Tool, ToolResult

#: Absolute path to the arkham-rules skill directory
_ARKHAM_RULES_DIR = Path(__file__).resolve().parents[5] / "arkham-rules"
_SCRIPTS_DIR = _ARKHAM_RULES_DIR / "scripts"
_OFFICIAL_FAQ_SCRIPT = str(_SCRIPTS_DIR / "official_faq_query.py")


def _execute(arguments: dict) -> ToolResult:
    query = arguments.get("query", "").strip()
    if not query:
        return ToolResult.failure("query cannot be empty")

    result = run_script(
        _OFFICIAL_FAQ_SCRIPT, query,
        timeout=20.0,
    )

    # Exit code 0 or 1 both produce useful stdout; only system errors matter
    if result.timed_out:
        return ToolResult.failure("FAQ query timed out after 20s")
    if result.returncode == 0:
        return ToolResult.success(result.stdout)
    # returncode 1 means no results found, still return the output (might have warnings)
    return ToolResult.success(result.stdout)


TOOL_SCHEMA = {
    "name": "official_faq",
    "description": (
        "Search the FFG Official FAQ v2.5 for Arkham Horror LCG rule clarifications, "
        "errata, and card FAQ entries. Covers Errata (highest priority), "
        "Rules and Clarifications, Official FAQ v2.5, and Taboo List. "
        "Use this when the user asks about official rulings, card interactions, "
        "or specific card errata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Card name or rule term to search in the official FAQ. "
                    "Examples: 'Dark Papermind', 'Amnesia', 'forced ability', "
                    "'Rod of Carnamagos'"
                ),
            },
        },
        "required": ["query"],
    },
}


def build_official_faq_tool() -> Tool:
    return Tool(
        name="official_faq",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
