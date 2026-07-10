# Manual Test Session: Headless Codex Fallback Timeout

Follow-up to [headless_fallback_timeout_report.md](headless_fallback_timeout_report.md).
This document adds the root-cause findings that report couldn't see, a staged
debugging tool, and a step-by-step manual session with proposed fixes.

## Root Cause (evidence-based)

The report correctly concluded "one codex subprocess exceeded 180s" but ranked
the causes by guesswork. Inspecting the headless wrapper
(`~/.npm/_npx/*/node_modules/@roberttlange/headless/dist/agents.js`) and local
config pins it down:

1. **Inherited `xhigh` reasoning effort (primary).** For `--allow read-only`,
   headless runs:

   ```
   codex --sandbox read-only --ask-for-approval never --search exec \
         --model gpt-5.5 --json --skip-git-repo-check -
   ```

   It always pins the **model** (`gpt-5.5`) but only passes
   `-c model_reasoning_effort=...` when `--reasoning-effort` is given.
   `headless/codex` (no `?effort=`) therefore inherits the user-level
   `~/.codex/config.toml`, which on this machine sets
   `model_reasoning_effort = "xhigh"`. A code-generation prompt at xhigh —
   with `--search` (web search) also enabled by the wrapper — routinely runs
   for many minutes. "What is 2+2" at xhigh still answers quickly, which is
   exactly why the health check passed while the fallback timed out.

2. **180s mini-script timeout vs 1200s production timeout (amplifier).**
   `headless_fallback_example.py` defaults to 180s; Shinka's provider uses
   `TIMEOUT = 1200` (`shinka/llm/constants.py`). The mini-script cut the call
   off at 15% of the budget production would have given it.

3. **Orphaned `codex` process on timeout (side effect, also in production).**
   On `subprocess.TimeoutExpired`, Python SIGKILLs only the direct child
   (`npx`/node). The `codex` grandchild is re-parented to init and keeps
   running — burning quota and possibly holding a session. The same flaw
   exists in `shinka/llm/providers/headless.py` in both
   `_run_headless_command_sync` and `_run_headless_command_async`
   (`process.kill()` only).

   Weak workdir context (report hypothesis 4) is a real but secondary
   contributor; stage 6 below measures it.

## The Debugging Tool

[debug_headless_timeout.py](debug_headless_timeout.py) runs an ordered ladder
of probes; the first stage that diverges localizes the fault. It streams
output live with `+elapsed` timestamps (so a silent hang is visible),
records time-to-first-output, kills the whole **process group** on timeout
(no orphans), and writes `report.json` + per-stage logs to
`.headless_debug/`.

| Stage | What it isolates | Cost |
|-------|------------------|------|
| 0 env | Versions, `~/.codex/config.toml` effort, auth presence | free |
| 1 cli-check | npx resolution + headless CLI health | free |
| 2 codex-trivial | Baseline latency at **inherited** effort | ~1 trivial call |
| 3 codex-trivial-low | Same prompt at `effort=low` → effort A/B | ~1 trivial call |
| 4 codex-task-low | Circle-packing prompt at `effort=low` | 1 task call |
| 5 codex-task | **The failing call**, generous (1200s) timeout | 1 task call, possibly long |
| 6 codex-task-workdir | Same but `--work-dir` = real task dir | 1 task call |
| 7 claude-task | The other fallback leg (subscription limits) | 1 claude call |

## Manual Session, Step by Step

Run from the repo root. Stages that call models consume quota — the order
below spends the cheap probes first. Record results in the log template at
the bottom.

### Step 1 — Free local checks

```bash
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 0,1
```

Expected: stage 0 PASS and a WARNING line if `model_reasoning_effort` is
high/xhigh (it is, currently); stage 1 PASS within ~30s. If stage 1 fails,
stop — fix the headless CLI install first.

### Step 2 — Bypass all Python: direct codex, low effort

Proves the codex endpoint itself works, independent of headless and the
mini-scripts (Layer A). Uses the prompt files stage 0/1 already wrote:

```bash
time codex --sandbox read-only --ask-for-approval never exec \
  --model gpt-5.5 -c 'model_reasoning_effort="low"' --json --skip-git-repo-check - \
  < examples/circle_packing/.headless_debug/headless_prompts/trivial.md
```

Expected: JSON event lines ending with a final answer within ~60s.
If this fails, the problem is codex CLI/auth — nothing above it matters.

### Step 3 — Effort A/B on a trivial prompt

```bash
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 2,3
```

Expected: both PASS, with stage 2 (inherited xhigh) noticeably slower than
stage 3 (low). If stage 2 already times out at 240s, the inherited effort is
proven dominant without spending a single task-shaped call.

### Step 4 — Task prompt at low effort

```bash
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 4
```

Expected: PASS well under 180s. PASS here + a slow/failed stage 5 is the
smoking gun for the effort hypothesis.

### Step 5 — Reproduce the failing call with production budget

The exact call that timed out, but with 1200s and live streaming:

```bash
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 5
```

