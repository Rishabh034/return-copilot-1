#!/usr/bin/env python3
"""Issue and manage the API keys that authenticate /integrations/query callers.

There is no HTTP endpoint for this on purpose: minting a credential that bills
to us is not something a running service should be able to do, and there is one
caller to provision. Run it where DATABASE_URL points at the right database.

    python scripts/manage_integration_keys.py create support-bot
    python scripts/manage_integration_keys.py list
    python scripts/manage_integration_keys.py set-limit support-bot --dollars 50
    python scripts/manage_integration_keys.py set-limit support-bot --unlimited
    python scripts/manage_integration_keys.py revoke support-bot
    python scripts/manage_integration_keys.py usage support-bot --days 7

`create` prints the key once. Only its SHA-256 reaches the database, so a lost
key is re-issued, never recovered.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update 

from app.db import AsyncSessionLocal 
from app.core.integrations.integration_api_key_model import(
    IntegrationApiKey,
    display_prefix,
    generate_api_key,
    hash_api_key
)
from app.core.integrations.integration_request_log_model import(
    IntegrationRequestLog
)
from app.core.billing.pricing import (
    dollars_to_micros,
    format_dollars
)

async def _find(session, name: str) -> IntegrationApiKey:
    """The key a name refers to right now.

    A name can belong to several rows over time — one active, plus every key
    previously revoked under it — so this resolves to the active one, falling
    back to the most recently revoked so `usage` still answers for a key that
    has since been rotated out.
    """
    row = (
        await session.execute(
            select(IntegrationApiKey)
            .where(IntegrationApiKey.name == name)
            .order_by(
                IntegrationApiKey.is_active.desc(), IntegrationApiKey.created_at.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        sys.exit(f"No API key named {name!r}.")
    if not row.is_active:
        print(f"note: {name!r} has no active key; using the one revoked at {row.revoked_at}.")
    return row


async def create(name: str, dollars: float | None) -> None:
    plaintext = generate_api_key()
    async with AsyncSessionLocal() as session:
        # Only an *active* key blocks the name. Revoking one frees it again, so
        # rotation is revoke-then-create under the same name.
        existing = (
            await session.execute(
                select(IntegrationApiKey.id).where(
                    IntegrationApiKey.name == name,
                    IntegrationApiKey.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            sys.exit(
                f"An active API key named {name!r} already exists. "
                f"Revoke it first to reissue under the same name."
            )

        session.add(
            IntegrationApiKey(
                name=name,
                key_hash=hash_api_key(plaintext),
                key_prefix=display_prefix(plaintext),
                monthly_limit_micros=(
                    dollars_to_micros(dollars) if dollars is not None else None
                ),
            )
        )
        await session.commit()

    limit = f"${dollars:.2f}/month" if dollars is not None else "unlimited"
    print(f"\nCreated API key {name!r} ({limit}).")
    print("Store this now — it is not recoverable:\n")
    print(f"    {plaintext}\n")
    print("Send it as:  X-API-Key: <key>\n")


async def list_keys() -> None:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(IntegrationApiKey).order_by(IntegrationApiKey.created_at)
            )
        ).scalars()

        print(
            f"{'name':<28} {'prefix':<14} {'state':<8} {'limit':>10} "
            f"{'spent':>10} {'held':>9} {'reqs':>7}  period"
        )
        for row in rows:
            limit = (
                format_dollars(row.monthly_limit_micros)
                if row.monthly_limit_micros is not None
                else "unlimited"
            )
            print(
                f"{row.name:<28} {row.key_prefix:<14} "
                f"{'active' if row.is_active else 'revoked':<8} {limit:>10} "
                f"{format_dollars(row.spent_micros):>10} "
                f"{format_dollars(row.reserved_micros):>9} "
                f"{row.request_count:>7}  "
                f"{row.period_start.isoformat() if row.period_start else '-'}"
            )


async def set_limit(name: str, dollars: float | None) -> None:
    async with AsyncSessionLocal() as session:
        row = await _find(session, name)
        row.monthly_limit_micros = (
            dollars_to_micros(dollars) if dollars is not None else None
        )
        await session.commit()
        # Takes effect on the next request: the cap is read from this row at
        # admission time, not copied into a per-period row.
        print(
            f"{name}: monthly limit set to "
            f"{format_dollars(row.monthly_limit_micros) if dollars is not None else 'unlimited'}"
            f" (currently spent {format_dollars(row.spent_micros)} this period)."
        )


async def revoke(name: str) -> None:
    async with AsyncSessionLocal() as session:
        row = await _find(session, name)
        if not row.is_active:
            sys.exit(f"No active API key named {name!r}.")
        # By id, not by name: older keys revoked under the same name keep the
        # timestamp that says when *they* were turned off.
        await session.execute(
            update(IntegrationApiKey)
            .where(IntegrationApiKey.id == row.id)
            .values(is_active=False, revoked_at=datetime.now(timezone.utc))
        )
        await session.commit()
    # A flag rather than a DELETE, so the request log stays attributable.
    print(f"{name}: revoked. Requests with this key now get 401.")


async def usage(name: str, days: int) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        row = await _find(session, name)
        result = await session.execute(
            select(
                IntegrationRequestLog.status,
                func.count().label("requests"),
                func.coalesce(func.sum(IntegrationRequestLog.cost_micros), 0),
                func.coalesce(func.avg(IntegrationRequestLog.latency_ms), 0),
            )
            .where(
                IntegrationRequestLog.api_key_id == row.id,
                IntegrationRequestLog.created_at >= since,
            )
            .group_by(IntegrationRequestLog.status)
            .order_by(func.count().desc())
        )
        print(f"\n{name} — last {days} day(s)\n")
        print(f"{'status':<22} {'requests':>9} {'cost':>10} {'avg latency':>12}")
        total_cost = 0
        for status, requests, cost_micros, avg_latency in result:
            total_cost += cost_micros
            print(
                f"{status:<22} {requests:>9} {format_dollars(cost_micros):>10} "
                f"{int(avg_latency):>10}ms"
            )
        print(f"\ntotal {format_dollars(total_cost)}\n")


def _limit_dollars(args: argparse.Namespace) -> float | None:
    return None if args.unlimited else args.dollars


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="mint a key (prints it once)")
    p_create.add_argument("name", help="the calling service, e.g. support-bot")
    p_create.add_argument(
        "--dollars",
        type=float,
        default=None,
        help="monthly cap in dollars; omit for unlimited",
    )

    sub.add_parser("list", help="all keys with their current-period counters")

    p_limit = sub.add_parser("set-limit", help="change a key's monthly cap")
    p_limit.add_argument("name")
    group = p_limit.add_mutually_exclusive_group(required=True)
    group.add_argument("--dollars", type=float)
    group.add_argument("--unlimited", action="store_true")

    p_revoke = sub.add_parser("revoke", help="turn a key off")
    p_revoke.add_argument("name")

    p_usage = sub.add_parser("usage", help="request counts and spend by outcome")
    p_usage.add_argument("name")
    p_usage.add_argument("--days", type=int, default=30)

    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(create(args.name, args.dollars))
    elif args.command == "list":
        asyncio.run(list_keys())
    elif args.command == "set-limit":
        asyncio.run(set_limit(args.name, _limit_dollars(args)))
    elif args.command == "revoke":
        asyncio.run(revoke(args.name))
    elif args.command == "usage":
        asyncio.run(usage(args.name, args.days))


if __name__ == "__main__":
    main()
