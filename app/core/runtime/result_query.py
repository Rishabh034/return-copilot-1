from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "that",
        "this",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "it",
        "they",
        "them",
        "their",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "so",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "to",
        "with",
        "about",
        "all",
        "any",
        "not",
        "no",
        "yes",
        "show",
        "tell",
        "give",
        "get",
        "find",
        "explain",
        "why",
        "please",
        "just",
        "also",
        "only",
        "was",
        "were",
        "did",
        "does",
    }
)


def extract_query_keywords(text: str) -> list[str]:
    """Tokenize user text into keywords for JSON matching."""
    tokens = re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def _keyword_matches(keyword: str, text: str) -> bool:
    return keyword in text.lower()


def _value_matches(keywords: list[str], key: str, value: Any) -> bool:
    key_lower = key.lower()
    for keyword in keywords:
        if _keyword_matches(keyword, key_lower):
            return True
        if isinstance(value, str) and _keyword_matches(keyword, value):
            return True
        if isinstance(value, (int, float, bool)) and _keyword_matches(keyword, str(value)):
            return True
    return False


def _walk_json(
    value: Any,
    keywords: list[str],
    path: str,
    matches: list[dict[str, Any]],
    max_matches: int,
) -> None:
    if len(matches) >= max_matches:
        return

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if _value_matches(keywords, key, child):
                matches.append({"path": child_path, "value": child})
                if len(matches) >= max_matches:
                    return
            _walk_json(child, keywords, child_path, matches, max_matches)
            if len(matches) >= max_matches:
                return
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            if _value_matches(keywords, str(index), item):
                matches.append({"path": child_path, "value": item})
                if len(matches) >= max_matches:
                    return
            _walk_json(item, keywords, child_path, matches, max_matches)
            if len(matches) >= max_matches:
                return


def match_json_keywords(
    data: dict[str, Any],
    keywords: list[str],
    *,
    max_matches: int = 20,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Return keyword-matched JSON subtrees with paths."""
    if not keywords:
        return {"match_count": 0, "matches": [], "truncated": False}

    char_limit = max_chars or settings.tool_result_query_max_chars
    matches: list[dict[str, Any]] = []
    _walk_json(data, keywords, "", matches, max_matches)

    serialized: list[dict[str, Any]] = []
    total_chars = 0
    truncated = len(matches) >= max_matches

    for match in matches:
        entry = {"path": match["path"], "value": match["value"]}
        entry_chars = len(json.dumps(entry, default=str))
        if total_chars + entry_chars > char_limit and serialized:
            truncated = True
            break
        serialized.append(entry)
        total_chars += entry_chars

    return {
        "match_count": len(serialized),
        "matches": serialized,
        "truncated": truncated,
        "keywords": keywords,
    }


def build_auto_prefetch_message(
    stored_results: list[Any],
    keywords: list[str],
) -> str | None:
    """Build an injected HumanMessage body from auto keyword pre-fetch, or None if no matches."""
    if not keywords or not stored_results:
        return None

    sections: list[str] = []
    total_chars = 0
    char_limit = settings.tool_result_query_max_chars

    for stored in stored_results:
        matched = match_json_keywords(
            stored.result,
            keywords,
            max_matches=10,
            max_chars=char_limit // max(len(stored_results), 1),
        )
        if not matched["matches"]:
            continue

        section = json.dumps(matched, default=str, indent=2)
        if total_chars + len(section) > char_limit:
            break
        sections.append(section)
        total_chars += len(section)

    if not sections:
        return None

    keyword_list = ", ".join(keywords)
    return (
        f"[Relevant prior evidence for: {keyword_list}]\n\n"
        + "\n\n".join(sections)
    )
