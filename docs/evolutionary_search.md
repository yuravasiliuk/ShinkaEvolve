# How Evolutionary Search Works in ShinkaEvolve

> **Audience:** new interns and contributors who have never touched an
> evolutionary-algorithm codebase. The goal of this page is to lower the entry
> barrier: read it once and you should understand *what* ShinkaEvolve does, *why*
> it does it, and *where* in the code each piece lives. Every concept links to the
> actual source so you can jump straight in.

---

## 1. The one-paragraph mental model

ShinkaEvolve improves a program the way nature improves a species. It keeps a
**population** of candidate programs, repeatedly **selects** good ones as
"parents", asks an **LLM to mutate** them into new candidate programs, **scores**
each new candidate with your evaluator, and keeps the better ones around to be
parents in the future. Do this for many **generations** and the population drifts
toward higher and higher scores. The twist versus classical evolution: the
"mutation operator" is not random bit-flipping, it is a Large Language Model that
reads the current code (plus examples of other good code) and writes a smarter
version. This is the idea behind [AlphaEvolve](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/);
ShinkaEvolve is an open, sample-efficient implementation of it. The original
paper is *ShinkaEvolve: Towards Open-Ended and Sample-Efficient Program Evolution*
([arXiv:2509.19349](https://arxiv.org/abs/2509.19349)).

---

## 2. Glossary (read this first)

| Term | Plain-English meaning | Where it lives |
|------|----------------------|----------------|
| **Program** | One candidate solution = a snapshot of the code plus its score and metadata. | [`Program`](../shinka/database/dbase.py#L146) |
| **Generation** | One iteration of the loop: pick parents → mutate → evaluate → store. | loop in [`async_runner.py`](../shinka/core/async_runner.py#L969) |
| **Population / Database** | All programs ever produced, stored in SQLite, with the bookkeeping that drives sampling. | [`ProgramDatabase`](../shinka/database/dbase.py#L264) |
| **Parent** | The program a new candidate is mutated *from*. | [`parents.py`](../shinka/database/parents.py#L69) |
| **Inspirations** | Extra example programs shown to the LLM for context (not mutated, just "look at these"). | [`inspirations.py`](../shinka/database/inspirations.py#L36) |
| **Patch / mutation** | The edit the LLM proposes: a `diff`, a `full` rewrite, or a `cross`-over. | [`sampler.py`](../shinka/core/sampler.py#L77) |
| **Evaluator** | *Your* `evaluate.py` that runs a candidate and returns a `combined_score`. | [`wrap_eval.py`](../shinka/core/wrap_eval.py) |
| **Archive** | A curated "hall of fame" of the best/most diverse programs. | [`_update_archive`](../shinka/database/dbase.py#L2188) |
| **Island** | An isolated sub-population that evolves on its own to preserve diversity. | [`islands.py`](../shinka/database/islands.py) |
| **Fitness** | The number we maximize, usually `combined_score` from the evaluator. | your `aggregate_fn` |

---

## 3. The evolution loop, step by step

Each generation runs the same six steps. This mirrors the summary in
[`core_concepts.md`](core_concepts.md) but here we follow each step into the code.

```
        ┌─────────────────────────────────────────────────────────┐
        │  1. SELECT   pick an island, a parent, and inspirations   │
        │  2. MUTATE   ask LLM(s) for a patch (diff / full / cross) │
        │  3. APPLY    materialize the new candidate program file    │
        │  4. EVALUATE run evaluate.py → combined_score + metrics    │
        │  5. STORE    insert the program into the database          │
        │  6. UPDATE   refresh archive, islands, bandit, meta-memory │
        └─────────────────────────────────────────────────────────┘
                          repeat for num_generations
```

The whole thing is orchestrated by
[`ShinkaEvolveRunner.run_async`](../shinka/core/async_runner.py#L969). The number
of generations comes from `EvolutionConfig.num_generations` (default `50`, see
[`config.py`](../shinka/core/config.py)).

### Step 1 — Select: who gets mutated?

Selection is the heart of "survival of the fittest". The database picks **one
island**, then **one parent** inside it, then a handful of **inspirations**. All
of this happens in [`ProgramDatabase.sample`](../shinka/database/dbase.py#L1189).

**Parent selection** ([`parents.py`](../shinka/database/parents.py#L69)) supports
several strategies via `DatabaseConfig.parent_selection_strategy`:

- `weighted` (default) — sigmoid-weighted by score, sharpness set by
  `parent_selection_lambda`.
- `power_law` — rank-based sampling; `exploitation_alpha` controls how greedily
  it favours top-ranked programs (`0` = uniform, higher = greedier). See
  [`sample_with_powerlaw`](../shinka/database/parents.py#L11).
- `beam_search` — keep a fixed set of `num_beams` frontiers.

A key idea here is the **exploration ↔ exploitation** trade-off: pick the current
best too often and you get stuck in a local optimum; pick randomly and you waste
budget. `exploitation_ratio` (default `0.2`) is the chance of pulling a parent
from the elite archive instead of the live island.

**Inspirations** ([`inspirations.py`](../shinka/database/inspirations.py#L36))
are *additional* programs shown to the LLM as worked examples — the best program,
island elites, and a couple of archive samples. They are controlled by
`num_archive_inspirations` and `num_top_k_inspirations`. They are never mutated;
they just make the LLM's proposal better-informed.

### Step 2 — Mutate: the LLM as a mutation operator

[`PromptSampler.sample`](../shinka/core/sampler.py#L77) builds the prompt sent to
the LLM. First it randomly picks a **patch type** using
`EvolutionConfig.patch_types` / `patch_type_probs` (default
`["diff", "full", "cross"]` with probabilities `[0.6, 0.3, 0.1]`):

- **`diff`** — a surgical `SEARCH/REPLACE` edit. Cheapest and most common. The
  exact format the LLM must obey is in
  [`prompts_diff.py`](../shinka/prompts/prompts_diff.py).
- **`full`** — rewrite the whole evolve-block from scratch. More exploratory.
- **`cross`** — *crossover*: combine ideas from the parent and an inspiration,
  the classic "breeding two parents" move. Automatically skipped when there are
  no inspirations to combine ([`sampler.py:91`](../shinka/core/sampler.py#L91)).

The prompt bundles: the parent's code, its score/metrics, the inspiration
examples, optional text feedback, and optional **meta-recommendations** (see
[§5](#5-things-that-make-shinka-sample-efficient)). Which LLM answers is itself a
learned decision — see the bandit in [§5](#5-things-that-make-shinka-sample-efficient).

### Step 3 — Apply: turn the LLM reply into a real file

Only the code **between** the `EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END` markers
may change; everything else is immutable. The patch is parsed and applied by
[`shinka/edit/apply_diff.py`](../shinka/edit/apply_diff.py) (and `apply_full.py`).
If the diff doesn't apply cleanly, ShinkaEvolve can resample up to
`max_patch_resamples` times, and even fall back to a **fix prompt**
([`sample_fix`](../shinka/core/sampler.py#L211)) that shows the LLM the error
output and asks it to repair the program.

### Step 4 — Evaluate: assign a fitness

The new candidate is run by *your* `evaluate.py` through
[`wrap_eval.py`](../shinka/core/wrap_eval.py). Your evaluator returns a
`combined_score` (higher = better, it's a maximization), plus public/private
metrics and optional text feedback. Jobs run locally or on Slurm depending on the
[`JobConfig`](../shinka/launch). This score is the program's **fitness** and
drives every future selection decision.

### Step 5 — Store: grow the population

The result is written into the SQLite database via
[`ProgramDatabase.add`](../shinka/database/dbase.py#L777). Each `Program` records
its code, score, parent (lineage), island, and generation — which is exactly what
the WebUI genealogy tree visualizes.

### Step 6 — Update: keep the good, prune the rest

After insertion, [`run_post_add_maintenance`](../shinka/database/dbase.py#L942)
refreshes derived state: the **archive** is updated (see [§4](#4-archives-and-islands-keeping-diversity)),
the **best program** is recomputed, the **bandit** that picks LLMs is rewarded,
and (periodically) islands migrate and meta-recommendations regenerate.

---

## 4. Archives and islands: keeping diversity

If you only ever keep the single best program, evolution collapses onto one idea
and stops discovering. ShinkaEvolve fights this with two mechanisms.

**The archive** is a size-capped "hall of fame" (`archive_size`, default `40`).
When it's full, a new program must earn its spot. Two replacement policies exist
([`dbase.py`](../shinka/database/dbase.py#L2188)):

- `fitness` — rank by `archive_criteria` (default: `combined_score`) and keep the
  top performers.
- `crowding` — also reward programs that are *different* from what's already
  there, so the archive stays diverse, not just high-scoring.

**Islands** ([`islands.py`](../shinka/database/islands.py)) split the population
into `num_islands` (default `2`) separate sub-populations that evolve
independently. Different islands explore different "neighbourhoods" of solution
space. Every `migration_interval` generations, a few programs hop between islands
([`ElitistMigrationStrategy`](../shinka/database/islands.py#L213)) to share
discoveries — the evolutionary equivalent of gene flow. With
`enable_dynamic_islands`, a fresh island can be spawned when progress stalls
(`stagnation_threshold`). This is the **island model** of genetic algorithms and
is what the README means by "knowledge transfer between evolutionary islands".

---

## 5. Things that make Shinka *sample-efficient*

Calling an LLM and running an evaluation costs money and time, so ShinkaEvolve
tries to learn the most per candidate. Four mechanisms matter:

1. **Bandit LLM selection.** When you give it several models, a multi-armed
   bandit ([`AsymmetricUCB`](../shinka/llm/prioritization.py#L295), selected by
   `llm_dynamic_selection="ucb"`) learns which model produces the best
   improvements per dollar (`cost_aware_coef`) and shifts traffic toward it.
   There's an interactive explainer in [`bandit_selection.md`](bandit_selection.md).

2. **Novelty rejection.** Before spending an evaluation, candidates that are
   near-duplicates of existing programs (code-embedding cosine similarity above
   `code_embed_sim_threshold`, default `0.99`) can be rejected and resampled —
   see [`NoveltyJudge`](../shinka/core/novelty_judge.py#L11). No point scoring the
   same idea twice.

3. **Meta-recommendations.** Every `meta_rec_interval` generations a
   [`MetaSummarizer`](../shinka/core/summarizer.py#L22) reads recent history and
   writes high-level advice ("try vectorizing the inner loop") that is injected
   into future mutation prompts ([`sampler.py:118`](../shinka/core/sampler.py#L118)).
   The system effectively reflects on its own search.

4. **Prompt co-evolution.** With `evolve_prompts=True`, the *system prompts* that
   drive mutation are themselves evolved with their own archive and bandit
   ([`prompt_evolver.py`](../shinka/core/prompt_evolver.py)). Sometimes the best
   lever is how you ask, not what you ask.

---

## 6. Async execution (why it's fast)

LLM proposals are slow; evaluations may be slow too. Instead of doing them one at
a time, the runner overlaps them: while some candidates are being evaluated,
others are being proposed. This pipeline lives in the proposal/eval coordinator
tasks of [`async_runner.py`](../shinka/core/async_runner.py#L2345) and is tuned
with `max_proposal_jobs`, `max_evaluation_jobs`, and `max_db_workers`. The full
story (and a throughput demo) is in [`async_evolution.md`](async_evolution.md).

---

## 7. Where to look next

- **Run one yourself:** [Getting Started](getting_started.md) →
  the [Circle Packing example](../examples/circle_packing).
- **Tune the knobs:** [Configuration](configuration.md) — every field referenced
  above is documented there.
- **See it move:** the [WebUI](webui.md) renders the genealogy tree, island
  populations, and score-over-time live.
- **Trace one generation in code:** start at
  [`run_async`](../shinka/core/async_runner.py#L969), follow
  [`_generate_evolved_proposal`](../shinka/core/async_runner.py#L2630) for the
  mutate→apply→evaluate path, then
  [`_persist_completed_job`](../shinka/core/async_runner.py#L4000) for storage.

If a single sentence has to stick: **ShinkaEvolve is a feedback loop where an LLM
mutates the best programs it has seen so far, and a scorer decides which mutations
survive.** Everything else — islands, archives, bandits, novelty checks — exists
to spend that loop's budget as wisely as possible.
