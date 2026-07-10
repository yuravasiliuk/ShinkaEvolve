#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
from urllib.parse import parse_qs


DEFAULT_HEADLESS_COMMAND = "npx -y @roberttlange/headless"

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


def _command_prefix(raw_command: str) -> list[str]:
    command = shlex.split(raw_command.strip())
    if not command:
        raise ValueError("headless command cannot be empty")
    return command


def _parse_model(model: str) -> tuple[str, str | None, str | None]:
    body = model.removeprefix("headless/")
    route, _, query = body.partition("?")
    agent, separator, agent_model = route.partition("@")
    effort = parse_qs(query).get("effort", [None])[0]
    return agent, agent_model if separator else None, effort


def _run(
    cmd: list[str], timeout: float, agent: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = None
    if agent == "claude":
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        return subprocess.run(
            shlex.join(cmd),
            shell=True,
            executable="/bin/sh",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _check_cli(command_prefix: list[str], timeout: float) -> None:
    completed = _run([*command_prefix, "--check"], timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"exit code {completed.returncode}")


def _write_prompt(work_dir: Path, system_prompt: str, user_prompt: str) -> Path:
    prompt_dir = work_dir / "headless_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / "circle_packing_fallback.md"
    prompt_path.write_text(
        "# System Instructions\n\n"
        f"{system_prompt}\n\n"
        "# User Request\n\n"
        f"{user_prompt}\n",
        encoding="utf-8",
    )
    return prompt_path


def _content_from_stdout(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    try:
        json.loads(lines[-1])
        lines = lines[:-1]
    except json.JSONDecodeError:
        pass
    return "\n".join(lines).strip()


def _query_model(
    *,
    command_prefix: list[str],
    model: str,
    work_dir: Path,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> str:
    agent, agent_model, effort = _parse_model(model)
    prompt_path = _write_prompt(work_dir, system_prompt, user_prompt)
    cmd = [
        *command_prefix,
        agent,
        "--prompt-file",
        str(prompt_path),
        "--work-dir",
        str(work_dir),
        "--allow",
        "read-only",
        "--usage",
    ]
    if agent_model:
        cmd.extend(["--model", agent_model])
    if effort:
        cmd.extend(["--reasoning-effort", effort])

    completed = _run(cmd, timeout=timeout, agent=agent)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"exit code {completed.returncode}")

    content = _content_from_stdout(completed.stdout)
    if not content:
        raise RuntimeError(f"{model} returned empty text")
    return content


def _print_block(title: str, text: str) -> None:
    print(f"\n--- {title} ---")
    print(text)
    print(f"--- end {title} ---")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one circle-packing prompt with Claude, falling back to Codex."
    )
    parser.add_argument(
        "--headless-command",
        default=os.getenv("SHINKA_HEADLESS_COMMAND", DEFAULT_HEADLESS_COMMAND),
    )
    parser.add_argument("--primary-model", default="headless/claude")
    parser.add_argument("--fallback-model", default="headless/codex")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(__file__).resolve().parent / ".headless_fallback_example",
    )
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    command_prefix = _command_prefix(args.headless_command)
    _check_cli(command_prefix, timeout=args.timeout)

    _print_block("system prompt", SYSTEM_PROMPT)
    _print_block("user prompt", USER_PROMPT)

    for model in (args.primary_model, args.fallback_model):
        print(f"\nTrying {model}...")
        try:
            response = _query_model(
                command_prefix=command_prefix,
                model=model,
                work_dir=args.work_dir,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=USER_PROMPT,
                timeout=args.timeout,
            )
        except Exception as exc:
            print(f"[FAIL] {model}: {exc}")
            continue

        print(f"[OK] used model: {model}")
        _print_block("model response", response)
        return 0

    print("\nNo model returned usable text.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