Record the elapsed time. Interpretation:
- **Completes in 180–1200s** → the original failure was purely the
  mini-script's 180s default. Fix #1 below closes it.
- **Completes < 180s now** → the original run was environmental (load,
  session state); keep fixes anyway for robustness.
- **Times out even at 1200s** → escalate: check the live stream for where
  output stalls, and run stage 6 to test the workdir hypothesis.

### Step 6 — Optional refinements

```bash
# Workdir-context contribution (compare elapsed vs stage 5)
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 6

# Claude leg (verifies the separate session-limit failure mode)
.venv/bin/python examples/circle_packing/debug_headless_timeout.py --stages 7
```

### Step 7 — Demonstrate the orphaned-codex bug (cheap, 10s)

```bash
.venv/bin/python - <<'EOF'
import shlex, subprocess, time
cmd = ("npx -y @roberttlange/headless codex "
       "--prompt-file examples/circle_packing/.headless_debug/headless_prompts/trivial.md "
       "--work-dir examples/circle_packing/.headless_debug --allow read-only --usage")
try:
    subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=5)
except subprocess.TimeoutExpired:
    print("wrapper killed at 5s; checking for orphaned codex ...")
time.sleep(2)
orphans = subprocess.run(["pgrep", "-af", "codex"], capture_output=True, text=True).stdout
print(orphans or "no orphans")
EOF
# cleanup any orphan it just demonstrated:
pkill -f "codex .*--skip-git-repo-check" 2>/dev/null; true
```

If `pgrep` lists a `codex ... exec` process after the 5s kill, the orphan bug
is confirmed for the mini-scripts *and* (same mechanism) the production
provider.

### Step 8 — Verify the fix end-to-end

After applying the proposed changes below:

```bash
.venv/bin/python examples/circle_packing/headless_fallback_example.py \
  --primary-model headless/claude \
  --fallback-model "headless/codex@gpt-5.5?effort=medium"
```

Expected: `[OK] used model: ...` with a Shinka-style `<NAME>/<DESCRIPTION>`
response, from either leg.

## Proposed Changes

### 1. `headless_fallback_example.py` — align defaults with production

```diff
-    parser.add_argument("--fallback-model", default="headless/codex")
-    parser.add_argument("--timeout", type=float, default=180.0)
+    parser.add_argument(
+        "--fallback-model", default="headless/codex@gpt-5.5?effort=medium"
+    )  # explicit effort: never inherit ~/.codex/config.toml reasoning level
+    parser.add_argument(
+        "--timeout",
+        type=float,
+        default=float(os.getenv("SHINKA_HEADLESS_TIMEOUT", 1200.0)),
+    )  # parity with shinka/llm/constants.py TIMEOUT
     parser.add_argument(
         "--work-dir",
         type=Path,
-        default=Path(__file__).resolve().parent / ".headless_fallback_example",
+        default=Path(__file__).resolve().parent,
     )
```

(The prompt artifacts still go to `<work-dir>/headless_prompts/`; using the
example dir gives the agent real task context — `initial.py`, `evaluate.py`.)

### 2. `shinka/llm/providers/headless.py` — kill the process group on timeout

Both `_run_headless_command_sync` and `_run_headless_command_async` currently
leave an orphaned `codex`/`claude` grandchild when a call times out. Sync
path sketch (async analogous via `start_new_session=True` on
`create_subprocess_*` + `os.killpg` in the `TimeoutError` handler):

```python
proc = subprocess.Popen(..., start_new_session=True)
try:
    stdout, stderr = proc.communicate(timeout=headless_timeout())
except subprocess.TimeoutExpired:
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    proc.wait()
    raise
```

Per CONTRIBUTING.md this touches the provider layer, so a PR should include
the step-7 orphan repro (before: orphan present / after: none) and a unit
test with a fake slow CLI script. Also worth adding: include elapsed seconds
and the prompt-file path in the `TimeoutError` message so future timeouts are
diagnosable from logs alone.

### 3. Docs / examples — recommend explicit effort in codex model strings

Anywhere `headless/codex` is suggested (README "Headless Agent Models",
`examples/sine_approx_headless/`), prefer the explicit form
`headless/codex@gpt-5.5?effort=medium`, with a note that bare
`headless/codex` inherits the user's `~/.codex/config.toml`
`model_reasoning_effort` — which may be `xhigh` and multiply call latency.

## Session Log

| Step | Stage(s) | Verdict | Elapsed | First output | Notes |
|------|----------|---------|---------|--------------|-------|
| 1 | 0,1 | | | | |
| 2 | direct codex | | | | |
| 3 | 2,3 | | | | |
| 4 | 4 | | | | |
| 5 | 5 | | | | |
| 6 | 6,7 | | | | |
| 7 | orphan repro | | | | |
| 8 | end-to-end | | | | |

## Cleanup

```bash
pkill -f "codex .*--skip-git-repo-check" 2>/dev/null   # stray codex, if any
rm -rf examples/circle_packing/.headless_debug          # logs + report
```
