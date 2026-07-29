from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import time
from pathlib import Path
from types import ModuleType

DEAFULT_DATA = b"Test shinka evolve reversable byte transformation."
DEAFULT_KEY = b"local-demo-key-#123%45#67%89!!"
CONFIG_ENV = "SHINKA_ENCRYPTION_CONFIG"
CONFIG_PATH_ENV = "SHINKA_ENCRYPTION_CONFIG_PATH"
MAX_OUTPUT_FACTOR = 8
BENCHMARK_REPEATS = 9
GUESSES_PER_SECOND = 1_000_000_000.0
MAX_CRACK_SECOND = (2.0**256) / (2.0 * GUESSES_PER_SECOND)

# Candidates should invent the transform, not wrap an existing crypto primitive.
BLOCKED_SOURCE_SNIPPETS = (
    "hashlib",
    "cryptography",
    "crypto.",
    "pycryptodome",
    "nacl",
    "fernet",
    "secrets"
)

def _load_program(program_path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("encryption_candidate", program_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not lead candidate: {program_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def _bits(data: bytes) -> str:

    return "".join(f"{value:08b}" for value in data)


def _flip_bit(data: bytes, bit_index: int) -> bytes:
    changed = bytearray(data)
    changed[bit_index//8] ^= 1 << (bit_index%8)

    return bytes(changed)


def _changed_bit_ratio(first: bytes, second: bytes) -> bytes:
    width = max(len(first), len(second))
    if width == 0:
        return 0.0
    first = first.ljust(width, b"\0")
    second = second.ljust(width, b"\0")
    changed = sum((a ^ b).bit_count() for a, b in zip(first, second))

    return changed/(width* 8.0)


def _timed_call(function, *args) -> tuple[bytes, float]:
    samples: list[float] = []
    output = b""

    for _ in range(BENCHMARK_REPEATS):
        started = time.perf_counter_ns()
        output = function(*args)
        samples.append((time.process_time_ns() - started) / 1_000_000_000.0)

    return output, max(statistics.median(samples), 1e-9)

def _read_configuration(cli_data: bytes | None, importance: float | None, key: bytes | None):
    config: dict = {}
    if os.environ.get(CONFIG_PATH_ENV):
        config_path = Path(os.environ[CONFIG_PATH_ENV])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        configured_data = (config_path.parent / config["data_file"]).read_bytes()
    elif os.environ.get(CONFIG_ENV):
        config = json.loads(os.environ[CONFIG_ENV])
        configured_data = bytes.fromhex(config.get("data_hex",""))
    else:
        configured_data = b""

    data = cli_data if cli_data is not None else configured_data
    selected_key = key if key is not None else bytes.fromhex(config.get("key_hex",""))
    selected_importance = importance if importance is not None else float(config.get("importance", 0.7))

    return data or DEAFULT_DATA, selected_importance, selected_key or DEAFULT_KEY

def _failure(error: str) -> tuple[dict, bool, str, bytes]:
    metrics = {
        "combined_score": 0.0,
        "public": {
            "final_score": 0.0,
            "corectness": 0.0,
            "avalanche_score": 0.0,
            "security_score": 0.0,
            "performance_score": 0.0,
        },
        "private": {"error": error}
    }

    return metrics, False, error, b""


def evaluate_candidate(program_path: str, data: bytes, importance: float, key: bytes) -> tuple[dict, bool, str, bytes]:
    if not 0.0 <= importance <= 1.0:
        return _failure("Importance must be in the range [0.0, 1.0]")
    source = Path(program_path).read_text(encoding="utf-8")
    violation = next((item for item in BLOCKED_SOURCE_SNIPPETS if item in source.lower()), None)
    if violation:
        return _failure(f"forbidden cryptographic helper: {violation}")

    try: 
        module = _load_program(program_path)
        encrypt = getattr(module, "encrypt", None)
        decrypt = getattr(module, "decrypt", None)
        if not callable(encrypt) or not callable(decrypt):
            return _failure("Candidate must define callable encrypt() and decrypt()")
        ciphertext, encrypt_seconds = _timed_call(encrypt, data, importance, key)
        plaintext, decrypt_seconds = _timed_call(decrypt, ciphertext, importance, key)
        if not isinstance(ciphertext, bytes) or not isinstance(plaintext, bytes):
            return _failure("encrypt() and decrypt() must return bytes")
        if len(ciphertext) > max(1, len(data)) * MAX_OUTPUT_FACTOR:
            return _failure("ciphertext expansion exceeds the allowed limit")

        corectness = float(plaintext == data)

        avalanche_samples: list[float] = []
        sample_bits = min(len(data)*8, 64)
        for bit_idx in range(sample_bits):
            altered = encrypt(_flip_bit(data, bit_idx), importance, key)
            if not isinstance(altered, bytes):
                return _failure("encrypt() must return bytes")
            avalanche_samples.append(_changed_bit_ratio(ciphertext, altered))
            avalanche = statistics.fmean(avalanche_samples) if avalanche_samples else 0.0
            avalanche_score = max(0.0, 1.0 - 2.0 * abs(avalanche - 0.5))

            key_bits = min(len(key) * 8, 256)
            total_combinations = 2.0 ** key_bits
            crack_seconds = total_combinations / (2*GUESSES_PER_SECOND)
            performance_tradeoff = crack_seconds / (encrypt_seconds + decrypt_seconds)
            max_tradeoff = MAX_CRACK_SECOND / 1e-9
            performance_score = min(1.0, math.log1p(performance_tradeoff)/math.log1p(max_tradeoff))
            security_score = 0.4*avalanche_score + 0.6 * (math.log1p(crack_seconds) / math.log1p(MAX_CRACK_SECOND))
            final_score = corectness * (importance * security_score + (1.0 - importance) *performance_score)

            metrics = {
                "combined_score": final_score,
                "public": {
                    "final_score": final_score,
                    "corectness": corectness,
                    "avalanche": avalanche,
                    "avalanche_score": avalanche_score,
                    "performance_score": performance_score,
                    "encrypt_seconds": encrypt_seconds,
                    "decrypt_seconds": decrypt_seconds
                },
                "private":{
                    "crack_seconds": crack_seconds,
                    "performance_tradeoff": performance_tradeoff,
                    "key_bits": key_bits,
                    "sample_bits" :sample_bits
                }
            }
            return metrics, bool(corectness), "" if corectness else "round trip failed", ciphertext

    except Exception as exc:
        return _failure(f"{type(exc).__name__}: {exc}")


def main(program_path: str, results_dir: str, data: bytes | None = None, importance: float | None = None, key: bytes | None = None) -> None:
    data, importance, key = _read_configuration(data, importance, key)
    metrics, correct, error, ciphertext = evaluate_candidate(program_path, data, importance, key)
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (output / "correct.json").write_text(json.dumps({"correct": correct, "error":error}, indent=2), encoding='utf-8')
    (output / "ciphertext.bin").write_bytes(ciphertext)
    (output / "ciphertext_bits.txt").write_text(_bits(ciphertext), encoding='ascii')

def _input_bytes(args: argparse.Namespace) -> bytes | None:
    if args.input_file:
        return Path(args.input_file).read_bytes()
    return args.text.encode(args.encoding) if args.text is not None else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program_path", default="initial.py")
    parser.add_argument("--results_dir", default="results/encryption")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--text", help="literal imput text")
    inputs.add_argument("--input-file", "--input_file", dest="input_file", help="path to any binary or text file")
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--importance", type=float)
    parser.add_argument("--key", help="UTF-8 key text")
    parser.add_argument("--key-hex", "--key-hex", dest="key_hex", help= "Key encoded as hexadecimal")
    parsed = parser.parse_args()
    parsed_key = bytes.fromhex(parsed.key_hex) if parsed.key_hex else parsed.key.encode("utf-8") if parsed.key else None

    main(parsed.program_path, parsed.results_dir, _input_bytes(parsed), parsed.importance, parsed_key)