"""Mask phone numbers and emails in assistant text before it is streamed or stored."""

from __future__ import annotations

import re

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s()]{7,}\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}")


def mask_pii(text: str) -> str:
    if not text:
        return text

    def _mask_phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        # Dates are 8 digits once separators are removed. Leave them, and leave
        # shorter numbers, so a return date or a short id is not treated as a phone.
        if _DATE_RE.fullmatch(raw.strip()):
            return raw
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 10:
            return raw
        return f"***{digits[-4:]}"

    def _mask_email(match: re.Match[str]) -> str:
        value = match.group(0)
        local, _, domain = value.partition("@")
        if not local or not domain:
            return "***@***"
        return f"{local[0]}***@{domain}"

    text = _EMAIL_RE.sub(_mask_email, text)
    return _PHONE_RE.sub(_mask_phone, text)


class PiiFilter:
    # Longer than a typical email or international phone, so a match split
    # across two stream chunks is still held until it is complete.
    stream_hold_chars = 128

    def filter_stream(self, buffer: str) -> tuple[str, str]:
        return mask_pii(buffer), ""

    def filter_final(self, text: str) -> str:
        return mask_pii(text)


PII_FILTER = PiiFilter()
