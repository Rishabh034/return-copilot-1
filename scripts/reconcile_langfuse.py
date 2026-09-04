"""Check our ledger's cost figures against Langfuse, per enterprise.

Our cap is enforced from `llm_usage_ledger.cost_micros`, which we compute
ourselves in `services.pricing` from token counts. Nothing has ever checked that
arithmetic against an independent source. If a rate in the price table is wrong,
or a call path never reaches the ledger, we would happily block a paying customer
on a number nobody verified — so this runs before enforcement is switched on for
anyone real, and periodically after.

Langfuse is the second opinion: it sees the same calls through the LangChain
callback and prices them from its own model table. It is deliberately NOT the
source of truth (it flushes in batches and is allowed to be down), but two
independent calculations that agree are far better evidence than one.

    python scripts/reconcile_langfuse.py                      # last 7 whole days
    python scripts/reconcile_langfuse.py --from 2026-08-01 --to 2026-08-31
    python scripts/reconcile_langfuse.py --enterprise 15 --by-day
    python scripts/reconcile_langfuse.py --tolerance 2.0 --fail-on-gap   # for cron

Reads only. Exits 1 with --fail-on-gap if any enterprise is outside tolerance.

Reading the output
------------------
Tokens are the ground truth both sides observe; cost is each side's opinion about
those tokens. So the two columns fail differently and mean different things:

  tokens agree, cost differs   → a price-table disagreement. Either our rates in
                                 services.pricing are stale, or Langfuse does not
                                 know this model and priced it at zero. Check
                                 which side is low before touching anything.
  tokens differ                → a call path is missing from one side. This is the
                                 serious one: it means the ledger does not see
                                 traffic the customer is really generating, and
                                 the cap under-counts.

Known and expected gaps
-----------------------
1. Conversation titling (`services.title`) is traced but never settled — it
   returns a string and drops `usage_metadata` on the floor. Langfuse will
   therefore read HIGHER than the ledger by one small Haiku call per new
   conversation. That is task B2; this script is what sizes it.
2. `app/agenticintegrations/client.py` runs in a separate consumer process that
   never calls `init_langfuse()`, so its spend is in neither system.
3. Traces flush in batches, so the last few minutes are usually missing from
   Langfuse. The default window ends at the last whole UTC day for this reason.
4. Nothing before 2026-08-14 carries an `enterprise:` tag — that is the day
   enterprise tagging shipped. Windows reaching further back report a large
   untagged share, which is history rather than a bug. See TAGGING_LIVE_FROM.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Run from anywhere: `python scripts/reconcile_langfuse.py` puts scripts/ on the
# path, not the repo root, so `import app` fails without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import date, datetime, time, timedelta, timezone

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.db import AsyncSessionLocal, engine
from app.rr_log_analyst.models.usage_ledger import LlmUsageLedger

# Langfuse returns cost as a float in dollars; we hold integer micro-dollars.
MICROS_PER_DOLLAR = 1_000_000

# Default band within which a difference is noise rather than a finding. Two
# systems rounding independently will never agree to the micro.
DEFAULT_TOLERANCE_PCT = 1.0

_HTTP_TIMEOUT = 30.0

# Enterprise tagging went live with the observability commit on this date.
# Before it, every trace is untagged and reconciles against nothing — which is a
# fact about our history, not a fault in the pipeline. Verified 2026-09-02:
# Aug 7-13 is 100% untagged, Aug 14 is the crossover, Aug 17 onward is 100%
# tagged with no untagged spend at all.
TAGGING_LIVE_FROM = date(2026, 8, 14)


# --------------------------------------------------------------------------
# shapes
# --------------------------------------------------------------------------


@dataclass
class Totals:
    """One side's view of an enterprise, optionally sliced by day."""

    cost_micros: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    # model -> (input, output, cost_micros); the per-model split is what
    # separates "one rate is wrong" from "a whole call path is missing".
    by_model: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    by_day: dict[date, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Row:
    enterprise_id: int
    ledger: Totals
    langfuse: Totals
    langfuse_available: bool = True

    @property
    def cost_gap_micros(self) -> int:
        return self.langfuse.cost_micros - self.ledger.cost_micros

    @property
    def token_gap(self) -> int:
        return self.langfuse.total_tokens - self.ledger.total_tokens

    def cost_gap_pct(self) -> float | None:
        """Gap as a percentage of the ledger figure, or None if it is zero.

        Percentage of *ledger*, not of Langfuse: the ledger is the number we
        actually enforce against, so it is the meaningful denominator.
        """
        if self.ledger.cost_micros == 0:
            return None
        return self.cost_gap_micros / self.ledger.cost_micros * 100

    def token_gap_pct(self) -> float | None:
        if self.ledger.total_tokens == 0:
            return None
        return self.token_gap / self.ledger.total_tokens * 100

    def verdict(self, tolerance_pct: float) -> tuple[str, str]:
        """(status, explanation) — the interpretation, not just the number."""
        if not self.langfuse_available:
            return "NO DATA", "Langfuse returned nothing for this enterprise tag"

        cost_pct = self.cost_gap_pct()
        token_pct = self.token_gap_pct()

        if self.ledger.cost_micros == 0 and self.langfuse.cost_micros == 0:
            return "EMPTY", "no priced traffic on either side"

        # Token disagreement is the serious finding, so it is tested first: a
        # cost gap that merely follows a token gap is a symptom, not the cause.
        if token_pct is not None and abs(token_pct) > tolerance_pct:
            direction = "Langfuse sees more" if self.token_gap > 0 else "ledger sees more"
            return "TOKEN GAP", f"{direction} — a call path is missing from one side"

        if cost_pct is None:
            return "CHECK", "ledger priced nothing but Langfuse did"

        if abs(cost_pct) <= tolerance_pct:
            return "OK", "both sides agree within tolerance"

        if self.langfuse.cost_micros == 0:
            return "PRICE GAP", "Langfuse priced at zero — it likely lacks a rate for this model"

        direction = "Langfuse higher" if self.cost_gap_micros > 0 else "ledger higher"
        return "PRICE GAP", f"tokens agree but {direction} — price tables disagree"


# --------------------------------------------------------------------------
# our side
# --------------------------------------------------------------------------


async def load_ledger(
    session, start: datetime, end: datetime, enterprise_id: int | None
) -> dict[int, Totals]:
    """Sum the ledger per enterprise over the window.

    Deliberately counts rows with `counted_against_pool = false` (ClickPost
    support traffic). Those turns are excluded from the customer's cap but they
    are real Anthropic calls and Langfuse traced them under the same enterprise
    tag — filtering them here would manufacture a gap that isn't there.
    """
    where = [LlmUsageLedger.created_at >= start, LlmUsageLedger.created_at <= end]
    if enterprise_id is not None:
        where.append(LlmUsageLedger.enterprise_id == enterprise_id)

    totals: dict[int, Totals] = defaultdict(Totals)

    # --- headline, per enterprise -----------------------------------------
    stmt = (
        select(
            LlmUsageLedger.enterprise_id,
            func.sum(LlmUsageLedger.cost_micros),
            func.sum(LlmUsageLedger.input_tokens + LlmUsageLedger.secondary_input_tokens),
            func.sum(LlmUsageLedger.output_tokens + LlmUsageLedger.secondary_output_tokens),
            func.count(),
        )
        .where(*where)
        .group_by(LlmUsageLedger.enterprise_id)
    )
    for ent_id, cost, tok_in, tok_out, turns in (await session.execute(stmt)).all():
        t = totals[ent_id]
        t.cost_micros = int(cost or 0)
        t.input_tokens = int(tok_in or 0)
        t.output_tokens = int(tok_out or 0)
        t.turns = int(turns or 0)

    # --- primary model split ----------------------------------------------
    stmt = (
        select(
            LlmUsageLedger.enterprise_id,
            LlmUsageLedger.model,
            func.sum(LlmUsageLedger.input_tokens),
            func.sum(LlmUsageLedger.output_tokens),
            func.sum(LlmUsageLedger.cost_micros),
        )
        .where(*where)
        .group_by(LlmUsageLedger.enterprise_id, LlmUsageLedger.model)
    )
    for ent_id, model, tok_in, tok_out, cost in (await session.execute(stmt)).all():
        totals[ent_id].by_model[model or "(unset)"] = (
            int(tok_in or 0),
            int(tok_out or 0),
            int(cost or 0),
        )

    # --- secondary (scratchpad) model split -------------------------------
    # Folded into the same map so the comparison against Langfuse, which has no
    # notion of primary vs secondary, lines up model-for-model. Cost is left at
    # 0 because the ledger stores one blended `cost_micros` per turn and does
    # not break out what the secondary call contributed.
    stmt = (
        select(
            LlmUsageLedger.enterprise_id,
            LlmUsageLedger.secondary_model,
            func.sum(LlmUsageLedger.secondary_input_tokens),
            func.sum(LlmUsageLedger.secondary_output_tokens),
        )
        .where(*where, LlmUsageLedger.secondary_model.isnot(None))
        .group_by(LlmUsageLedger.enterprise_id, LlmUsageLedger.secondary_model)
    )
    for ent_id, model, tok_in, tok_out in (await session.execute(stmt)).all():
        prev_in, prev_out, prev_cost = totals[ent_id].by_model.get(model, (0, 0, 0))
        totals[ent_id].by_model[model] = (
            prev_in + int(tok_in or 0),
            prev_out + int(tok_out or 0),
            prev_cost,
        )

    # --- per day ----------------------------------------------------------
    day = func.date(LlmUsageLedger.created_at)
    stmt = (
        select(LlmUsageLedger.enterprise_id, day, func.sum(LlmUsageLedger.cost_micros))
        .where(*where)
        .group_by(LlmUsageLedger.enterprise_id, day)
    )
    for ent_id, d, cost in (await session.execute(stmt)).all():
        totals[ent_id].by_day[d] = int(cost or 0)

    return dict(totals)


async def load_unpriced(session, start: datetime, end: datetime) -> dict[int, int]:
    """Turns the ledger recorded but could not price, per enterprise.

    These are a known-zero contribution on our side, so they explain a Langfuse
    surplus without any price table being wrong.
    """
    stmt = (
        select(LlmUsageLedger.enterprise_id, func.count())
        .where(
            LlmUsageLedger.created_at >= start,
            LlmUsageLedger.created_at <= end,
            LlmUsageLedger.pricing_failed.is_(True),
        )
        .group_by(LlmUsageLedger.enterprise_id)
    )
    return {e: int(c) for e, c in (await session.execute(stmt)).all()}


# --------------------------------------------------------------------------
# their side
# --------------------------------------------------------------------------


class LangfuseClient:
    """Minimal read client for the Langfuse v4 metrics API.

    Not the langfuse SDK: that is built for writing traces from inside a request
    and pulls in an OTEL stack we do not want in a CLI. This needs two GETs.

    Targets `/api/public/v2/metrics`. The older `/api/public/metrics/daily` and
    `/api/public/traces` are both 404 on our deployment, which runs v4 in
    events_only mode — and a 404 there is indistinguishable from "no data",
    which is why `check()` below refuses to let the report run blind.
    """

    def __init__(self, base_url: str, public_key: str, secret_key: str):
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Basic {token}"},
            timeout=_HTTP_TIMEOUT,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def check(self) -> None:
        """Fail loudly and early on bad keys, an unreachable host, or a v4 shift.

        Every failure mode here otherwise surfaces as an empty result set, and an
        empty result set renders as "every enterprise is 100% unaccounted for" —
        an alarming report that looks exactly like real data loss but means only
        that we asked the wrong host, the wrong project, or the wrong API version.
        """
        try:
            r = await self._client.get("/api/public/projects")
        except httpx.RequestError as exc:
            raise SystemExit(
                f"Cannot reach Langfuse at {self._client.base_url}: {exc}\n"
                "  The host is VPC-private, so this must run somewhere with a route to it."
            ) from exc
        if r.status_code == 401:
            raise SystemExit(
                "Langfuse rejected the credentials (401).\n"
                "  Check CLICKHELP_LANGFUSE_PUBLIC_KEY / _SECRET_KEY — keys are\n"
                "  per-project, so keys from another project authenticate but return nothing."
            )
        r.raise_for_status()

        probe = await self._client.get(
            "/api/public/v2/metrics", params={"query": json.dumps(_query(["tags"], None, TODAY, TODAY))}
        )
        if probe.status_code == 404:
            raise SystemExit(
                "This Langfuse does not serve /api/public/v2/metrics.\n"
                "  Older deployments used /api/public/metrics/daily instead; check the version."
            )
        probe.raise_for_status()

    async def usage(
        self, start: date, end: date, *, granularity: str | None = None
    ) -> list[dict]:
        """Cost and tokens grouped by trace tags and model over the window.

        One request for every enterprise at once. Grouping by `tags` rather than
        filtering per enterprise matters: it also returns the rows carrying *no*
        enterprise tag, which is the number that decides whether this
        reconciliation means anything at all.
        """
        dims = ["tags", "providedModelName"]
        r = await self._client.get(
            "/api/public/v2/metrics",
            params={"query": json.dumps(_query(dims, granularity, start, end))},
        )
        r.raise_for_status()
        return r.json().get("data") or []


TODAY = datetime.now(timezone.utc).date()


def _query(dimensions: list[str], granularity: str | None, start: date, end: date) -> dict:
    """Build a v4 metrics query body.

    The `traces` view does not exist in v4 (only observations and scores), so
    enterprise attribution has to come from tags propagated down onto each
    observation rather than read off the parent trace.
    """
    return {
        "view": "observations",
        "dimensions": [{"field": d} for d in dimensions],
        "metrics": [
            {"measure": "count", "aggregation": "count"},
            {"measure": "totalCost", "aggregation": "sum"},
            {"measure": "inputTokens", "aggregation": "sum"},
            {"measure": "outputTokens", "aggregation": "sum"},
        ],
        "filters": [],
        "timeDimension": {"granularity": granularity} if granularity else None,
        "fromTimestamp": f"{start.isoformat()}T00:00:00Z",
        "toTimestamp": f"{end.isoformat()}T23:59:59Z",
        "orderBy": None,
    }


def _enterprise_of(tags: list) -> int | None:
    """Pull the enterprise id out of a trace's tag array, if it carries one."""
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith("enterprise:"):
            suffix = tag.split(":", 1)[1]
            if suffix.isdigit():
                return int(suffix)
    return None


def _num(value) -> float:
    """Langfuse returns null, not 0, for an empty bucket."""
    return float(value or 0.0)


def fold_langfuse(rows: list[dict]) -> tuple[dict[int, Totals], Totals]:
    """Collapse the metrics rows into per-enterprise totals, plus the untagged rest.

    The same enterprise appears in several rows — one per distinct tag array, so
    internal and external traffic arrive separately — and they are summed
    together here, matching the ledger, which also counts both.
    """
    per_enterprise: dict[int, Totals] = defaultdict(Totals)
    unattributed = Totals()

    for row in rows:
        enterprise_id = _enterprise_of(row.get("tags"))
        target = per_enterprise[enterprise_id] if enterprise_id is not None else unattributed

        cost = int(round(_num(row.get("sum_totalCost")) * MICROS_PER_DOLLAR))
        tok_in = int(_num(row.get("sum_inputTokens")))
        tok_out = int(_num(row.get("sum_outputTokens")))

        target.cost_micros += cost
        target.input_tokens += tok_in
        target.output_tokens += tok_out
        target.turns += int(_num(row.get("count_count")))

        model = row.get("providedModelName") or "(unattributed)"
        prev = target.by_model.get(model, (0, 0, 0))
        target.by_model[model] = (prev[0] + tok_in, prev[1] + tok_out, prev[2] + cost)

        raw_day = row.get("time_dimension")
        if raw_day:
            try:
                day = date.fromisoformat(str(raw_day)[:10])
            except ValueError:
                continue
            target.by_day[day] = target.by_day.get(day, 0) + cost

    return dict(per_enterprise), unattributed

# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def usd(micros: int) -> str:
    return f"${micros / MICROS_PER_DOLLAR:,.4f}"


def pct(value: float | None) -> str:
    return "     —" if value is None else f"{value:+6.1f}%"


def print_report(
    rows: list[Row],
    *,
    tolerance_pct: float,
    unpriced: dict[int, int],
    show_days: bool,
    unattributed: Totals,
    attributed: int,
) -> None:
    # Coverage first, because it decides whether anything below is meaningful.
    # Cost that reached Langfuse without an `enterprise:` tag cannot be compared
    # against any enterprise, so a large share here does not mean the ledger is
    # wrong — it means this reconciliation has little to say either way.
    total_traced = attributed + unattributed.cost_micros
    if total_traced:
        share = unattributed.cost_micros / total_traced * 100
        print()
        print(f"traced spend  {usd(total_traced)}   "
              f"attributed {usd(attributed)}   "
              f"untagged {usd(unattributed.cost_micros)} ({share:.1f}%)")
        if share > 10:
            print(f"  WARNING: {share:.1f}% of traced spend carries no `enterprise:` tag, so it")
            print( "  cannot be reconciled against any enterprise.")
            print(f"  If the window reaches before {TAGGING_LIVE_FROM}, that is expected — enterprise")
            print( "  tagging shipped that day and everything earlier is untagged by nature.")
            print( "  Untagged spend *after* that date is a real propagation bug: fix it before")
            print( "  trusting the per-enterprise rows below.")
    print()
    print(f"{'ent':>10}  {'ledger':>13}  {'langfuse':>13}  {'Δcost':>7}  "
          f"{'Δtokens':>8}  {'turns':>6}  status")
    print("-" * 96)

    for row in sorted(rows, key=lambda r: -abs(r.cost_gap_micros)):
        status, _ = row.verdict(tolerance_pct)
        print(
            f"{row.enterprise_id:>10}  "
            f"{usd(row.ledger.cost_micros):>13}  "
            f"{usd(row.langfuse.cost_micros):>13}  "
            f"{pct(row.cost_gap_pct()):>7}  "
            f"{pct(row.token_gap_pct()):>8}  "
            f"{row.ledger.turns:>6}  "
            f"{status}"
        )

    print()
    interesting = [r for r in rows if r.verdict(tolerance_pct)[0] not in ("OK", "EMPTY")]
    if not interesting:
        print(f"All {len(rows)} enterprise(s) agree within {tolerance_pct}%.")
    else:
        print(f"{len(interesting)} of {len(rows)} enterprise(s) need a look:")
        print()

    for row in sorted(interesting, key=lambda r: -abs(r.cost_gap_micros)):
        status, why = row.verdict(tolerance_pct)
        print(f"  enterprise {row.enterprise_id} — {status}: {why}")
        print(f"    ledger   {usd(row.ledger.cost_micros):>13}   "
              f"{row.ledger.total_tokens:>12,} tokens   {row.ledger.turns:>5} turns")
        print(f"    langfuse {usd(row.langfuse.cost_micros):>13}   "
              f"{row.langfuse.total_tokens:>12,} tokens   {row.langfuse.turns:>5} traces")
        if row.enterprise_id in unpriced:
            print(f"    note: {unpriced[row.enterprise_id]} ledger turn(s) had "
                  f"pricing_failed=true and contributed $0 on our side")

        models = sorted(set(row.ledger.by_model) | set(row.langfuse.by_model))
        if models:
            print(f"    {'model':<26} {'ledger tok':>12} {'langfuse tok':>13}  {'Δ':>10}")
            for model in models:
                l_in, l_out, _ = row.ledger.by_model.get(model, (0, 0, 0))
                f_in, f_out, _ = row.langfuse.by_model.get(model, (0, 0, 0))
                l_tot, f_tot = l_in + l_out, f_in + f_out
                flag = ""
                if l_tot == 0 and f_tot > 0:
                    flag = "  ← traced, never settled"
                elif f_tot == 0 and l_tot > 0:
                    flag = "  ← settled, never traced"
                print(f"    {model:<26} {l_tot:>12,} {f_tot:>13,}  "
                      f"{f_tot - l_tot:>+10,}{flag}")

        if show_days:
            days = sorted(set(row.ledger.by_day) | set(row.langfuse.by_day))
            print(f"    {'day':<26} {'ledger':>12} {'langfuse':>13}")
            for d in days:
                print(f"    {d.isoformat():<26} "
                      f"{usd(row.ledger.by_day.get(d, 0)):>12} "
                      f"{usd(row.langfuse.by_day.get(d, 0)):>13}")
        print()


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reconcile llm_usage_ledger cost against Langfuse, per enterprise.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                   help="inclusive UTC start (default: 7 whole days ago)")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                   help="inclusive UTC end (default: yesterday, so traces have flushed)")
    p.add_argument("--enterprise", type=int, help="restrict to one enterprise id")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PCT,
                   help=f"percent difference treated as noise (default {DEFAULT_TOLERANCE_PCT})")
    p.add_argument("--by-day", action="store_true",
                   help="add a per-day breakdown for each flagged enterprise")
    p.add_argument("--fail-on-gap", action="store_true",
                   help="exit 1 if any enterprise is outside tolerance (for cron)")
    return p.parse_args()


