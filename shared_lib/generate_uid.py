from __future__ import annotations

# NOTE: Generates nanoid or UUID IDs.

import secrets
import time
import uuid

from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success

DEFAULT_NANOID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz-"
DEFAULT_NANOID_LENGTH = 21


def generate_nanoid(length: int = DEFAULT_NANOID_LENGTH, *, alphabet: str = DEFAULT_NANOID_ALPHABET) -> SharedResult:
    """Generate a short random nanoid."""
    if length < 1:
        return failure("ID length must be greater than zero", exit_code=EXIT_VALIDATION_ERROR, length=length)
    if not alphabet:
        return failure("ID alphabet is required", exit_code=EXIT_VALIDATION_ERROR)

    backend = "secrets"
    try:
        from nanoid import generate

        uid = generate(alphabet, length)
        backend = "nanoid"
    except ImportError:
        uid = "".join(secrets.choice(alphabet) for _ in range(length))

    return success("ID created", uid=uid, kind="nanoid", length=len(uid), backend=backend)


def _uuid7_fallback() -> uuid.UUID:
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def _uuid7_from_library() -> tuple[uuid.UUID, str]:
    if hasattr(uuid, "uuid7"):
        return uuid.uuid7(), "uuid"  # type: ignore[attr-defined]

    try:
        from uuid6 import uuid7

        return uuid7(), "uuid6"
    except ImportError:
        return _uuid7_fallback(), "python"


def generate_uuid() -> SharedResult:
    """Generate a UUID (version 7). ``uid`` is the full string; no ``full_uid`` duplicate field."""
    value, backend = _uuid7_from_library()
    uid = str(value)

    return success(
        "ID created",
        uid=uid,
        kind="uuid",
        version=7,
        length=len(uid),
        backend=backend,
    )


def generate_uid(kind: str = "nanoid", *, length: int | None = None) -> SharedResult:
    """Generate a nanoid or UUID."""
    normalized = str(kind or "").strip().lower().replace("-", "").replace("_", "")
    if normalized in {"nanoid", "nano"}:
        return generate_nanoid(DEFAULT_NANOID_LENGTH if length is None else length)
    if normalized == "uuid":
        if length is not None:
            return failure(
                "UUID has a fixed standard length; length is only supported for nanoid.",
                exit_code=EXIT_VALIDATION_ERROR,
                kind=kind,
                length=length,
            )
        return generate_uuid()
    return failure(
        "Unknown ID type. Use nanoid or uuid.",
        exit_code=EXIT_VALIDATION_ERROR,
        kind=kind,
        supported=["nanoid", "uuid"],
    )
