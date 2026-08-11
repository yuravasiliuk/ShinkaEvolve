# Encryption Evolution

This example uses Shinka Evolve to discover reversible byte-encryption algorithms. 
It accepts either text or an arbitary binary file and evolves the implementation of `encrypt()` and `decrypt()` 
toward stronger diffusion while presserving exact recovery of the original data.

> [WARNING]
> This is an experimental optimalization task, not audited production cryptography.
> Do not use generated algorithms to protect sensitive data.

## How it works

The initial candidate is deliberately simple. It generates an 8-byte random nonce, appends it to the key to form a repeating byte stream, and XORs that stream with the input. The nonce is stored at the beginning of the ciphertext so `decrypt()` can reconstruct the same stream.

The complete implementation between `EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END` may be changed during evolution. The search is encouraged to replace the seed with a more nonlinear, well-mixed construction based on reversible arithmetic, bit operations, rotations, permutations, round constants, and evolving initial state. Candidates must retain the public interfaces:

```python
encrypt(data: bytes, importance: float, key: bytes) -> bytes
decrypt(data: bytes, importance: float, key: bytes) -> bytes
```

They also must retain `_encrypt_with_nonce(...)`, which lets the evaluator use a fixed nonce for reproducible avalanche measurements. The `importance` value is available to envolved candidates so they can balance protection strength and execution cost. It also controls the diffusion/performance weighting in the final score.

Before reciving a score, every candidate must round-trip empty input, boundary sizes, zero-heavy data, all byte values, a sample of the supplied input and the compute input. Valid candidates are then scored using:

- plaintext and key avalanche quality;
- encyption and decription throughput;
- a soft complexity penalty for an oversized evolve block.

Avalanche measurements use a fixed evaluation nonce, preventing random nonce changes from artifically improving the score.

## Parameters
| Parameter | Description |
|----|----|
| `--task-dir` | Tas directory. Use `examples/encryption`. |
| `--config-fname` | Task configuration file. Use `shinka.yaml`.|
| `--results_dir` | Directory for the evolution database and generated results. |
| `--num_generations` | Number of evolutionary generations to run. |
| `text` | Literal UTF-8 input. Mutually exclusive with `input-file`. |
| `input-file` | Path to any binary or text file, such as PDF, PNG or TXT to be encrypted. |
| `importance` | Float from `0.0` to `1.0`. It controls the diffusion/performance score weighting and may be used by candidates to adapt their work factor. |
| `key` | Key supplied as UTF-8 text. |
| `key-hex` | Alternative hexadecimal hey representation for arbitrary bunary bytes (takes precedence over `key`). |

The input parameters are passed through `job.extra_cmd_args`. This object replaces the deafults in `shinka.yaml`, so include the input, importance and key in the same JSON value. Prefer absolute file paths because evaluator processes may use a different working directory.

## Usage
Run form the repository root. The following examples use Bash.

### Evlove using text input

```Bash
shinka_run `
 --task-dir examples/encryption \
 --config-fname shinka.yaml \
 --results_dir results/encryption_example \
 --num_generations 20 \
 --max-evaluation-jobs 2 \
 --set 'job.extra_cmd_args={"text":"ultra secret message that must be encrypted","importance":0.85,"key":"secret-local-key-#123!345%678"}' \
 --set 'evo.llm_kwargs={"temperatures":[0.65,0.8,0.95],"max_tokens":12288,"reasoning_efforts":["high"]}'

```



```Bash
shinka_run --task-dir examples/encryption \
 --config-fname shinka.yaml \
 --results_dir results/encryption_pdf_example \
 --num_generations 20 \
 --max-evaluation-jobs 2 \
 --set 'job.extra_cmd_args={"input-file":".../file.pdf","importance":0.7,"key":"secret-local-key-#123!345%678"}' \
 --set 'evo.llm_kwargs={"temperatures":[0.5,0.75,0.85],"max_tokens":12288,"reasoning_efforts":["high"]}'

```

## Evaluation output

Each candidate evaluation produces:
| File | Contents | 
|----|----|
|`metrics.json`| Final score, avalanche metrics, timing, throughput and complexity. |
|`correct.json` | Round-trip status and a diagnostic message on failure. |
|`ciphertext.bin` | Raw encrypted output from the evaluated input. |
|`ciphertext_bits.txt`| Ciphertext represented as bits for inspection. |


