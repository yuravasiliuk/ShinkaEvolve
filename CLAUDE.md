# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

ShinkaEvolve (`shinka`) combines LLMs with evolutionary search to automatically improve programs. It maintains a population of candidate programs; LLMs act as mutation operators proposing code edits, an evaluator scores each candidate, and a database of islands/archives decides which programs become parents for the next generation. Paper: arXiv 2509.19349 (also at [papers/shinka_evolve.pdf](papers/shinka_evolve.pdf)).

The [papers/](papers/) directory also has related reading: `ai_scientist_v1.pdf`, `ai_scinetist_v2.pdf`, `darwin_godell_machine.pdf`, `meta_harness.pdf`.

The PyPI distribution name is `shinka-evolve`; the Python import stays `import shinka`.

## Common Commands

```bash
# Install for development (Python >= 3.10, uses uv)
uv sync --dev                     # or: uv pip install -e .

# The exact CI checks (also run by .githooks pre-push):
uv run ruff check tests --exclude tests/file.py
uv run mypy --follow-imports=skip --ignore-missing-imports tests/test_*.py tests/conftest.py
uv run --with pytest-cov pytest -q -m "not requires_secrets" --cov=shinka --cov-report=term-missing --cov-report=xml:coverage.xml

# Run tests without coverage / a single test
uv run pytest -q -m "not requires_secrets"
uv run pytest tests/test_island_sampler.py -q
uv run pytest tests/test_llm_query_routing.py -k "headless" -q

# Tests needing live credentials (skip locally unless you have them)
uv run pytest -q -m "requires_secrets"

# Quick smoke evolution run (needs API keys in .env)
shinka_launch variant=circle_packing_example

# Agent-friendly launcher (no Hydra configs; task dir must contain evaluate.py + initial.<ext>)
shinka_run --task-dir examples/circle_packing --results_dir results/my_run --num_generations 20

# WebUI for inspecting a run (separate terminal)
shinka_visualize --port 8888 --open

# Docs site preview
uv sync --group docs && uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8000
```

Pytest markers (`--strict-markers` is on): `integration`, `requires_secrets`. Tests import the repo root via `tests/conftest.py` path insertion. `tests/file.py` and `tests/circle.py` are fixture data for edit tests — excluded from lint, don't "fix" them.

## The Mental Map: One Generation of Evolution

Everything orbits `ShinkaEvolveRunner` (`shinka/core/async_runner.py`, the ~6000-line async orchestrator). One generation flows like this:

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. SAMPLE PARENT   shinka/database/  (ProgramDatabase, SQLite)      │
│    Pick an island → pick a parent (weighted/power-law/beam) →       │
│    pick inspiration programs (archive elites + top-k)               │
│ 2. BUILD PROMPT    shinka/core/sampler.py + shinka/prompts/         │
│    Patch type sampled from ["diff", "full", "cross"]                │
│ 3. QUERY LLM       shinka/llm/  (ensemble + UCB1 bandit selection)  │
│    Model string → provider routing → query                          │
│ 4. APPLY PATCH     shinka/edit/  (SEARCH/REPLACE diff, full         │
│    rewrite, or crossover; EVOLVE-BLOCK markers protect code)        │
│ 5. NOVELTY GATE    shinka/embed/ + core/novelty_judge.py            │
│    Embedding cosine sim > threshold → LLM-as-judge → maybe reject   │
│    and resample                                                     │
│ 6. EVALUATE        shinka/launch/  (local subprocess or Slurm       │
│    conda/docker) runs the task's evaluate.py on the new program     │
│ 7. STORE + LEARN   metrics back into the database; bandit rewards   │
│    updated from fitness improvement; meta-scratchpad summarizer     │
│    (core/summarizer.py) every N generations; optional prompt        │
│    evolution (core/prompt_evolver.py)                               │
└─────────────────────────────────────────────────────────────────────┘
```

Proposals (steps 1–5) and evaluations (step 6) run concurrently — `max_proposal_jobs`, `max_evaluation_jobs`, `max_db_workers` on the runner control this, with an adaptive oversubscription controller when proposals are slower than evals.

### Directory Map

| Path | Role |
|------|------|
| `shinka/core/` | Orchestration: `async_runner.py` (ShinkaEvolveRunner), `config.py` (EvolutionConfig), `sampler.py` (prompt construction), `wrap_eval.py` (`run_shinka_eval` — the helper task `evaluate.py` scripts call), summarizer, novelty judge, prompt evolver |
| `shinka/database/` | `dbase.py` (Program, ProgramDatabase, DatabaseConfig), `islands.py`, `parents.py` (selection strategies), `inspirations.py`, `async_dbase.py` |
| `shinka/llm/` | `client.py`/`query.py` (provider routing), `providers/` (per-provider query fns + `pricing.csv`), `prioritization.py` (bandits), `subscription_usage.py` (Claude usage gating), `token_estimate.py` |
| `shinka/edit/` | Patch application: `apply_diff.py` (SEARCH/REPLACE blocks), `apply_full.py`, `async_apply.py` |
| `shinka/launch/` | Job execution: `local.py`, `slurm.py`, `scheduler.py`; JobConfig classes (LocalJobConfig, SlurmCondaJobConfig, SlurmDockerJobConfig) |
| `shinka/embed/` | Embedding clients for code-novelty similarity |
| `shinka/prompts/` | All prompt templates (diff/full/cross/init/meta/novelty/prompt-evo) |
| `shinka/configs/` | Hydra presets, groups: `task/`, `database/`, `evolution/`, `cluster/`, `variant/` |
| `shinka/cli/` | Entry points: `shinka_launch` (Hydra), `shinka_run` (task-dir, agent-friendly), `shinka_models`, `shinka_visualize` |
| `shinka/webui/` + `shinka/plots/` | Interactive visualization (HTML served by `webui/visualization.py`) and matplotlib plots |
| `examples/` | Runnable tasks (circle_packing, game_2048, julia_prime_counting, fortran_heat_diffusion, novelty_generator, sine_approx_headless) |
| `skills/` | Agent skills for coding agents (shinka-setup, shinka-convert, shinka-run, shinka-inspect) |
| `docs/` | mkdocs-material site (published to sakanaai.github.io/ShinkaEvolve) |

`build/` contains a stale copy of the package from packaging — never edit it.

### Task Anatomy (what users evolve)

A task = `initial.<ext>` + `evaluate.py`:
- `initial.py` marks mutable regions with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` and exposes a `run_experiment(**kwargs)` entry function. Code outside the markers is immutable — patch application enforces this and resamples on violation.
- `evaluate.py` calls `run_shinka_eval` (from `shinka.core`) and returns a metrics dict whose `combined_score` is **maximized**. It can include `public` metrics (LLM-visible), `private` metrics (hidden from the LLM), `extra_data`, and `text_feedback`.

