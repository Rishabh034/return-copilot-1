"""app.core.registry — pure in-memory agent discovery, no DB involved.

`_discover()` is `@lru_cache(maxsize=1)` and scans `app/agents/` once per
process. Nothing else in this suite triggers it during collection (verified:
no `conftest.py`, and `registry.enabled()` is only called inside
`app.main.create_app()`'s function body), so this file is the first to
populate the cache — and the duplicate-slug test below has to explicitly
`cache_clear()` before and after itself, or the throwaway packages it injects
would either be missed (if it ran after the cache was already warm) or leak
into every other test in the same process (if left populated).
"""

from __future__ import annotations

import sys

import pytest

from app.core import registry


def test_enabled_excludes_the_disabled_scaffold_agent():
    enabled = [spec.slug for spec in registry.enabled()]
    assert "allocation" in enabled
    assert "example" not in enabled


def test_get_resolves_a_disabled_agent_regardless_of_the_enabled_flag():
    assert registry.get("example").slug == "example"
    assert registry.get("example").enabled is False


def test_get_raises_key_error_naming_the_known_slugs():
    with pytest.raises(KeyError) as exc:
        registry.get("does-not-exist")
    message = str(exc.value)
    assert "does-not-exist" in message
    assert "allocation" in message


def test_all_specs_is_sorted_by_slug():
    slugs = [spec.slug for spec in registry.all_specs()]
    assert slugs == sorted(slugs)


def test_duplicate_agent_slug_across_two_packages_fails_at_discovery(tmp_path, monkeypatch):
    """Mirrors the real failure mode: two agent packages claiming the same
    slug must raise at import/discovery time, not surface as a silent mount
    conflict later. Injected via a throwaway path on app.agents.__path__ so
    this never touches the real app/agents/ directory.
    """
    import app.agents

    for i, name in enumerate(("zz_dupe_a", "zz_dupe_b")):
        pkg_dir = tmp_path / name
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            "from app.core.spec import AgentSpec, RuntimeSettings\n"
            "SPEC = AgentSpec(\n"
            "    slug='zz-dupe',\n"
            f"    url_prefix='zz-dupe-{i}',\n"
            "    display_name='Duplicate',\n"
            "    build_tools=lambda ctx: [],\n"
            "    build_system_prompt=lambda: '',\n"
            "    build_context_note=lambda **kw: '',\n"
            "    runtime=RuntimeSettings.from_settings(),\n"
            "    enabled=False,\n"
            ")\n"
        )

    monkeypatch.setattr(app.agents, "__path__", list(app.agents.__path__) + [str(tmp_path)])
    registry._discover.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="duplicate agent slug"):
            registry.all_specs()
    finally:
        # Leave the cache empty rather than eagerly repopulating it here:
        # monkeypatch only restores app.agents.__path__ *after* this function
        # returns, so calling all_specs() while still inside this block would
        # hit the exact same injected duplicate again. The next real caller,
        # once the path is back to normal, repopulates it lazily.
        registry._discover.cache_clear()
        sys.modules.pop("app.agents.zz_dupe_a", None)
        sys.modules.pop("app.agents.zz_dupe_b", None)
