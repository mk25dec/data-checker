from __future__ import annotations

# NOTE: Loads and combines settings from config files.

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

from .path_utils import check_path
from .results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def expand_env_vars(value: Any, *, env: dict[str, str] | None = None) -> Any:
    """Replace ${NAME} or ${NAME:-default} in settings."""
    env_map = env or dict(os.environ)

    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2)
            return env_map.get(name, default if default is not None else match.group(0))

        return _ENV_VAR_RE.sub(repl, value)
    if isinstance(value, list):
        return [expand_env_vars(item, env=env_map) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_vars(item, env=env_map) for key, item in value.items()}
    return value


def load_toml_config(
    path: str | Path | None,
    *,
    section: str | None = None,
    expand_env: bool = True,
) -> SharedResult:
    """Load settings from a TOML file."""
    path_result = check_path(path, expected="file")
    if not path_result.success:
        return path_result

    resolved = Path(path_result.data["path"])
    try:
        with resolved.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return failure(
            f"Could not load TOML config: {resolved}",
            exit_code=EXIT_ERROR,
            error=str(exc),
            path=str(resolved),
        )

    if expand_env:
        data = expand_env_vars(data)

    selected: Any = data
    if section:
        selected = data.get(section)
        if not isinstance(selected, dict):
            return failure(
                f"Config section not found: {section}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(resolved),
                section=section,
                available_sections=[key for key, value in data.items() if isinstance(value, dict)],
            )

    if not isinstance(selected, dict):
        return failure(
            "Config content must be a table",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(resolved),
            section=section,
        )

    return success("Config loaded", path=str(resolved), section=section, config=selected)


def load_yaml_config(
    path: str | Path | None,
    *,
    section: str | None = None,
    expand_env: bool = True,
) -> SharedResult:
    """Load settings from a YAML file when PyYAML is installed."""
    path_result = check_path(path, expected="file")
    if not path_result.success:
        return path_result

    try:
        import yaml
    except ImportError:
        return failure("PyYAML is not installed", exit_code=EXIT_ERROR, path=str(path))

    resolved = Path(path_result.data["path"])
    try:
        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return failure(
            f"Could not load YAML config: {resolved}",
            exit_code=EXIT_ERROR,
            error=str(exc),
            path=str(resolved),
        )

    if expand_env:
        data = expand_env_vars(data)

    selected: Any = data
    if section:
        sections = data.get("sections", data) if isinstance(data, dict) else {}
        selected = sections.get(section) if isinstance(sections, dict) else None
        if not isinstance(selected, dict):
            return failure(
                f"Config section not found: {section}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(resolved),
                section=section,
                available_sections=list(sections.keys()) if isinstance(sections, dict) else [],
            )

    if not isinstance(selected, dict):
        return failure("Config content must be a table", exit_code=EXIT_VALIDATION_ERROR, path=str(resolved))

    return success("Config loaded", path=str(resolved), section=section, config=selected)


def merge_settings(*configs: dict[str, Any], skip_none: bool = True) -> dict[str, Any]:
    """Combine settings, with later values winning."""
    merged: dict[str, Any] = {}
    for config in configs:
        for key, value in (config or {}).items():
            if skip_none and value is None:
                continue
            merged[key] = value
    return merged


def validate_required_settings(config: dict[str, Any], required_keys: Iterable[str]) -> SharedResult:
    """Check that required settings are present."""
    missing = [key for key in required_keys if config.get(key) in (None, "")]
    if missing:
        return failure(
            f"Missing required settings: {', '.join(missing)}",
            exit_code=EXIT_VALIDATION_ERROR,
            missing=missing,
        )
    return success("Required settings are present", required=list(required_keys))


def validate_allowed_settings(config: dict[str, Any], allowed_keys: Iterable[str]) -> SharedResult:
    """Check that settings only use allowed names."""
    allowed = set(allowed_keys)
    unknown = sorted(set(config) - allowed)
    if unknown:
        return failure(
            f"Unknown settings: {', '.join(unknown)}",
            exit_code=EXIT_VALIDATION_ERROR,
            unknown=unknown,
            allowed=sorted(allowed),
        )
    return success("Only allowed settings are present", allowed=sorted(allowed))


def pick_setting(cli_value: Any, config: dict[str, Any], name: str, default: Any = None) -> Any:
    """Choose a command-line value first, then config, then default."""
    if cli_value is not None:
        return cli_value
    return config.get(name, default)
