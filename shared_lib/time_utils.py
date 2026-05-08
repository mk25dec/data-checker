from __future__ import annotations

# NOTE: Makes timestamps and readable durations.

from datetime import datetime, timezone
from time import time
from typing import Any
from zoneinfo import ZoneInfo

from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success


def now_utc() -> datetime:
    """Get the current time in UTC."""
    return datetime.now(timezone.utc)


def now_local(timezone_name: str | None = None) -> datetime:
    """Get the current time in a chosen timezone."""
    if not timezone_name or timezone_name.lower() == "local":
        return datetime.now().astimezone()
    if timezone_name.lower() == "utc":
        return now_utc()
    return datetime.now(ZoneInfo(timezone_name))


def timestamp_iso(dt: datetime | None = None, *, timezone_name: str | None = None) -> str:
    """Make an ISO timestamp for logs or records."""
    value = dt or now_local(timezone_name)
    return value.isoformat(timespec="microseconds")


def timestamp_compact(dt: datetime | None = None, *, timezone_name: str | None = None) -> str:
    """Make a compact timestamp for file names."""
    value = dt or now_local(timezone_name)
    return value.strftime("%Y%m%d_%H%M%S")


def timestamp_compact_microseconds(dt: datetime | None = None, *, timezone_name: str | None = None) -> str:
    """Make a compact timestamp with microseconds."""
    value = dt or now_local(timezone_name)
    return value.strftime("%Y%m%d_%H%M%S_%f")


def parse_iso_timestamp(value: str | None) -> SharedResult:
    """Turn ISO timestamp text into a datetime."""
    if value is None or str(value).strip() == "":
        return failure("Timestamp is required", exit_code=EXIT_VALIDATION_ERROR, value=value)
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        return failure("Timestamp is not valid ISO text", exit_code=EXIT_VALIDATION_ERROR, error=str(exc), value=value)
    return success("Timestamp parsed", datetime=parsed, iso=parsed.isoformat())


def format_time_short(timestamp_seconds: float | None = None, *, timezone_name: str | None = "UTC") -> str:
    """Show time as HH:MM:SS."""
    ts = time() if timestamp_seconds is None else timestamp_seconds
    tz = ZoneInfo("UTC") if timezone_name in (None, "UTC", "utc") else ZoneInfo(timezone_name)
    return datetime.fromtimestamp(ts, tz=tz).strftime("%H:%M:%S")


_DAY = 86400.0
_MEAN_MONTH = (365.25 / 12.0) * _DAY
_YEAR = 365.25 * _DAY


def _fmt_decimal(value: float, unit: str, *, decimals: int) -> str:
    """Format a positive duration quotient with trailing zeros stripped (e.g. 0.12s, 1.08min)."""
    if value != value or value < 0:  # NaN or negative treated upstream
        return f"0{unit}"
    s = f"{value:.{decimals}f}"
    s = s.rstrip("0").rstrip(".")
    return f"{s}{unit}"


def format_duration(seconds: float) -> str:
    """Turn seconds into a short label using one scale (decimal seconds, minutes, hours, days, months, years)."""
    if seconds < 0 or seconds != seconds:
        return "0s"
    if seconds < 60:
        return _fmt_decimal(seconds, "s", decimals=3)
    if seconds < 3600:
        return _fmt_decimal(seconds / 60.0, "min", decimals=2)
    if seconds < _DAY:
        return _fmt_decimal(seconds / 3600.0, "h", decimals=2)
    # Use days through 30d inclusive; from ~31d use mean months until a full year.
    if seconds < 31 * _DAY:
        return _fmt_decimal(seconds / _DAY, "d", decimals=2)
    if seconds < _YEAR:
        return _fmt_decimal(seconds / _MEAN_MONTH, "mo", decimals=2)
    return _fmt_decimal(seconds / _YEAR, "y", decimals=2)


def run_stats(start_timestamp: float, end_timestamp: float | None = None) -> dict[str, Any]:
    """Summarize start time, end time, and duration."""
    end = time() if end_timestamp is None else end_timestamp
    duration = end - start_timestamp
    start_dt = datetime.fromtimestamp(start_timestamp, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end, tz=timezone.utc)
    return {
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "duration_sec": duration,
        "duration_human": format_duration(duration),
    }
