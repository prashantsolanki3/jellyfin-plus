# Plan: -Werror fixes in the CUDA-free gu_inputs build (PR 6 follow-up)

Premise check: the three fixes named in the brief are already in this worktree, left by an earlier round (commit 84958db, notes in `.workbench/IMPLEMENTATION.md`). The paths in the brief are stale: the header is `ffmpeg/gu_inputs.h` (copied to `libavfilter/` only inside the test's scratch FFmpeg tree), and the line numbers have moved.

- `ffmpeg/gu_inputs.h` flow-warp clamp (about 535-542) and depth stabiliser clamp (about 817-824): each `if` is on its own line with its own body line. No `if (...) x; if (...)` one-liner remains.
- `ffmpeg/gu_inputs.h` `gu_depth_close` (585-591): both `AllocatorFree` results are captured in `OrtStatus *st` and released with `ReleaseStatus`, as at lines 648, 671 and 786.
- `scripts/gu-inputs-test/no_cuda.c:54`: `((unsigned)x * 73856093u) ^ ((unsigned)y * 19349663u)`; there are only two factors in the hash.

The implement stage should change nothing more unless a re-read finds a miss. Then it writes `.workbench/PR_BODY.md` for the existing diff.

The shell here has no gcc, and `bash scripts/test-gu-inputs.sh` is not an allowed command. It also needs network access to ffmpeg.org and nuget.org. So the acceptance result "47 PASS, 0 FAIL with -Werror" cannot be produced in this session. Report it as NOT RUN. The orchestrator's Docker run (gcc 13.3) is where it must be confirmed. Step 3 (the default CUDA/NVOFA compile against stub headers) is also run with -Werror and may hold warnings that step 1 never reached.

Scope guard: do not remove -Werror, and do not touch `scripts/test-gu-inputs.sh`, the patches or any other file. The commit subject belongs to the trusted process: "GPU vendors: fix -Werror in the CUDA-free gu_inputs build (PR 6)".

```yaml
{
  "goal": "Make `bash scripts/test-gu-inputs.sh` pass step 1 under gcc 13.3 -Wall -Wextra -Werror by fixing the misleading-indentation clamps and the unused AllocatorFree result in ffmpeg/gu_inputs.h, and the signed overflow in scripts/gu-inputs-test/no_cuda.c.",
  "files": ["ffmpeg/gu_inputs.h", "scripts/gu-inputs-test/no_cuda.c"],
  "test_commands": ["bash scripts/test-gu-inputs.sh"],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "bash scripts/test-gu-inputs.sh with -Werror still enabled prints 47 PASS and 0 FAIL (to be confirmed by the orchestrator's Docker run; not runnable in the workbench)",
    "No `if (...) x; if (...)` one-liner clamps remain in ffmpeg/gu_inputs.h",
    "Both GU_ORT->AllocatorFree results in gu_depth_close are captured and released with ReleaseStatus",
    "no_cuda.c texture() hash multiplies unsigned operands with u-suffixed constants",
    "-Werror is not removed from scripts/test-gu-inputs.sh"
  ],
  "premises": [
    {"id": "P1", "claim": "The sx/sy clamps in ffmpeg/gu_inputs.h are already one statement per line (flow warp at 535-542, depth stabiliser at 817-824).", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h lines 525-554 and 805-829; Grep for `if (...) x; if` found nothing.", "load_bearing": true},
    {"id": "P2", "claim": "gu_depth_close already captures and releases the OrtStatus from both AllocatorFree calls.", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h lines 580-599.", "load_bearing": true},
    {"id": "P3", "claim": "no_cuda.c:54 already uses unsigned operands with u-suffixed constants.", "label": "VERIFIED", "check": "Read scripts/gu-inputs-test/no_cuda.c lines 40-70; Grep for the constants 73856093, 19349663 and 83492791 shows only the two in line 54.", "load_bearing": true},
    {"id": "P4", "claim": "These fixes come from commit 84958db and are the only diff relative to the earlier branch.", "label": "VERIFIED", "check": "git diff a264b08 84958db --stat lists gu_inputs.h, no_cuda.c and .workbench/IMPLEMENTATION.md; git status is clean.", "load_bearing": false},
    {"id": "P5", "claim": "The test cannot be run in this workbench (no gcc, no allowed command for the script, no external network).", "label": "INFERRED", "check": "IMPLEMENTATION.md records exit 2 'gcc not found'; the allowed shell commands exclude `bash scripts/...`. Settle by the orchestrator's Docker run.", "load_bearing": false},
    {"id": "P6", "claim": "After these three fixes no further -Werror diagnostic stops step 1 or step 3.", "label": "INFERRED", "check": "Run bash scripts/test-gu-inputs.sh in Docker (ubuntu:24.04, gcc 13.3) and read the compiler output.", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "A way to prove the header compiles clean and runs without CUDA", "checked": "existing scripts/test-gu-inputs.sh (steps 1-4) and scripts/gu-inputs-test/no_cuda.c in this repo", "verdict": "use", "reason": ""}
  ]
}
```
