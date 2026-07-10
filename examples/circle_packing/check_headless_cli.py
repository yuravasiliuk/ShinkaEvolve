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
DEFAULT_PROMPT = "What is 2 + 2? Reply in one short sentence."


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


def _write_prompt(work_dir: Path, prompt: str) -> Path:
    prompt_dir = work_dir / "headless_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / "cli_health_check.md"
    prompt_path.write_text(
        "# System Instructions\n\n"
        "You are a CLI health-check endpoint. Return plain text only.\n\n"
        "# User Request\n\n"
        f"{prompt}\n",
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


def _probe(
    *,
    command_prefix: list[str],
    model: str,
    work_dir: Path,
    prompt: str,
    timeout: float,
) -> str:
    agent, agent_model, effort = _parse_model(model)
    prompt_path = _write_prompt(work_dir, prompt)
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


def _print_result(label: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {label}: {detail}")


def _print_text_block(title: str, text: str) -> None:
    print(f"\n--- {title} ---")
    print(text)
    print(f"--- end {title} ---")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that the headless CLI and codex/claude endpoints return text."
    )
    parser.add_argument(
        "--headless-command",
        default=os.getenv("SHINKA_HEADLESS_COMMAND", DEFAULT_HEADLESS_COMMAND),
    )
    parser.add_argument("--codex-model", default="headless/codex")
    parser.add_argument("--claude-model", default="headless/claude")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(__file__).resolve().parent / ".headless_cli_check",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
    )
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    command_prefix = _command_prefix(args.headless_command)

    try:
        _check_cli(command_prefix, timeout=args.timeout)
        _print_result("headless cli", True, "availability check passed")
    except Exception as exc:
        _print_result("headless cli", False, str(exc))
        return 1

    failures = 0
    for label, model in (("codex", args.codex_model), ("claude", args.claude_model)):
        _print_text_block(f"{label} input", args.prompt)
        try:
            text = _probe(
                command_prefix=command_prefix,
                model=model,
                work_dir=args.work_dir,
                prompt=args.prompt,
                timeout=args.timeout,
            )
            _print_result(label, True, "returned text")
            _print_text_block(f"{label} response", text)
        except Exception as exc:
            failures += 1
            _print_result(label, False, str(exc))

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
