"""Resolve a CPID to the courier's name and service folder in the Clickpost repo."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_ENUM_MEMBER_RE = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)\s*$", re.M)
_MAPPING_RE = re.compile(
    r"CourierPartnerEnum\.([A-Za-z_][A-Za-z0-9_]*)\.value\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(",
)
_IMPORT_RE = re.compile(
    r"^from\s+([\w.]+)\s+import\s+\(?\s*([A-Za-z0-9_ ,\n]+)\s*\)?",
    re.M,
)


class CourierNotFoundError(Exception):
    def __init__(self, cp_id: int):
        self.cp_id = cp_id
        super().__init__(f"No courier partner found for CPID {cp_id} in the Clickpost repo")


@dataclass(frozen=True)
class CourierLocation:
    cp_id: int
    enum_name: str
    display_name: str
    order_creation_class: str | None
    class_file: Path | None
    service_dir: Path | None


def extract_top_level_class(source: str, class_name: str) -> str | None:
    """Return the source of a column-0 `class Name` through the last indented line."""
    lines = source.splitlines(keepends=True)
    header = re.compile(rf"^class {re.escape(class_name)}\b")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() == "" or line[0] in " \t#":
            end += 1
            continue
        break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[start:end])


def parse_courier_partner_enum(source: str) -> dict[int, str]:
    """Returns {cp_id: enum member name} from CourierPartnerEnum."""
    block = extract_top_level_class(source, "CourierPartnerEnum")
    if not block:
        return {}
    return {int(cp_id): name for name, cp_id in _ENUM_MEMBER_RE.findall(block)}


def parse_import_map(source: str) -> dict[str, str]:
    """Returns {ClassName: dotted.module.path} from `from x import Y` lines."""
    mapping: dict[str, str] = {}
    for match in _IMPORT_RE.finditer(source):
        module = match.group(1)
        names = match.group(2).replace("\n", " ").replace("(", "").replace(")", "")
        for raw in names.split(","):
            name = raw.strip()
            if not name or name == "\\":
                continue
            if " as " in name:
                name = name.split(" as ", 1)[1].strip()
            mapping[name] = module
    return mapping


def parse_order_creation_mapping(source: str) -> dict[str, str]:
    """Returns {enum_name: OrderCreationClassName} from courier_partner_mapping."""
    return {enum_name: class_name for enum_name, class_name in _MAPPING_RE.findall(source)}


def _module_to_path(repo: Path, dotted: str) -> Path:
    return repo.joinpath(*dotted.split(".")).with_suffix(".py")


def _display_name(enum_name: str) -> str:
    return enum_name.replace("_", " ").title()


def _find_class_file(repo: Path, class_name: str) -> Path | None:
    search_roots = [
        repo / "clickpost" / "service",
        repo / "order_services",
        repo / "clickpost" / "views",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if f"class {class_name}" in text:
                return path
    return None


def _guess_service_dir(repo: Path, enum_name: str) -> Path | None:
    slug = enum_name.lower()
    compact = slug.replace("_", "")
    candidates = {slug, compact, slug.replace("_", "-")}
    roots = [repo / "clickpost" / "service", repo / "order_services" / "b2b_courier_service"]
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower().replace("-", "_")
            compact_name = name.replace("_", "")
            if name in candidates or compact_name in {compact, slug.replace("_", "")}:
                return child
    return None


def locate_courier(repo: Path, cp_id: int) -> CourierLocation:
    enum_path = repo / "utility" / "courier_partner.py"
    if not enum_path.is_file():
        raise CourierNotFoundError(cp_id)

    enum_by_id = parse_courier_partner_enum(
        enum_path.read_text(encoding="utf-8", errors="replace")
    )
    enum_name = enum_by_id.get(cp_id)
    if not enum_name:
        raise CourierNotFoundError(cp_id)

    mapping_path = repo / "clickpost" / "views" / "ordercreation" / "clickpost_order_creation.py"
    class_name: str | None = None
    class_file: Path | None = None
    service_dir: Path | None = None

    if mapping_path.is_file():
        mapping_source = mapping_path.read_text(encoding="utf-8", errors="replace")
        class_name = parse_order_creation_mapping(mapping_source).get(enum_name)
        if class_name:
            import_map = parse_import_map(mapping_source)
            dotted = import_map.get(class_name)
            if dotted:
                candidate = _module_to_path(repo, dotted)
                if candidate.is_file():
                    class_file = candidate
            if class_file is None:
                class_file = _find_class_file(repo, class_name)

    if class_file is None:
        service_dir = _guess_service_dir(repo, enum_name)
    else:
        service_dir = class_file.parent

    if service_dir is None or not service_dir.is_dir():
        logger.warning(
            "No service folder resolved for cp_id=%s enum=%s class=%s",
            cp_id,
            enum_name,
            class_name,
        )

    return CourierLocation(
        cp_id=cp_id,
        enum_name=enum_name,
        display_name=_display_name(enum_name),
        order_creation_class=class_name,
        class_file=class_file,
        service_dir=service_dir,
    )
