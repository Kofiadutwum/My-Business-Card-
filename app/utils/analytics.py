"""Analytics helpers: visitor hashing and time-series bucketing."""

import hashlib
from collections import OrderedDict
from datetime import timedelta

from flask import current_app, request


def visitor_hash():
    """A salted, truncated digest of IP + user agent.

    Enough to tell a repeat scan from a fresh one; not enough to identify a
    person or to be reversed back to an address. Ghana's Data Protection Act
    (Act 843) treats an IP address as personal data, so it is never stored raw.
    """
    raw = "|".join(
        [
            request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            request.headers.get("User-Agent", ""),
            current_app.config["SECRET_KEY"],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def daily_series(rows, start, days, date_attr="viewed_at"):
    """Bucket rows into a dense day-by-day series.

    Dense matters. A sparse series silently drops zero days, and a chart
    drawn from it overstates activity by closing the gaps.
    """
    buckets = OrderedDict()
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        buckets[day] = 0
    for row in rows:
        value = getattr(row, date_attr)
        if value is None:
            continue
        key = value.date()
        if key in buckets:
            buckets[key] += 1
    return buckets


def monthly_totals(payments, months=12):
    """Revenue per calendar month, in minor units."""
    buckets = OrderedDict()
    for payment in payments:
        if payment.status != "success" or not payment.paid_at:
            continue
        key = payment.paid_at.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + payment.amount_minor
    ordered = OrderedDict(sorted(buckets.items()))
    trimmed = list(ordered.items())[-months:]
    return OrderedDict(trimmed)
