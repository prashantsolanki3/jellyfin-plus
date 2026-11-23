# Implementation: rev-1j1w0

Files: `scripts/bench-intel.sh`, `scripts/test_bench_intel.py` (the plan's list, nothing else).

## Order

1. Tests first in `scripts/test_bench_intel.py`: fake curl (Authorization-header check, `-w` status, `/System/Info`,
   `items_status`/`items_empty`/`info_status` knobs), ffmpeg stub (argv log, `FAKE_XPSNR_FAIL`), ffprobe stub (per-clip
   width/height), `token_failures` extended, new `size_failures`, `xpsnr_failures`, `item_failures`,
   `test_token_rejected`, and four mutants (`legacy-header`, `xpsnr-zero`, `item-error-swallowed`, `dims-nominal`).
   The first run against the unmodified script aborted on the `legacy-header` mutant ("pattern not in script"), i.e. red.
2. Then `scripts/bench-intel.sh`.

## Script changes

- `api()` sends `Authorization: MediaBrowser Token="<key>"` on curl's stdin config (curl line unchanged).
- `api_get` (sets `API_CODE`/`API_BODY`), `have_api` probes `/System/Info` and sets `API_WHY` (rejected on 401/403),
  `no_api` records FAIL "token rejected" or the old SKIP. Rows 1, 6, 10, 12 use it; row 5 and `shader_row` e2e FAIL on a
  rejected token.
- `item_id` sets `ITEM_ID`/`ITEM_ERR` and returns 1 on an HTTP error (`item lookup HTTP <code>`); an HTTP 200 with no match
  still ends in "not in the Jellyfin library". Called directly, never in `$(...)`.
- `scaled_dims` derives output sizes from ffprobe width/height of the source clip; rows 2, 3, 4, 8, 11 use it.
  `--dry-run` keeps the nominal sizes (no clips to probe), so the goldens are unchanged.
- `xpsnr_y` returns 1 with no output on failure; `xpsnr_delta` propagates; rows 2, 3, 4, 8, 9 record FAIL "xpsnr failed".

## Tests

`python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed` (baseline before the change: 208).

tdd: required, followed. I could not run the new checks against the unmodified script as a whole because the mutant
assertion aborts first; the four new mutants restore each old behaviour and are each caught.

## Test stage (round 1)

- `python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed` (re-run twice).
- Found: `row_9`'s `printf "%.1f\n", b > 0 ? ... : 0` in awk is an output redirect to a file named `0`: the gain was never
  printed and every test run wrote a stray `./0` in the cwd. Parenthesised the ternary (one line, same file). The stray
  untracked `0` at the repo root predates this and cannot be deleted from this session; it must not be committed.
- Live: `kubectl get pods -n media` shows jellyfin-55c59fcb97-vsgvh Running (two older replicas Unknown); the service
  `jellyfin.media.svc.cluster.local:8096` is not reachable from this session (curl exit 7), so the 12.4 header
  behaviour was not re-checked live.

## Round 2

- The wrapper's REJECT lists `scripts/test-gpu-resident.sh`, `test-gpu-vendor.sh`, `test-gu-inputs.sh`,
  `test-hwmap-bridge.sh` and `test-vf-ort.sh`, each run with `/opt/jellyfin` (a directory, not an ffmpeg binary or
  tarball) and failing on a missing GPU/ffmpeg/tarball. None of them reads `bench-intel.sh` and none is in the plan's
  `test_commands`; the diff touches neither them nor the code they exercise. No change made for them; they need the
  GPU host and the FFmpeg tarball, which this session does not have.
- Re-ran `python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed`. Diff: the two planned files only.
- The untracked `./0` at the repo root is the old awk-redirect artefact (see above); it is not part of the change.

## Test stage (round 2)

- `python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed`. `git diff --stat`: the two planned files only
  (280 insertions, 47 deletions).
- Live: `kubectl get pods -n media` -> jellyfin-55c59fcb97-vsgvh 1/1 Running; jellyfin-plus-bench-run6 Running (the run
  whose 401s motivated the fix). Not re-checked against a live 12.4 token.
- The untracked `./0` at the repo root is the old awk-redirect artefact and must not be committed (this session cannot
  delete it).

## Round 3

- Re-ran `python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed`. Diff: the two planned files only.
- The wrapper's harness findings (`test-gpu-resident.sh`, `test-gpu-vendor.sh`, `test-gu-inputs.sh`,
  `test-hwmap-bridge.sh`, `test-vf-ort.sh` run against `/opt/jellyfin`) are unchanged from round 2: they need the GPU
  host and the FFmpeg tarball and do not read `bench-intel.sh`. No change made for them.
- PR title shortened to fit 72 characters.

## Test stage (round 3)

- `python3 scripts/test_bench_intel.py` -> `220 checks passed, 0 failed`. `git diff --stat`: the two planned files only
  (280 insertions, 47 deletions). The run did not recreate `./0`.
- Live: `kubectl get pods -n media` -> jellyfin-55c59fcb97-vsgvh 1/1 Running; jellyfin-plus-bench-run6-czpvb 1/1 Running.
  Not re-checked against a live 12.4 token.
- The wrapper's harness findings (`test-gpu-resident.sh` etc. against `/opt/jellyfin`) are outside the plan's
  `test_commands` and the diff; they need the GPU host and the FFmpeg tarball. No change made.
- The untracked `./0` at the repo root is the old awk-redirect artefact; it must not be committed.

Not verified: bench run7 on the A380 (needs a rebuilt image and a cluster Job). `pre-commit` has no config in this checkout.
