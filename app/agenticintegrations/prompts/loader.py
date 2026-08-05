"""
Composes the final system prompt from:
  - a base prompt (prompts/system_prompt.md)
  - reusable fragments (prompts/fragments/*.md)
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_base_prompt() -> str:
    return _read(PROMPTS_DIR / "system_prompt.md")


def load_fragment(name: str) -> str:
    """e.g. load_fragment('persona') -> prompts/fragments/persona.md"""
    return _read(PROMPTS_DIR / "fragments" / f"{name}.md")


def load_all_fragments(order: list[str] | None = None) -> str:
    frag_dir = PROMPTS_DIR / "fragments"
    files = [f.stem for f in frag_dir.glob("*.md")]
    if order:
        files = [f for f in order if f in files] + [f for f in files if f not in order]
    return "\n\n".join(load_fragment(f) for f in files)


def build_system_prompt(
    fragment_order: list[str] | None = None
) -> str:
    """Main entrypoint: assemble the full system prompt for a given turn."""
    parts = [load_base_prompt()]

    fragments = load_all_fragments(fragment_order)
    if fragments:
        parts.append(fragments)

    return "\n\n---\n\n".join(parts)

