from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "encryption"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_candidate(tmp_path: Path, source: str, name: str = "cand.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def test_seed_round_trips_binary_sizes() -> None:
    candidate = _load("encryption_seed", EXAMPLE / "initial.py")
    key = b"test-key-with-enough-variation"
    for size in (0, 1, 15, 16, 17, 255, 256, 4097):
        data = bytes((index * 37) & 0xFF for index in range(size))
        encrypted = candidate.encrypt(data, 0.8, key)
        if size > 0:
            assert encrypted != data
        assert candidate.decrypt(encrypted, 0.8, key) == data


def test_seed_nonce_changes_payload() -> None:
    """Nonce must mix into the payload even when key is longer than data."""
    candidate = _load("encryption_seed_nonce_payload", EXAMPLE / "initial.py")
    data = b"0123456789abcdef"  # 16 bytes
    key = b"local-demo-key-#123%45#67%89!!"  # longer than data
    a = candidate._encrypt_with_nonce(data, 0.7, key, b"Shinka01")
    b = candidate._encrypt_with_nonce(data, 0.7, key, b"Shinka02")
    assert a.startswith(b"Shinka01")
    assert b.startswith(b"Shinka02")
    assert a[8:] != b[8:]


def test_evaluator_accepts_binary_file_sized_input() -> None:
    evaluator = _load("encryption_evaluator", EXAMPLE / "evaluate.py")
    data = b"%PDF-1.7\n" + bytes(range(256)) * 20
    metrics, correct, error, ciphertext = evaluator.evaluate_candidate(
        str(EXAMPLE / "initial.py"), data, 0.8, b"evaluation-key"
    )

    assert correct, error
    public = metrics["public"]
    assert public["correctness"] == 1.0
    assert 0.0 <= public["avalanche_score"] <= 1.0
    assert 0.0 <= public["key_avalanche_score"] <= 1.0
    assert 0.0 <= public["performance_score"] <= 1.0
    assert metrics["combined_score"] > 0.0
    assert ciphertext != data
    # Schema parity with failure path defaults.
    assert set(public) == set(evaluator.PUBLIC_METRIC_DEFAULTS)


def test_fixed_nonce_hook_is_reproducible_and_framed() -> None:
    candidate = _load("encryption_seed_fixed_nonce", EXAMPLE / "initial.py")
    data = b"binary\x00payload"
    key = b"test-key"
    nonce = b"12345678"
    first = candidate._encrypt_with_nonce(data, 0.7, key, nonce)
    second = candidate._encrypt_with_nonce(data, 0.7, key, nonce)

    assert first == second
    assert first.startswith(nonce)
    assert candidate.decrypt(first, 0.7, key) == data


def test_empty_input_is_not_replaced_by_default() -> None:
    evaluator = _load("encryption_evaluator_empty", EXAMPLE / "evaluate.py")
    data, _, _ = evaluator._read_configuration(b"", 0.5, b"k")
    assert data == b""


def test_identity_transform_is_incorrect(tmp_path: Path) -> None:
    evaluator = _load("encryption_evaluator_identity", EXAMPLE / "evaluate.py")
    path = _write_candidate(
        tmp_path,
        """
        import os
        NONCE_SIZE = 8
        def _encrypt_with_nonce(data, importance, key, nonce):
            return nonce + data
        def encrypt(data, importance, key):
            return _encrypt_with_nonce(data, importance, key, os.urandom(NONCE_SIZE))
        def decrypt(data, importance, key):
            return data[NONCE_SIZE:]
        """,
    )
    metrics, correct, error, _ = evaluator.evaluate_candidate(
        str(path), b"hello world payload", 0.7, b"key-material"
    )
    assert not correct
    assert metrics["public"]["correctness"] == 0.0
    assert metrics["combined_score"] == 0.0
    assert "identity" in error.lower() or "nonce" in error.lower() or "wrong key" in error.lower()
    assert set(metrics["public"]) == set(evaluator.PUBLIC_METRIC_DEFAULTS)


def test_crypto_import_is_blocked(tmp_path: Path) -> None:
    evaluator = _load("encryption_evaluator_crypto", EXAMPLE / "evaluate.py")
    path = _write_candidate(
        tmp_path,
        """
        from Crypto.Cipher import AES  # noqa: F401
        import os
        NONCE_SIZE = 8
        def _encrypt_with_nonce(data, importance, key, nonce):
            return nonce + bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        def encrypt(data, importance, key):
            return _encrypt_with_nonce(data, importance, key, os.urandom(NONCE_SIZE))
        def decrypt(data, importance, key):
            nonce, ct = data[:NONCE_SIZE], data[NONCE_SIZE:]
            return bytes(b ^ key[i % len(key)] for i, b in enumerate(ct))
        """,
    )
    metrics, correct, error, _ = evaluator.evaluate_candidate(
        str(path), b"payload-bytes-here", 0.7, b"key-material"
    )
    assert not correct
    assert "forbidden" in error.lower()
    assert metrics["combined_score"] == 0.0


def test_from_import_hashlib_is_blocked(tmp_path: Path) -> None:
    evaluator = _load("encryption_evaluator_hashlib", EXAMPLE / "evaluate.py")
    path = _write_candidate(
        tmp_path,
        """
        from math import hashlib  # sneaky alias form
        """,
    )
    # Even without encrypt APIs, forbidden import should fire first if parse works.
    # math doesn't export hashlib — AST still sees the name.
    metrics, correct, error, _ = evaluator.evaluate_candidate(
        str(path), b"payload", 0.7, b"key"
    )
    assert not correct
    assert "forbidden" in error.lower() or "must define" in error.lower() or "Error" in error


def test_wrong_key_exception_counts_as_dependence(tmp_path: Path) -> None:
    evaluator = _load("encryption_evaluator_wrong_key", EXAMPLE / "evaluate.py")
    path = _write_candidate(
        tmp_path,
        """
        import os
        NONCE_SIZE = 8
        def _mix(data, key, nonce):
            stream = nonce + key
            return bytes(b ^ stream[i % len(stream)] for i, b in enumerate(data))
        def _encrypt_with_nonce(data, importance, key, nonce):
            # Embed a 1-byte key tag so decrypt can raise on mismatch.
            tag = bytes([key[0] if key else 0])
            return nonce + tag + _mix(data, key, nonce)
        def encrypt(data, importance, key):
            return _encrypt_with_nonce(data, importance, key, os.urandom(NONCE_SIZE))
        def decrypt(data, importance, key):
            nonce = data[:NONCE_SIZE]
            tag = data[NONCE_SIZE:NONCE_SIZE + 1]
            ct = data[NONCE_SIZE + 1:]
            if not key or tag != bytes([key[0]]):
                raise ValueError("bad key")
            return _mix(ct, key, nonce)
        """,
    )
    metrics, correct, error, _ = evaluator.evaluate_candidate(
        str(path), b"payload-for-dependence-check-0123456789", 0.7, b"key-material-xx"
    )
    assert correct, error
    assert metrics["public"]["correctness"] == 1.0


def test_malformed_source_is_soft_failure(tmp_path: Path) -> None:
    evaluator = _load("encryption_evaluator_syntax", EXAMPLE / "evaluate.py")
    path = _write_candidate(tmp_path, "def encrypt( def broken")
    metrics, correct, error, _ = evaluator.evaluate_candidate(
        str(path), b"payload", 0.7, b"key"
    )
    assert not correct
    assert metrics["combined_score"] == 0.0
    assert metrics["public"]["correctness"] == 0.0
    assert set(metrics["public"]) == set(evaluator.PUBLIC_METRIC_DEFAULTS)
    assert error  # some diagnostic
