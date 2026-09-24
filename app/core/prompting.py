"""Prompt assembly mechanics. Deliberately holds no prompt text.

Every agent writes its own system prompt in its own package. Nothing here
decides what an agent says — this only reads the files the agent points at,
joins them in a stable order, and caches the result.

That split is on purpose. Extracting a "shared preamble" out of the one agent
we have today would be a rewrite disguised as a refactor, and it is the single
change a byte-snapshot could not protect: the output is meant to differ, so
there would be nothing to compare against. If real commonality shows up once
three agents exist, it can be extracted then, from evidence.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def load_knowledge(knowledge_dir: Path) -> str:
    """Read and join an agent's knowledge base, in filename order.

    Cached per directory — the content never varies within a process, and this
    text is the bulk of the cached prompt prefix, so re-globbing and re-reading
    it on every user message is pure waste. Editing a knowledge file therefore
    needs a restart to take effect (the dev server's autoreload handles that).

    Keyed by directory rather than cached once globally, so two agents cannot
    end up sharing one another's knowledge — the failure this package exists to
    make structurally impossible.
    """
    if not knowledge_dir.is_dir():
        return ""
    sections: list[str] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        content = path.read_text().strip()
        if content:
            sections.append(content)
    return "\n\n".join(sections)
