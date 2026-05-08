from __future__ import annotations

# NOTE: Creates simple console and file loggers.

import logging
from pathlib import Path

from .path_utils import ensure_directory
from .results import EXIT_ERROR, SharedResult, failure, success


def log_level(value: str | int | None) -> int:
    """Turn a log level name into a logging number."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        parsed = logging.getLevelName(value.strip().upper())
        if isinstance(parsed, int):
            return parsed
    return logging.INFO


def get_logger(
    name: str,
    *,
    level: str | int | None = None,
    log_dir: str | Path | None = None,
    log_file_name: str | None = None,
    console: bool = True,
) -> SharedResult:
    """Create a logger that can write to screen and file."""
    logger_name = (name or "app").strip() or "app"
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level(level))
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    if console and not any(getattr(handler, "_shared_console", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler._shared_console = True  # type: ignore[attr-defined]
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    log_path: Path | None = None
    if log_dir is not None:
        dir_result = ensure_directory(log_dir)
        if not dir_result.success:
            return dir_result

        safe_file_name = (log_file_name or f"{logger_name}.log").strip()
        log_path = Path(dir_result.data["path"]) / safe_file_name
        if not any(getattr(handler, "_shared_log_path", None) == str(log_path) for handler in logger.handlers):
            try:
                file_handler = logging.FileHandler(log_path, encoding="utf-8")
            except OSError as exc:
                return failure(
                    f"Could not create log file: {log_path}",
                    exit_code=EXIT_ERROR,
                    error=str(exc),
                    path=str(log_path),
                )
            file_handler._shared_log_path = str(log_path)  # type: ignore[attr-defined]
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return success("Logger is ready", logger=logger, name=logger_name, log_path=str(log_path) if log_path else None)


def log_result(logger: logging.Logger, result: SharedResult, *, action: str = "result") -> None:
    """Write a success or failure result to the logger."""
    if result.success:
        logger.info("%s success message=%s", action, result.message)
    else:
        logger.error("%s failed error=%s", action, result.error)
