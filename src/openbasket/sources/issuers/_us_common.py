"""Helpers shared by the US issuer adapters (Vanguard US, Avantis, First Trust).

The leading underscore keeps :func:`openbasket.sources.issuers.discover` from
treating this module as an adapter.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from ...identifiers import is_valid_cusip, is_valid_isin
from . import HOLDING_COLUMNS

_NUM_RE = re.compile(r"[^0-9.\-]")
_SEDOL_RE = re.compile(r"^[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}[0-9]$")
_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9)


def clean(value: object) -> str | None:
    """Strip a text value; blanks, dashes and NaN become ``None``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return None if s in ("", "-", "--", "N/A", "n/a", "nan", "None") else s


def number(value: object) -> float | None:
    """Parse ``"8.39%"``, ``"$1,234.50"``, ``"($65.10)"`` or ``"-0.47"``.

    Parentheses mean a negative number, as in accounting tables. Returns
    ``None`` for blanks and text that holds no number.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if pd.isna(value) else float(value)
    s = clean(value)
    if s is None:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = _NUM_RE.sub("", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -abs(v) if negative else v


def isin_or_none(value: object) -> str | None:
    s = clean(value)
    return s.upper() if s and is_valid_isin(s) else None


def cusip_or_none(value: object) -> str | None:
    s = clean(value)
    return s.upper() if s and is_valid_cusip(s) else None


def sedol_or_none(value: object) -> str | None:
    """A SEDOL with a correct check digit, else ``None``."""
    s = clean(value)
    if not s:
        return None
    s = s.upper()
    if not _SEDOL_RE.match(s):
        return None
    total = sum((int(c) if c.isdigit() else ord(c) - 55) * w for c, w in zip(s[:6], _SEDOL_WEIGHTS))
    return s if str((10 - total % 10) % 10) == s[6] else None


def us_date(value: object) -> str | None:
    """``"11/2/2026"`` or ``"2026-09-30T00:00:00-04:00"`` to ISO ``2026-11-02``."""
    s = clean(value)
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def holdings_frame(rows: list[dict], extra: tuple[str, ...] = ()) -> pd.DataFrame:
    """Rows to a frame with :data:`HOLDING_COLUMNS` first, then ``extra``."""
    cols = list(HOLDING_COLUMNS) + [c for c in extra if c not in HOLDING_COLUMNS]
    df = pd.DataFrame(rows, columns=cols)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    return df


def cached(
    cache_dir: str | Path | None,
    key: str,
    fetch: Callable[[], bytes],
    parse: Callable[[bytes], object],
    suffix: str = "json",
):
    """Return ``parse(content)``, using one cache file per ``key`` per day.

    The response is written to the cache only after ``parse`` succeeded, so a
    block page or a truncated download is never cached.
    """
    path = None
    if cache_dir:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
        path = Path(cache_dir) / f"{safe}_{date.today().isoformat()}.{suffix}"
        if path.exists():
            return parse(path.read_bytes())
    content = fetch()
    result = parse(content)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return result


def load_json(content: bytes) -> object:
    return json.loads(content.decode("utf-8"))
