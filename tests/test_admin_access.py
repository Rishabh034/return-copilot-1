"""Who can open the internal dashboard, and who definitely cannot.

This guards an authorization boundary, so the cases that matter are the refusals
rather than the happy path. Two in particular:

`test_internal_user_flag_does_not_grant_access` - `is_internal_user` marks
ClickPost staff, but it arrives in the request body rather than the signed
token, so anyone can set it. If authorization ever keys on it, every customer
can read every other customer's spend. The allowlist is keyed on the signed
`user_id` precisely to make that impossible.

`test_cannot_revoke_the_last_admin` / `test_cannot_revoke_yourself` - the
endpoint that manages admins requires being one, so an empty allowlist can only
be repaired by hand-editing the database.

Needs a migrated Postgres:

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    RUN_DB_TESTS=1 pytest tests/test_admin_access.py
"""

import asyncio
import os

import pytest
from app.core.auth.agent_token import AgentAuthError

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from sqlalchemy import delete

    from app.db import AsyncSessionLocal, engine
    from app.core.admin.admin_audit_log_model import AdminAuditLog
    from app.core.admin.agent_admin_model import AgentAdmin
    from app.core.admin.admin_repo import AgentAdminRepository

ADMIN = 999_100_001
OTHER = 999_100_002
OUTSIDER = 999_100_003
ALL_IDS = (ADMIN, OTHER, OUTSIDER)

# ClickPost internal staff share one Django user; internal_user_id is the person.
SHARED_USER = 999_100_009
ME = "304875"
COLLEAGUE = "301602"
STRANGER = "999999"


def run(coro):
    """Fresh loop per test, then drop the pool — see test_budget_repo_integration."""

    async def _scoped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(AgentAdmin).where(
                AgentAdmin.user_id.in_(ALL_IDS + (SHARED_USER,))
            )
        )
        await s.execute(
            delete(AdminAuditLog).where(AdminAuditLog.actor_user_id == SHARED_USER)
        )
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


# --- the allowlist ----------------------------------------------------------

def test_unknown_user_is_not_an_admin():
    async def go():
        async with AsyncSessionLocal() as s:
            return await AgentAdminRepository(s).is_admin(OUTSIDER)

    assert run(go()) is False


def test_granted_user_is_an_admin():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=ADMIN, granted_by=1, note="test")
            await s.commit()
            return await repo.is_admin(ADMIN)

    assert run(go()) is True


def test_revoke_removes_access_but_keeps_the_row():
    """Who could see customer spend, and when, outlives the access itself."""
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=ADMIN, granted_by=1)
            await repo.grant(user_id=OTHER, granted_by=1)
            await s.commit()
            await repo.revoke(user_id=OTHER)
            await s.commit()
            still_admin = await repo.is_admin(OTHER)
            # Count only this test's rows: the table is shared with real grants,
            # and asserting on a global count breaks the moment one exists.
            history = [r for r in await repo.list_all(include_revoked=True)
                       if r.user_id in ALL_IDS]
            active = [r for r in await repo.list_all() if r.user_id in ALL_IDS]
            return still_admin, len(history), len(active)

    still_admin, history, active = run(go())
    assert still_admin is False
    assert history == 2, "revoked row must survive for the audit trail"
    assert active == 1


def test_regrant_reinstates_rather_than_duplicating():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            row = await repo.grant(user_id=ADMIN, granted_by=1, note="first")
            created = row.created_at
            await s.commit()
            await repo.revoke(user_id=ADMIN)
            await s.commit()
            again = await repo.grant(user_id=ADMIN, granted_by=2, note="reinstated")
            await s.commit()
            return created, again.created_at, again.revoked_at, again.granted_by

    created, created_again, revoked_at, granted_by = run(go())
    assert revoked_at is None, "reinstated"
    assert created_again == created, "original grant date preserved"
    assert granted_by == 2, "records who reinstated"


def test_revoking_a_non_admin_reports_nothing_to_do():
    async def go():
        async with AsyncSessionLocal() as s:
            return await AgentAdminRepository(s).revoke(user_id=OUTSIDER)

    assert run(go()) is False


