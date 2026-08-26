#!/usr/bin/env python3
"""Smoke-test a deployed rr-log-analyst against the real HTTP surface.

Built from the "RR Log Analyst - Full Flow" request collection. The point is to
catch the class of failure that unit tests structurally cannot: the code is
correct, but the *deployed configuration* is not. A missing entry in
ALLOWED_ORIGINS is invisible to every test in tests/ and to the server's own
logs, while every customer on that origin sees "Failed to fetch".

The CORS and reachability checks need no credentials:

    python scripts/smoke_test.py
    python scripts/smoke_test.py --base-url http://127.0.0.1:8000

Add --token (a Clickpost API token) to mint a JWT and exercise the full
conversation flow end to end:

    python scripts/smoke_test.py --token "$CP_TOKEN" --user-id 15 --enterprise-id 15

Exits non-zero if any check fails, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

# Every browser origin that is expected to reach this API. Keep in sync with
# ALLOWED_ORIGINS on the server: an origin serving the UI but missing from both
# is exactly the outage this script exists to catch.
KNOWN_FRONTENDS = [
    "https://alerts.clickpost.in",
    "https://dashboard.clickpost.in",
    "https://dashboard.clickpost.ai",
    "https://dashboard-v2.clickpost.in",
]

# Must never be accepted. If it is, allow_origins has been widened to a wildcard
# and any site on the internet can call this API with the user's browser.
CONTROL_ORIGIN = "https://not-a-clickpost-domain.example"

DEFAULT_BASE = "https://clickgpt.clickpost.ai"
API = "/api/rr-log-analyst"
TOKEN_URL = "https://ds.clickpost.in/api/v1/ai-agent/token"

_passed = 0
_failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _passed
    if ok:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -> {detail}" if detail else ""))
    return ok


def preflight(base: str, origin: str, timeout: int) -> int:
    r = requests.options(
        f"{base}{API}/messages/",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        timeout=timeout,
    )
    return r.status_code


def run_config_checks(base: str, timeout: int) -> None:
    print("\nreachability")
    try:
        r = requests.get(f"{base}/health", timeout=timeout)
        check("GET /health is 200", r.status_code == 200, f"got {r.status_code}")
    except requests.RequestException as exc:
        check("GET /health is 200", False, str(exc))
        return

    print("\nCORS preflight (the check that would have caught the outage)")
    for origin in KNOWN_FRONTENDS:
        code = preflight(base, origin, timeout)
        check(
            f"{origin} is allowed",
            code == 200,
            f"got {code} — add it to ALLOWED_ORIGINS and recreate the app container",
        )
    code = preflight(base, CONTROL_ORIGIN, timeout)
    check(
        "an unknown origin is still rejected",
        code == 400,
        f"got {code} — allow_origins looks like a wildcard, which it must not be",
    )

    print("\nmethod guards")
    r = requests.get(f"{base}{API}/messages/", timeout=timeout)
    check("GET /messages/ is 405 (POST-only)", r.status_code == 405, f"got {r.status_code}")


def run_flow_checks(base: str, token: str, user_id: int, enterprise_id: int,
                    timeout: int) -> None:
    print("\nauthenticated flow")
    r = requests.post(TOKEN_URL, headers={"Authorization": f"Token {token}"},
                      timeout=timeout)
    if not check("mint JWT", r.status_code == 200, f"got {r.status_code}"):
        return
    jwt = r.json().get("jwt") or r.json().get("token") or r.json().get("access_token")
    if not check("response carries a JWT", bool(jwt), f"keys: {list(r.json())}"):
        return

    auth = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    identity = {"user_id": user_id, "enterprise_id": enterprise_id}

    r = requests.post(f"{base}{API}/conversations/", headers=auth,
                      json=identity, timeout=timeout)
    if not check("create conversation", r.status_code == 200, f"got {r.status_code} {r.text[:200]}"):
        return
    conversation_id = r.json().get("data", {}).get("id") or r.json().get("id")
    if not check("conversation id returned", bool(conversation_id), r.text[:200]):
        return

    body = {
        **identity,
        "message": "smoke test: reply with the single word ok",
        "conversation_id": conversation_id,
        "enterprise_name": "smoke-test",
        "username": "smoke-test",
    }
    r = requests.post(f"{base}{API}/messages/",
                      headers={**auth, "Accept": "text/event-stream"},
                      json=body, timeout=timeout * 6, stream=True)
    ok = r.status_code == 200
    chunks = sum(1 for line in r.iter_lines() if line) if ok else 0
    check("send message (SSE)", ok and chunks > 0,
          f"status {r.status_code}, {chunks} event lines")

    r = requests.get(f"{base}{API}/conversations/{conversation_id}",
                     headers=auth, params=identity, timeout=timeout)
    check("get conversation", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = requests.get(f"{base}{API}/conversations/", headers=auth,
                     params=identity, timeout=timeout)
    check("list conversations", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    # is_internal_user=true without internal_user_id is rejected by design;
    # assert the contract so a frontend sending one without the other is caught.
    r = requests.get(f"{base}{API}/conversations/", headers=auth,
                     params={**identity, "is_internal_user": "true"}, timeout=timeout)
    check("is_internal_user without internal_user_id is a 400",
          r.status_code == 400, f"got {r.status_code}")


def _normalise_token(raw: str) -> str:
    """Accept the token however it was pasted.

    A .env line is commonly quoted and often keeps the scheme word copied out
    of a curl command, so the value arrives as `"Token abc123"`. Sending that
    verbatim yields `Authorization: Token Token abc123`, which DRF rejects with
    "Token string should not contain spaces" — a 401 that looks like a bad
    credential rather than a malformed header.
    """
    value = raw.strip().strip("\"'").strip()
    parts = value.split()
    return parts[-1] if len(parts) > 1 else value


def _load_token_from_dotenv(args: argparse.Namespace) -> None:
    """Fall back to a CP_TOKEN line in .env, which is already gitignored."""
    try:
        with open(".env", encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if key.strip() == "CP_TOKEN":
                    args.token = _normalise_token(value)
                    return
    except OSError:
        pass


def _wait_for_health(base: str, wait: int, timeout: int) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base}/health", timeout=timeout).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument(
        "--token",
        default=os.environ.get("CP_TOKEN"),
        help="Clickpost API token; enables the full flow. Prefer the CP_TOKEN "
             "environment variable (or a CP_TOKEN line in .env) so the secret "
             "never lands in shell history.",
    )
    p.add_argument(
        "--config-only",
        action="store_true",
        help="skip the authenticated flow even when a token is available; it "
             "creates a real conversation and spends LLM tokens, so a deploy "
             "should not run it on every release",
    )
    p.add_argument(
        "--wait",
        type=int,
        default=0,
        help="seconds to keep retrying /health before starting, for use right "
             "after a deploy while the app is still booting",
    )
    p.add_argument("--user-id", type=int, default=15)
    p.add_argument("--enterprise-id", type=int, default=15)
    p.add_argument("--timeout", type=int, default=15)
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    if args.config_only:
        args.token = None
    elif args.token:
        args.token = _normalise_token(args.token)
    else:
        _load_token_from_dotenv(args)
    print(f"smoke-testing {base}")
    if args.wait and not _wait_for_health(base, args.wait, args.timeout):
        print(f"  FAIL  app did not answer /health within {args.wait}s")
        sys.exit(1)
    run_config_checks(base, args.timeout)
    if args.token:
        run_flow_checks(base, args.token, args.user_id, args.enterprise_id, args.timeout)
    else:
        reason = "--config-only" if args.config_only else "no --token given"
        print(f"\nauthenticated flow\n  SKIP  {reason}")

    print(f"\n{_passed} passed, {len(_failed)} failed")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
