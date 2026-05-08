"""Escape helpers for DuckDB SQL fragments built in Python."""

from __future__ import annotations


def sql_string_literal(value: str | object) -> str:
    """Single-quoted SQL string literal (standard SQL escaping)."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_double_quoted_ident(identifier: str) -> str:
    """Double-quoted SQL identifier (DuckDB / PostgreSQL style)."""
    return '"' + str(identifier).replace('"', '""') + '"'
