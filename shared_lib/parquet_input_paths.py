"""Resolve Parquet input paths from globs and optional JSON manifests (data-processor CLIs)."""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path
from typing import Sequence

PARQUET_SUFFIXES = frozenset({".parquet", ".pq"})


def load_manifest_parquet_paths(manifest_path: str | Path) -> list[str]:
    """
    Read ``artifacts[].output_file_abs`` / ``output_file`` from a manifest JSON; return existing
    ``.parquet`` / ``.pq`` file paths (deduped, sorted). Non-Parquet paths are skipped with a stderr warning.
    """
    path = Path(str(manifest_path)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to parse input manifest JSON: {path}") from exc
    artifacts = payload.get("artifacts", []) if isinstance(payload, dict) else []
    if not isinstance(artifacts, list):
        raise ValueError(f"Invalid manifest schema (artifacts must be a list): {path}")
    inputs: list[str] = []
    skipped_non_parquet: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        out_file = artifact.get("output_file_abs") or artifact.get("output_file")
        if not out_file:
            continue
        resolved = Path(str(out_file)).expanduser().resolve()
        if resolved.exists() and resolved.is_file():
            if resolved.suffix.lower() in PARQUET_SUFFIXES:
                inputs.append(str(resolved))
            else:
                skipped_non_parquet.append(str(resolved))
    if skipped_non_parquet:
        preview = ", ".join(skipped_non_parquet[:5])
        more = f" (+{len(skipped_non_parquet) - 5} more)" if len(skipped_non_parquet) > 5 else ""
        print(
            f"Warning: manifest skipped {len(skipped_non_parquet)} non-Parquet path(s) "
            f"(only .parquet / .pq accepted): {preview}{more}",
            file=sys.stderr,
        )
    return sorted(set(inputs))


def resolve_parquet_file_inputs(
    raw_inputs: Sequence[str],
    *,
    allow_empty_match: bool,
    max_files: int,
    include_dirs: bool = False,
    supported_suffixes: frozenset[str] | set[str] | None = None,
) -> list[Path]:
    """
    Expand globs / paths; keep only files whose suffix is allowed (default: ``.parquet`` / ``.pq``).
    Preserves first-seen order, dedupes by resolved path.
    """
    allowed = {str(s).lower() for s in (supported_suffixes or PARQUET_SUFFIXES)}
    paths: list[Path] = []
    for raw in raw_inputs:
        text = str(raw or "").strip()
        if not text:
            continue
        expanded = os.path.expandvars(os.path.expanduser(text))
        has_glob = glob.has_magic(expanded)
        matches = [Path(m).resolve() for m in glob.glob(expanded, recursive=True)] if has_glob else []
        if has_glob:
            if not matches and not allow_empty_match:
                raise FileNotFoundError(f"No matches for pattern: {text}")
            paths.extend(matches)
            continue
        p = Path(expanded).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Input path not found: {p}")
        paths.append(p)

    out: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        if p.is_dir():
            if include_dirs and p not in seen:
                seen.add(p)
                out.append(p)
            continue
        if p.is_file() and p.suffix.lower() in allowed:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    if len(out) > max_files:
        raise ValueError(f"Resolved input file count {len(out)} exceeds max_files={max_files}.")
    return out
