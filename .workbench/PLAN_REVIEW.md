# Plan review: rev-1j1w0 (round 1)

I re-ran each check myself. Baseline: `python3 scripts/test_bench_intel.py` prints `208 checks passed, 0 failed` on the unmodified tree. `git status` is clean, so the plan is not yet implemented.

## Premises

- P1 VERIFIED. `bench-intel.sh:364` sends `X-Emby-Token`. `have_api` (line 366) probes only the unauthenticated `/System/Info/Public`. A wrong token therefore passes the probe.
- P2 VERIFIED for the header format. `web/src/controller/image-preload.js:60-61` uses `Authorization: MediaBrowser Token="..."` and notes that legacy headers are off by default. I have no beads tool in this stage, so I could not re-read rev-1j1w0 or the owner's run6 401 logs. The plan says the same. This does not change the fix: the header format is verified in-repo.
- P3 VERIFIED. `item_id` (lines 371-373) ends in `|| true`. An HTTP error becomes an empty id, and `api_stream` (line 382) reports "the clip is not in the Jellyfin library".
- P4 VERIFIED. `xpsnr_y` (line 349) does `echo 0` on a failed run. It also prints `v + 0 = 0` when no `XPSNR y:` line parses (line 350). `shader_row` line 442 does `|| d=0`, so a failed xpsnr prints `xpsnr_delta=+0.00`.
- P5 VERIFIED. The `EXACT` goldens (test lines 113-128) use the nominal `1920x1080` and `3840x2160` constants. The plan keeps those for `--dry-run` and derives sizes only in real runs, which is consistent.
- P6 INFERRED, non-load-bearing, accepted. The factors 3x, 1.5x and 2x, even-rounded, give 640x286, 1280x572 and 1920x858. They cannot be settled without ffprobe on the bench pod.
- P7 VERIFIED. Line 46 sets `set -euo pipefail`, and rows are called plainly at lines 695-698. A failing `id=$(item_id ...)` would abort the run, so calling `item_id` directly and setting globals is the right design.
- P8 VERIFIED. `FAKE_CURL`, `FFMPEG_STUB`, `FFPROBE_STUB` and `World` are in the test file at lines 327-489. They can be extended as planned. The ffprobe case pattern `*height,codec_name*` does not collide with the new `stream=width,height` pattern.
- P9 VERIFIED. `X-Emby-Token` appears only at `bench-intel.sh:364`, the test mutant at line 576, and a comment in `image-preload.js`.

## Scope and TDD

- `files` (`bench-intel.sh` and `test_bench_intel.py`) is the whole change. No mirrored doc is involved, and the other `bench-intel` references are prose or build files. The `.workbench/PR_BODY.md` file is separate.
- `tdd: required` is honest. Each listed test targets a real bug:
  - the 401 from a wrong auth header
  - a 500 reported as "not in the library"
  - xpsnr failure scored as 0 dB
  - wrong output size against a letterboxed master
- The 16:9-clip case guards against a hard-coded 858.
- The `legacy-header` and `echo 0` mutants are behaviour checks, not string checks.

## Notes for the implementer (not blockers)

1. The existing test at line 1145 uses `fail_all` ("answers only `/System/Info/Public`") and expects rows 3, 4, 5, 6, 10 and 12 to FAIL. The fake curl must serve `/System/Info` before the `fail_all` branch. Otherwise `have_api` goes `unreachable`, rows 6, 10 and 12 become SKIP, and that test breaks.
2. `xpsnr_delta` is called as `d=$(xpsnr_delta ...) || ...`, which runs with errexit disabled inside. The failure must be propagated explicitly with `|| return 1` after each `xpsnr_y`.
3. In row 8 (line 568), `d=$(xpsnr_delta ...)` has no `||` today. Under `set -e` a failure would kill the script, so it needs `|| { record 8 FAIL "xpsnr failed"; return; }`.
4. Acceptance item 2 (run7 on the A380) cannot run in this session and must be reported unverified, as the plan says.

VERDICT: APPROVE
