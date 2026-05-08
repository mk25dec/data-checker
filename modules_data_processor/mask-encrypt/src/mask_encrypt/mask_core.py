from __future__ import annotations

import base64
import csv
import glob
import hashlib
import hmac
import os
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import pandas as pd
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shared_lib import add_timestamp_to_path, format_duration
from shared_lib.cli_token_lists import parse_comma_separated_cli_tokens
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xls", ".parquet", ".json"}
SEED_USERS = {
    "user1": "user1_dev_pass",
    "admin": "admin_dev_pass",
    "user0": "user0_dev_pass",
}
PBKDF2_ITERATIONS = 210_000
DEFAULT_PASSWORD_ENV_VAR = "MASK_ENCRYPT_PASSWORD"
VAULT_FIELDS = ["username", "password_salt", "password_hash", "key_id", "key_b64"]


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode("ascii"))


def _password_hash(password: str, salt_b64: str) -> str:
    salt = _b64url_decode(salt_b64)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    return _b64url_no_pad(digest)


def _new_password_record(password: str) -> tuple[str, str]:
    salt = os.urandom(16)
    salt_b64 = _b64url_no_pad(salt)
    return salt_b64, _password_hash(password, salt_b64)


def _vault_path(auth_directory: Path) -> Path:
    return auth_directory / "auth.csv"


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [{k: str(v or "") for k, v in row.items()} for row in reader]


def _write_csv_dicts(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(row.get(k, "")) for k in fieldnames})


def _init_auth_store(auth_directory: Path) -> Path:
    auth_path = _vault_path(auth_directory)
    rows = _read_csv_dicts(auth_path)

    # Backward compatibility: if legacy files exist, migrate them into auth.csv.
    if not rows:
        legacy_users = _read_csv_dicts(auth_directory / "users.csv")
        legacy_keys = _read_csv_dicts(auth_directory / "keys.csv")
        users_by_name = {r.get("username", ""): r for r in legacy_users if r.get("username")}
        active_keys_by_user: dict[str, dict[str, str]] = {}
        for row in legacy_keys:
            username = str(row.get("username", ""))
            if not username:
                continue
            if str(row.get("status", "active")).lower() != "active":
                continue
            active_keys_by_user[username] = row
        for username, user_row in users_by_name.items():
            key_row = active_keys_by_user.get(username)
            if not key_row:
                continue
            rows.append(
                {
                    "username": username,
                    "password_salt": str(user_row.get("password_salt", "")),
                    "password_hash": str(user_row.get("password_hash", "")),
                    "key_id": str(key_row.get("key_id", "")),
                    "key_b64": str(key_row.get("key_b64", "")),
                }
            )

    rows_by_username = {r.get("username", ""): r for r in rows if r.get("username")}
    for username, password in SEED_USERS.items():
        if username in rows_by_username:
            continue
        salt_b64, hash_b64 = _new_password_record(password)
        rows.append(
            {
                "username": username,
                "password_salt": salt_b64,
                "password_hash": hash_b64,
                "key_id": f"{username}-k1",
                "key_b64": _b64url_no_pad(os.urandom(32)),
            }
        )

    _write_csv_dicts(auth_path, rows, fieldnames=VAULT_FIELDS)
    return auth_path


def _authenticate_user(auth_path: Path, username: str, password: str) -> None:
    rows = _read_csv_dicts(auth_path)
    row = next((r for r in rows if r.get("username") == username), None)
    if row is None:
        raise ValueError("Authentication failed.")
    expected = str(row.get("password_hash", ""))
    actual = _password_hash(password, str(row.get("password_salt", "")))
    if not hmac.compare_digest(expected, actual):
        raise ValueError("Authentication failed.")


def _get_active_key(auth_path: Path, username: str) -> tuple[str, AESGCM]:
    rows = _read_csv_dicts(auth_path)
    row = next((r for r in rows if r.get("username") == username), None)
    if row is None:
        raise ValueError("No active encryption key found for user.")
    key = _b64url_decode(str(row.get("key_b64", "")))
    if len(key) != 32:
        raise ValueError("Invalid key length.")
    return str(row.get("key_id")), AESGCM(key)


def _get_key_by_id(auth_path: Path, username: str, key_id: str) -> AESGCM:
    rows = _read_csv_dicts(auth_path)
    row = next((r for r in rows if r.get("username") == username and r.get("key_id") == key_id), None)
    if row is None:
        raise ValueError("Decryption key not found for key_id.")
    key = _b64url_decode(str(row.get("key_b64", "")))
    if len(key) != 32:
        raise ValueError("Invalid key length.")
    return AESGCM(key)


def _collect_input_files(raw_inputs: list[str], *, recursive: bool, allow_empty_match: bool, max_files: int) -> list[Path]:
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
            walker = p.rglob("*") if recursive else p.glob("*")
            for child in walker:
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    c = child.resolve()
                    if c not in seen:
                        seen.add(c)
                        out.append(c)
        elif p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    if len(out) > max_files:
        raise ValueError(f"Resolved input file count {len(out)} exceeds max_files={max_files}.")
    return out


