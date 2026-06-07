import re


_CARD_QUERY_HINTS = (
    "card",
    "effect",
    "text",
    "what does",
    "how does",
    "card text",
    "卡牌",
    "效果",
    "文本",
    "作用",
)

_ARKHAM_QUERY_KEYWORDS = (
    "阿卡姆",
    "诡镇奇谭",
    "AHLCG",
    "诡镇奇谈",
    "诡镇",
    "Arkham",
)


def _looks_like_card_query(query: str) -> bool:
    lowered = query.lower()
    if any(hint in lowered or hint in query for hint in _CARD_QUERY_HINTS):
        return True
    if re.search(r"\b[A-Z][A-Za-z0-9'’.-]+(?:\s+[A-Z][A-Za-z0-9'’.-]+)+\b", query):
        return True
    if re.search(r"\b\d{5}\b", query):
        return True
    return False


def _looks_like_arkham_query(query: str) -> bool:
    lowered = query.lower()
    return any(kw in lowered or kw in query for kw in _ARKHAM_QUERY_KEYWORDS)


def looks_like_arkham_or_card_query(query: str) -> bool:
    """Return True if query matches Arkham or card keywords, triggering async SkillAgent."""
    return _looks_like_arkham_query(query) or _looks_like_card_query(query)


class RulesRouter:
    def __init__(self, local_index, card_index=None, remote_lookup=None) -> None:
        self.local_index = local_index
        self.card_index = card_index
        self.remote_lookup = remote_lookup or (lambda query: "fallback")

    def lookup_rules(self, query: str) -> dict:
        hit = self.local_index.search(query)
        if hit:
            return {"source": "local", "answer": hit}
        if _looks_like_arkham_query(query):
            return {"source": "local", "answer": None}
        if self.card_index is not None and _looks_like_card_query(query):
            card_hit = self.card_index.search(query)
            if card_hit:
                return {"source": "local", "answer": card_hit}
        return {"source": "remote", "answer": self.remote_lookup(query)}
