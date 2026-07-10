#!/usr/bin/env python3
import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from shinka.core import ShinkaEvolveRunner, EvolutionConfig
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig


def install_null_response_guard() -> None:
    """Terminate the whole run the moment any LLM query yields a null/empty
    message, instead of letting retry loops keep spending quota on a broken
    channel. Exits the process with code 3."""
    import shinka.llm.llm as llm_mod
    import shinka.llm.query as query_mod

    logger = logging.getLogger("null_response_guard")

    def _terminate(reason: str) -> None:
        message = f"NULL RESPONSE GUARD: {reason} — terminating run."
        logger.error(message)
        print(f"\n{message}", flush=True)
        os._exit(3)

    def _checked(result):
        content = getattr(result, "content", None) if result is not None else ""
        if result is not None and not (content or "").strip():
            _terminate(
                f"empty content from {getattr(result, 'model_name', '?')}"
            )
        return result

    def _is_null_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "content was empty" in text
            or "stdout was empty" in text
            or "returned empty text" in text
        )

    orig_query = query_mod.query
    orig_query_async = query_mod.query_async

    def guarded_query(*args, **kwargs):
        try:
            return _checked(orig_query(*args, **kwargs))
        except ValueError as exc:
            if _is_null_error(exc):
                _terminate(str(exc))
            raise

    async def guarded_query_async(*args, **kwargs):
        try:
            return _checked(await orig_query_async(*args, **kwargs))
        except ValueError as exc:
            if _is_null_error(exc):
                _terminate(str(exc))
            raise

    # llm.py binds `from .query import query, query_async` at import time,
    # so patch both the source module and the bound references.
    for module in (query_mod, llm_mod):
        module.query = guarded_query
        module.query_async = guarded_query_async
    logger.info("Null-response guard installed (exit code 3 on null message).")

search_task_sys_msg = """You are an expert mathematician specializing in circle packing problems and computational geometry. The best known result for the sum of radii when packing 26 circles in a unit square is 2.635.

Key directions to explore:
1. The optimal arrangement likely involves variable-sized circles
2. A pure hexagonal arrangement may not be optimal due to edge effects
3. The densest known circle packings often use a hybrid approach
4. The optimization routine is critically important - simple physics-based models with carefully tuned parameters
5. Consider strategic placement of circles at square corners and edges
6. Adjusting the pattern to place larger circles at the center and smaller at the edges
7. The math literature suggests special arrangements for specific values of n
8. You can use the scipy optimize package (e.g. LP or SLSQP) to optimize the radii given center locations and constraints

Be creative and try to find a new solution better than the best known result."""


def local_eval_python() -> str:
    """Prefer the repo-local virtualenv for evaluator subprocesses."""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / ".venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main(config_path: str):
    install_null_response_guard()
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["evo_config"]["task_sys_msg"] = search_task_sys_msg
    evo_config = EvolutionConfig(**config["evo_config"])
    job_config = LocalJobConfig(
        eval_program_path="evaluate.py",
        python_executable=local_eval_python(),
        time="00:05:00",
    )
    db_config = DatabaseConfig(**config["db_config"])

    runner = ShinkaEvolveRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        max_evaluation_jobs=config.get("max_evaluation_jobs"),
        max_proposal_jobs=config.get("max_proposal_jobs"),
        max_db_workers=config.get("max_db_workers"),
        debug=False,
        verbose=True,
    )
    runner.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", type=str, default="shinka_small.yaml")
    args = parser.parse_args()
    main(args.config_path)
