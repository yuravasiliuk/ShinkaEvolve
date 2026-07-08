# Parent Selection: Balancing Exploration & Exploitation

> Source notes for slides. Each `##` section ≈ one slide. The paper's math is
> mapped onto the real implementation in
> [`parents.py`](../shinka/database/parents.py).
>
> Math renders with MathJax/KaTeX (GitHub renders `$...$` and `$$...$$`).

---

## Slide 1 — The question

Given one island's subpopulation: **which program do we mutate next?**

- Always pick the **best** parent → hill-climb into a **local optimum**
  (tiny incremental edits to one lineage).
- Always pick **randomly** → waste budget on weak programs.

ShinkaEvolve offers two parent-sampling strategies, each a tunable knob on the
**exploration ↔ exploitation** dial.

---

## Slide 2 — Strategy 1: Power-law sampling (rank-based)

$$p_i = \frac{r_i^{-\alpha}}{\sum_{j=1}^{n} r_j^{-\alpha}}$$

| Symbol | Meaning |
|---|---|
| $r_i$ | program's **rank** by fitness — best is $r=1$, second $r=2$, … |
| $\alpha$ | **exploitation intensity** (`exploitation_alpha`, default `1.0`) |
| $p_i$ | probability of choosing program $i$ as parent |

**Key idea:** uses *rank only*, not the actual score. Best-by-0.001 and
best-by-100 are treated identically (rank 1 vs rank 2).

---

## Slide 3 — Power-law: the two extremes

$$p_i = \frac{r_i^{-\alpha}}{\sum_{j} r_j^{-\alpha}}$$

- $\alpha = 0 \Rightarrow r_i^{0} = 1$ for all → **uniform sampling** = pure
  exploration.
- $\alpha \to \infty \Rightarrow$ only rank 1 survives normalization →
  **hill-climbing** = pure exploitation (the "always best parent" trap).

In code ([`sample_with_powerlaw`](../shinka/database/parents.py#L11)):

```python
probs = np.array([(i + 1) ** (-alpha) for i in range(len(items))])  # r_i = i+1
probs = probs / probs.sum()
```

Called on the score-sorted archive at
[`parents.py:144`](../shinka/database/parents.py#L144).

---

## Slide 4 — Strategy 2: Weighted sampling (performance × novelty)

Uses **actual fitness values** *and* a **novelty term**
(inspired by Zhang et al., 2025). The weight is a product of two factors:

$$p_i = \frac{w_i}{\sum_j w_j}, \qquad w_i = s_i \cdot h_i$$

- $s_i$ = performance factor (sigmoid)
- $h_i$ = novelty factor (offspring count)

A program is a good parent only if it is **both** high-performing **and**
under-explored.

---

## Slide 5 — Weighted, Factor A: performance via sigmoid

$$s_i = \sigma\big(\lambda \cdot (F(P_i) - \alpha_0)\big), \qquad
  \sigma(x) = \frac{1}{1 + e^{-x}}$$

| Symbol | Meaning |
|---|---|
| $\alpha_0$ | **median fitness** of the candidates — the "baseline" |
| $F(P_i)$ | fitness (`combined_score`) of program $i$ |
| $\lambda$ | **selection pressure** (`parent_selection_lambda`, default `10.0`) |

Sigmoid centered on the median turns *"above or below average?"* into a soft
0→1 gate:

- well **above** median → $s_i \to 1$
- well **below** median → $s_i \to 0$
- at the median → $s_i = 0.5$

$\lambda$ sets how sharp the cutoff is. Code:
[`parents.py:391`](../shinka/database/parents.py#L391).

---

## Slide 6 — Weighted, Factor B: novelty via offspring count

$$h_i = \frac{1}{1 + N(P_i)}$$

| Symbol | Meaning |
|---|---|
| $N(P_i)$ | **number of offspring** program $i$ has produced (`children_count`) |

The anti-stagnation term:

- never mutated → $N=0 \Rightarrow h_i = 1$
- mutated 9 times → $h_i = 0.1$

**The more you've already exploited a program, the less likely you pick it
again** — forcing the search to spread out. Code:
[`parents.py:394`](../shinka/database/parents.py#L394).

---

## Slide 7 — Weighted: the product

$$w_i = s_i \cdot h_i$$

A program is selected as parent only when it is **both**:

| | high performance ($s_i \to 1$) | low performance ($s_i \to 0$) |
|---|---|---|
| **fresh** ($h_i \to 1$) | ✅ ideal parent | ❌ suppressed |
| **exhausted** ($h_i \to 0$) | ❌ suppressed | ❌ suppressed |

A great program that's been mutated to death gets down-weighted; a fresh but
below-median program also gets down-weighted.

---

## Slide 8 — Power-law vs. Weighted

| | Power-law | Weighted |
|---|---|---|
| Uses | **rank only** (ordinal) | **actual scores** (cardinal) + **offspring count** |
| Exploration knob | $\alpha$ | $\lambda$ |
| Anti-stagnation term? | no | yes ($h_i$ novelty) |

Selected via `DatabaseConfig.parent_selection_strategy`
(`"power_law"` / `"weighted"`; `weighted` is the default).

---

## Slide 9 — ⚠️ Detail the paper formula hides: MAD normalization

The code's $s_i$ is **not** exactly $\sigma(\lambda(F_i - \alpha_0))$. It first
normalizes the gap by the **median absolute deviation (MAD)**
([`parents.py:373-391`](../shinka/database/parents.py#L373)):

```python
mad = np.median([abs(score - alpha_0) for score in scores])
normalized_diff = (alpha_i - alpha_0) / max(mad, 1e-6)
s_i = stable_sigmoid(lambda_ * normalized_diff)
```

So really:

$$s_i = \sigma\!\left(\lambda \cdot \frac{F(P_i) - \alpha_0}{\mathrm{MAD}}\right)$$

This makes $\lambda$ **scale-invariant** — otherwise $\lambda=10$ behaves
completely differently on a task scored in $[0,1]$ vs one scored in the
thousands. The paper omits this.

---

## Slide 10 — Takeaway

> Both strategies are dials on the same exploration↔exploitation axis.
> **Power-law** tunes greediness by *rank* ($\alpha$). **Weighted** tunes it by
> *score* ($\lambda$) and additionally pushes the search off over-mutated
> programs via the novelty term ($h_i$) — directly countering the
> "always-best-parent → incremental-only" failure mode.
