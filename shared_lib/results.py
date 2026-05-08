from __future__ import annotations

# NOTE: Gives every shared tool the same success or failure shape.

from dataclasses import dataclass, field
from typing import Any

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_VALIDATION_ERROR = 2
EXIT_NOT_FOUND = 3
EXIT_TYPE_MISMATCH = 4
EXIT_IO_ERROR = 5


@dataclass(frozen=True)
class SharedResult:
    """A clear success or failure answer from a shared tool."""

    success: bool
    exit_code: int
    message: str
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "message": self.message,
            "error": self.error,
            "data": dict(self.data),
        }


def success(message: str = "Success", **data: Any) -> SharedResult:
    return SharedResult(
        success=True,
        exit_code=EXIT_SUCCESS,
        message=message,
        error=None,
        data=data,
    )


def failure(
    message: str,
    *,
    exit_code: int = EXIT_ERROR,
    error: str | None = None,
    **data: Any,
) -> SharedResult:
    return SharedResult(
        success=False,
        exit_code=exit_code,
        message=message,
        error=error or message,
        data=data,
    )
