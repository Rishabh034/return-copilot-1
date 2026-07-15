#!/usr/bin/env python3
"""Search the app's gzipped, time-partitioned logs in S3.

Logs are uploaded by app/log_uploader.py under keys like
    <prefix>/YYYY/MM/DD/HH/<hostname>_<epoch>.log.gz   (partitioned in UTC)
so a time-bounded search only lists/downloads the hour prefixes in range.

Examples:
    # everywhere in today's logs (UTC)
    python scripts/search_s3_logs.py "a2647c1e-4a03"

    # one date, hours 08-12 UTC
    python scripts/search_s3_logs.py -d 2026-07-14 -t 08-12 "ERROR"

    # multiple dates
    python scripts/search_s3_logs.py -d 2026-07-13,2026-07-14 "enterprise_id=123"

    # explicit range, entered in IST
    python scripts/search_s3_logs.py -s "2026-07-14 09:00" -e "2026-07-14 13:30" --ist "Timeout"

    # regex, case-insensitive, stop after 50 matches
    python scripts/search_s3_logs.py -r -i -c 50 "clickpost (GET|POST)"
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from datetime import datetime, timedelta, timezone

import boto3

DEFAULT_BUCKET = "clickpost-ai-agent-recom-clickhelp-logs"
DEFAULT_PREFIX = "logs/clickpost-ai-projects"
IST_OFFSET = timedelta(hours=5, minutes=30)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("pattern", help="string (or regex with -r) to search for")
    p.add_argument("-d", "--date", help="date(s) YYYY-MM-DD, comma-separated; default: today")
    p.add_argument("-t", "--time", help='hour or hour range, e.g. "09" or "08-12"')
    p.add_argument("-s", "--start", help='range start "YYYY-MM-DD HH:MM" (overrides -d/-t)')
    p.add_argument("-e", "--end", help='range end "YYYY-MM-DD HH:MM"; default: now')
    p.add_argument("--ist", action="store_true", help="interpret -d/-t/-s/-e as IST instead of UTC")
    p.add_argument("-b", "--bucket", default=DEFAULT_BUCKET)
    p.add_argument("-p", "--prefix", default=DEFAULT_PREFIX)
    p.add_argument("-r", "--regex", action="store_true", help="treat pattern as a regex")
    p.add_argument("-i", "--ignore-case", action="store_true")
    p.add_argument("-c", "--count", type=int, help="stop after this many matching lines")
    p.add_argument("-l", "--list-only", action="store_true", help="only list the S3 files in range")
    return p.parse_args()


def hours_in_range(args: argparse.Namespace) -> list[datetime]:
    """Expand the CLI time filters into a list of UTC hour datetimes."""
    tz_shift = IST_OFFSET if args.ist else timedelta(0)

    def to_utc(local: datetime) -> datetime:
        return (local - tz_shift).replace(tzinfo=timezone.utc)

    if args.start:
        start = to_utc(datetime.strptime(args.start, "%Y-%m-%d %H:%M"))
        end = (
            to_utc(datetime.strptime(args.end, "%Y-%m-%d %H:%M"))
            if args.end
            else datetime.now(timezone.utc)
        )
        if end < start:
            sys.exit("error: --end is before --start")
        hours = []
        cur = start.replace(minute=0, second=0, microsecond=0)
        while cur <= end:
            hours.append(cur)
            cur += timedelta(hours=1)
        return hours

    if args.date:
        dates = [datetime.strptime(d.strip(), "%Y-%m-%d") for d in args.date.split(",")]
    else:
        dates = [datetime.now(timezone.utc) + tz_shift]  # "today" in the chosen tz

    if args.time:
        parts = args.time.split("-")
        first, last = int(parts[0]), int(parts[-1])
    else:
        first, last = 0, 23

    hours = []
    for d in dates:
        for h in range(first, last + 1):
            hours.append(to_utc(d.replace(hour=h, minute=0, second=0, microsecond=0)))
    # never scan the future
    now = datetime.now(timezone.utc)
    return [h for h in hours if h <= now]


def main() -> None:
    args = parse_args()
    flags = re.IGNORECASE if args.ignore_case else 0
    pattern = re.compile(args.pattern if args.regex else re.escape(args.pattern), flags)

    s3 = boto3.client("s3")
    keys: list[str] = []
    for hour in hours_in_range(args):
        prefix = f"{args.prefix}/{hour:%Y/%m/%d/%H}/"
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=args.bucket, Prefix=prefix
        ):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))

    if args.list_only:
        for key in keys:
            print(key)
        print(f"-- {len(keys)} file(s) in range", file=sys.stderr)
        return

    matches = 0
    for key in keys:
        body = s3.get_object(Bucket=args.bucket, Key=key)["Body"]
        with gzip.GzipFile(fileobj=body) as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if pattern.search(line):
                    print(f"{key}: {line}")
                    matches += 1
                    if args.count and matches >= args.count:
                        print(f"-- stopped at {matches} matches (-c)", file=sys.stderr)
                        return

    print(f"-- {matches} match(es) across {len(keys)} file(s)", file=sys.stderr)
    if matches == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
