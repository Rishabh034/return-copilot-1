from __future__ import annotations

from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_ROLE_PREAMBLE = (
    "You are the RR Log Analyst — an AI assistant that helps users understand "
    "Clickpost's courier allocation (recommendation) decisions."
)


def _load_knowledge_files() -> str:
    """Load all .md files from the knowledge directory, sorted by filename."""
    if not _KNOWLEDGE_DIR.is_dir():
        return ""
    files = sorted(_KNOWLEDGE_DIR.glob("*.md"))
    sections: list[str] = []
    for f in files:
        content = f.read_text().strip()
        if content:
            sections.append(content)
    return "\n\n".join(sections)


def build_system_prompt(enterprise_name: str | None = None) -> str:
    parts = [_ROLE_PREAMBLE, "", _load_knowledge_files()]

    if enterprise_name:
        parts.append(
            f"\n## Context\n\nAssisting enterprise: **{enterprise_name}**. "
            "All tool calls are automatically scoped to this enterprise."
        )

    return "\n".join(parts)