def resolve_window(args) -> tuple[date, date]:
    # Default ends yesterday: Langfuse flushes in batches, so today is always
    # partially missing on their side and would read as a ledger surplus.
    today = datetime.now(timezone.utc).date()
    end = date.fromisoformat(args.date_to) if args.date_to else today - timedelta(days=1)
    start = date.fromisoformat(args.date_from) if args.date_from else end - timedelta(days=6)
    if start > end:
        raise SystemExit(f"--from ({start}) is after --to ({end}).")
    if not args.date_to and end >= today:
        print("warning: window includes today; Langfuse may not have flushed recent traces.")
    return start, end


async def main() -> int:
    args = parse_args()
    start_day, end_day = resolve_window(args)

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        raise SystemExit(
            "Langfuse keys are not configured in this environment.\n"
            "  Needs CLICKHELP_LANGFUSE_PUBLIC_KEY and CLICKHELP_LANGFUSE_SECRET_KEY.\n"
            "  Note the host defaults to a VPC-private address "
            f"({settings.langfuse_base_url}), so this must run somewhere that can reach it."
        )

    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_day, time.max, tzinfo=timezone.utc)

    print(f"window   {start_day} .. {end_day}  (UTC, inclusive)")
    print(f"langfuse {settings.langfuse_base_url}")

    async with AsyncSessionLocal() as session:
        ledger = await load_ledger(session, start, end, args.enterprise)
        unpriced = await load_unpriced(session, start, end)

    if not ledger:
        print("\nNo ledger rows in this window — nothing to reconcile.")
        return 0

    async with LangfuseClient(
        settings.langfuse_base_url,
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
    ) as client:
        await client.check()
        raw = await client.usage(
            start_day, end_day, granularity="day" if args.by_day else None
        )

    traced, unattributed = fold_langfuse(raw)

    rows = [
        Row(
            enterprise_id=enterprise_id,
            ledger=our_totals,
            langfuse=traced.get(enterprise_id, Totals()),
            langfuse_available=enterprise_id in traced,
        )
        for enterprise_id, our_totals in ledger.items()
    ]

    print_report(rows, tolerance_pct=args.tolerance, unpriced=unpriced,
                 show_days=args.by_day, unattributed=unattributed,
                 attributed=sum(t.cost_micros for t in traced.values()))

    breached = [r for r in rows if r.verdict(args.tolerance)[0] not in ("OK", "EMPTY")]
    if args.fail_on_gap and breached:
        return 1
    return 0


async def _run() -> int:
    # Dispose inside the same loop that opened the connections: asyncpg binds
    # them to a loop, and disposing from a second asyncio.run() raises
    # "Event loop is closed" instead of shutting down cleanly.
    try:
        return await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
