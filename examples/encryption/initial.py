"""
Shinka can modify the block below. The deliberately simple seed is an identity transform. 
The evolutionary search is expected to replace it with a reversible construction based on arithmetic, 
bit operations, rotations and lookup tables or similar.

Prefer nonlinear and well-mixed transformations. Encryption should be non-deterministic and use a nonce. 
Use at least 8 rounds with both forward and backward passes and different round constants. 
Maximize diffusion and avalanche properties so that small changes in the plaintext, 
key or nonce significantly affect the ciphertext. Internal state should continuously evolve during encryption.

Avoid linear-only transformations, simple XOR stream ciphers, deterministic byte substitutions, 
fixed repeating patterns and independent per-byte encryption schemes. Security and diffusion are preferred over execution speed.
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
    key_stream = key + nonce
    ciphertext = _xor_transform(data, key_stream)

    return nonce + ciphertext


def encrypt(data: bytes, importance: float, key: bytes) -> bytes:
    """
    Encrypt data using key and with protection strength adapted
    to the importance level.
    """

    if not 0.0 <= importance <= 1.0:
        raise ValueError("importance must be in the range [0.0, 1.0]")

    if not key:
        raise ValueError("key must not be empty")
    
    nonce = os.urandom(NONCE_SIZE)

    return _encrypt_with_nonce(data, importance, key, nonce)


def decrypt(data: bytes, importance: float, key: bytes) -> bytes:
    """
    Reverse `encrypt` function for the same importance and key.
    """

    if not 0.0 <= importance <= 1.0:
        raise ValueError("importance must be in the range [0.0, 1.0]")

    if not key:
        raise ValueError("key must not be empty")

    if len(data) < NONCE_SIZE:
        raise ValueError("invalid ciphertext")

    nonce = data[:NONCE_SIZE]
    ciphertext = data[NONCE_SIZE:]

    key_stream = key + nonce

    return _xor_transform(ciphertext, key_stream)


# EVOLVE-BLOCK-END