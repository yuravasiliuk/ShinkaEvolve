"""Heuristic token-count estimation with no external calls.

Real tokenizers (tiktoken / Anthropic's) use byte-pair encoding, so exact
counts require the model's vocab. But for budgeting — especially in *headless*
mode where the CLI returns ``inputTokens: 0`` — a good heuristic is enough.

Rules of thumb this module relies on (English text):
  * ~4 characters per token.
  * ~0.75 tokens per word  (i.e. 1 token ~= 0.7-0.75 words, 1 word ~= 1.3 tokens).
  * Long words split into multiple sub-word tokens (~1 token per 4 chars).
  * Digit runs tokenize in small groups (~1 token per 3 digits).
  * Most punctuation symbols become their own token.
  * A single space is usually absorbed into the following token, so it is
    not counted separately; extra whitespace / newlines do cost tokens.

Accuracy is typically within ~10-15% of a real BPE tokenizer for prose, and a
bit looser for code. That is plenty for spend estimation.
"""

from __future__ import annotations

import math
import re
from typing import Literal

Method = Literal["chunk", "char", "word"]

# Standard divisors behind the rules of thumb above.
_CHARS_PER_TOKEN = 4.0
_WORDS_PER_TOKEN = 0.75  # OpenAI's guidance; use 0.70 for a slightly higher estimate

# One pass classifies text into word / number / whitespace / symbol runs.
_CHUNK_RE = re.compile(r"[A-Za-z]+|\d+|\s+|[^\sA-Za-z\d]")


def _chunk_tokens(text: str) -> float:
    """BPE-flavored estimate: split into runs and size each one.

    More robust than a flat ratio because it handles long words, numbers,
    punctuation-heavy text, and code without wild over/under-counting.
    """
    total = 0.0
    for chunk in _CHUNK_RE.findall(text):
        ch = chunk[0]
        if ch.isspace():
            # A lone space rides along with the next token (BPE leading space),
            # so it is free. Newlines and runs of spaces each cost ~1 token.
            newlines = chunk.count("\n") + chunk.count("\t")
            spaces = len(chunk) - newlines
            total += newlines + max(0, spaces - 1)
        elif ch.isalpha():
            # ~1 sub-word token per 4 characters, at least 1. Round (not ceil)
            # so common 4-7 letter words stay a single token, as BPE does.
            total += max(1, round(len(chunk) / 4))
        elif ch.isdigit():
            # Digits group in ~3s (e.g. "1234" -> "123","4").
            total += max(1, math.ceil(len(chunk) / 3))
        else:
            # Single punctuation / symbol.
            total += 1
    return total


def estimate_tokens(
    text: str,
    method: Method = "chunk",
    chars_per_token: float = _CHARS_PER_TOKEN,
    words_per_token: float = _WORDS_PER_TOKEN,
) -> int:
    """Roughly estimate the number of tokens in ``text`` (no external call).

    Args:
        text: The string to estimate.
        method: Estimation strategy.
            - ``"chunk"``  (default): regex sub-word estimate; most accurate,
              handles code / numbers / punctuation.
            - ``"char"``:  characters / ``chars_per_token``.
            - ``"word"``:  words / ``words_per_token``.
        chars_per_token: Divisor for the ``"char"`` method (default 4.0).
        words_per_token: Divisor for the ``"word"`` method (default 0.75;
            pass 0.70 to match the "1 token ~= 0.7 words" rule of thumb).

    Returns:
        Estimated token count as an int (>= 0, and >= 1 for any non-empty text).
    """
    if not text:
        return 0

    if method == "char":
        est = len(text) / chars_per_token
    elif method == "word":
        words = len(text.split())
        est = words / words_per_token
    elif method == "chunk":
        est = _chunk_tokens(text)
    else:
        raise ValueError(f"Unknown method: {method!r}")

    return max(1, round(est))


def estimate_tokens_blended(text: str) -> int:
    """Average the char- and word-based estimates.

    A common practical hedge: neither ratio is reliable alone, but their mean
    is stable across both short and long inputs. Kept separate from the
    ``"chunk"`` method so callers can pick a philosophy.
    """
    if not text:
        return 0
    char_est = len(text) / _CHARS_PER_TOKEN
    word_est = len(text.split()) / _WORDS_PER_TOKEN
    return max(1, round((char_est + word_est) / 2))


if __name__ == "__main__":
    samples = [
        "Hello world!",
        "The best known result for packing 26 circles is 2.635.",
        "def f(x):\n    return x ** 2 + 3 * x - 1  # quadratic",
        "1234567890 and some words mixed with 42 numbers",
    ]
    for s in samples:
        print(
            f"chunk={estimate_tokens(s):>3}  "
            f"char={estimate_tokens(s, 'char'):>3}  "
            f"word={estimate_tokens(s, 'word'):>3}  "
            f"blended={estimate_tokens_blended(s):>3}  |  {s!r}"
        )
