from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/"examples"/"encryption"

def _load(name:str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_seed_round_trips_binary_sizes() -> None:
    candidate = _load("encryption_seed", EXAMPLE / "initial.py")
    key = b"test-key-with-enough-variation"
    for size in (0, 1, 15, 16, 17, 255, 256, 4097):
        data = bytes((index * 37) & 0xFF for index in range(size))
        encrypted = candidate.encrypt(data, 0.8, key)
        assert encrypted != data
        assert candidate.decrypt(encrypted, 0.8, key) == data


def test_rvaluator_accepts_binary_fole_sized_input() -> None:
    evaluator = _load("encryption_evaluator", EXAMPLE / "evaluate.py")
    data = b"%PDF-1.7\n" + bytes(range(256)) * 20
    metrics, correct, error, ciphertext = evaluator.evaluate_candidate(str(EXAMPLE/"initial.py"), data, 0.8, b"evaluation-key")

    assert correct, error
    assert metrics["public"]["correctness"] == 1.0
    assert 0.0 <= metrics["public"]["avalance_score"] <= 1.0
    assert 0.0 <= metrics["public"]["key_avalanche_score"] <= 1.0
    assert 0.0 <= metrics["public"]["performance_score"] <= 1.0
    assert ciphertext != data


def test_fixed_nonce_hook_is_reproducible_and_framed() -> None:
    candidate = _load("encryption_seed_fixed_nonce", EXAMPLE / "initial.py")
    data = b"binary\x00payload"
    key =  b"test-key"
    nonce = b"12345678"
    first = candidate._encrypt_with_nonce(data, 0.7, key, nonce) 
    second = candidate._encrypt_with_nonce(data, 0.7, key, nonce)

    assert first == second
    assert first.startswith(nonce)
    assert candidate.decrypt(first, 0.7, key) == data


def test_empty_input_is_not_replaced_by_default() -> None:
    evaluator = _load("encryption_evaluator_empty", EXAMPLE/"evaluate.py")
    data, _, _ = evaluator._read_configuration(b"", 0.5, b"k")

    assert data == b""
