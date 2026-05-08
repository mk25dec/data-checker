from __future__ import annotations

# NOTE: Stops work as soon as a check fails.

from collections.abc import Iterable

from .results import SharedResult, success


class SharedResultError(RuntimeError):
    """Error raised when a shared tool failed."""

    def __init__(self, result: SharedResult) -> None:
        self.result = result
        super().__init__(result.error or result.message)


def raise_if_failed(result: SharedResult) -> SharedResult:
    """Stop immediately if the result failed."""
    if not result.success:
        raise SharedResultError(result)
    return result


def first_failed(results: Iterable[SharedResult]) -> SharedResult | None:
    """Find the first failed result."""
    for result in results:
        if not result.success:
            return result
    return None


def stop_on_first_failure(results: Iterable[SharedResult]) -> SharedResult:
    """Return the first failure, or success if all passed."""
    failed = first_failed(results)
    if failed is not None:
        return failed
    return success("All checks passed")


def exit_code(result: SharedResult) -> int:
    """Get the number code for success or failure."""
    return result.exit_code
