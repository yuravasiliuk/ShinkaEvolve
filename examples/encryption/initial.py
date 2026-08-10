"""
Shinka may rewrite the evolve block below.

This seed is intentionally weak: a keyed XOR stream with a random nonce.
Evolution should replace it with a *new* reversible construction — not a
micro-optimized clone of the parent and not a drop-in port of AES, ChaCha20,
Salsa20, Speck, Simon, Blowfish, or a stock textbook Feistel/ARX layout.

When rewriting, invent a different algorithmic family from prior candidates
(e.g. lag-based reciprocal maps, keyed Latin-square layers, sponge-like
absorbing permutations, carry-save reversible networks, custom multi-pass
bit-slice mixes). These are inspiration categories, not templates to copy.

Requirements that the evaluator enforces:
- encrypt / decrypt / _encrypt_with_nonce keep their public signatures
- exact byte recovery on every probe (including empty)
- non-identity ciphertext on non-empty data
- wrong-key decrypt must not silently recover plaintext
- nonce must influence the ciphertext payload (not only the framing prefix)
- public encrypt() must be non-deterministic (fresh nonce via os.urandom only)
- no cryptographic / hashing library imports
"""

from __future__ import annotations

import os


# EVOLVE-BLOCK-START

NONCE_SIZE = 8


def _xor_transform(data: bytes, key_stream: bytes) -> bytes:
    return bytes(
        value ^ key_stream[index % len(key_stream)]
        for index, value in enumerate(data)
    )


def _encrypt_with_nonce(
    data: bytes,
    importance: float,
    key: bytes,
    nonce: bytes,
) -> bytes:
    if not key:
        raise ValueError("key must not be empty")
    # Nonce first so short messages still mix nonce bytes into the payload.
    key_stream = nonce + key
    ciphertext = _xor_transform(data, key_stream)
    return nonce + ciphertext


def encrypt(data: bytes, importance: float, key: bytes) -> bytes:
    """Encrypt data with a fresh nonce; importance may adapt work factor."""
    if not 0.0 <= importance <= 1.0:
        raise ValueError("importance must be in the range [0.0, 1.0]")
    if not key:
        raise ValueError("key must not be empty")
    nonce = os.urandom(NONCE_SIZE)
    return _encrypt_with_nonce(data, importance, key, nonce)


def decrypt(data: bytes, importance: float, key: bytes) -> bytes:
    """Exact inverse of encrypt for the same importance and key."""
    if not 0.0 <= importance <= 1.0:
        raise ValueError("importance must be in the range [0.0, 1.0]")
    if not key:
        raise ValueError("key must not be empty")
    if len(data) < NONCE_SIZE:
        raise ValueError("invalid ciphertext")
    nonce = data[:NONCE_SIZE]
    ciphertext = data[NONCE_SIZE:]
    key_stream = nonce + key
    return _xor_transform(ciphertext, key_stream)


# EVOLVE-BLOCK-END
