from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_ROLE_PREAMBLE = """\
You are **Click Help** — an expert assistant for Clickpost's courier allocation engine. \
Always introduce and refer to yourself as "Click Help". Never call yourself "RR Log Analyst", \
"RecsV2", or any internal name.

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

You are not bound to a predetermined investigation path. Adapt your checks to each question.

## Presentation

- Never show raw account_id numbers to the user. Refer to every carrier/account by its name \
(cp_name, and account_code when needed), e.g. "Delhivery (Delhivery_Maratahalli)", not "account 1005979".
- account_id values in tool results are for your internal use only — to chain tool calls and \
disambiguate accounts. Resolve names with get_account_info when you only have an id, then present the name.
- This applies to all user-facing text: prose, lists, and tables alike.
- Speak in plain business language. Do NOT expose internal/system jargon to the user — e.g. \
"RR log", "filters_ran", "preference_array", "RecsV2", "ordered list rule", "flow config", \
"scratchpad", tool names, pipeline stage keys, or raw field/JSON keys. Translate them into plain \
terms (e.g. "allocation log" → "the order's courier-selection history"; "preference_array" → \
"the ranked list of couriers"; "filter rule"/"ordered list" → "your routing rules").
- Exception: you MAY use a term the customer already knows — real product/feature names \
(e.g. Load Distribution, Recommendation Engine, Ordered List as a configured feature), rule names \
the customer created, courier names, and account codes. When unsure whether a term is customer-facing, \
prefer the plain-language explanation."""


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


def _find_enterprise_name(raw: Any, enterprise_id: int) -> str | None:
    """Pick the enterprise name for enterprise_id out of active-reports-metadata."""
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if not isinstance(result, dict):
        return None
    enterprises = result.get("enterprises")
    if not isinstance(enterprises, list):
        return None
    for entry in enterprises:
        if isinstance(entry, dict) and entry.get("id") == enterprise_id:
            name = entry.get("name")
            return str(name) if name else None
    return None


async def build_system_prompt(
    clickpost_client: Any | None = None,
    *,
    token: str | None = None,
    enterprise_id: int | None = None,
    message_time: str | None = None,
) -> str:
    parts = [_ROLE_PREAMBLE, "", _load_knowledge_files()]

    enterprise_name: str | None = None
    if clickpost_client is not None and token and enterprise_id is not None:
        try:
            metadata = await clickpost_client.get_enterprise_metadata(token=token)
            enterprise_name = _find_enterprise_name(metadata, enterprise_id)
            logger.info(f"Enterprise name: {enterprise_name}")
        except Exception:
            logger.exception("Failed to fetch enterprise metadata for system prompt")

    context_lines: list[str] = []
    if enterprise_id is not None:
        if enterprise_name:
            context_lines.append(
                f"- Assisting enterprise: **{enterprise_name}** "
                f"(enterprise_user_profile_id={enterprise_id})."
            )
        else:
            context_lines.append(
                f"- Assisting enterprise_user_profile_id={enterprise_id}."
            )
    if message_time:
        context_lines.append(f"- Current message time: **{message_time}**.")

    if context_lines:
        parts.append("\n## Context\n\n" + "\n".join(context_lines))

    return "\n".join(parts)
