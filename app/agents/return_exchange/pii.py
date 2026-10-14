"""Mask phones, emails, payout links, and labeled customer names.

AWB numbers, reference numbers, order ids, and timestamps stay visible.
Those are the identifiers an ops user needs to keep working.
"""

from __future__ import annotations

import re

PHONE_RE = re.compile(r"(?<![A-Za-z0-9])(\+?\d[\d\-\s()]{7,}\d)(?![A-Za-z0-9])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}")
PAYOUT_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:rzp\.io|razorpay\.com)/\S+",
    re.IGNORECASE,
)
NAME_RE = re.compile(
    r"(?i)(\bname\b[^A-Za-z\n]{0,24})([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})"
)


def mask_pii(text: str) -> str:
    if not text:
        return text

    def mask_phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        # A timestamp or a date is not a phone. Bare digit runs are AWB,
        # order id, or reference fragments and must stay readable.
        if ":" in raw or DATE_RE.search(raw):
            return raw
        digits = re.sub(r"\D", "", raw)
        if not (10 <= len(digits) <= 15):
            return raw
        if not raw.strip().startswith("+") and not re.search(r"[\s()]", raw):
            return raw
        return f"***{digits[-4:]}"

    def mask_email(match: re.Match[str]) -> str:
        value = match.group(0)
        local, _, domain = value.partition("@")
        if not local or not domain:
            return "***@***"
        return f"{local[0]}***@{domain}"

    def mask_name(match: re.Match[str]) -> str:
        return f"{match.group(1)}***"

    text = PAYOUT_URL_RE.sub("[payout link hidden]", text)
    text = EMAIL_RE.sub(mask_email, text)
    text = NAME_RE.sub(mask_name, text)
    return PHONE_RE.sub(mask_phone, text)


class PiiFilter:
    # Longer than a typical email, phone, or payout link, so a match split
    # across two stream chunks is still held until it is complete.
    stream_hold_chars = 128

    def filter_stream(self, buffer: str) -> tuple[str, str]:
        return mask_pii(buffer), ""

    def filter_final(self, text: str) -> str:
        return mask_pii(text)


PII_FILTER = PiiFilter()
