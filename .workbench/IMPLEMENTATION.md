# Implementation (round 1, test stage)

Changes in the working tree, checked against the brief with `git diff`:

- `ffmpeg/gu_inputs.h`: both `sx`/`sy` clamp blocks (flow warp, depth stabiliser) now have each `if` on its own line. This clears `-Wmisleading-indentation`. No other `if (...) x; if (...)` one-liner remains in the file.
- `ffmpeg/gu_inputs.h`, `gu_depth_close`: `AllocatorFree` returns an `OrtStatus *`, which is kept and released with `GU_ORT->ReleaseStatus`, as at lines 648, 671 and 786. This clears `-Wunused-result`.
- `scripts/gu-inputs-test/no_cuda.c:54`: `((unsigned)x * 73856093u) ^ ((unsigned)y * 19349663u)`. This removes the signed overflow.

`-Werror` is still on in `scripts/test-gu-inputs.sh` (steps 1 and 3).

## Test results

- `bash scripts/test-gu-inputs.sh`: NOT RUN. It exited 2 with `test-gu-inputs: gcc not found`; this workbench has no gcc and the shell cannot install one. The 47 PASS, 0 FAIL result with `-Werror` is therefore unconfirmed here.
- Orchestrator (Docker, arm64, gcc 13.3, a264b08): test-gpu-vendor.sh 1111 passed, 0 failed; test-vf-ort.sh 26/26; the 0016 patch applies; test-gu-inputs.sh with `-Werror` removed passed 47 checks.
- Live checks: none apply. The change is a host-side C header and a test file, and it touches no cluster state.

## Not verified

- A `-Werror` run of `test-gu-inputs.sh`, including step 3 (the default CUDA/NVOFA compile against stub headers), which may hold warnings that step 1 never reached.
- Any run on Intel or NVIDIA hardware.
