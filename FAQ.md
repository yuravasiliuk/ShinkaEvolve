# ShinkaEvolve FAQ — Summer Internship Edition

A living document for the experimental summer internship program. **Students and reviewers: this file is yours to edit.** Add your question to the matching section (or open a new one), even if you don't have the answer yet — mark it `**A:** _(unanswered)_` and someone will fill it in. Keep answers short and link to code or docs instead of re-explaining them.

Related reading, in rough order:

1. [README.md](README.md) — install, quick start, config tables
2. [CLAUDE.md](CLAUDE.md) — the one-page mental map of the codebase
3. [docs/](docs/) — the full documentation site (`mkdocs serve` to preview, or https://sakanaai.github.io/ShinkaEvolve/)
4. The paper: [arXiv 2509.19349](https://arxiv.org/abs/2509.19349) (local copy: [papers/shinka_evolve.pdf](papers/shinka_evolve.pdf), alongside AI Scientist, Darwin Gödel Machine, and AlphaEvolve-adjacent papers in [papers/](papers/))

---

## Big Picture

**Q: What is ShinkaEvolve in one sentence?**
**A:** It's an evolutionary loop where LLMs propose code edits (mutations), an evaluator script scores each variant, and a database of program populations decides which variants become parents for the next round — so programs get better over generations without a human editing them.

**Q: How is this different from just asking an LLM to improve my code in a chat?**
**A:** Three things, per the paper: (1) it keeps an *archive* of many past solutions organized into islands, and samples parents/inspirations from it, so good ideas aren't lost and can recombine; (2) it rejection-samples proposals for *novelty* (embedding similarity + LLM judge) so the search doesn't collapse onto one idea; (3) it runs a *bandit* (UCB1) over an ensemble of LLMs, learning which model actually improves fitness on your task. A chat session has none of that memory or selection pressure.

**Q: What do "generation", "island", "archive", and "inspiration" mean here?**
**A:** A *generation* is one proposal→evaluate→store cycle. *Islands* are semi-isolated subpopulations that evolve in parallel for diversity, with occasional migration between them. The *archive* holds the best programs found so far (globally capped, default 40). *Inspirations* are extra programs (archive elites + top-k) shown to the LLM alongside the parent so it can borrow ideas.

**Q: Where's the actual evolution loop in code?**
**A:** `ShinkaEvolveRunner` in [shinka/core/async_runner.py](shinka/core/async_runner.py). It's big; read CLAUDE.md's "One Generation of Evolution" diagram first, then trace one subsystem at a time (database → prompts → llm → edit → launch).

## Getting Started

**Q: What's the fastest way to see it work?**
**A:** `uv sync --dev`, put API keys in `.env`, then `shinka_launch variant=circle_packing_example`. Watch progress with `shinka_visualize --port 8888 --open` in a second terminal.

**Q: What files do I need to write to evolve my own program?**
**A:** Exactly two, in one task directory: `initial.py` (your starting solution, with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` around the code the LLM may change, plus a `run_experiment(**kwargs)` entry point) and `evaluate.py` (calls `run_shinka_eval` and returns metrics including `combined_score`). Then `shinka_run --task-dir your_dir --results_dir results/run1 --num_generations 20`. Copy [examples/circle_packing/](examples/circle_packing/) as a template, or use the `shinka-setup` skill in [skills/](skills/).

**Q: Is a higher or lower `combined_score` better?**
**A:** Higher. Shinka always maximizes. If your task is a minimization (e.g. error), negate it or invert it in `evaluate.py`.

**Q: What's the difference between `shinka_launch` and `shinka_run`?**
**A:** Same engine, different config surface. `shinka_launch` is Hydra-based — composable presets (`task=`, `database=`, `evolution=`, `cluster=`, `variant=`) from `shinka/configs/`, good for experiments you'll rerun and sweep. `shinka_run` is a flag-based task-directory launcher designed for coding agents and quick starts — no Hydra files needed. There's also a plain Python API (`ShinkaEvolveRunner`) shown in the README.

**Q: Where do results go and what's in there?**
**A:** The `results_dir` you pass (or an auto-timestamped `results_*/`). It contains the SQLite program database, `evolution_run.log`, and per-generation program folders. `shinka_visualize` serves an interactive tree/metrics view of it; `examples/*/load_results.ipynb` shows programmatic loading.

**Q: `public` vs `private` metrics — who sees what?**
**A:** `public` metrics are shown to the LLM in future mutation prompts (they steer evolution). `private` metrics are stored but hidden from the LLM — use them for held-out checks so the LLM can't overfit to them. `text_feedback` (a string) is also LLM-visible when `use_text_feedback=True`.

## Running Cheaply (intern budgets are finite)

**Q: How do I avoid burning API credits?**
**A:** Options, roughly in order: (1) use `headless/claude` or `headless/codex` model strings to run off a Claude/ChatGPT subscription via the Headless CLI instead of API keys (see [examples/sine_approx_headless/](examples/sine_approx_headless/)); (2) set `evo_config.max_api_costs` (USD cap — the runner stops proposing at the cap); (3) keep `num_generations` small and use cheap models in `llm_models` while debugging your evaluate/initial pair; (4) debug your `evaluate.py` standalone before evolving — a broken evaluator wastes every generation.

**Q: My subscription-backed run printed "Subscription usage gate ... pausing" — is that a bug?**
**A:** No, it's the self-throttle. For `headless/claude`, Shinka polls your Claude usage (same data as Claude Code's `/usage`) and pauses new proposals when the 5-hour window hits `subscription_pause_threshold` (default 0.95), resuming automatically at reset. Weekly-cap exhaustion stops new proposals for the rest of the run. Set `subscription_pause_threshold=None` to disable. If credentials are missing the gate simply switches off (fails open).

**Q: Why does my run report token counts/costs for subscription models that "should be free"?**
**A:** The CLI reports zero tokens for subscription calls, so Shinka backfills heuristic estimates (`shinka/llm/token_estimate.py`) to keep cost plots and `max_api_costs` budgets meaningful. They're estimates, not billing.

## How the Search Actually Works

**Q: What are the three patch types?**
**A:** `diff` (targeted SEARCH/REPLACE edits), `full` (complete rewrite of the mutable blocks), and `cross` (crossover — an extra archive program is sampled and the LLM combines ideas). Sampled per-generation from `patch_types` with `patch_type_probs` (default 0.6/0.3/0.1). Application lives in `shinka/edit/`.

**Q: What stops the LLM from editing code it shouldn't touch?**
**A:** The `EVOLVE-BLOCK` markers. Patch application verifies immutable regions are byte-identical after the edit; violations are rejected and resampled (with parsing feedback to the LLM, up to `max_patch_resamples`).

**Q: What is novelty rejection sampling?**
**A:** Before evaluating a proposal, Shinka embeds its mutable code and computes cosine similarity to the island's existing programs. Above the threshold (`code_embed_sim_threshold`, default 0.99), an LLM judge is asked whether it's meaningfully different; if not, the proposal is rejected and resampled. This avoids spending evaluations on near-duplicates. See `shinka/embed/` + `shinka/core/novelty_judge.py`.

**Q: How does Shinka pick which LLM to use each generation?**
**A:** `llm_dynamic_selection="ucb"` (default) runs a UCB1 bandit over `llm_models` (`shinka/llm/prioritization.py`), rewarding models by *relative* fitness improvement over the parent/baseline, cost-aware via `cost_aware_coef`. There's an interactive explainer in the docs: [docs/bandit_selection.md](docs/bandit_selection.md).

**Q: What's the "meta-scratchpad"?**
**A:** Every `meta_rec_interval` generations, a summarizer LLM reviews recent evaluations and distills recommendations that get appended to future mutation prompts (`shinka/core/summarizer.py`) — accumulated learning across the run.

**Q: Parent selection has several strategies — which do I pick?**
**A:** Default `weighted` combines performance (sigmoid-scaled fitness) with novelty (fewer offspring → more likely picked). `power_law` gives tunable greediness via `exploitation_alpha` (0 = uniform/explore, large = hill-climb). `beam_search` keeps the top `num_beams`. The math is in [docs/parent_selection_math.md](docs/parent_selection_math.md); Figure 2 of the paper visualizes them.

## Development & Contributing

**Q: Which checks must pass before I push?**
**A:** The three CI commands (also enforced by the `.githooks` pre-push hook):
```bash
uv run ruff check tests --exclude tests/file.py
uv run mypy --follow-imports=skip --ignore-missing-imports tests/test_*.py tests/conftest.py
uv run --with pytest-cov pytest -q -m "not requires_secrets" --cov=shinka --cov-report=term-missing --cov-report=xml:coverage.xml
```

**Q: I changed something in the core evolution pipeline. Why is my reviewer asking for benchmarks?**
**A:** [CONTRIBUTING.md](CONTRIBUTING.md) requires it: changes to parent selection, mutation generation, archive updates, prompt evolution, novelty logic, evaluation scheduling, oversubscription, or island behavior need results on a representative runnable example vs. a baseline (current `main` is fine), with the exact commands, the metric compared, and a short interpretation.

**Q: How do I add a new LLM model?**
**A:** Add a row to `shinka/llm/providers/pricing.csv` (prices, `is_reasoning`, temperature flags). Routing is by model-string prefix in `shinka/llm/client.py` / `providers/model_resolver.py`. Watch out: adaptive-thinking Anthropic models (Opus/Fable/Sonnet-5 era) need `is_reasoning=False` in the CSV *and* an entry in `_NO_TEMPERATURE_MODELS` in `shinka/llm/providers/pricing.py`, otherwise the API returns 400.

**Q: I edited `dbase.py` (or `apply_diff.py`, `summarizer.py`, …) but the runner ignores my change. Why?**
**A:** Most subsystems have sync/async twins (`async_dbase.py`, `async_apply.py`, `async_summarizer.py`, `async_novelty_judge.py`) and `ShinkaEvolveRunner` uses the async path. Check whether your change belongs in both. Also make sure you didn't edit the stale copy under `build/`.

**Q: Why does ruff/mypy only check `tests/` in CI?**
**A:** That's the current scope of the enforced checks; `tests/file.py` and `tests/circle.py` are deliberately-imperfect fixture files for the edit tests and are excluded. You're welcome to run `ruff check shinka` locally, but don't mass-reformat the package in an unrelated PR (see CONTRIBUTING's Occam's razor note).

## Student Questions

_Add new questions from internship students below. Include your name/date if you want follow-up._

_(none yet)_

## Reviewer Questions

_Add questions and clarification requests from reviewers below._

_(none yet)_
