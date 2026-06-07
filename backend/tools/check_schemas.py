#!/usr/bin/env python3
"""检查 tool schemas 是否正确注入"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gamevoice_server.skill_runner import ToolRegistry
from gamevoice_server.skill_runner.tools import (
    build_arkham_rules_tool,
    build_arkham_cards_tool,
    build_official_faq_tool,
    build_web_faq_tool,
    build_file_reader_tool,
    build_web_search_tool,
)

registry = ToolRegistry()
registry.register(build_arkham_rules_tool())
registry.register(build_arkham_cards_tool())
registry.register(build_official_faq_tool())
registry.register(build_web_faq_tool())
registry.register(build_file_reader_tool())
registry.register(build_web_search_tool())

schemas = registry.tool_schemas()
import json
print(json.dumps(schemas, ensure_ascii=False, indent=2))
