#!/usr/bin/env python3
"""Minimal Shinka <-> headless CLI round-trip check. No evolution loop.

Unlike debug_headless_timeout.py (raw subprocess mimicry), this goes through
Shinka's REAL provider stack — shinka.llm.query.query() -> query_headless()
-> prompt-file rendering -> stdout/usage parsing -> QueryResult — so it
validates that Shinka's schemas actually work against the CLI:

  turn 1: ask for a function name, expect non-null plain-text content
  turn 2: feed turn 1's new_msg_history back and ask for a DIFFERENT name —
          proves the "# Previous Messages" history schema round-trips and the
          ping-pong is not null messages

Each session also writes a self-contained markdown report (messages sent,
full responses, tokens/cost, check results, rendered prompt files) to
--report-dir, default: examples/circle_packing/headless_roundtrip_reports/.

Both turns use natural task-shaped content on purpose. Canary-style probes
("repeat this exact verification code from the previous message") trip the
Claude CLI's prompt-injection defenses — it then disowns the injected history
("there are no previous messages...") and the check fails even though the
schema is fine. Use --canary to observe that behavior deliberately.

Usage:
  .venv/bin/python examples/circle_packing/check_shinka_headless_roundtrip.py
  .venv/bin/python ... --model headless/codex@gpt-5.5?effort=low
  .venv/bin/python ... --turns 1          # single message only
  .venv/bin/python ... --canary           # adversarial history probe
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

EXAMPLE_DIR = Path(__file__).resolve().parent

SYSTEM_MSG = (
    "You are a helpful assistant for the Shinka evolution framework. "
    "Follow instructions exactly. Reply with plain text only."
)

NATURAL_TURN1 = (
    "Suggest a short snake_case name for a Python function that packs 26 "
    "circles into a unit square, maximizing the sum of radii. "
    "Reply with just the name."
)
NATURAL_TURN2 = (
    "Good. Now suggest one alternative name, different from the one you "
    "proposed before. Reply with just the name."
)

CANARY_CODE = "PING-7423"
CANARY_TURN1 = (
    "Connectivity check. Reply with exactly this verification code and "
    f"nothing else: {CANARY_CODE}"
)
CANARY_TURN2 = (
    "What exact verification code did you reply with in the previous message? "
    "Reply with just the code and nothing else."
)


@dataclass
class TurnRecord:
    turn: int
    message: str
    ok: bool = False
    content: str = ""
    elapsed: float = 0.0
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    tokens_estimated: bool = False
    cost_estimated: bool = False
    prompt_file: str = ""
    history_len_before: int = 0
    history_len_after: int = 0
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"


def show(label: str, value: object) -> None:
    print(f"    {label:<20} {value}")


def run_turn(
    *,
    turn: int,
    model: str,
    msg: str,
    msg_history: list,
    work_dir: Path,
    checks: dict[str, Callable[[str], bool]],
) -> tuple[TurnRecord, list]:
    from shinka.llm.query import query

    record = TurnRecord(
        turn=turn, message=msg, history_len_before=len(msg_history)
    )
    print(f"\n=== Turn {turn}: sending {len(msg)} chars "
          f"(history: {len(msg_history)} messages) ===")
    start = time.monotonic()
    try:
        result = query(
            model,
            msg,
            SYSTEM_MSG,
            msg_history=msg_history,
            headless_work_dir=str(work_dir),
        )
    except Exception as exc:
        record.elapsed = time.monotonic() - start
        record.error = f"{type(exc).__name__}: {exc}"
        print(f"  => FAIL after {record.elapsed:.1f}s: {record.error}")
        return record, msg_history

    record.elapsed = time.monotonic() - start
    record.content = (result.content or "").strip()
    record.input_tokens = result.input_tokens
    record.output_tokens = result.output_tokens
    record.cost = result.cost
    record.tokens_estimated = bool(result.kwargs.get("tokens_estimated"))
    record.cost_estimated = bool(result.kwargs.get("cost_estimated"))
    record.prompt_file = str(result.kwargs.get("headless_prompt_path", ""))
    record.history_len_after = len(result.new_msg_history)

    show("elapsed", f"{record.elapsed:.1f}s")
    show("content", repr(record.content[:200])
         if record.content else "<< NULL / EMPTY >>")
    show("input_tokens", record.input_tokens)
    show("output_tokens", record.output_tokens)
    show("cost", f"${record.cost:.6f}")
    show("prompt_file", record.prompt_file)

    record.checks = {
        "non-null content": bool(record.content),
        "history grew by 2": record.history_len_after == len(msg_history) + 2,
        "output tokens > 0": record.output_tokens > 0,
        **{name: check(record.content) for name, check in checks.items()},
    }
    record.ok = all(record.checks.values())
    for name, passed in record.checks.items():
        print(f"    [{'ok' if passed else 'XX'}] {name}")
    print(f"  => {record.verdict}")
    return record, result.new_msg_history


def write_report(
    path: Path,
    *,
    model: str,
    scenario: str,
    timeout: str,
    records: list[TurnRecord],
    overall: bool,
) -> None:
    lines = [
        "# Shinka Headless Round-Trip Report",
        "",
        f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Model:** `{model}`",
        f"- **Scenario:** {scenario}",
        f"- **Timeout per call:** {timeout}s",
        f"- **Overall:** {'PASS ✅' if overall else 'FAIL ❌'}",
        "",
        "Validates Shinka's real provider path: `shinka.llm.query.query()` -> "
        "`query_headless()` -> prompt rendering -> stdout/usage parsing -> "
        "`QueryResult`.",
    ]
    for record in records:
        lines += [
            "",
            f"## Turn {record.turn} — {record.verdict}",
            "",
            f"- elapsed: {record.elapsed:.1f}s",
            f"- input/output tokens: {record.input_tokens} / "
            f"{record.output_tokens}"
            + (" (estimated)" if record.tokens_estimated else ""),
            f"- cost: ${record.cost:.6f}"
            + (" (estimated)" if record.cost_estimated else ""),
            f"- history: {record.history_len_before} -> "
            f"{record.history_len_after} messages",
            "",
            "### Message sent",
            "",
            "```text",
            record.message,
            "```",
            "",
            "### Response received",
            "",
            "```text",
            record.content or record.error or "<< NULL / EMPTY >>",
            "```",
            "",
            "### Checks",
            "",
            *(f"- [{'x' if passed else ' '}] {name}"
              for name, passed in record.checks.items()),
        ]
        if record.error:
            lines += ["", f"**Error:** `{record.error}`"]
        prompt_path = Path(record.prompt_file) if record.prompt_file else None
        if prompt_path and prompt_path.exists():
            lines += [
                "",
                "### Rendered prompt file (what the CLI actually received)",
                "",
                f"`{prompt_path}`",
                "",
                "````markdown",
                prompt_path.read_text(encoding="utf-8").rstrip(),
                "````",
            ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", default="headless/claude")
    parser.add_argument("--turns", type=int, choices=(1, 2), default=2)
    parser.add_argument(
        "--canary",
        action="store_true",
        help="Adversarial variant: exact-echo verification code. Expected to "
        "FAIL turn 2 on headless/claude (injection defense disowns history).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="Per-call timeout; sets SHINKA_HEADLESS_TIMEOUT unless already set",
    )
    parser.add_argument(
        "--work-dir", type=Path, default=EXAMPLE_DIR / ".headless_roundtrip"
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=EXAMPLE_DIR / "headless_roundtrip_reports",
        help="Where the markdown session report is saved",
    )
    args = parser.parse_args()

    os.environ.setdefault("SHINKA_HEADLESS_TIMEOUT", str(args.timeout))
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(EXAMPLE_DIR.parents[1]))  # repo root, for dev runs

    scenario = "canary (adversarial)" if args.canary else "natural"
    print(f"model: {args.model}")
    print(f"scenario: {scenario}")
    print(f"timeout per call: {os.environ['SHINKA_HEADLESS_TIMEOUT']}s")

    if args.canary:
        turn1_msg, turn2_msg = CANARY_TURN1, CANARY_TURN2
        turn1_checks = {f"contains {CANARY_CODE}": lambda c: CANARY_CODE in c}
    else:
        turn1_msg, turn2_msg = NATURAL_TURN1, NATURAL_TURN2
        turn1_checks = {
            "looks like an identifier": lambda c: bool(
                re.search(r"[a-z][a-z0-9_]{3,}", c)
            )
        }

    records: list[TurnRecord] = []
    record1, history = run_turn(
        turn=1, model=args.model, msg=turn1_msg, msg_history=[],
        work_dir=args.work_dir, checks=turn1_checks,
    )
    records.append(record1)

    if args.turns == 2 and record1.ok:
        if args.canary:
            turn2_checks = {
                f"recalls {CANARY_CODE}": lambda c: CANARY_CODE in c
            }
        else:
            content1 = record1.content
            turn2_checks = {
                "differs from turn 1": lambda c: bool(c) and content1 not in c,
                "looks like an identifier": lambda c: bool(
                    re.search(r"[a-z][a-z0-9_]{3,}", c)
                ),
            }
        record2, _ = run_turn(
            turn=2, model=args.model, msg=turn2_msg, msg_history=history,
            work_dir=args.work_dir, checks=turn2_checks,
        )
        records.append(record2)
    elif args.turns == 2:
        print("\nTurn 1 failed — skipping ping-pong turn.")

    overall = all(record.ok for record in records) and len(records) == args.turns
    model_slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.model).strip("_")
    report_path = (
        args.report_dir
        / f"roundtrip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{model_slug}.md"
    )
    write_report(
        report_path,
        model=args.model,
        scenario=scenario,
        timeout=os.environ["SHINKA_HEADLESS_TIMEOUT"],
        records=records,
        overall=overall,
    )

    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}"
          + (" (schemas render, CLI responds, history round-trips)"
             if overall else ""))
    print(f"report saved: {report_path}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
