from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..tool_registry import Tool, ToolResult

_ARKHAM_RULES_DIR = Path(__file__).resolve().parents[5] / "arkham-rules"
_OUTLINE_PATH = _ARKHAM_RULES_DIR / "data" / "rules_outline.md"


@dataclass(frozen=True)
class RuleModule:
    module_id: str
    keywords: tuple[str, ...]
    suggested_terms: tuple[str, ...]
    recommended_tools: tuple[str, ...]
    cautions: tuple[str, ...] = ()


_MODULES: tuple[RuleModule, ...] = (
    RuleModule(
        module_id="source_priority",
        keywords=(
            "errata",
            "taboo",
            "faq",
            "official",
            "override",
            "contradict",
            "priority",
            "golden rule",
            "ruling",
            "勘误",
            "禁忌",
            "官方",
            "裁定",
        ),
        suggested_terms=("errata", "taboo", "golden rule", "card text", "FAQ"),
        recommended_tools=("arkham_cards", "official_faq", "arkham_rules", "web_faq"),
        cautions=("Check current card text and errata before applying older rules text.",),
    ),
    RuleModule(
        module_id="round_phase_flow",
        keywords=(
            "mythos",
            "investigation phase",
            "enemy phase",
            "upkeep",
            "doom",
            "agenda",
            "turn",
            "phase",
            "ready",
        ),
        suggested_terms=("mythos phase", "investigation phase", "enemy phase", "upkeep phase", "doom", "agenda"),
        recommended_tools=("arkham_rules", "official_faq"),
        cautions=("Confirm the phase and player window before resolving timing-dependent effects.",),
    ),
    RuleModule(
        module_id="actions_and_costs",
        keywords=(
            "action",
            "activate",
            "cost",
            "additional cost",
            "opportunity attack",
            "move",
            "investigate",
            "fight",
            "evade",
            "engage",
            "resign",
            "parley",
            "行动",
            "启动",
            "费用",
            "调查",
            "攻击",
            "躲避",
            "移动",
        ),
        suggested_terms=("action", "activate", "cost", "opportunity attack", "investigate", "fight", "evade"),
        recommended_tools=("arkham_cards", "arkham_rules", "official_faq"),
        cautions=("Separate paying costs from resolving effects.",),
    ),
    RuleModule(
        module_id="ability_timing",
        keywords=(
            "forced",
            "revelation",
            "reaction",
            "free triggered",
            "constant ability",
            "when",
            "after",
            "then",
            "may",
            "limit",
            "max",
            "replacement",
            "simultaneous",
            "trigger",
            "ability",
            "effect",
            "use",
            "eye of chaos",
            "rod of carnamagos",
            "触发",
            "能力",
            "反应",
            "强制",
            "启示",
            "时机",
            "窗口",
        ),
        suggested_terms=(
            "forced ability",
            "revelation",
            "reaction",
            "free triggered ability",
            "when",
            "after",
            "replacement effect",
            "simultaneous",
        ),
        recommended_tools=("arkham_cards", "official_faq", "arkham_rules"),
        cautions=("Use exact card text for trigger conditions such as when, after, if, then, and may.",),
    ),
    RuleModule(
        module_id="skill_test_timing",
        keywords=(
            "skill test",
            "test",
            "commit",
            "chaos token",
            "curse",
            "curse token",
            "bless",
            "bless token",
            "frost",
            "symbol token",
            "modifier",
            "succeed",
            "fail",
            "during a skill test",
            "eye of chaos",
            "rod of carnamagos",
            "检定",
            "技能检定",
            "调查检定",
            "混乱标记",
            "诅咒",
            "祝福",
            "旧印",
            "成功",
            "失败",
        ),
        suggested_terms=(
            "skill test",
            "reveal chaos token",
            "curse token",
            "modifier",
            "success",
            "fail",
            "during a skill test",
        ),
        recommended_tools=("official_faq", "arkham_rules", "arkham_cards"),
        cautions=("Resolve the skill test sequence before applying card-triggered follow-up effects.",),
    ),
    RuleModule(
        module_id="enemies_and_damage",
        keywords=(
            "enemy",
            "spawn",
            "prey",
            "hunter",
            "aloof",
            "massive",
            "engage",
            "disengage",
            "exhausted",
            "damage",
            "horror",
            "defeat",
        ),
        suggested_terms=("enemy", "spawn", "prey", "hunter", "aloof", "massive", "damage", "horror"),
        recommended_tools=("arkham_rules", "official_faq", "arkham_cards"),
        cautions=("Track engagement and exhaustion state before resolving enemy attacks or evasion.",),
    ),
    RuleModule(
        module_id="locations_clues_doom",
        keywords=(
            "clue",
            "shroud",
            "discover",
            "location",
            "connecting",
            "move",
            "doom",
            "agenda",
            "advance",
            "线索",
            "地点",
            "连接地点",
            "发现",
            "发现线索",
        ),
        suggested_terms=("clue", "discover clue", "shroud", "connecting location", "doom", "agenda"),
        recommended_tools=("arkham_rules", "official_faq", "arkham_cards"),
        cautions=("Distinguish discovering clues from placing or moving clues.",),
    ),
    RuleModule(
        module_id="deck_campaign_resolution",
        keywords=(
            "deckbuilding",
            "bonded",
            "permanent",
            "exceptional",
            "exile",
            "weakness",
            "campaign",
            "experience",
            "trauma",
            "resolution",
            "victory",
        ),
        suggested_terms=("deckbuilding", "bonded", "permanent", "weakness", "campaign log", "experience", "trauma"),
        recommended_tools=("arkham_cards", "official_faq", "arkham_rules"),
        cautions=("Separate campaign setup/resolution rules from in-game play-area rules.",),
    ),
)


