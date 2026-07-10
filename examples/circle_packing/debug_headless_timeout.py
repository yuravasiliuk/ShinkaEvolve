#!/usr/bin/env python3
"""Staged bisection of the headless/codex fallback timeout.

Companion to headless_fallback_timeout_report.md. Runs an ordered ladder of
probes so the first failing stage localizes the fault:

  0 env                  local facts only: versions, codex config effort, auth
  1 cli-check            headless --check (includes npx resolution overhead)
  2 codex-trivial        "2+2" prompt, effort inherited from ~/.codex/config.toml
  3 codex-trivial-low    "2+2" prompt, --reasoning-effort low
  4 codex-task-low       circle-packing prompt, --reasoning-effort low
  5 codex-task           circle-packing prompt, inherited effort (the failing
                         call from the report), generous timeout
  6 codex-task-workdir   like 5 but --work-dir is the real circle_packing dir
  7 claude-task          circle-packing prompt via headless/claude

Every model stage streams stdout/stderr live with +elapsed timestamps,
records time-to-first-output, kills the whole process group on timeout (so
no orphaned `codex` keeps running), and saves full logs plus a JSON report
under --debug-dir. A diagnosis section at the end compares stages and names
the most likely root cause.

Typical sessions:
  python debug_headless_timeout.py --quick          # stages 0-4
  python debug_headless_timeout.py                  # full ladder
  python debug_headless_timeout.py --stages 2,3     # effort A/B only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
DEFAULT_HEADLESS_COMMAND = "npx -y @roberttlange/headless"

TRIVIAL_PROMPT = "What is 2 + 2? Reply in one short sentence."

# Same prompts as headless_fallback_example.py (inlined so this tool stays
# standalone for manual sessions).
SYSTEM_PROMPT = """You are an expert mathematician and Python programmer specializing
in circle packing problems and computational geometry. The target task is to improve
a constructor for packing 26 circles in a unit square, maximizing the sum of radii."""

USER_PROMPT = """We are evolving a Python solution for packing 26 circles in a unit
square. The current baseline places one center circle, 8 circles in an inner ring,
and 16 circles in an outer ring, then computes maximum non-overlapping radii.

Suggest one concrete improvement to the construction. Return a compact Shinka-style
response with these exact sections:

<NAME>
a_short_patch_name
</NAME>

<DESCRIPTION>
one paragraph explaining the geometric idea
</DESCRIPTION>

```python
# only include replacement code for construct_packing() and helper functions
```

