# Headless Codex Fallback Timeout Analysis

## Scope

This report analyzes why `headless_fallback_example.py` can time out on `headless/codex` even though `check_headless_cli.py` previously confirmed that Codex can return text.

No live model calls were run for this analysis. The findings are based on the scripts, generated prompt files, Shinka's headless provider implementation, and the terminal output from the failed run.

## Short Answer

The fallback example did not run the Shinka evolution loop. It made one headless Codex subprocess call and that subprocess exceeded the script's `180s` timeout.

The successful Codex health check only proves that the local Codex CLI can be spawned and can answer a trivial prompt. It does not prove that a more agentic, code-generation-style prompt will terminate within the same timeout.

## Evidence

The health-check prompt is tiny:

- File: `examples/circle_packing/.headless_cli_check/headless_prompts/cli_health_check.md`
- Size: `145 bytes`
- User request: `What is 2 + 2? Reply in one short sentence.`

The fallback prompt is materially more complex:

- File: `examples/circle_packing/.headless_fallback_example/headless_prompts/circle_packing_fallback.md`
- Size: `897 bytes`
- User request asks for a Shinka-style circle-packing patch with:
  - `<NAME>`
  - `<DESCRIPTION>`
  - fenced Python replacement code
  - a geometric improvement proposal

The failing command from the user output was one subprocess:

```text
npx -y @roberttlange/headless codex --prompt-file ... --work-dir ... --allow read-only --usage
```

It failed as:

```text
timed out after 180.0 seconds
```

That is not an empty model response and not an evolution-loop failure. It is the Python wrapper killing a single headless Codex process after the configured timeout.

## Single Request vs Evolution Loop

`headless_fallback_example.py` does not import or invoke:

- `ShinkaEvolveRunner`
- the sampler
- patch application
- evaluation jobs
- the database
- generation loops

Its core behavior is:

1. Write one prompt file.
2. Run one headless subprocess for the primary model.
3. If that fails, run one headless subprocess for the fallback model.
4. Print the first successful response.

So the Codex timeout observed here is a single-request timeout.

## Why Health Check Passed But Fallback Timed Out

Most likely causes, ranked:

1. Prompt complexity changed the Codex CLI behavior.
   The health check asks for one short arithmetic sentence. The fallback prompt asks for a coding/geometric patch. Codex CLI is an agentic coding tool, so a coding prompt can trigger longer reasoning, repo inspection, or task-planning behavior before producing final stdout.

2. The script captures stdout only after process exit.
   Both mini scripts use `subprocess.run(..., capture_output=True)`. If Codex produced intermediate text internally, the Python script would still show nothing until the subprocess exits. On timeout, `subprocess.run` raises before the script has a normal response to parse.

3. The timeout is shorter than Shinka's production headless timeout.
   `headless_fallback_example.py` defaults to `180s`. Shinka's headless provider defaults to `TIMEOUT = 1200` seconds through `shinka/llm/constants.py`, unless `SHINKA_HEADLESS_TIMEOUT` is set. A prompt that fails in the mini script after 180 seconds might still eventually complete under Shinka's default.

4. The fallback work directory lacks the actual task files.
   The script uses:

   ```text
   examples/circle_packing/.headless_fallback_example
   ```

   as `--work-dir`. That directory contains prompt artifacts, not the circle-packing task source files. For a coding agent, this is a weak context directory. It may try to inspect context and find little useful project state. The health check does not care about workdir context.

5. The prompt asks for implementable code without giving the full current code.
   It describes the baseline but does not include `initial.py`. Codex may spend time reconstructing assumptions or trying to inspect files, especially because the requested output is a code patch.

## Claude Result Is Separate

Claude did not time out. It returned a provider/session-limit failure:

```text
You've hit your session limit · resets 10:10pm (Europe/Warsaw)
```

So Claude's failure mode is account/session quota, while Codex's failure mode in this run is subprocess timeout.

## Run Order Note

The current source of `headless_fallback_example.py` defaults to:

```text
primary-model: headless/claude
fallback-model: headless/codex
```

The terminal output supplied for the timeout shows:

```text
Trying headless/codex...
...
Trying headless/claude...
```

That means the command was likely run with reversed arguments, or from an earlier local script version. This does not change the timeout diagnosis, but it matters when interpreting whether "fallback" was actually exercised.

## What This Does Not Prove

The timeout does not prove:

- Codex CLI is unavailable.
- Codex cannot return model text.
- Shinka's full evolution loop is hanging.
- The provider returned an empty response.

It only proves:

- One headless Codex invocation for the circle-packing-style prompt did not complete within `180s` under this mini script.

## Recommended Next Experiments

These are command-level experiments; they do not require code changes.

1. Match Shinka's production timeout:

   ```bash
   python examples/circle_packing/headless_fallback_example.py \
     --primary-model headless/codex \
     --fallback-model headless/claude \
     --timeout 1200
   ```

2. Use the actual circle-packing directory as workdir:

   ```bash
   python examples/circle_packing/headless_fallback_example.py \
     --primary-model headless/codex \
     --fallback-model headless/claude \
     --work-dir examples/circle_packing \
     --timeout 300
   ```

3. If the goal is specifically "Claude first, Codex fallback", run:

   ```bash
   python examples/circle_packing/headless_fallback_example.py \
     --primary-model headless/claude \
     --fallback-model headless/codex \
     --timeout 300
   ```

4. Test prompt sensitivity by sending the same longer task through `check_headless_cli.py --prompt ...` and comparing with the short arithmetic prompt. This isolates whether the timeout is caused by prompt complexity rather than the fallback script's control flow.

5. If visibility during long calls matters, use a streaming subprocess wrapper in a future script change. The current scripts intentionally capture stdout/stderr after completion, so they cannot show partial model output before timeout.

## Practical Interpretation

The Codex endpoint is installed and can answer a trivial request. The timeout happens when the request becomes code-task-shaped. The two most plausible levers are timeout length and workdir/task context. For parity with Shinka, test with a longer timeout and a task-relevant workdir before treating the timeout as a provider failure.