Languages beyond Python (julia, fortran, markdown/text) are supported via `evo_config.language` and `shinka/utils/languages.py`; see the julia/fortran examples.

## LLM Provider Layer

Model strings route by prefix/lookup in `shinka/llm/client.py` + `shinka/llm/providers/model_resolver.py`: e.g. `gpt-*`/`o*` → openai, `claude-*` → anthropic (or `anthropic.` → bedrock), `gemini-*` → google, `deepseek-*`, `local/<model>@<url>` → local OpenAI-compatible server, `headless/<agent>[@model][?effort=]` → the Headless CLI (subscription-backed Claude/Codex agents).

Adding/adjusting a model means editing `shinka/llm/providers/pricing.csv` (pricing, `is_reasoning`, temperature capabilities). Gotcha: adaptive-thinking Anthropic models (Opus/Fable/Sonnet-5 era) need `is_reasoning=False` in the CSV **and** membership in `_NO_TEMPERATURE_MODELS` in `shinka/llm/providers/pricing.py`, or requests 400.

`headless/claude` runs self-throttle against the user's Claude subscription limits (`shinka/llm/subscription_usage.py`): reads `~/.claude/.credentials.json`, polls the usage endpoint, pauses proposals near the 5h/weekly caps (`subscription_pause_threshold`, `subscription_usage_poll_interval` in EvolutionConfig), and fails open if credentials are missing. Subscription calls report zero tokens, so costs are backfilled via `token_estimate.py` to keep `max_api_costs` budgets meaningful.

API keys load from `.env` (python-dotenv). Pinned deps to respect: `hydra-core==1.3.2`, `httpx==0.27`.

## Conventions and Gotchas

- **Sync/async pairs**: several modules exist in both flavors (`dbase.py`/`async_dbase.py`, `apply_diff.py`+`apply_full.py`/`async_apply.py`, `summarizer.py`/`async_summarizer.py`, `novelty_judge.py`/`async_novelty_judge.py`). The runner uses the async path; when changing behavior in one, check whether its twin needs the same change.
- **Config precedence** for `shinka_run`: config YAML < `--set key=value` < authoritative flags (`--results_dir`, `--num_generations` always win).
- Hydra shorthand for `shinka_launch`: `variant=`, `task=`, `database=`, `evolution=`, `cluster=` map onto `shinka/configs/` groups; users can add their own presets via `--config-dir`.
- Results land in a `results_*/` directory containing the SQLite DB, `evolution_run.log`, and per-generation program folders; `shinka_visualize` serves from it.
- From CONTRIBUTING.md: changes to the core evolution pipeline (parent selection, mutation/edit generation, archive updates, prompt evolution, novelty logic, evaluation scheduling, proposal oversubscription, island behavior) require results on a representative runnable example compared against a baseline, with exact commands and metrics. Prefer the smallest change that solves the problem.
- FAQ.md in the repo root is a living document edited by internship students and reviewers — append/refine answers there rather than duplicating explanations in other docs.