Keep the response concise, deterministic, and directly implementable."""


@dataclass
class StageResult:
    name: str
    command: str
    elapsed: float = 0.0
    time_to_first_output: float | None = None
    returncode: int | None = None
    timed_out: bool = False
    got_usage_json: bool = False
    content_chars: int = 0
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    skipped: bool = False

    @property
    def verdict(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.timed_out:
            return "TIMEOUT"
        if self.error or self.returncode != 0:
            return "FAIL"
        return "PASS"


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_streamed(
    cmd: list[str],
    *,
    timeout: float,
    label: str,
    log_path: Path,
    env: dict[str, str] | None = None,
    use_shell: bool = False,
    live: bool = True,
) -> StageResult:
    """Run one subprocess, streaming output with timestamps, killing the
    entire process group on timeout (npx spawns codex/claude as grandchildren
    which plain subprocess timeouts would orphan)."""
    result = StageResult(name=label, command=shlex.join(cmd))
    chunks: list[tuple[float, str, str]] = []
    lock = threading.Lock()
    start = time.monotonic()

    def reader(stream, tag: str) -> None:
        for line in iter(stream.readline, ""):
            now = time.monotonic() - start
            with lock:
                if result.time_to_first_output is None:
                    result.time_to_first_output = now
                chunks.append((now, tag, line.rstrip("\n")))
            if live:
                print(f"    [+{now:7.1f}s {tag}] {line.rstrip()}")
        stream.close()

    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,  # own process group -> killpg reaches codex
    )
    if use_shell:
        proc = subprocess.Popen(
            shlex.join(cmd), shell=True, executable="/bin/sh", **popen_kwargs
        )
    else:
        proc = subprocess.Popen(cmd, **popen_kwargs)

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, "OUT"), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, "ERR"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result.timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    for thread in threads:
        thread.join(timeout=5)

    result.elapsed = time.monotonic() - start
    result.returncode = proc.returncode

    stdout = "\n".join(line for _, tag, line in chunks if tag == "OUT")
    stderr = "\n".join(line for _, tag, line in chunks if tag == "ERR")
    result.stdout_tail = _tail(stdout)
    result.stderr_tail = _tail(stderr)

    # Did headless finish properly? Its last stdout line is the usage JSON.
    out_lines = [line for line in stdout.splitlines() if line.strip()]
    if out_lines:
        try:
            payload = json.loads(out_lines[-1])
            result.got_usage_json = isinstance(payload, dict) and "usage" in payload
            result.content_chars = len("\n".join(out_lines[:-1]).strip())
        except json.JSONDecodeError:
            result.content_chars = len("\n".join(out_lines).strip())
    if result.timed_out:
        result.error = f"timed out after {timeout:.0f}s"
    elif proc.returncode != 0:
        result.error = _tail(stderr, 5) or f"exit code {proc.returncode}"

    log_path.write_text(
        f"$ {result.command}\n\n"
        + "\n".join(f"[+{t:7.1f}s {tag}] {line}" for t, tag, line in chunks)
        + f"\n\n[verdict] {result.verdict} elapsed={result.elapsed:.1f}s\n",
        encoding="utf-8",
    )
    return result


def write_prompt(debug_dir: Path, stage: str, system_msg: str, user_msg: str) -> Path:
    prompt_dir = debug_dir / "headless_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / f"{stage}.md"
    path.write_text(
        "# System Instructions\n\n"
        f"{system_msg}\n\n"
        "# User Request\n\n"
        f"{user_msg}\n",
        encoding="utf-8",
    )
    return path


def headless_cmd(
    prefix: list[str],
    agent: str,
    prompt_path: Path,
    work_dir: Path,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> list[str]:
    cmd = [
        *prefix,
        agent,
        "--prompt-file",
        str(prompt_path),
        "--work-dir",
        str(work_dir),
        "--allow",
        "read-only",
        "--usage",
    ]
    if model:
        cmd.extend(["--model", model])
    if effort:
        cmd.extend(["--reasoning-effort", effort])
    return cmd


def claude_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env


def stage_env() -> StageResult:
    """Stage 0: purely local facts. Never calls a model."""
    result = StageResult(name="0 env", command="(local inspection)", returncode=0)
    findings: list[str] = []

    for label, cmd in (
        ("node", ["node", "--version"]),
        ("codex", ["codex", "--version"]),
    ):
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, check=False
            )
            findings.append(f"{label}: {out.stdout.strip() or out.stderr.strip()}")
        except FileNotFoundError:
            findings.append(f"{label}: NOT FOUND")
            result.error = f"{label} missing"

    codex_config = Path.home() / ".codex" / "config.toml"
    inherited_effort = None
    if codex_config.exists():
        text = codex_config.read_text(encoding="utf-8", errors="replace")
        model = re.search(r'^model\s*=\s*"([^"]+)"', text, re.M)
        effort = re.search(r'^model_reasoning_effort\s*=\s*"([^"]+)"', text, re.M)
        inherited_effort = effort.group(1) if effort else None
        findings.append(
            f"~/.codex/config.toml: model={model.group(1) if model else '(unset)'} "
            f"model_reasoning_effort={inherited_effort or '(unset)'}"
        )
        if inherited_effort in ("high", "xhigh"):
            findings.append(
                f"  ** WARNING: global codex effort '{inherited_effort}' is inherited "
                "by every headless/codex call that omits ?effort=. This alone can "
                "push a code-shaped prompt past a 180s timeout. **"
            )
    else:
        findings.append("~/.codex/config.toml: absent")

    for label, path in (
        ("codex auth", Path.home() / ".codex" / "auth.json"),
        ("claude creds", Path.home() / ".claude" / ".credentials.json"),
    ):
        findings.append(f"{label}: {'present' if path.exists() else 'ABSENT'}")

    result.stdout_tail = "\n".join(findings)
    print("\n".join(f"    {line}" for line in findings))
    return result


def diagnose(results: dict[str, StageResult]) -> list[str]:
    """Compare stage outcomes and name the most likely root cause."""
    notes: list[str] = []

    def r(key: str) -> StageResult | None:
        hit = results.get(key)
        return hit if hit and not hit.skipped else None

    cli = r("1 cli-check")
    if cli and cli.verdict != "PASS":
        notes.append(
            "headless --check failed: install/network problem with the headless "
            "CLI itself. Nothing downstream is meaningful until this passes."
        )
        return notes

    triv, triv_low = r("2 codex-trivial"), r("3 codex-trivial-low")
    task_low, task = r("4 codex-task-low"), r("5 codex-task")
    task_wd, claude = r("6 codex-task-workdir"), r("7 claude-task")

    if triv and triv.verdict == "TIMEOUT" and triv_low and triv_low.verdict == "PASS":
        notes.append(
            "Trivial prompt times out at inherited effort but passes at low: "
            "the ~/.codex/config.toml reasoning effort dominates even simple "
            "calls. Root cause: inherited effort, not prompt complexity."
        )
    if triv and triv.verdict in ("TIMEOUT", "FAIL") and triv_low and triv_low.verdict in ("TIMEOUT", "FAIL"):
        notes.append(
            "Trivial prompt fails regardless of effort: codex endpoint/auth/"
            "network problem, independent of the circle-packing prompt."
        )

    if task_low and task_low.verdict == "PASS" and task and task.verdict in ("TIMEOUT", "FAIL"):
        notes.append(
            f"Task prompt passes at effort=low ({task_low.elapsed:.0f}s) but not at "
            "inherited effort: root cause is the inherited high/xhigh reasoning "
            "effort on code-shaped prompts. Fix: pass ?effort=... explicitly "
            "(headless/codex@gpt-5.5?effort=medium) or lower the global default."
        )
    if task and task.verdict == "PASS" and task.elapsed > 180:
        notes.append(
            f"The failing call simply needs {task.elapsed:.0f}s — more than the "
            "mini-script's 180s default but within Shinka's production 1200s. "
            "Fix: align the example's default timeout with SHINKA_HEADLESS_TIMEOUT."
        )
    if task and task_wd and task_wd.verdict == "PASS" and task.verdict == "PASS" and task.elapsed - task_wd.elapsed > 60:
        notes.append(
            f"Real task workdir is {task.elapsed - task_wd.elapsed:.0f}s faster: "
            "weak workdir context contributes (agent wanders without files)."
        )
    if claude and claude.verdict == "FAIL" and re.search(
        r"limit|quota|resets", claude.error + claude.stdout_tail, re.I
    ):
        notes.append(
            "Claude leg failed on a subscription/session limit — a separate "
            "failure mode from the codex timeout (as the report noted)."
        )

    if not notes:
        notes.append(
            "No decisive divergence between stages. Compare per-stage elapsed and "
            "time-to-first-output in the report JSON; if all codex stages pass "
            "quickly here, the original failure was environmental (load, network, "
            "or session state at the time)."
        )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--headless-command",
        default=os.getenv("SHINKA_HEADLESS_COMMAND", DEFAULT_HEADLESS_COMMAND),
    )
    parser.add_argument("--codex-agent", default="codex")
    parser.add_argument("--claude-agent", default="claude")
    parser.add_argument("--trivial-timeout", type=float, default=240.0)
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=float(os.getenv("SHINKA_HEADLESS_TIMEOUT", 1200)),
        help="Timeout for task-shaped stages (default: production parity, 1200s)",
    )
    parser.add_argument(
        "--debug-dir", type=Path, default=EXAMPLE_DIR / ".headless_debug"
    )
    parser.add_argument(
        "--stages", default=None, help="Comma-separated stage numbers, e.g. 0,1,2,3"
    )
    parser.add_argument(
        "--quick", action="store_true", help="Run stages 0-4 only (no long calls)"
    )
    parser.add_argument("--no-live", action="store_true")
    args = parser.parse_args()

    live = not args.no_live
    prefix = shlex.split(args.headless_command)
    debug_dir = args.debug_dir
    debug_dir.mkdir(parents=True, exist_ok=True)

    if args.stages:
        selected = {int(s) for s in args.stages.split(",")}
    elif args.quick:
        selected = {0, 1, 2, 3, 4}
    else:
        selected = set(range(8))

    results: dict[str, StageResult] = {}

    def record(result: StageResult) -> StageResult:
        results[result.name] = result
        print(
            f"  => {result.verdict} ({result.elapsed:.1f}s"
            + (
                f", first output at {result.time_to_first_output:.1f}s"
                if result.time_to_first_output is not None
                else ""
            )
            + ")"
        )
        return result

    def banner(num: int, title: str) -> bool:
        wanted = num in selected
        print(f"\n=== Stage {num}: {title} {'' if wanted else '(skipped)'} ===")
        if not wanted:
            results[f"{num} {title.split()[0]}"] = StageResult(
                name=f"{num} {title.split()[0]}", command="", skipped=True
            )
        return wanted

    if banner(0, "env — local facts, no model calls"):
        record(stage_env())

    if banner(1, "cli-check — headless --check (npx resolution + CLI health)"):
        record(
            run_streamed(
                [*prefix, "--check"],
                timeout=120,
                label="1 cli-check",
                log_path=debug_dir / "stage1_cli_check.log",
                live=live,
            )
        )
        if results["1 cli-check"].verdict != "PASS":
            print("\nCLI check failed — aborting model stages.")
            for note in diagnose(results):
                print(f"* {note}")
            return 1

    trivial_prompt = write_prompt(
        debug_dir,
        "trivial",
        "You are a CLI health-check endpoint. Return plain text only.",
        TRIVIAL_PROMPT,
    )
    task_prompt = write_prompt(debug_dir, "task", SYSTEM_PROMPT, USER_PROMPT)

    codex_stages = [
        (2, "codex-trivial (inherited effort — mirrors the failing setup)",
         trivial_prompt, debug_dir, None, args.trivial_timeout),
        (3, "codex-trivial-low (--reasoning-effort low)",
         trivial_prompt, debug_dir, "low", args.trivial_timeout),
        (4, "codex-task-low (circle-packing prompt, effort low)",
         task_prompt, debug_dir, "low", args.task_timeout),
        (5, "codex-task (circle-packing prompt, inherited effort — THE failing call)",
         task_prompt, debug_dir, None, args.task_timeout),
        (6, "codex-task-workdir (real circle_packing dir as --work-dir)",
         task_prompt, EXAMPLE_DIR, None, args.task_timeout),
    ]
    for num, title, prompt, work_dir, effort, timeout in codex_stages:
        if banner(num, title):
            record(
                run_streamed(
                    headless_cmd(
                        prefix, args.codex_agent, prompt, work_dir, effort=effort
                    ),
                    timeout=timeout,
                    label=f"{num} {title.split()[0]}",
                    log_path=debug_dir / f"stage{num}.log",
                    live=live,
                )
            )

    if banner(7, "claude-task (circle-packing prompt via headless/claude)"):
        record(
            run_streamed(
                headless_cmd(prefix, args.claude_agent, task_prompt, debug_dir),
                timeout=args.task_timeout,
                label="7 claude-task",
                log_path=debug_dir / "stage7.log",
                env=claude_env(),
                use_shell=True,  # parity with shinka's claude invocation path
                live=live,
            )
        )

    print("\n=== Summary ===")
    for result in results.values():
        if result.skipped:
            continue
        print(
            f"  {result.name:<24} {result.verdict:<8} {result.elapsed:7.1f}s"
            + (
                f"  first-output {result.time_to_first_output:6.1f}s"
                if result.time_to_first_output is not None
                else ""
            )
            + (f"  usage-json={'yes' if result.got_usage_json else 'no'}")
        )

    print("\n=== Diagnosis ===")
    for note in diagnose(results):
        print(f"* {note}")

    report_path = debug_dir / "report.json"
    report_path.write_text(
        json.dumps(
            {
                name: {**vars(result), "verdict": result.verdict}
                for name, result in results.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nFull logs and report: {debug_dir}/ (report.json + stage*.log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