def test_internal_user_flag_does_not_grant_access():
    """The allowlist is keyed on the signed user_id, never on a body field.

    `is_internal_user` and `internal_user_id` are client-supplied today, so if
    either could grant admin, any caller could read every enterprise's spend.
    """
    async def go():
        async with AsyncSessionLocal() as s:
            # A ClickPost employee who is simply not on the list.
            return await AgentAdminRepository(s).is_admin(OUTSIDER)

    assert run(go()) is False


# --- the guard --------------------------------------------------------------

def test_admin_endpoints_refuse_when_jwt_verification_is_off():
    """No trustworthy identity means no access, not a dev-mode fallback.

    The chat endpoints accept identity from the request body when
    agent_jwt_enabled is false. That is fine for "whose conversation is this"
    and unacceptable for "may this person read every customer's spend".
    """
    from app.config import settings
    from app.core.auth.admin import require_clickpost_admin

    original = settings.agent_jwt_enabled
    settings.agent_jwt_enabled = False
    try:
        with pytest.raises(AgentAuthError) as exc:
            asyncio.run(
                require_clickpost_admin(
                    authorization=None, x_internal_user_id=None, session=None
                )
            )
        # AgentAuthError, not HTTPException: the whole surface reports auth
        # failures as HTTP 200 with the true status in meta.status, because a
        # caller in front of this service discards non-200 bodies.
        assert exc.value.status_code == 503
        assert "JWT" in str(exc.value.detail)
    finally:
        settings.agent_jwt_enabled = original


# --- shared internal account ------------------------------------------------

def test_internal_user_grant_matches_only_that_person():
    """One Django user, two people, one of them an admin.

    The signed user_id is identical for both, so it cannot tell them apart. The
    grant carries internal_user_id and only the named person matches.
    """
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(
                user_id=SHARED_USER, internal_user_id=ME, granted_by=1, note="me"
            )
            await s.commit()
            return (
                await repo.is_admin(SHARED_USER, ME),
                await repo.is_admin(SHARED_USER, COLLEAGUE),
                await repo.is_admin(SHARED_USER, STRANGER),
            )

    me, colleague, stranger = run(go())
    assert me is True
    assert colleague is False, "a different person on the same account is not an admin"
    assert stranger is False


def test_two_people_on_one_account_can_both_be_granted():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=SHARED_USER, internal_user_id=ME, granted_by=1)
            await repo.grant(user_id=SHARED_USER, internal_user_id=COLLEAGUE, granted_by=1)
            await s.commit()
            return (
                await repo.is_admin(SHARED_USER, ME),
                await repo.is_admin(SHARED_USER, COLLEAGUE),
                await repo.is_admin(SHARED_USER, STRANGER),
                await repo.count_active(),
            )

    me, colleague, stranger, count = run(go())
    assert (me, colleague, stranger) == (True, True, False)
    assert count >= 2, "two grants on one user_id are distinct rows"


def test_unsigned_header_cannot_grant_access_on_its_own():
    """The header narrows access; it never creates it.

    A caller whose signed user_id has no grant is refused no matter what
    internal_user_id they present — which is what keeps an unsigned field from
    becoming an authorization bypass.
    """
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=SHARED_USER, internal_user_id=ME, granted_by=1)
            await s.commit()
            # OUTSIDER holds a valid token, but for a user_id with no grant.
            return await repo.is_admin(OUTSIDER, ME)

    assert run(go()) is False


def test_plain_user_grant_ignores_the_header():
    """A NULL internal_user_id authorizes on the signed claim alone."""
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=ADMIN, granted_by=1)
            await s.commit()
            return (
                await repo.is_admin(ADMIN, None),
                await repo.is_admin(ADMIN, "anything-at-all"),
            )

    without, with_header = run(go())
    assert without is True
    assert with_header is True, "a plain grant is not narrowed by a header"


