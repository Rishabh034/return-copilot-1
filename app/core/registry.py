"""Finds the agents. Adding one is adding a directory, not editing this file.

Every package under `app/agents/` exposing a module-level `SPEC` is an agent.
Discovery is deliberately eager and loud: a duplicate slug or url_prefix, or a
package that forgot its SPEC, fails at import rather than at 3am.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

import app.agents
from app.core.spec import AgentSpec


@lru_cache(maxsize=1)
def _discover() -> dict[str, AgentSpec]:
    found: dict[str, AgentSpec] = {}
    prefixes: dict[str, str] = {}

    for module_info in pkgutil.iter_modules(app.agents.__path__):
        if not module_info.ispkg:
            continue
        package = f"{app.agents.__name__}.{module_info.name}"
        module = importlib.import_module(package)
        spec = getattr(module, "SPEC", None)
        if spec is None:
            raise RuntimeError(f"{package} is under app/agents/ but exposes no SPEC")
        if spec.slug in found:
            raise RuntimeError(f"duplicate agent slug {spec.slug!r} in {package}")
        if spec.url_prefix in prefixes:
            raise RuntimeError(
                f"agents {prefixes[spec.url_prefix]!r} and {spec.slug!r} both claim "
                f"/api/{spec.url_prefix}"
            )
        found[spec.slug] = spec
        prefixes[spec.url_prefix] = spec.slug

    return found


def all_specs() -> list[AgentSpec]:
    return sorted(_discover().values(), key=lambda spec: spec.slug)


def enabled() -> list[AgentSpec]:
    return [spec for spec in all_specs() if spec.enabled]


def get(slug: str) -> AgentSpec:
    specs = _discover()
    if slug not in specs:
        raise KeyError(f"no agent {slug!r}; known: {sorted(specs)}")
    return specs[slug]
