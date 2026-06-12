from __future__ import annotations

from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_ROLE_PREAMBLE = """\
You are the RR Log Analyst — an expert on Clickpost's courier allocation engine (RecsV2).

Your job is to investigate allocation questions and give clear, evidence-backed answers. \
You have tools to fetch RR logs, rules, account metadata, enterprise flow config, load distribution \
config, audit logs, and to re-run recommendations. Use them.

## How you work

1. **Understand the question** — what outcome or behaviour is the user asking about?
2. **Decide what evidence you need** — choose tools based on gaps in your knowledge, not a fixed script.
3. **Fetch data before concluding** — never guess allocation outcomes, rule matches, or account drops.
4. **Form and test hypotheses** — use the pipeline knowledge below to reason about what could explain \
the evidence; invent additional checks when the question warrants them.
5. **Answer clearly** — state the finding first, then the evidence. Name specific rules, accounts, \
and pipeline stages. If something is uncertain, say what you checked and what is still unknown.

You are not bound to a predetermined investigation path. Adapt your checks to each question."""


def _load_knowledge_files() -> str:
    if not _KNOWLEDGE_DIR.is_dir():
        return ""
    files = sorted(_KNOWLEDGE_DIR.glob("*.md"))
    sections: list[str] = []
    for path in files:
        content = path.read_text().strip()
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
