# Implementation, round 1

## Order written
1. `scripts/test_bench_intel.py`: `driver_failures` / `driver_checks` (called from `dockerfile_checks`), with mutants.
   Run before the Dockerfile change: `200 checks passed, 2 failed` (the driver check and the early-check mutant; the other mutants
   passed vacuously because the unmodified file already failed). Baseline before any edit was 189 passed, 0 failed.
2. `docker/intel/Dockerfile` (final stage): a RUN that writes a `non-free` deb822 source, `apt-get update`, installs
   `mesa-vulkan-drivers intel-media-va-driver-non-free libigdgmm12` with `--no-install-recommends`, removes the source and the lists;
   after `RUN ldconfig`, an acceptance RUN (an Intel ICD JSON must exist; `ldd` of each ICD's `library_path` and of
   `iHD_drv_video.so` must show no `not found`) with the "vainfo/vulkaninfo need a GPU" comment.
3. `docs/architecture/GPU_VENDORS.md` 5.8: a paragraph on why, and that non-free is enabled for that one RUN.
4. One regex bug in my own test (`final_runs` swallowed the line-continuation backslash) fixed; then `202 checks passed, 0 failed`.

## Tests
`python3 scripts/test_bench_intel.py`: fail first (200 passed, 2 failed), then `202 checks passed, 0 failed`.
New checks: 1 positive, 3 package mutants, free-iHD mutant, non-free-left-behind mutant, no-ICD-check, no-ldd-iHD,
no-ldd-ICD-lib, no-not-found-gate, no-GPU-caveat, check-before-install, and the spec-page check (13 in all).

## tdd
Required and followed. No `tdd: n/a`.

## Test stage, round 1 (final results)
- `python3 scripts/test_bench_intel.py` -> `202 checks passed, 0 failed`; `python3 -m py_compile scripts/test_bench_intel.py` clean.
- `pre-commit run --files ...` -> `.pre-commit-config.yaml is not a file` (checkout has none); not runnable.
- Live: `kubectl logs jellyfin-plus-bench-run3-q5fc2 -n media` still prints `row 2/3/4 FAIL ffmpeg failed` and
  `row 9 FAIL hardware decode chain failed`, so the symptom is current; the fix is unproven until the Arc bench runs on a rebuilt image.
- Diff touches only the three planned files; no build dispatched, nothing posted upstream.

## Round 2
No code change. The round-1 wrapper findings are five GPU/ffmpeg harnesses (`test-gpu-resident.sh`, `test-gpu-vendor.sh`,
`test-gu-inputs.sh`, `test-hwmap-bridge.sh`, `test-vf-ort.sh`) run with `/opt/jellyfin`, which is a directory here, not the ffmpeg
tarball, binary or GPU they need. They exercise none of the three files in this diff (Dockerfile, its Python test, a doc), and this
sandbox has no GPU or ffmpeg build, so they cannot pass here and were not changed. Re-ran `python3 scripts/test_bench_intel.py` -> `202 checks passed, 0 failed`.

## Test stage, round 2 (final results)
- `python3 scripts/test_bench_intel.py` -> `202 checks passed, 0 failed`; `python3 -m py_compile scripts/test_bench_intel.py` clean.
- `pre-commit run --files ...` -> `.pre-commit-config.yaml is not a file`; not runnable in this checkout.
- Live: `kubectl logs jellyfin-plus-bench-run3-q5fc2 -n media` prints `row 2/3/4 FAIL ffmpeg failed`, `row 9 FAIL hardware decode chain failed`,
  row 10 SKIP (no Jellyfin API). The symptom is current; the fix is unproven until the Arc bench runs on a rebuilt image.
- The five `/opt/jellyfin` harnesses named in the round-1 wrapper finding are not in the plan's `test_commands`, need an ffmpeg build/GPU, and
  exercise none of the three changed files; not run here.
- Diff touches only the three planned files. Acceptance and Do-not checked: no build dispatched, nothing posted upstream.

## Round 3
No code change. The wrapper findings repeat the five `/opt/jellyfin` GPU/ffmpeg harnesses; they are not in the plan's `test_commands`
and exercise none of the three changed files. Re-ran `python3 scripts/test_bench_intel.py` -> `202 checks passed, 0 failed`; diff re-read.

## Test stage, round 3 (final results)
- `python3 scripts/test_bench_intel.py` -> `202 checks passed, 0 failed`; `python3 -m py_compile scripts/test_bench_intel.py` clean.
- `pre-commit run --files ...` -> `.pre-commit-config.yaml is not a file`; not runnable here.
- Live: `kubectl get jobs -n media` lists `jellyfin-plus-bench-run1/2/3` all `Failed 0/1`, so the symptom is current; the fix is unproven until
  the Arc bench runs on a rebuilt image.
- The five `/opt/jellyfin` harnesses are outside the plan's `test_commands`, need an ffmpeg build and a GPU, and touch none of the three changed files; not run.
- Diff touches only the three planned files (`git status`). No build dispatched, nothing posted upstream, nothing committed.

## Not done
No build, no push, no upstream post. `pre-commit run` could not run: this worktree has no `.pre-commit-config.yaml`
("is not a file"). `scripts/sync_mcp_filesystem_docs.py` does not exist in this checkout.
