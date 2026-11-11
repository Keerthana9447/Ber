"""Small, dependency-free helpers for background-task failure handling."""
from __future__ import annotations

MAX_RETRY_DELAY_SECONDS = 900


def retry_delay_seconds(base_seconds: int, consecutive_failures: int, cap: int = MAX_RETRY_DELAY_SECONDS) -> int:
    """Delay before the next attempt: the normal interval after success, exponential backoff
    (base, 2x, 4x, ...) after consecutive failures, never above `cap`."""
    if base_seconds < 1:
        raise ValueError("base_seconds must be >= 1")
    if consecutive_failures <= 0:
        return base_seconds
    return min(base_seconds * 2 ** min(consecutive_failures - 1, 10), max(cap, base_seconds))


def should_log_traceback(consecutive_failures: int) -> bool:
    """Full traceback on the first failure and then every 10th, so a long outage does not flood logs."""
    return consecutive_failures == 1 or consecutive_failures % 10 == 0
