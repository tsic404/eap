"""Slow-hash storage and verification for local account passwords (F8).

A local account's password is never persisted, logged or returned in a
reversible form — only this encoding is. ``hashlib.scrypt`` (a memory-hard KDF
from the standard library) does the work, and the parameters travel with the
hash, so verification accepts encodings written with older parameters without a
migration: ``scrypt$<n>$<r>$<p>$<salt-b64>$<hash-b64>``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

# Cost parameters (OWASP's floor for scrypt is n=2**14, r=8, p=1).
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32
_SCHEME = "scrypt"
_MAX_PARAM = {"n": 2**20, "r": 32, "p": 16}


def hash_password(password: str) -> str:
    """Return the storable encoding of ``password``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, n=_N, r=_R, p=_P)
    return f"{_SCHEME}${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Whether ``password`` matches a stored encoding, in constant time.

    An encoding this module did not write (unknown scheme, malformed parts,
    out-of-range parameters) verifies nothing instead of raising, so a corrupt
    row can only ever reject a login.
    """
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return False
    try:
        n, r, p = (int(part) for part in parts[1:4])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return False
    if not salt or not expected:
        return False
    if n > _MAX_PARAM["n"] or r > _MAX_PARAM["r"] or p > _MAX_PARAM["p"]:
        return False
    try:
        digest = _derive(password, salt, n=n, r=r, p=p, key_bytes=len(expected))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def _derive(
    password: str,
    salt: bytes,
    *,
    n: int,
    r: int,
    p: int,
    key_bytes: int = _KEY_BYTES,
) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=key_bytes,
        maxmem=128 * n * r * 2,
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")
