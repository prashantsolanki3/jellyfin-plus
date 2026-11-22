# Implementation: BLOCKED, nothing changed (test stage, round 1)

The plan's step 0 (merge fork `main` into this branch, merge commit, no rebase, excluding
`.github/workflows/build-arc-image.yml` and `.workbench`) has not happened and cannot be done from this session.
The implement stage stopped there, and the test stage re-checked.

## Test-stage results
- `git diff --stat HEAD`: empty. `git log --oneline HEAD..main`: 30+ commits still missing (main 9ad61d0).
- `scripts/bench-intel.sh:239-240` still reads HHMM into `[[ $hm -ge 0100 ... ]]` (octal blocker unfixed).
- `python3 scripts/test_bench_intel.py`: 74 checks passed, 0 failed. This is the pre-merge copy; none of the new checks exist.
- `python3 scripts/test_build_ffmpeg_vendor.py`: 114 passed, 0 failed, mutants 19 caught of 19.
- `python3 scripts/test_install_shaders.py`: file not found (it arrives with the merge).
- No live cluster check applies; nothing was run against an Arc card or a live Jellyfin.
- No `PR_BODY.md` written: there is no diff to describe.

## What a process with merge rights must do first
1. `git merge main` on `agent/workbench-d9374ed10c974cc1` (merge commit).
2. Remove `.github/workflows/build-arc-image.yml` and `.workbench` from the result.
3. Re-run the implement stage (`.workbench/PLAN.md` steps 1-2: tests first, then fixes), then this test stage.

Item 5 (webinject `dpkg-query` fallback) is already fixed on fork main by #11 and arrives with that merge.
