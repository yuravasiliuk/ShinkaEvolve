# Crossover Mutation in ShinkaEvolve

> Source notes for slides. Each `##` section ≈ one slide. Code references point at
> the real implementation so they can be cited or screenshotted.

---

## Slide 1 — What the paper says

From the ShinkaEvolve paper ([arXiv:2509.19349](https://arxiv.org/abs/2509.19349)):

> *"Crossover Mutation. We leverage crossover mutations where an additional archive
> program is sampled and an LLM is prompted to combine programs."*

The natural worry: *if mutations run at the same time, won't combining them cause
logical conflicts?*

**Key point:** that worry assumes crossover means *merging two in-flight edits*.
It doesn't. No diffs are ever merged.

---

## Slide 2 — Two things that are easy to confuse

| | What it is | Do things get merged? |
|---|---|---|
| **Async parallelism** | Many proposals generated concurrently to keep workers busy | **No.** Each is independent, evaluated separately, stored as its own program. |
| **Crossover mutation** | *One* LLM call that reads *two already-scored* programs and writes one new program | Yes — but by one LLM doing semantic synthesis, **not** by splicing diffs. |

The "logical conflict" risk only exists if you mechanically merge concurrent
edits. Shinka never does that.

---

## Slide 3 — What crossover actually is

When the patch type rolls `cross` ([`sampler.py:186`](../shinka/core/sampler.py#L186)):

1. **Sample one extra program** from the archive/inspirations
   — `random.choice(all_inspirations)` in
   [`get_cross_component`](../shinka/prompts/prompts_cross.py#L57).
   Both programs are **already complete and already evaluated** — finished
   citizens of the database, not concurrent mutations.

2. **Build one prompt with both full programs** — the parent
   ([`CROSS_ITER_MSG`](../shinka/prompts/prompts_cross.py#L36)) plus the sampled
   inspiration and its score
   ([`prompts_cross.py:69`](../shinka/prompts/prompts_cross.py#L69)).

3. **Ask the LLM to write one new complete program** that combines the best of
   both ([`CROSS_SYS_FORMAT`](../shinka/prompts/prompts_cross.py#L8)):
   > *"...generate a new code snippet that combines these code scripts... combine
   > the best parts of both implementations that improves the score."*

The output is a **full rewrite** (`<CODE>` block), not a diff.

---

## Slide 4 — The mechanism in one picture

```
   Parent program (scored)      ─┐
                                 ├─►  ONE LLM call  ─►  one new complete program  ─►  evaluate
   Archive inspiration (scored) ─┘   "combine the best of both"
```

- Inputs: two finished, scored programs.
- Operator: a single LLM call (semantic recombination).
- Output: one coherent new program, then validated and scored like any mutation.

---

## Slide 5 — Why "logical conflict" is not a problem

This is where the LLM-as-operator design beats classical genetic algorithms:

- **Classical GA crossover** = blind splicing (child = first half of A + second
  half of B). This genuinely *can* produce broken, conflicting offspring — the
  risk you'd intuit.
- **Shinka's crossover** = the LLM **reads and understands both programs and
  reconciles them** into one program meant to still run. Conflicts are resolved
  by the model at authoring time, not left as a broken merge.

---

## Slide 6 — And if the LLM gets it wrong? Selection cleans up

Even a bad combination is harmless, because the new program still must:

1. **apply / parse** and keep the `EVOLVE-BLOCK` markers
   ([`prompts_cross.py:29`](../shinka/prompts/prompts_cross.py#L29))
2. **run** under your `evaluate.py`
3. **beat selection** — a broken combination scores poorly or fails, and is
   discarded.

> Broken offspring are *expected and harmless*. Evolution doesn't need every
> offspring to be valid — it just needs *some* to be improvements.

---

## Slide 7 — Implementation details worth knowing

- The crossover partner is currently chosen **uniformly at random**
  ([`prompts_cross.py:67`](../shinka/prompts/prompts_cross.py#L67)). There's a
  `TODO` ([line 64](../shinka/prompts/prompts_cross.py#L64)) to instead pick a
  more *embedding-distant* partner for richer crossover.
- `cross` is **automatically skipped** when there are no inspirations to combine
  ([`sampler.py:91`](../shinka/core/sampler.py#L91)) — you can't cross a program
  with nothing.
- Crossover is one of three patch types (`diff`, `full`, `cross`), sampled by
  `patch_type_probs` (default `[0.6, 0.3, 0.1]`).

---

## Slide 8 — One-line takeaway

> **Crossover = one LLM call that synthesizes a single new program from two
> finished, scored programs — semantic recombination, not diff-merging — and the
> result is validated and scored like any other mutation.**
