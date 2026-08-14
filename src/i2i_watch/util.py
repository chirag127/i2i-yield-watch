"""Shared helpers: number/date parsing, NA detection, logging."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone

log = logging.getLogger("i2i_watch")

_NA_RE = re.compile(r"^(n/?a|na|null|none|-|unknown|#####)$", re.IGNORECASE)


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_na(v: object) -> bool:
    """True for None, empty string, or an NA sentinel token."""
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        return s == "" or bool(_NA_RE.match(s))
    return False


def to_number(v: object) -> float | None:
    """'6345.00' / '₹ 6,345' / 12 -> float; None on failure or NA."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if _finite(v) else None
    s = re.sub(r"[,₹\s]", "", str(v))
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n if _finite(n) else None


def _finite(n: float) -> bool:
    return n == n and n not in (float("inf"), float("-inf"))


def parse_posted_on(s: object) -> str | None:
    """'DD-MM-YYYY' -> ISO UTC string; None on bad input."""
    if not isinstance(s, str):
        return None
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        d = datetime(yyyy, mm, dd, tzinfo=timezone.utc)
    except ValueError:
        return None
    return d.isoformat().replace("+00:00", "Z")


def format_posted_on(iso: object) -> str | None:
    """ISO string -> 'DD-MM-YYYY' for display; None on failure."""
    if not iso or not isinstance(iso, str):
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def inr(n: float | None) -> str | None:
    """Indian-grouped rupee string, e.g. 123456 -> '₹1,23,456'; None if invalid."""
    if n is None or not _finite(float(n)):
        return None
    return "₹" + _group_indian(round(n))


def _group_indian(n: int) -> str:
    """Group an integer with Indian thousands separators (last 3, then pairs)."""
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts) + "," + tail


def bare(x: str | None) -> str | None:
    """Strip a leading '₹' so an amount can be embedded inline."""
    if not x:
        return None
    return re.sub(r"^₹\s*", "", str(x)).strip()


def tenure_months(v: object) -> float | None:
    """Loan tenure -> months as float. Accepts an int/float count of months,
    or strings like '6 Months', '6', '1 Year', '1.5 yrs'. None on failure/NA.
    """
    if v is None or is_na(v):
        return None
    if isinstance(v, (int, float)):
        return float(v) if _finite(v) else None
    s = str(v).strip().lower()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    n = float(m.group(1))
    if re.search(r"year|yr|annum", s):
        n *= 12.0
    return n if _finite(n) else None