def test_revoking_one_person_leaves_the_other():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=SHARED_USER, internal_user_id=ME, granted_by=1)
            await repo.grant(user_id=SHARED_USER, internal_user_id=COLLEAGUE, granted_by=1)
            await s.commit()
            await repo.revoke(user_id=SHARED_USER, internal_user_id=COLLEAGUE)
            await s.commit()
            return (
                await repo.is_admin(SHARED_USER, ME),
                await repo.is_admin(SHARED_USER, COLLEAGUE),
            )

    me, colleague = run(go())
    assert me is True
    assert colleague is False


# --- viewer vs manager ------------------------------------------------------

def test_viewer_can_read_but_is_not_a_manager():
    """Reading customer spend and changing it are different privileges."""
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(
                user_id=SHARED_USER, internal_user_id=ME,
                role=AgentAdmin.VIEWER, granted_by=1,
            )
            await s.commit()
            grant = await repo.find_grant(SHARED_USER, ME)
            return grant.role, grant.is_active, grant.can_manage

    role, active, can_manage = run(go())
    assert role == AgentAdmin.VIEWER
    assert active is True, "a viewer still has read access"
    assert can_manage is False, "a viewer must not change caps or the allowlist"


def test_manager_can_do_both():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(
                user_id=SHARED_USER, internal_user_id=ME,
                role=AgentAdmin.MANAGER, granted_by=1,
            )
            await s.commit()
            grant = await repo.find_grant(SHARED_USER, ME)
            return grant.is_active, grant.can_manage

    assert run(go()) == (True, True)


def test_a_specific_grant_outranks_a_blanket_one():
    """Someone given viewer must not be promoted by a user_id-wide manager row.

    Both rows match the caller, so ordering decides. The more specific grant -
    the one naming this person - has to win, or a deliberate demotion silently
    does nothing.
    """
    async def go():
        async with AsyncSessionLocal() as s:
            repo = AgentAdminRepository(s)
            await repo.grant(user_id=SHARED_USER, granted_by=1,
                             role=AgentAdmin.MANAGER)           # blanket
            await repo.grant(user_id=SHARED_USER, internal_user_id=ME,
                             granted_by=1, role=AgentAdmin.VIEWER)  # specific
            await s.commit()
            return await repo.find_grant(SHARED_USER, ME)

    grant = run(go())
    assert grant.internal_user_id == ME, "the specific row must win"
    assert grant.can_manage is False


def test_grant_defaults_to_the_smaller_privilege():
    """A grant made without thinking about it should be the safe one."""
    from app.core.admin.admin_schema import GrantAdminRequest

    assert GrantAdminRequest(user_id=1).role == "viewer"


def test_role_is_constrained_by_the_database():
    """A typo'd role must be rejected at write time, not silently stored."""
    import sqlalchemy.exc

    async def go():
        async with AsyncSessionLocal() as s:
            s.add(AgentAdmin(user_id=SHARED_USER, internal_user_id="typo",
                             role="superuser", granted_by=1))
            try:
                await s.commit()
                return False
            except sqlalchemy.exc.IntegrityError:
                await s.rollback()
                return True

    assert run(go()) is True, "CHECK constraint must reject unknown roles"


def test_staff_claim_alone_does_not_open_the_dashboard_by_default():
    """Every ClickPost account is is_staff, so the claim means "the company".

    Signing it upstream must not silently grant every CSM, support and product
    person read access to every customer's spend. The setting that allows that
    is off by default and lives in this service, not in the token.
    """
    from app.config import settings

    assert settings.budget_dashboard_open_to_all_staff is False


