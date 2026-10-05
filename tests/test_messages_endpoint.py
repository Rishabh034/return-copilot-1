"""POST /messages/ end to end — the budget hold through every way a turn ends.

`tests/test_messages_settlement.py` proves `_settle_turn` and
`_release_budget_hold` each move money correctly. This file proves
`send_message` actually calls them on every path, which is what those tests
cannot see: a hold placed before a check that then raises is never handed to
either helper, and leaks for the rest of the month.

The real app handles the request — middleware, exception handlers, the router,
Postgres. Only three things are replaced, none of which is under test here:

- `AgentRunner`, with a scripted stream, so no LLM is called;
- `generate_conversation_title`, which would otherwise make a billed Haiku call;
- the Slack error webhook, which `.env` may point at a real channel.

Langfuse is initialised in the app's lifespan, which ASGITransport does not
run, so no traces leave the machine either.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_messages_endpoint.py

Rows use a synthetic enterprise id well outside the real range and are removed
in the fixture, so this is safe to run against a dev database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    import httpx
    from langchain_core.messages import AIMessage
    from sqlalchemy import delete, select
    from starlette.requests import Request

    from app.config import settings
    from app.core import registry
    from app.core.auth.agent_token import AuthContext
    from app.core.billing.budget_block_event_model import BudgetBlockEvent
    from app.core.billing.enterprise_budget_model import EnterpriseBudget
    from app.core.billing.pricing import cost_micros
    from app.core.billing.usage_ledger_model import LlmUsageLedger
    from app.core.billing.user_token_model import UserToken
    from app.core.conversations.conversation_model import Conversation
    from app.core.conversations.messages_schema import SendMessageRequest
    from app.core.repositories import BudgetRepository, ConversationRepository
    from app.core.routers import messages
    from app.db import AsyncSessionLocal, engine
    from app.main import create_app

ENTERPRISE_ID = 999_300_001
USER_ID = 1
OTHER_USER_ID = 2
DOLLAR = 1_000_000
USAGE = {"input_tokens": 1_000, "output_tokens": 200}


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
        # Messages and feedback cascade from Conversation.
        for model in (
            LlmUsageLedger,
            BudgetBlockEvent,
            EnterpriseBudget,
            UserToken,
            Conversation,
        ):
            await s.execute(delete(model).where(model.enterprise_id == ENTERPRISE_ID))
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


@pytest.fixture
def spec():
    return registry.get("allocation")


# --- the scripted agent ------------------------------------------------------


def _answer(text="done", usage=USAGE):
    msg = AIMessage(content=text)
    msg.usage_metadata = {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
    }
    return msg


def completed_turn():
    """What the real runner emits for a one-iteration answer."""
    return [
        ("text_delta", {"delta": "done"}),
        ("message_complete", {"message": _answer()}),
        (
            "done",
            {"usage": USAGE, "usage_by_iteration": [USAGE], "secondary_usage": {}},
        ),
    ]


def error_before_any_output():
    """The runner's own `error` event, e.g. an empty model response. No `done`."""
    return [("error", {"code": "llm_error", "message": "Empty model response"})]


def error_after_one_iteration():
    """An iteration completed and was billed, then the loop gave up."""
    return [
        ("message_complete", {"message": _answer()}),
        ("error", {"code": "max_iterations", "message": "Maximum iterations reached"}),
    ]


class _Boom(RuntimeError):
    pass


def raises_mid_stream():
    return [("text_delta", {"delta": "partial"}), _Boom("tool blew up")]


@pytest.fixture
def agent(monkeypatch):
    """Install a scripted runner; returns a setter for the next turn's script."""
    script: dict[str, list] = {"events": completed_turn()}

    class _ScriptedRunner:
        def __init__(self, **_):
            pass

        async def handle_message_stream(self, **_):
            for item in script["events"]:
                if isinstance(item, BaseException):
                    raise item
                yield item

    async def _no_title(**_):
        return None, None

    monkeypatch.setattr(messages, "AgentRunner", _ScriptedRunner)
    monkeypatch.setattr(messages, "generate_conversation_title", _no_title)
    monkeypatch.setattr(messages, "alert_slack", lambda *a, **k: None)
    # Also covers the app-wide unhandled-error handler, which posts via the
    # same webhook setting.
    monkeypatch.setattr(settings, "slack_error_webhook_url", None)

    def use(events):
        script["events"] = events

    return use


