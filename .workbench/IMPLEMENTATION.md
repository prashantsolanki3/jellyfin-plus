# Implementation (test stage, round 1)

Reviewed the working-tree diff against `.workbench/PLAN.md` and its review (configPage.html scope defect
is addressed: `withheldText()` groups by served reason; harness line 911 asserts on a rig without the marker).

## Run here (real output)

- `python3 scripts/test_patch_stack.py` -> PASS (60 of 90 old-side lines pinned), PASS (checker fails on bad input), SKIP (no --tree).
- `python3 -m py_compile scripts/test_patch_stack.py` -> no output.
- `pre-commit run --files ...` -> `InvalidConfigError: .pre-commit-config.yaml is not a file` (the repo has none).
- `git diff --stat`: 13 files changed, 381 insertions, 96 deletions, plus new `scripts/test_patch_stack.py` and `src/patcher/CachedProbe.cs`.

## Read-through against Acceptance

- gu_inputs.h: hard errors back under `#ifndef GU_NO_NVOF`; only NVOFA init soft; `cuda_on` and `gu_cuda_open` removed. OK by reading.
- UpscaleEngine: `NoCudaGameNow` = built marker on Intel and `Fsr2Probe.Peek()?.Ok == true`; probe started from `PatcherHost.Apply` only when the marker is present; withheld reason distinct. OK by reading.
- build-ffmpeg.sh: marker removed on every build, written only for WITH_FSR2 + openvino. OK.
- 0016: `+` starts 4156 and 7363. Checked only by the python reconstruction.

## NOT run (cannot be, in this shell)

`scripts/test-gu-inputs.sh`, `scripts/test-gpu-vendor.sh`, the C# build, `git apply` of 0016 on real FFmpeg 8.1.2,
the probe on a real Intel GPU, the config page render. No cluster check applies. Verdict BLOCKED, not APPROVE.
