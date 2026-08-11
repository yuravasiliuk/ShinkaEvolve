from __future__ import annotations




import argparse
import importlib.util
import json
import math
import os
import ast
import statistics
import time
from pathlib import Path
from types import ModuleType




DEFAULT_DATA = b"Test shinka evolve reversible byte transformation."
DEFAULT_KEY = b"local-demo-key-#123%45#67%89!!"
CONFIG_ENV = "SHINKA_ENCRYPTION_CONFIG"
CONFIG_PATH_ENV = "SHINKA_ENCRYPTION_CONFIG_PATH"
MAX_OUTPUT_FACTOR = 8
BENCHMARK_REPEATS = 9
FIXED_NONCE = b"Shinka01"
AVALANCHE_BITS = 32
GUESSES_PER_SECOND = 1_000_000_000.0
MAX_CRACK_SECOND = (2.0**256) / (2.0 * GUESSES_PER_SECOND)




BLOCKED_SOURCE_SNIPPETS = (
   "hashlib",
   "cryptography",
   "crypto.",
   "pycryptodome",
   "nacl",
   "fernet"
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








def _timed_call(function, *args, repeats: int = 1) -> tuple[bytes, float]:
   samples: list[float] = []
   output = b""




   for _ in range(repeats):
       started = time.perf_counter_ns()
       output = function(*args)
       samples.append((time.perf_counter_ns() - started) / 1_000_000_000.0)




   return output, max(statistics.median(samples), 1e-9)




def _read_configuration(cli_data: bytes | None, importance: float | None, key: bytes | None) -> tuple[bytes, float, bytes]:
   config: dict = {}
   configured_data: bytes | None = None
   if os.environ.get(CONFIG_PATH_ENV):
       config_path = Path(os.environ[CONFIG_PATH_ENV])
       config = json.loads(config_path.read_text(encoding="utf-8"))
       configured_data = (config_path.parent / config["data_file"]).read_bytes()
   elif os.environ.get(CONFIG_ENV):
       config = json.loads(os.environ[CONFIG_ENV])
       configured_data = bytes.fromhex(config.get("data_hex",""))




   data = cli_data if cli_data is not None else configured_data
   selected_key = key if key is not None else bytes.fromhex(config.get("key_hex",""))
   selected_importance = importance if importance is not None else float(config.get("importance", 0.7))




   return DEFAULT_DATA if data is None else data, selected_importance, selected_key or DEFAULT_KEY




def _failure(error: str) -> tuple[dict, bool, str, bytes]:
   metrics = {
       "combined_score": 0.0,
       "public": {
           "final_score": 0.0,
           "correctness": 0.0,
           "avalanche_score": 0.0,
           "security_score": 0.0,
           "performance_score": 0.0,
       },
       "private": {"error": error}
   }




   return metrics, False, error, b""




def _probe_cases(data: bytes) -> list[tuple[str, bytes]]:
   patterns = [
       ("empty", b""),
       ("one-byte", b"\x00"),
       ("short-binary", bytes(range(17))),
       ("block-boundary", bytes(range(32))),
       ("all-byte-values", bytes(range(256))),
       ("zero-heavy", b"\0" * 255)
   ]
   if data:
       patterns.append(("input-prefix", data[:4096]))




   return patterns




def _correctness_gate(encrypt, decrypt, data: bytes, importance: float, key: bytes) -> tuple[bool, str]:
   for name, probe in _probe_cases(data):
       encrypted = encrypt(probe, importance, key)
       if not isinstance(encrypted, bytes):
           return False, f"encrypt returned non-bytes for {name}"
       if len(encrypted) > max(32, max(1, len(probe)) * MAX_OUTPUT_FACTOR):
           return False, f"ciphered expansion too large for {name}"
       recovered = decrypt(encrypted, importance, key)
       if not isinstance(recovered, bytes) or recovered != probe:
           return False, f"round trip failed for {name}"




   return True, ""




def _complexity_score(source: str) -> tuple[float, int]:
   start_marker = "# EVOLVE-BLOCK-START"
   end_marker = "# EVOLVE-BLOCK-END"




   if start_marker in source and end_marker in source:
       source = source.split(start_marker, 1)[1].split(end_marker, 1)[0]
   tree = ast.parse(source)
   nodes = sum(1 for _ in ast.walk(tree))




   return 1.0 / (1.0 + max(0, nodes - 100) / 300.0), nodes




def _forbidden_dependency(source: str) -> str | None:
   tree = ast.parse(source)
   for node in ast.walk(tree):
       names: list[str] = []
       if isinstance(node, ast.Import):
           names = [alias.name for alias in node.names]
       elif isinstance(node, ast.ImportFrom) and node.module:
           names = [node.module]
       for name in names:
           root = name.lower().split(".", 1)[0]
           if root in BLOCKED_SOURCE_SNIPPETS:
               return name
   return None




def _avalanche(encrypt, data: bytes, importance: float, key: bytes) -> tuple[float, float]:
   probe = (data[:16] if data else b"").ljust(16, b"\xA5")
   base = encrypt(probe, importance, key, FIXED_NONCE)
   if not isinstance(base, bytes) or not base.startswith(FIXED_NONCE):
       raise ValueError("_encrypt_with_nonce must prefix ciphertext with the supplied nonce")
   base_payload = base[len(FIXED_NONCE):]
   ratios = []
   for bit_index in range(min(len(probe) * 8, AVALANCHE_BITS)):
       changed = encrypt(_flip_bit(probe, bit_index), importance, key, FIXED_NONCE)
       if not isinstance(changed, bytes) or not changed.startswith(FIXED_NONCE):
           raise ValueError("_encrypt_with_nonce must prefix ciphertext with the supplied nonce")
       ratios.append(_changed_bit_ratio(base_payload, changed[len(FIXED_NONCE):]))
   avalanche = statistics.fmean(ratios) if ratios else 0.0




   return avalanche, max(0, 1.0 - 2.0 * abs(avalanche - 0.5))












def evaluate_candidate(program_path: str, data: bytes, importance: float, key: bytes) -> tuple[dict, bool, str, bytes]:
   if not 0.0 <= importance <= 1.0:
       return _failure("Importance must be in the range [0.0, 1.0]")
   source = Path(program_path).read_text(encoding="utf-8")
   violation = _forbidden_dependency(source)
   if violation:
       return _failure(f"forbidden cryptographic helper: {violation}")




   try:
       module = _load_program(program_path)
       encrypt = getattr(module, "encrypt", None)
       decrypt = getattr(module, "decrypt", None)
       encrypt_fixed = getattr(module, "_encrypt_with_nonce", None)
       if not all(callable(item) for item in (encrypt, decrypt, encrypt_fixed)):
           return _failure("Candidate must define callable encrypt(), decrypt() and _encrypt_with_nonce()")




       gate_ok, gate_err = _correctness_gate(encrypt, decrypt, data, importance, key)
       if not gate_ok:
           return _failure(gate_err)




       repeats = 3 if len(data) <= 65536 else 1
       ciphertext, encrypt_seconds = _timed_call(encrypt, data, importance, key, repeats=repeats)
       plaintext, decrypt_seconds = _timed_call(decrypt, ciphertext, importance, key, repeats=repeats)




       if not isinstance(ciphertext, bytes) or not isinstance(plaintext, bytes):
           return _failure("encrypt() and decrypt() must return bytes")
       if len(ciphertext) > max(32, max(1, len(data)) * MAX_OUTPUT_FACTOR):
           return _failure("ciphertext expansion exceeds the allowed limit")
       if plaintext != data:
           return _failure(f"round trip failed for full input (length={len(data)})")








       avalanche, avalanche_score = _avalanche(encrypt_fixed, data, importance, key)
       changed_key = _flip_bit(key, 0)
       fixed_plain = (data[:16] if data else b"").ljust(16, b"\xA5")
       base = encrypt_fixed(fixed_plain, importance, key, FIXED_NONCE)
       key_changed = encrypt_fixed(fixed_plain, importance, changed_key, FIXED_NONCE)
       if not base.startswith(FIXED_NONCE) or not key_changed.startswith(FIXED_NONCE):
           return _failure("_encrypt_with_nonce must prefix ciphertext with the supplied nonce")
       key_avalanche = _changed_bit_ratio(
           base[len(FIXED_NONCE):], key_changed[len(FIXED_NONCE):]
       )
       key_avalanche_score = max(0, 1.0 - 2.0 * abs(key_avalanche - 0.5))




       total_seconds = encrypt_seconds + decrypt_seconds
       throughput = len(data) / max(total_seconds, 1e-9)
       performance_score = min(1.0, math.log1p(throughput) / math.log1p(8*1024*1024))
       complexity_score, ast_nodes = _complexity_score(source)
       diffusion_score = 0.7 * avalanche_score + 0.3 * key_avalanche_score
       quality_score = importance * diffusion_score + (1.0 - importance) * performance_score




       final_score = quality_score * (0.85 + 0.15 * complexity_score)




       metrics = {
           "combined_score": final_score,
           "public": {
               "final_score": final_score,
               "correctness": 1.0,
               "avalanche": avalanche,
               "avalanche_score": avalanche_score,
               "key_avalanche": key_avalanche,
               "key_avalanche_score": key_avalanche_score,
               "performance_score": performance_score,
               "complexity_score": complexity_score,
               "encrypt_seconds": encrypt_seconds,
               "decrypt_seconds": decrypt_seconds,
               "throughput_bytes_per_second": throughput
           },
           "private":{
               "sample_bits": AVALANCHE_BITS,
               "ast_nodes": ast_nodes,
               "input_bytes": len(data)
           }
       }
       return metrics, True, "", ciphertext




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
   parser.add_argument("--key-hex", dest="key_hex", help="key encoded as hexadecimal")
   parsed = parser.parse_args()
   parsed_key = bytes.fromhex(parsed.key_hex) if parsed.key_hex else parsed.key.encode("utf-8") if parsed.key else None




   main(parsed.program_path, parsed.results_dir, _input_bytes(parsed), parsed.importance, parsed_key)









