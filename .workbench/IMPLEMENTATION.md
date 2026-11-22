# Implementation: keep scripts/ffmpeg-vendor.sh valid POSIX sh

Changed files: `scripts/ffmpeg-vendor.sh` (here-string replaced by `printf '%s\n' "$filters" | grep -qE ...`; header comment notes it is sourced by sh), `scripts/test_build_ffmpeg_vendor.py` (new `suite_posix`, a `BASHISMS` text scan, a here-string mutant, docstring).

Order: tests first (`suite_posix`, bashism scan, mutant), which failed on the unfixed file (173 passed, 10 failed); then the one-line fix. `tdd` is required, not n/a.

## Test results (round 2)
- `python3 scripts/test_build_ffmpeg_vendor.py` -> `checks: 183 passed, 0 failed`, `mutants: 30 caught of 30` (was 166 / 29). The dash section ran, none skipped: `dash -n`, five guard checks, two required-filter checks, one vendor-valid check, eight bashism scans.
- The new mutant "guard goes back to a here-string" is caught by 10 checks.
- `python3 -m py_compile scripts/test_build_ffmpeg_vendor.py` -> clean.
- Round 2 only re-wrapped the over-long header comment in ffmpeg-vendor.sh (comment text only).
- The wrapper's round-1 harness rejections (`test-gpu-resident.sh`, `test-gpu-vendor.sh`, `test-gu-inputs.sh`, `test-hwmap-bridge.sh`, `test-vf-ort.sh` run against `/opt/jellyfin`) concern GPU/ffmpeg-tarball harnesses this change does not touch; they cannot run in the workbench.

## TEST stage (round 2) re-run
- `python3 scripts/test_build_ffmpeg_vendor.py` -> `checks: 183 passed, 0 failed`, `mutants: 30 caught of 30`; the "ffmpeg-vendor.sh under dash" section ran (dash -n, 5 guard, 2 filter-list, 1 vendor-valid checks), nothing skipped.
- The here-string mutant (the old file's line) is caught by 10 checks, which shows the new checks fail on the unfixed form.
- `python3 -m py_compile scripts/test_build_ffmpeg_vendor.py` -> clean.
- `pre-commit run --files ...` -> no `.pre-commit-config.yaml` in the repo, nothing to run.
- Live cluster checks: none apply (a shell-script and test-only change).
- Verdict: APPROVE.

## Round 3 re-run
- P3 confirmed by the orchestrator: the workbench image has `/bin/sh -> dash` (Debian 13.7), so the dash checks run, not skip.
- `python3 scripts/test_build_ffmpeg_vendor.py` -> `checks: 183 passed, 0 failed`, `mutants: 30 caught of 30`; `python3 -m py_compile` clean. On the unfixed file (here-string mutant) 10 checks fail.
- No code change this round. The `test-*.sh` `/opt/jellyfin` harness rejections stay out of scope: they need a GPU/ffmpeg tarball and are not touched by this diff.

## TEST stage (round 3) re-run
- `python3 scripts/test_build_ffmpeg_vendor.py` -> `checks: 183 passed, 0 failed`, `mutants: 30 caught of 30`; the "ffmpeg-vendor.sh under dash" section ran (8 bashism scans, `dash -n`, 5 guard, 2 filter-list, 1 vendor-valid), nothing skipped.
- `python3 -m py_compile scripts/test_build_ffmpeg_vendor.py` -> clean.
- Acceptance: all six items met; the here-string mutant is caught by 10 checks, i.e. the new checks fail on the unfixed form. Do-not: only the two planned files changed (`git status`).
- Live cluster checks: none apply (shell-script and test-only change).
- The wrapper's `scripts/test-*.sh /opt/jellyfin` harnesses were not re-run: they need a GPU and the FFmpeg tarball, which the workbench lacks (earlier runs failed with "/opt/jellyfin: Is a directory"), and this diff touches none of them.

## Not verified
- The Arc image's own acceptance step (it is not in this repo).