def _read_df(path: Path, *, delimiter: str) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext == ".csv":
        return pd.read_csv(path, sep=delimiter, dtype=object)
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object)
    if ext == ".parquet":
        return pd.read_parquet(path)
    if ext == ".json":
        return pd.read_json(path, dtype=object)
    raise ValueError(f"Unsupported input format: {path}")


def _mask_value(value: Any, *, style: str, mask_char: str) -> Any:
    if pd.isna(value):
        return value
    text = str(value)
    if text == "":
        return text
    if style == "full":
        return mask_char * len(text)
    if style == "email" and "@" in text:
        local, domain = text.split("@", 1)
        if not local:
            return f"{mask_char}@{domain}"
        return f"{local[:1]}{mask_char * max(1, len(local) - 1)}@{domain}"
    if len(text) <= 4:
        return mask_char * len(text)
    return f"{mask_char * (len(text) - 4)}{text[-4:]}"


def _encrypt_text(aesgcm: AESGCM, key_id: str, value: Any) -> Any:
    if pd.isna(value):
        return value
    text = str(value)
    if text == "":
        return text
    nonce = os.urandom(12)
    encrypted = aesgcm.encrypt(nonce, text.encode("utf-8"), None)
    token = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
    return f"enc:v1:{key_id}:{token}"


def _decrypt_text(value: Any, *, username: str, auth_path: Path, active_key_id: str, active_aesgcm: AESGCM | None) -> Any:
    if pd.isna(value):
        return value
    text = str(value)
    if not text.startswith("enc:v1:"):
        return value
    parts = text.split(":", 3)
    if len(parts) != 4:
        raise ValueError("Invalid encrypted payload format.")
    _, version, key_id, token = parts
    if version != "v1":
        raise ValueError("Unsupported encrypted payload version.")
    aesgcm = active_aesgcm if (active_aesgcm is not None and key_id == active_key_id) else _get_key_by_id(auth_path, username, key_id)
    blob = _b64url_decode(token)
    if len(blob) < 13:
        raise ValueError("Invalid encrypted payload.")
    nonce, ciphertext = blob[:12], blob[12:]
    plain = aesgcm.decrypt(nonce, ciphertext, None)
    return plain.decode("utf-8")


def _apply_action(
    df: pd.DataFrame,
    *,
    action: str,
    sensitive_cols: list[str],
    mask_style: str,
    mask_char: str,
    username: str,
    auth_path: Path,
    key_id: str,
    aesgcm: AESGCM | None,
) -> pd.DataFrame:
    out = df.copy()
    missing = [c for c in sensitive_cols if c not in out.columns]
    if missing:
        raise ValueError(f"Sensitive columns not found in input: {missing}")
    for col in sensitive_cols:
        if action == "mask":
            out[col] = out[col].map(lambda v: _mask_value(v, style=mask_style, mask_char=mask_char))
        elif action == "encrypt":
            if aesgcm is None:
                raise ValueError("Encryption key is required for encrypt action.")
            out[col] = out[col].map(lambda v: _encrypt_text(aesgcm, key_id, v))
        elif action == "decrypt":
            out[col] = out[col].map(
                lambda v: _decrypt_text(
                    v,
                    username=username,
                    auth_path=auth_path,
                    active_key_id=key_id,
                    active_aesgcm=aesgcm,
                )
            )
    return out


def _resolve_password(username: str, password_env_var: str) -> str:
    env_val = os.environ.get(password_env_var, "").strip()
    if env_val:
        return env_val
    # Convenience for local dev: seeded users work without prompts/env vars.
    seeded = SEED_USERS.get(username)
    if seeded:
        return seeded
    if not os.isatty(0):
        raise ValueError(f"Missing password in env var {password_env_var}.")
    import getpass

    return getpass.getpass("Password: ")


