"""Build a per-CPID context bundle by skimming the Clickpost checkout."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.agenticintegrations.utility.locator import CourierLocation, extract_top_level_class
from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
_TEXT_SUFFIXES = {".py", ".json", ".xml", ".wsdl", ".yml", ".yaml", ".md", ".txt"}
_MAX_FILE_CHARS = 80_000
_SIGNUP_IMPORT_RE = re.compile(
    r"from\s+signup\.models\s+import\s+\(?\s*([A-Za-z0-9_ ,\n]+)\s*\)?",
    re.M,
)
_CREDENTIAL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:Credential|Credentials)$")
_CLASS_NAME_RE = re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.M)


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _iter_service_files(service_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(service_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        files.append(path)
    return files


def _file_priority(path: Path, location: CourierLocation) -> tuple[int, str]:
    name = path.name.lower()
    if location.class_file and path.resolve() == location.class_file.resolve():
        return (0, name)
    if "order_creation" in name:
        return (1, name)
    if "credential" in name:
        return (2, name)
    if path.suffix == ".py":
        return (3, name)
    return (4, name)


def _read_capped(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_FILE_CHARS:
        return text[:_MAX_FILE_CHARS] + "\n\n# ... truncated ...\n"
    return text


def _credential_names_from_sources(sources: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for match in _SIGNUP_IMPORT_RE.finditer(source):
            for raw in match.group(1).replace("\n", " ").split(","):
                name = raw.strip()
                if " as " in name:
                    name = name.split(" as ", 1)[1].strip()
                if _CREDENTIAL_NAME_RE.match(name) and name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _credential_names_from_models(models_source: str, enum_name: str) -> list[str]:
    needle = enum_name.replace("_", "").lower()
    matches: list[str] = []
    for class_name in _CLASS_NAME_RE.findall(models_source):
        compact = class_name.replace("_", "").lower()
        if "credential" not in compact:
            continue
        if needle and needle in compact:
            matches.append(class_name)
    return matches


def _dump_credential_models(repo: Path, class_names: list[str]) -> str:
    models_path = repo / "signup" / "models.py"
    if not models_path.is_file() or not class_names:
        return ""
    source = models_path.read_text(encoding="utf-8", errors="replace")
    chunks: list[str] = []
    seen: set[str] = set()
    for name in class_names:
        if name in seen:
            continue
        seen.add(name)
        block = extract_top_level_class(source, name)
        if not block:
            continue
        chunks.append(
            f"===== CREDENTIAL MODEL: {name} (signup/models.py) =====\n{block}\n"
        )
    return "\n".join(chunks)


def build_courier_context(repo: Path, location: CourierLocation) -> str:
    parts: list[str] = [
        f"# Context bundle for CPID {location.cp_id} "
        f"(CourierPartnerEnum.{location.enum_name})\n",
    ]
    if location.order_creation_class:
        parts.append(f"# Order creation class: {location.order_creation_class}\n")

    sources_for_creds: list[str] = []
    dumped_chars = 0
    max_chars = settings.ONBOARDING_CONTEXT_MAX_CHARS

    if location.service_dir and location.service_dir.is_dir():
        files = sorted(
            _iter_service_files(location.service_dir),
            key=lambda path: _file_priority(path, location),
        )
        tree = "\n".join(f"- {_rel(repo, path)}" for path in files)
        parts.append(
            f"===== SERVICE FOLDER: {_rel(repo, location.service_dir)}"
            + (
                f" (class {location.order_creation_class})"
                if location.order_creation_class
                else ""
            )
            + " =====\n"
        )
        parts.append(f"## File tree\n{tree}\n")

        for path in files:
            body = _read_capped(path)
            sources_for_creds.append(body)
            chunk = f"----- FILE: {_rel(repo, path)} -----\n{body.rstrip()}\n"
            if dumped_chars + len(chunk) > max_chars:
                remaining = max(0, max_chars - dumped_chars)
                if remaining > 200:
                    parts.append(chunk[:remaining] + "\n# ... truncated for context budget ...\n")
                parts.append(
                    f"# Stopped dumping further files after {_rel(repo, path)} "
                    f"(onboarding_context_max_chars={max_chars}).\n"
                )
                dumped_chars = max_chars
                break
            parts.append(chunk)
            dumped_chars += len(chunk)
    else:
        parts.append(
            "# WARNING: no service folder could be resolved for this CPID. "
            "Credential model (if found) is the only local code context.\n"
        )

    cred_names = _credential_names_from_sources(sources_for_creds)
    models_path = repo / "signup" / "models.py"
    if models_path.is_file() and not cred_names:
        models_source = models_path.read_text(encoding="utf-8", errors="replace")
        cred_names = _credential_names_from_models(models_source, location.enum_name)

    creds_block = _dump_credential_models(repo, cred_names)
    if creds_block:
        parts.append(creds_block)
    else:
        parts.append(
            "# WARNING: credential model was not auto-resolved in signup/models.py.\n"
        )

    context = "\n".join(parts).strip() + "\n"
    logger.info(
        "Built context for cp_id=%s enum=%s chars=%d service_dir=%s creds=%s",
        location.cp_id,
        location.enum_name,
        len(context),
        location.service_dir,
        cred_names,
    )
    return context
