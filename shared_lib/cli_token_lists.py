"""
Parse CLI list arguments: repeated tokens with comma-splitting, and/or a JSON array of strings.

Typical Typer/Click pattern: ``-k a -k b,c`` or ``--keys-json '["a","b"]'`` for identifiers that may contain commas.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success

_JSON_ITEMS_KEY = "items"


def parse_comma_separated_cli_tokens(raw: Sequence[Any]) -> list[str]:
    """
    Flatten repeatable CLI values where each value may be comma-separated.

    Trims whitespace, skips empty pieces, preserves order, drops duplicates (first wins).
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        for piece in str(item).split(","):
            name = piece.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def parse_json_array_of_strings(payload: str, *, label: str = "JSON list") -> SharedResult:
    """
    Parse a JSON document that must be an array of strings (after strip, empty strings are skipped).

    On success, ``result.data["items"]`` is the ``list[str]``. On failure, ``exit_code`` is
    ``EXIT_VALIDATION_ERROR``.
    """
    text = str(payload).strip()
    if not text:
        return failure(
            f"{label}: empty payload.",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return failure(
            f"{label}: not valid JSON ({exc}).",
            exit_code=EXIT_VALIDATION_ERROR,
            error=str(exc),
        )
    if not isinstance(parsed, list):
        return failure(
            f"{label}: expected a JSON array of strings, e.g. [\"a\",\"b\"].",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    seen: set[str] = set()
    out: list[str] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, str):
            return failure(
                f"{label}: element at index {i} must be a string, got {type(item).__name__}.",
                exit_code=EXIT_VALIDATION_ERROR,
            )
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    if not out:
        return failure(
            f"{label}: need at least one non-empty string.",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    return success(f"{label} parsed", **_json_items_payload(out))


def resolve_comma_or_json_cli_list(
    comma_tokens: Sequence[Any],
    json_payload: str | None,
    *,
    mutual_exclusive: bool = True,
    comma_option_desc: str = "comma-separated CLI tokens",
    json_option_desc: str = "JSON list flag",
    data_key: str = "items",
    non_empty: bool = True,
    empty_error: str | None = None,
    json_label: str = "JSON list",
) -> SharedResult:
    """
    Resolve a list from repeatable comma-splitting tokens and/or a JSON array string.

    * If ``json_payload`` is non-empty after strip: parse JSON (unless ``mutual_exclusive`` and comma tokens
      were also given—then error). When ``mutual_exclusive`` is false and both are given, JSON entries come
      first, then comma tokens, with de-duplication preserving that order.
    * If ``json_payload`` is empty/None: use only comma tokens.

    On success, the list is stored under ``data[data_key]`` (default ``\"items\"``).
    """
    jp = str(json_payload).strip() if json_payload is not None else ""
    flat = parse_comma_separated_cli_tokens(comma_tokens or ())

    if jp:
        if mutual_exclusive and flat:
            return failure(
                f"Pass either {comma_option_desc} or {json_option_desc}, not both.",
                exit_code=EXIT_VALIDATION_ERROR,
            )
        jr = parse_json_array_of_strings(jp, label=json_label)
        if not jr.success:
            return jr
        items = list(jr.data.get(_JSON_ITEMS_KEY) or [])
        if not mutual_exclusive and flat:
            items = _dedupe_merge_ordered(items, flat)
    else:
        items = flat

    if non_empty and not items:
        msg = empty_error or (
            f"Pass at least one value ({comma_option_desc} or {json_option_desc})."
        )
        return failure(msg, exit_code=EXIT_VALIDATION_ERROR)

    extra: dict[str, Any] = {data_key: items} if data_key != _JSON_ITEMS_KEY else _json_items_payload(items)
    return success("List resolved", **extra)


def _json_items_payload(items: list[str]) -> dict[str, Any]:
    return {_JSON_ITEMS_KEY: items}


def _dedupe_merge_ordered(first: list[str], second: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in first + second:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out
