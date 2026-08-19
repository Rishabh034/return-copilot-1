"""
Loads an agent's base system prompt from prompts/<prompt_name>.md.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_base_prompt(prompt_name: str) -> str:
    return _read(PROMPTS_DIR / f"{prompt_name}.md")


def build_system_prompt(prompt_name: str) -> str:
    """Main entrypoint: assemble the full system prompt for a given turn."""
    return load_base_prompt(prompt_name)
