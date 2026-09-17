"""Who gets billed for a turn.

`resolve_internal_actor` decides whether a turn counts against the enterprise's
spend pool. Before clickpost signed anything that came from the request body,
which meant a customer could send `is_internal_user: true` and spend against no
budget at all. These tests pin both halves: that a signed claim overrides the
body, and that behaviour is unchanged while the claim is absent, so this is
safe to deploy before clickpost ships it.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.dependencies.auth import AuthContext
from app.exceptions import BadRequestError
from app.rr_log_analyst.auth import (
    resolve_body_internal_actor,
    resolve_internal_actor,
)


def ctx(**overrides) -> AuthContext:
    base = dict(user_id=14, enterprise_id=15, cp_token="tok")
    base.update(overrides)
    return AuthContext(**base)


class TestNoSignedClaim:
    """JWT auth off, or clickpost has not shipped the claim yet."""

    @pytest.mark.parametrize("context", [None, ctx(), ctx(is_internal_user=None)])
    def test_falls_back_to_body(self, context):
        assert resolve_internal_actor(
            ctx=context,
            body_is_internal_user=True,
            body_internal_user_id="304875",
        ) == (True, "304875")

    def test_body_external_stays_external(self):
        assert resolve_internal_actor(
            ctx=None, body_is_internal_user=False, body_internal_user_id=None
        ) == (False, None)

    def test_external_discards_a_supplied_internal_id(self):
        """An id sent alongside is_internal_user=false must not leak through and
        make the turn look like support activity downstream."""
        assert resolve_internal_actor(
            ctx=None, body_is_internal_user=False, body_internal_user_id="304875"
        ) == (False, None)

    def test_internal_without_an_id_is_rejected(self):
        with pytest.raises(BadRequestError):
            resolve_internal_actor(
                ctx=None, body_is_internal_user=True, body_internal_user_id=None
            )

    def test_blank_internal_id_is_rejected(self):
        with pytest.raises(HTTPException):
            resolve_internal_actor(
                ctx=None, body_is_internal_user=True, body_internal_user_id="   "
            )


class TestSignedClaimWins:
    def test_signed_false_beats_body_true(self):
        """The budget bypass this whole change exists to close: a customer
        asserting they are internal is overruled by clickpost's signed answer."""
        assert resolve_internal_actor(
            ctx=ctx(is_internal_user=False),
            body_is_internal_user=True,
            body_internal_user_id="304875",
        ) == (False, None)

    def test_signed_true_beats_body_false(self):
        assert resolve_internal_actor(
            ctx=ctx(is_internal_user=True, internal_user_id="304875", is_staff=True),
            body_is_internal_user=False,
            body_internal_user_id=None,
        ) == (True, "304875")

    def test_signed_id_beats_a_different_body_id(self):
        """Attribution comes from the token, so one employee cannot log support
        activity under another's id."""
        assert resolve_internal_actor(
            ctx=ctx(is_internal_user=True, internal_user_id="304875", is_staff=True),
            body_is_internal_user=True,
            body_internal_user_id="301602",
        ) == (True, "304875")

    def test_internal_claim_without_an_id_is_refused(self):
        """The two claims disagree. Refuse rather than guess: billing the
        customer for support work and dropping the attribution are both wrong."""
        with pytest.raises(HTTPException) as exc:
            resolve_internal_actor(
                ctx=ctx(is_internal_user=True, internal_user_id=None),
                body_is_internal_user=True,
                body_internal_user_id="304875",
            )
        assert exc.value.status_code == 400


class TestConversationOwnerIgnoresSignedClaim:
    """a chat is filed under the flags the client lists by, or the sidebar never
    shows it billing is a separate decision and stays signed."""

    def test_owner_follows_body_where_billing_follows_token(self):
        signed = ctx(is_internal_user=True, internal_user_id="304875")
        body = dict(body_is_internal_user=False, body_internal_user_id=None)
        assert resolve_body_internal_actor(**body) == (False, None)
        assert resolve_internal_actor(ctx=signed, **body) == (True, "304875")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_id_on_a_customer_chat_is_ignored(self, blank):
        """Some clients send an empty id for a non-internal user. That was never
        validated on the signed path, and must not start failing chats now."""
        assert resolve_body_internal_actor(
            body_is_internal_user=False, body_internal_user_id=blank
        ) == (False, None)

    def test_internal_owner_needs_an_id(self):
        with pytest.raises(BadRequestError):
            resolve_body_internal_actor(
                body_is_internal_user=True, body_internal_user_id=None
            )


class TestAuthContextDefaults:
    def test_claim_absent_is_none_not_false(self):
        """Tri-state is load-bearing: collapsing absent to false would make
        every pre-rollout token look like a signed 'this is a customer'."""
        assert ctx().is_internal_user is None

    def test_existing_three_argument_construction_still_works(self):
        c = AuthContext(user_id=14, enterprise_id=15, cp_token="tok")
        assert (c.is_staff, c.internal_user_id, c.is_internal_user) == (
            False,
            None,
            None,
        )