def run_mask_encrypt(args: SimpleNamespace) -> SharedResult:
    t0 = perf_counter()
    raw_inputs = list(getattr(args, "input_file", []) or [])
    inputs = parse_comma_separated_cli_tokens(raw_inputs)
    if not inputs:
        return failure("Pass at least one -i/--input-file.", exit_code=EXIT_VALIDATION_ERROR)

    action = str(getattr(args, "action", "mask") or "mask").strip().lower()
    if action not in {"mask", "encrypt", "decrypt"}:
        return failure("action must be one of: mask, encrypt, decrypt.", exit_code=EXIT_VALIDATION_ERROR)

    sensitive_cols = list(getattr(args, "sensitive_columns", []) or [])
    if not sensitive_cols:
        return failure("Pass at least one sensitive column.", exit_code=EXIT_VALIDATION_ERROR)

    username = str(getattr(args, "username", "") or "").strip()
    if not username:
        return failure("username is required.", exit_code=EXIT_VALIDATION_ERROR)

    recursive = bool(getattr(args, "recursive", False))
    allow_empty_match = bool(getattr(args, "allow_empty_match", False))
    max_files = int(getattr(args, "max_files", 5000) or 5000)
    delimiter = str(getattr(args, "delimiter", ",") or ",")
    output_dir_arg = getattr(args, "output_directory", None)
    output_dir = Path(output_dir_arg).expanduser().resolve() if output_dir_arg else None
    output_name = str(getattr(args, "output_file_name", "mask_encrypt") or "mask_encrypt").strip()
    mask_style = str(getattr(args, "mask_style", "last4") or "last4").strip().lower()
    mask_char = str(getattr(args, "mask_char", "*") or "*")[:1] or "*"
    add_timestamp = bool(getattr(args, "add_timestamp", True))
    auth_directory = Path(getattr(args, "auth_directory", "vault")).expanduser().resolve()
    password_env_var = str(getattr(args, "password_env_var", DEFAULT_PASSWORD_ENV_VAR) or DEFAULT_PASSWORD_ENV_VAR)

    if mask_style not in {"last4", "full", "email"}:
        return failure("mask_style must be one of: last4, full, email.", exit_code=EXIT_VALIDATION_ERROR)

    try:
        input_files = _collect_input_files(inputs, recursive=recursive, allow_empty_match=allow_empty_match, max_files=max_files)
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR, error=str(exc))
    if not input_files:
        if allow_empty_match:
            return success("No files matched; nothing to do.", outputs=[])
        return failure("No valid input files found.", exit_code=EXIT_VALIDATION_ERROR)

    auth_path = _init_auth_store(auth_directory)
    try:
        password = _resolve_password(username, password_env_var)
        _authenticate_user(auth_path, username, password)
        key_id, aesgcm = _get_active_key(auth_path, username)
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR, error=str(exc))

    output_files: list[str] = []
    run_report_rows: list[dict[str, str]] = []
    error_messages: list[str] = []

    for input_file in input_files:
        try:
            frame = _read_df(input_file, delimiter=delimiter)
            result_df = _apply_action(
                frame,
                action=action,
                sensitive_cols=sensitive_cols,
                mask_style=mask_style,
                mask_char=mask_char,
                username=username,
                auth_path=auth_path,
                key_id=key_id,
                aesgcm=aesgcm,
            )
            base_out_dir = output_dir or input_file.parent.resolve()
            base_out_dir.mkdir(parents=True, exist_ok=True)
            out_path = base_out_dir / f"{output_name}_{input_file.stem}_{action}.csv"
            if add_timestamp:
                ts = add_timestamp_to_path(out_path)
                if not ts.success:
                    raise ValueError(ts.error or ts.message)
                out_path = Path(str(ts.data["path"]))
            result_df.to_csv(out_path, index=False, sep=delimiter)
            output_files.append(str(out_path))
            run_report_rows.append(
                {
                    "input_file": str(input_file),
                    "output_file": str(out_path),
                    "action": action,
                    "username": username,
                    "key_id": key_id,
                    "status": "ok",
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"{input_file}: {exc}"
            error_messages.append(msg)
            run_report_rows.append(
                {
                    "input_file": str(input_file),
                    "output_file": "",
                    "action": action,
                    "username": username,
                    "key_id": key_id,
                    "status": "error",
                    "error": str(exc),
                }
            )

    report_dir = output_dir or input_files[0].parent.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{output_name}_run_report.csv"
    if add_timestamp:
        ts = add_timestamp_to_path(report_path)
        if ts.success:
            report_path = Path(str(ts.data["path"]))
    pd.DataFrame(run_report_rows).to_csv(report_path, index=False)

    duration = perf_counter() - t0
    artifacts = [{"role": "masked_output", "path": p, "format": "csv"} for p in output_files]
    artifacts.extend(
        [
            {"role": "run_report", "path": str(report_path), "format": "csv"},
            {"role": "auth_vault", "path": str(auth_path), "format": "csv"},
        ]
    )

    if error_messages and not output_files:
        return failure(
            "Mask/encrypt/decrypt failed for all files.",
            exit_code=EXIT_ERROR,
            error="; ".join(error_messages),
            artifacts=artifacts,
        )

    msg = f"Processed {len(output_files)}/{len(input_files)} file(s); report -> {report_path} ({format_duration(duration)})"
    if error_messages:
        msg += f"; errors -> {len(error_messages)}"
    return success(
        msg,
        action=action,
        username=username,
        key_id=key_id,
        outputs=output_files,
        run_report=str(report_path),
        auth_csv=str(auth_path),
        duration_sec=round(duration, 4),
        duration_human=format_duration(duration),
        artifacts=artifacts,
        warnings=error_messages or None,
    )