# --- driving the endpoint ----------------------------------------------------


def _app(ctx=None):
    app = create_app()
    app.dependency_overrides[messages._resolve_identity] = lambda: (
        USER_ID,
        ENTERPRISE_ID,
        "cp-token",
        ctx,
    )
    return app


async def _post(spec, *, ctx=None, **body):
    payload = {
        "message": "where is my shipment",
        "user_id": USER_ID,
        "enterprise_id": ENTERPRISE_ID,
        **body,
    }
    transport = httpx.ASGITransport(app=_app(ctx), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(f"/api/{spec.url_prefix}/messages/", json=payload)


async def _budget(spec, *, limit=25 * DOLLAR, enforcement=True):
    async with AsyncSessionLocal() as s:
        row = await BudgetRepository(s).get_or_create(
            enterprise_id=ENTERPRISE_ID, agent_slug=spec.slug
        )
        row.limit_micros = limit
        row.enforcement_enabled = enforcement
        await s.commit()


async def _read(spec):
    """(spent, reserved) for this agent's row, or None if none was created."""
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(
                select(EnterpriseBudget).where(
                    EnterpriseBudget.enterprise_id == ENTERPRISE_ID,
                    EnterpriseBudget.agent_slug == spec.slug,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else (row.spent_micros, row.reserved_micros)


async def _ledger():
    async with AsyncSessionLocal() as s:
        return (
            (
                await s.execute(
                    select(LlmUsageLedger).where(
                        LlmUsageLedger.enterprise_id == ENTERPRISE_ID
                    )
                )
            )
            .scalars()
            .all()
        )


async def _conversation_owned_by(user_id, spec):
    async with AsyncSessionLocal() as s:
        conv = await ConversationRepository(s).create(
            user_id=user_id, enterprise_id=ENTERPRISE_ID, agent_slug=spec.slug
        )
        await s.commit()
        return str(conv.id)


# --- every stream exit settles or releases ------------------------------------


def test_a_completed_turn_charges_the_measured_cost_and_drops_the_hold(spec, agent):
    async def go():
        await _budget(spec)
        resp = await _post(spec)
        return resp, await _read(spec), await _ledger()

    resp, (spent, reserved), rows = run(go())
    assert resp.status_code == 200
    assert "event: done" in resp.text
    assert reserved == 0, "the hold was replaced by the real cost"
    assert spent == cost_micros(spec.runtime.model, USAGE)
    assert len(rows) == 1 and rows[0].counted_against_pool is True


def test_a_turn_that_errors_before_any_output_releases_its_hold(spec, agent):
    agent(error_before_any_output())

    async def go():
        await _budget(spec)
        resp = await _post(spec)
        return resp, await _read(spec)

    resp, state = run(go())
    assert "event: error" in resp.text
    assert state == (0, 0), "nothing was produced, so nothing is charged or held"


def test_a_turn_that_errors_after_a_billed_iteration_settles_what_it_used(spec, agent):
    agent(error_after_one_iteration())

    async def go():
        await _budget(spec)
        await _post(spec)
        return await _read(spec)

    spent, reserved = run(go())
    assert reserved == 0
    assert spent == cost_micros(spec.runtime.model, USAGE), (
        "the completed iteration really was paid to Anthropic"
    )


def test_a_runner_exception_mid_stream_still_drops_the_hold(spec, agent):
    agent(raises_mid_stream())

    async def go():
        await _budget(spec)
        resp = await _post(spec)
        return resp, await _read(spec)

    resp, state = run(go())
    assert "internal_error" in resp.text
    assert "tool blew up" not in resp.text, "raw exception text must not reach the browser"
    assert state == (0, 0)


def test_a_client_disconnect_mid_stream_still_drops_the_hold(spec, agent):
    """Closing the body iterator is what Starlette does when the client goes.

    Driven below the HTTP layer because an in-process transport always reads
    the body to the end, so it cannot hang up halfway.
    """
    agent(raises_mid_stream()[:1] + [("text_delta", {"delta": " never sent"})])

    async def go():
        await _budget(spec)
        app = _app()
        request = Request({"type": "http", "app": app, "headers": [], "method": "POST"})
        request.state.agent_spec = spec
        body = SendMessageRequest(
            message="hi", user_id=USER_ID, enterprise_id=ENTERPRISE_ID
        )
        resp = await messages.send_message(
            body, request, identity=(USER_ID, ENTERPRISE_ID, "cp-token", None)
        )
        stream = resp.body_iterator
        await stream.__anext__()  # the client saw something, then left
        held = (await _read(spec))[1]
        await stream.aclose()
        return held, await _read(spec)

    held, state = run(go())
    assert held > 0, "the hold was live while the stream was open"
    assert state == (0, 0)


@pytest.mark.parametrize(
    "script",
    [
        completed_turn,
        error_before_any_output,
        error_after_one_iteration,
        raises_mid_stream,
    ],
)
def test_a_turn_drops_only_its_own_hold(spec, agent, script):
    """Holds are a single counter per row, not per request.

    With nothing else in flight, a turn that dropped its hold twice (settle and
    then release) would floor at zero and every test above would still pass.
    A concurrent request's hold is what makes the double drop visible.
    """
    agent(script())

    async def go():
        await _budget(spec)
        async with AsyncSessionLocal() as s:
            other = await BudgetRepository(s).reserve(
                enterprise_id=ENTERPRISE_ID, agent_slug=spec.slug
            )
            await s.commit()
        await _post(spec)
        return other.estimate_micros, await _read(spec)

    other_hold, (_, reserved) = run(go())
    assert reserved == other_hold, "the concurrent request's hold must survive"


# --- requests refused before the stream never leave a hold -------------------


def test_a_conversation_owned_by_someone_else_is_a_404_that_holds_nothing(spec, agent):
    async def go():
        await _budget(spec)
        theirs = await _conversation_owned_by(OTHER_USER_ID, spec)
        resp = await _post(spec, conversation_id=theirs)
        return resp, await _read(spec)

    resp, (spent, reserved) = run(go())
    assert resp.status_code == 404
    assert reserved == 0, "a refused request must not leave $0.50 on the cap"
    assert spent == 0


def test_a_malformed_conversation_id_is_a_400_that_holds_nothing(spec, agent):
    async def go():
        await _budget(spec)
        resp = await _post(spec, conversation_id="not-a-uuid")
        return resp, await _read(spec)

    resp, (spent, reserved) = run(go())
    assert resp.status_code == 400, "a bad id is the client's mistake, not a 500"
    assert reserved == 0
    assert spent == 0


def test_a_budget_refusal_is_a_429_that_holds_nothing(spec, agent):
    async def go():
        await _budget(spec, limit=1)
        resp = await _post(spec)
        return resp, await _read(spec)

    resp, (spent, reserved) = run(go())
    assert resp.status_code == 429
    assert "budget_enterprise" in resp.text
    assert (spent, reserved) == (0, 0)


def test_repeated_404s_do_not_accumulate_holds(spec, agent):
    """The prod shape of the leak: each bad request strands another $0.50."""

    async def go():
        await _budget(spec)
        theirs = await _conversation_owned_by(OTHER_USER_ID, spec)
        for _ in range(5):
            await _post(spec, conversation_id=theirs)
        return await _read(spec)

    assert run(go()) == (0, 0)


# --- staff activity ----------------------------------------------------------


def test_signed_staff_turn_takes_no_hold_and_is_not_counted(spec, agent):
    ctx = AuthContext(
        user_id=USER_ID,
        enterprise_id=ENTERPRISE_ID,
        cp_token="cp-token",
        is_internal_user=True,
        internal_user_id="304875",
    )

    async def go():
        await _budget(spec)
        await _post(spec, ctx=ctx, is_internal_user=True, internal_user_id="304875")
        return await _read(spec), await _ledger()

    (spent, reserved), rows = run(go())
    assert (spent, reserved) == (0, 0), "support work never touches the customer's cap"
    assert len(rows) == 1, "but it is still attributed to the enterprise"
    assert rows[0].counted_against_pool is False
    assert rows[0].internal_user_id == "304875"