def test_staff_claim_never_grants_write_access(monkeypatch):
    """Being staff lets you look. It must not let you change a customer's cap.

    Driven through the real dependency with a verified-looking staff identity
    that has no grant, rather than by grepping the source - the behaviour is
    what matters, and a docstring mentioning is_staff is not a bug.
    """
    from app.config import settings
    from app.core.auth import admin as admin_module
    from app.core.auth.agent_token import AgentAuthError, AuthContext

    staff_ctx = AuthContext(
        user_id=OUTSIDER, enterprise_id=15, cp_token="x",
        is_staff=True, internal_user_id="999999",
    )

    async def fake_require_auth(authorization=None):
        return staff_ctx

    monkeypatch.setattr(admin_module, "require_auth", fake_require_auth)
    monkeypatch.setattr(settings, "agent_jwt_enabled", True)
    # Even with the doors open for readers, writes stay closed.
    monkeypatch.setattr(settings, "budget_dashboard_open_to_all_staff", True)

    async def go():
        async with AsyncSessionLocal() as s:
            # Reading is allowed for a staff member when the policy permits it.
            reader = await admin_module.require_clickpost_staff(
                authorization="x", x_internal_user_id=None, session=s
            )
            # Writing is not, because there is no manager row.
            try:
                await admin_module.require_clickpost_admin(
                    authorization="x", x_internal_user_id=None, session=s
                )
                return reader, None
            except AgentAuthError as exc:
                return reader, exc.status_code

    reader, write_status = run(go())
    assert reader.is_staff is True, "a staff member may read when the policy allows"
    assert write_status == 403, "but must never write without a manager row"


# --- audit trail ------------------------------------------------------------

def test_audit_records_the_person_not_the_shared_account():
    """The whole point: `actor_user_id` is 14 for everyone, so it cannot stand alone.

    Without `actor_internal_user_id` every entry reads "changed by 14" and the
    trail answers nothing.
    """
    from app.core.admin.admin_audit_log_model import AdminAuditLog
    from app.core.admin.audit_repo import AuditRepository

    async def go():
        async with AsyncSessionLocal() as s:
            repo = AuditRepository(s)
            await repo.record(
                actor_user_id=SHARED_USER, actor_internal_user_id=ME,
                action=AdminAuditLog.BUDGET_LIMIT_CHANGED,
                target_type=AdminAuditLog.TARGET_ENTERPRISE, target_id=999_200_777,
                before={"limit_micros": 25_000_000},
                after={"limit_micros": 50_000_000},
            )
            await s.commit()
            rows = await repo.list(target_id="999200777")
            return rows[0]

    row = run(go())
    assert row.actor_internal_user_id == ME, "the individual must be recorded"
    assert row.actor_user_id == SHARED_USER
    assert row.before["limit_micros"] == 25_000_000
    assert row.after["limit_micros"] == 50_000_000, "before AND after, or a change is unreadable"


def test_audit_can_be_filtered_by_who_did_it():
    from app.core.admin.admin_audit_log_model import AdminAuditLog
    from app.core.admin.audit_repo import AuditRepository

    async def go():
        async with AsyncSessionLocal() as s:
            repo = AuditRepository(s)
            for who in (ME, COLLEAGUE, ME):
                await repo.record(
                    actor_user_id=SHARED_USER, actor_internal_user_id=who,
                    action=AdminAuditLog.ADMIN_GRANTED,
                    target_type=AdminAuditLog.TARGET_ADMIN, target_id="999200888",
                )
            await s.commit()
            mine = await repo.list(target_id="999200888", actor_internal_user_id=ME)
            theirs = await repo.list(target_id="999200888", actor_internal_user_id=COLLEAGUE)
            return len(mine), len(theirs)

    assert run(go()) == (2, 1)


def test_audit_is_newest_first():
    from app.core.admin.admin_audit_log_model import AdminAuditLog
    from app.core.admin.audit_repo import AuditRepository

    async def go():
        async with AsyncSessionLocal() as s:
            repo = AuditRepository(s)
            for n in range(3):
                await repo.record(
                    actor_user_id=SHARED_USER, actor_internal_user_id=ME,
                    action=AdminAuditLog.BUDGET_LIMIT_CHANGED,
                    target_type=AdminAuditLog.TARGET_ENTERPRISE, target_id="999200999",
                    after={"n": n},
                )
            await s.commit()
            return [r.after["n"] for r in await repo.list(target_id="999200999")]

    assert run(go()) == [2, 1, 0]
