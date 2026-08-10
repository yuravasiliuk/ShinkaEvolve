# Encryption Evolution

This example uses ShinkaEvolve to discover reversible byte-encryption
algorithms. It accepts either text or an arbitrary binary file and evolves
the implementation of `encrypt()` / `decrypt()` toward stronger diffusion
while requiring exact recovery of the original data.

> [WARNING]
> This is an experimental optimization task, not audited production
> cryptography. Do not use generated algorithms to protect sensitive data.

## How it works

The seed candidate is intentionally weak: an 8-byte random nonce is mixed
into a repeating XOR stream as `nonce + key` (nonce first, so short messages
still depend on the nonce payload). The nonce is stored at the front of the
ciphertext so `decrypt()` can rebuild the same stream.

The complete implementation between `EVOLVE-BLOCK-START` and
`EVOLVE-BLOCK-END` may be rewritten during evolution. The task prompt pushes
the search toward **new algorithmic families**, not micro-edits of the same
S-box/ARX parent and not drop-in ports of AES / ChaCha / Speck / etc.
Candidates must retain the public interfaces:

```python
encrypt(data: bytes, importance: float, key: bytes) -> bytes
decrypt(data: bytes, importance: float, key: bytes) -> bytes
_encrypt_with_nonce(data, importance, key, nonce) -> bytes
```

`_encrypt_with_nonce` must prefix the ciphertext with the supplied nonce so
avalanche measurements stay reproducible. `os.urandom` is allowed only for
fresh nonces on the public `encrypt()` path.

### Correctness metric (hard gate)

`public.correctness` is **1.0 only when every check below passes**, and it
multiplies into `combined_score` (so "correctness" is not a decorative label):

1. **Round-trip** on empty, boundary, all-byte-values, dependence, and
   full-input probes.
2. **Non-identity** — non-empty ciphertext must not equal plaintext.
3. **Key dependence** — decrypt with a wrong key must not silently recover
   the plaintext (raising is accepted as dependence).
4. **Nonce dependence** — different nonces must change the ciphertext
   *payload*, not only the framing prefix.
5. **Non-determinism** — two `encrypt()` calls on the same non-empty data
   must differ (fresh nonce).

Static import screening rejects `hashlib`, `cryptography`, `Crypto` /
`Cryptodome`, `nacl`, `fernet`, and similar roots (including
`from somewhere import hashlib`). Dynamic import evasion is out of scope.

### Quality terms (after correctness)

Valid candidates are scored with:

- plaintext and key avalanche quality (fixed evaluation nonce);
- encryption / decryption throughput;
- a soft complexity penalty for an oversized evolve block.

`importance ∈ [0, 1]` weights diffusion vs performance inside the quality
term. Avalanche uses a fixed evaluation nonce so random nonces cannot
inflate the score.

## Parameters

| Parameter | Description |
|----|----|
| `--task-dir` | Task directory. Use `examples/encryption`. |
| `--config-fname` | Task configuration file. Use `shinka.yaml`. |
| `--results_dir` | Directory for the evolution database and generated results. |
| `--num_generations` | Number of evolutionary generations to run. |
| `text` | Literal UTF-8 input. Mutually exclusive with `input-file`. |
| `input-file` | Path to any binary or text file (PDF, PNG, TXT, …). |
| `importance` | Float in `[0.0, 1.0]`. Weights diffusion vs performance and may adapt candidate work factor. |
| `key` | Key supplied as UTF-8 text. |
| `key-hex` | Hex key for arbitrary binary bytes (takes precedence over `key`). |

Input parameters are passed through `job.extra_cmd_args`. That object
replaces the defaults in `shinka.yaml`, so include the input, importance,
and key in the same JSON value. Prefer absolute file paths because evaluator
processes may use a different working directory.

## Usage

Run from the repository root.

### Evolve using text input

```bash
shinka_run \
  --task-dir examples/encryption \
  --config-fname shinka.yaml \
  --results_dir results/encryption_example \
  --num_generations 20 \
  --max-evaluation-jobs 2 \
  --set 'job.extra_cmd_args={"text":"ultra secret message that must be encrypted","importance":0.85,"key":"secret-local-key-#123!345%678"}' \
  --set 'evo.llm_kwargs={"temperatures":[0.65,0.8,0.95],"max_tokens":12288,"reasoning_efforts":["high"]}'
```

### Evolve using a binary / PDF input

```bash
shinka_run \
  --task-dir examples/encryption \
  --config-fname shinka.yaml \
  --results_dir results/encryption_pdf_example \
  --num_generations 20 \
  --max-evaluation-jobs 2 \
  --set 'job.extra_cmd_args={"input-file":".../file.pdf","importance":0.7,"key":"secret-local-key-#123!345%678"}' \
  --set 'evo.llm_kwargs={"temperatures":[0.5,0.75,0.85],"max_tokens":12288,"reasoning_efforts":["high"]}'
```

### Manual single-candidate evaluation

```bash
python examples/encryption/evaluate.py \
  --program_path examples/encryption/initial.py \
  --results_dir results/manual_encryption_eval \
  --text "hello" \
  --importance 0.7 \
  --key "local-demo-key-#123%45#67%89!!"
```

## Evaluation output

| File | Contents |
|----|----|
| `metrics.json` | `combined_score`, `correctness`, avalanche, timing, throughput, complexity (same public keys on success and failure). |
| `correct.json` | Pass/fail flag and a diagnostic message on failure. |
| `ciphertext.bin` | Raw encrypted output from the evaluated input. |
| `ciphertext_bits.txt` | Ciphertext as bits for inspection. |
