"""CorsRejectionAlertMiddleware: does a rejected preflight reach Slack?

The bug this guards against is not "CORS is misconfigured" — that will happen
again — it is that the misconfiguration was *silent*. CORSMiddleware returns its
400 rather than raising, so it never reaches an exception handler and the
unhandled-exception alerting never saw it.
"""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.cors_alert import CorsRejectionAlertMiddleware

ALLOWED = "https://good.example"
DENIED = "https://bad.example"


@pytest.fixture
def stack(monkeypatch):
    """The real middleware order from create_app: alerter wrapping CORS."""
    posted = []
    monkeypatch.setattr(
        "app.cors_alert.post_alert",
        lambda title, fields: posted.append((title, fields)),
    )

    async def ok(_request):
        return PlainTextResponse("hi")

    async def rejects(_request):
        # a route that answers OPTIONS with its own 400, so the "is this really
        # a preflight?" guard has something to distinguish
        return PlainTextResponse("no", status_code=400)

    inner = Starlette(routes=[
        Route("/api/x", ok, methods=["GET", "POST"]),
        Route("/api/route400", rejects, methods=["OPTIONS"]),
    ])
    cors = CORSMiddleware(
        inner, allow_origins=[ALLOWED], allow_methods=["*"],
        allow_headers=["*"], allow_credentials=False,
    )
    return CorsRejectionAlertMiddleware(cors), posted


def call(mw, method, path="/api/x", headers=None):
    """Drive the ASGI stack and wait for the fire-and-forget alert task."""

    async def go():
        transport = httpx.ASGITransport(app=mw)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.request(method, path, headers=headers or {})
        if mw._tasks:
            await asyncio.gather(*list(mw._tasks))
        return response

    return asyncio.run(go())


def preflight(mw, origin):
    return call(mw, "OPTIONS", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})


def test_allowed_origin_is_accepted_and_silent(stack):
    mw, posted = stack
    assert preflight(mw, ALLOWED).status_code == 200
    assert posted == []


def test_rejected_preflight_alerts_with_the_origin(stack):
    mw, posted = stack
    assert preflight(mw, DENIED).status_code == 400
    assert len(posted) == 1
    title, fields = posted[0]
    assert title == "CORS preflight rejected"
    # the origin is the whole point: without it the alert cannot be acted on
    assert DENIED in fields["Origin"]
    assert fields["Path"] == "/api/x"


def test_repeat_rejections_are_deduped_per_origin(stack):
    mw, posted = stack
    for _ in range(3):
        preflight(mw, DENIED)
    assert len(posted) == 1, "one broken frontend must not flood the channel"


def test_a_second_bad_origin_still_alerts(stack):
    mw, posted = stack
    preflight(mw, DENIED)
    preflight(mw, "https://other.example")
    assert [f["Origin"] for _, f in posted] == [f"`{DENIED}`", "`https://other.example`"]


def test_options_without_preflight_headers_is_ignored(stack):
    """Not a preflight, so CORS passes it through and any 400 is the route's."""
    mw, posted = stack
    call(mw, "OPTIONS")
    assert posted == []


def test_route_owned_options_400_is_not_blamed_on_cors(stack):
    """The status alone is ambiguous — only the preflight headers settle it.

    Without this the middleware would report any OPTIONS 400 as a missing
    origin and send people hunting through ALLOWED_ORIGINS for nothing.
    """
    mw, posted = stack
    assert call(mw, "OPTIONS", path="/api/route400").status_code == 400
    assert posted == []


def test_normal_requests_are_untouched(stack):
    mw, posted = stack
    r = call(mw, "GET", headers={"Origin": ALLOWED})
    assert (r.status_code, r.text) == (200, "hi")
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert posted == []


def test_non_preflight_from_denied_origin_does_not_alert(stack):
    """A simple POST is not a preflight; the browser blocks the *response*."""
    mw, posted = stack
    assert call(mw, "POST", headers={"Origin": DENIED}).status_code == 200
    assert posted == []


def test_dedupe_map_stays_bounded(stack):
    """Origin is attacker-controlled and this host gets scanned.

    Without a cap the dedupe map grows once per distinct Origin header and
    never shrinks, for the life of the process.
    """
    mw, _ = stack
    for i in range(mw._MAX_TRACKED_ORIGINS * 3):
        preflight(mw, f"https://scanner-{i}.example")
    assert len(mw._last_alert) <= mw._MAX_TRACKED_ORIGINS


def test_a_bounded_map_still_dedupes_the_origins_it_holds(stack):
    """Eviction must not cost us suppression for origins we are tracking."""
    mw, posted = stack
    preflight(mw, DENIED)
    preflight(mw, DENIED)
    assert len(posted) == 1