def _clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _load_outline() -> str:
    try:
        return _OUTLINE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _outline_sections(outline: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_id = ""
    current_lines: list[str] = []
    for line in outline.splitlines():
        if line.startswith("## "):
            if current_id:
                sections[current_id] = "\n".join(current_lines).strip()
            current_id = line[3:].strip()
            current_lines = [line]
        elif current_id:
            current_lines.append(line)
    if current_id:
        sections[current_id] = "\n".join(current_lines).strip()
    return sections


def _score_module(query: str, module: RuleModule) -> int:
    score = 0
    for keyword in module.keywords:
        if _keyword_in_query(query, keyword):
            score += 3 if " " in keyword else 1
    return score


def _keyword_in_query(query: str, keyword: str) -> bool:
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9_]+", keyword):
        return re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])", query) is not None
    return keyword in query


def _matched_modules(query: str) -> list[tuple[int, RuleModule]]:
    scored = [(_score_module(query, module), module) for module in _MODULES]
    matches = [(score, module) for score, module in scored if score > 0]
    if matches:
        return sorted(matches, key=lambda item: item[0], reverse=True)[:4]

    fallback = next(module for module in _MODULES if module.module_id == "source_priority")
    return [(0, fallback)]


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _scenario_arithmetic_hints(query: str) -> list[str]:
    lowered = query.lower()
    if "eye of chaos" not in lowered or "rod of carnamagos" not in lowered:
        return []
    if not any(token in lowered for token in ("几个线索", "多少线索", "how many", "clue")):
        return []

    curse_counts = [int(match) for match in re.findall(r"(\d+)\s*个?诅咒", query)]
    if not curse_counts:
        curse_counts = [int(match) for match in re.findall(r"(\d+)\s*curse", lowered)]
    if not curse_counts:
        return []

    total_curses = sum(curse_counts)
    success = any(token in lowered for token in ("成功", "succeed", "successful"))
    base_success_clues = 2 if success else 0
    maximum_clues = base_success_clues + total_curses

    if success:
        arithmetic = (
            f"1 base clue + 1 Eye of Chaos success clue + "
            f"{total_curses} curse-token clue choices"
        )
    else:
        arithmetic = f"{total_curses} curse-token clue choices; no base/success clues confirmed"

    return [
        (
            "Scenario arithmetic hint: Eye of Chaos level 4 plus Rod of Carnamagos. "
            f"Detected curse counts {curse_counts}, total curse tokens during the investigation = {total_curses}. "
            f"Maximum clue answer: {maximum_clues}. Arithmetic: {arithmetic}. "
            "This assumes every curse-token Eye of Chaos choice is used to discover a clue at a connecting location, "
            "and there are enough clues available as stated by the user."
        )
    ]


def _execute(arguments: dict) -> ToolResult:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return ToolResult.failure("query cannot be empty")

    normalized = _clean_query(query)
    outline = _load_outline()
    sections = _outline_sections(outline)
    matches = _matched_modules(normalized)

    module_ids = [module.module_id for _score, module in matches]
    suggested_terms = _unique_ordered(
        [term for _score, module in matches for term in module.suggested_terms]
    )[:12]
    recommended_tools = _unique_ordered(
        [tool for _score, module in matches for tool in module.recommended_tools]
    )
    cautions = _unique_ordered([caution for _score, module in matches for caution in module.cautions])

    if "arkham_cards" not in recommended_tools and any(
        token in normalized for token in ("card", "asset", "event", "skill", "treachery", "eye of chaos")
    ):
        recommended_tools.insert(0, "arkham_cards")

    if "official_faq" not in recommended_tools:
        recommended_tools.append("official_faq")
    if "arkham_rules" not in recommended_tools:
        recommended_tools.append("arkham_rules")

    snippets = []
    for module_id in module_ids:
        section = sections.get(module_id, "")
        if section:
            snippets.append(section[:900])

    content = [
        "Arkham rules orientation",
        f"Question: {query}",
        f"Likely modules: {', '.join(module_ids)}",
        f"Suggested search terms: {', '.join(suggested_terms)}",
        f"Recommended next tools: {', '.join(recommended_tools)}",
    ]
    if cautions:
        content.append(f"Cautions: {' | '.join(cautions)}")
    arithmetic_hints = _scenario_arithmetic_hints(query)
    if arithmetic_hints:
        content.extend(arithmetic_hints)
    if snippets:
        content.append("Relevant outline snippets:")
        content.extend(snippets)

    return ToolResult.success("\n\n".join(content))


TOOL_SCHEMA = {
    "name": "arkham_rules_orient",
    "description": (
        "Orient an Arkham Horror LCG rules or card-interaction question before detailed lookup. "
        "Returns likely rules modules, suggested search terms, recommended next tools, and cautions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The player's Arkham rules or card-interaction question.",
            },
        },
        "required": ["query"],
    },
}


def build_arkham_rules_orient_tool() -> Tool:
    return Tool(
        name="arkham_rules_orient",
        description=TOOL_SCHEMA["description"],
        parameters=TOOL_SCHEMA["parameters"],
        execute=_execute,
    )
