# Plan: rev-1j1w0 - bench-intel.sh auth header, item lookup, output sizes

Scope is `scripts/bench-intel.sh` and `scripts/test_bench_intel.py` only. `--dry-run` goldens stay as they are: the
plan keeps the nominal 1920x1080 / 3840x2160 sizes, because dry-run has no clips to probe. Only real runs derive sizes.

Tests first (each must fail on the current script, then pass):

1. Fake curl in the test file: it rejects every call except `/System/Info/Public` with HTTP 401 unless stdin carries
   `Authorization: MediaBrowser Token="<FAKE_TOKEN>"`. It also serves `/System/Info`, honours `-w` (prints the HTTP
   code on a last line), and takes scenario knobs for `/Items` HTTP errors and an empty `/Items`.
   - Header: `token_failures` also requires the `Authorization: MediaBrowser Token="` line on stdin and no `X-Emby-Token`.
     The `token-on-argv` mutant changes to the new header text, and a new `legacy-header` mutant is caught by a
     behaviour check.
   - Rejected token: a wrong `BENCH_JELLYFIN_TOKEN` makes rows 1 and 3 FAIL with "token rejected", not SKIP or PASS.
   - item_id: a scenario with `/Items` answering 500 must make row 3's e2e read `item lookup HTTP 500`, and rows
     6, 10 and 12 FAIL with it. An empty `/Items` (HTTP 200) must still read "not in the Jellyfin library".
   - Sizes: the ffprobe stub answers `stream=width,height` per clip (tears_360p 640x286, 720p 1280x572, 1080p
     1920x858). The ffmpeg stub logs its argv to `FAKE_FFMPEG_LOG`. Rows 2, 3, 4, 8 and 11 must call
     libplacebo/scale with w=1920:h=858 (2, 3, 8) and w=3840:h=1716 (4, 11), never 1920:1080 or 3840:2160. The
     test also covers a 16:9 clip keeping 1920x1080, which pins that the derivation is not a hard-coded 858.
   - xpsnr: the ffmpeg stub fails xpsnr when `FAKE_XPSNR_FAIL=1`. Rows 2, 3, 4, 8 and 9 must then FAIL with
     "xpsnr failed" and the detail must not contain `xpsnr_delta=+0.00`. A mutant restoring `echo 0` is caught.
2. Run `python3 scripts/test_bench_intel.py`; the counts go in the PR body.

Implementation (smallest diff):

- `api()`: the header becomes `Authorization: MediaBrowser Token="<key>"`, still fed to `curl --config -` on stdin
  with the same escaping. The curl line is unchanged, so the existing argv guard keeps working.
- `api_get <path>` (new, direct call, sets `API_CODE`/`API_BODY` from `-w '\n%{http_code}'`).
- `have_api`: needs a token, then probes `/System/Info` through `api_get`. 401/403 sets `API_WHY=rejected`. Any other
  failure sets `API_WHY=unreachable`. A new `no_api <row> <skip text>` records FAIL "token rejected (HTTP 401) by
  $JF_URL/System/Info" for a rejected token, otherwise the old SKIP text. Rows 1, 6, 10 and 12 use it. Row 5 and
  shader_row's e2e branch treat a rejected token as FAIL.
- `item_id`: no longer `|| true`. It is called directly and sets `ITEM_ID`/`ITEM_ERR`, returning 1 with
  `item lookup HTTP <code>` on an HTTP or transport error. A genuine miss (HTTP 200, no match) gives an empty
  `ITEM_ID` and still reaches api_stream's "not in the library". Callers (rows 3/4 e2e, 5, 6, 10, 12) use
  `item_id X || { record N FAIL "$ITEM_ERR"; return; }`. It is not called in `$(...)`, because a failing substitution
  would kill the script under `set -e`.
- `scaled_dims <clip> <nominal-src-h> <dst-w> <dst-h>`: ffprobes the clip's width and height, multiplies by
  dst-h/nominal-src-h (360 to 1080 is 3x, 720 to 1080 is 1.5x, 1080 to 2160 is 2x), and rounds each to an even
  number. Letterbox stays letterbox (640x286 to 1920x858). If ffprobe fails it falls back to the nominal sizes, so the
  failure shows up loudly in xpsnr. `shader_row`, row 8 and row 11 use it for chain_shader/chain_bicubic. The master
  passed to xpsnr stays `clip $h`.
- `xpsnr_y`: returns 1 and prints nothing when ffmpeg fails or no `XPSNR y:` line parses (the `echo 0` goes).
  `xpsnr_delta` propagates the failure. `shader_row`, row 8 and row 9 record FAIL "xpsnr failed" instead of using 0.
  `shader_row`'s `|| d=0` goes.
- Header comment in the script: one line saying the token travels as the MediaBrowser Authorization header.

Not done, per the bead's "Do not": the NVIDIA/default paths, `--dry-run` output, tokens on argv or in logs, and
revenant are all untouched. Acceptance item 2 (bench run7 on the A380) needs an image rebuild and a cluster Job. It
cannot run from this session, so it is reported unverified. There is no docs/agent-lessons.md in this repo, so the PR
will carry `lesson: none (<reason>)`.

```yaml
{
  "goal": "Fix bench-intel.sh so it authenticates to Jellyfin 12.4 (MediaBrowser Authorization header, authed have_api probe with a 'token rejected' result), reports item lookup HTTP errors instead of 'not in the library', derives output sizes from the letterboxed masters, and treats xpsnr failure as FAIL instead of 0 dB.",
  "files": ["scripts/bench-intel.sh", "scripts/test_bench_intel.py"],
  "test_commands": ["python3 scripts/test_bench_intel.py"],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "scripts/test_bench_intel.py passes (counts reported) with new checks for the auth header, item_id HTTP errors, size derivation and xpsnr failure handling, each failing on the old script",
    "api() sends Authorization: MediaBrowser Token=\"<key>\" via curl --config on stdin; the token is never on argv, in env dumps or in logs",
    "have_api probes /System/Info and a 401 yields FAIL 'token rejected' rather than a pass or a silent SKIP",
    "item_id turns an HTTP error into 'item lookup HTTP <code>', distinct from 'clip is not in the Jellyfin library'",
    "rows 2, 3, 4, 8, 11 scale to sizes derived from the master (letterbox kept); xpsnr failure is FAIL 'xpsnr failed', never a 0 score",
    "--dry-run plan and NVIDIA/default paths unchanged (existing goldens still pass)",
    "NOT verifiable here: bench run7 on the A380 (no 401, rows 3-4 find the clip, non-zero xpsnr_delta) needs a rebuilt image and a cluster Job"
  ],
  "premises": [
    {"id": "P1", "claim": "api() currently sends X-Emby-Token and have_api only probes the unauthenticated /System/Info/Public", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 360-366", "load_bearing": true},
    {"id": "P2", "claim": "Jellyfin 12.4 rejects legacy auth headers and the fork's own client uses Authorization: MediaBrowser Token=\"...\"", "label": "VERIFIED", "check": "web/src/controller/image-preload.js:60-61 states 'Not X-Emby-Token: legacy auth headers are off by default (EnableLegacyAuthorization)'; the 401 logs in the bead are from the owner's run6 and were not re-read here", "load_bearing": true},
    {"id": "P3", "claim": "item_id ends in `|| true`, so an HTTP error becomes an empty id that api_stream reports as 'the clip is not in the Jellyfin library'", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 371-373 and 382", "load_bearing": true},
    {"id": "P4", "claim": "xpsnr_y echoes 0 on failure and shader_row does `|| d=0`, so a failed xpsnr prints xpsnr_delta=+0.00", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 345-352, 430-443", "load_bearing": true},
    {"id": "P5", "claim": "The dry-run goldens use nominal 1920x1080 / 3840x2160 and must not change, so the size derivation applies only to real runs", "label": "VERIFIED", "check": "scripts/test_bench_intel.py EXACT list lines 113-128; print_plan builds its commands with the constants", "load_bearing": true},
    {"id": "P6", "claim": "Letterboxed masters are 1920x858 at 1080p, 3840x1716 at 2160p and 640x286 / 1280x572 at 360p / 720p (scale by nominal height factor, even-rounded, matches them)", "label": "INFERRED", "check": "Arithmetic from the bead's 1920x858 and the script header (lower rungs are scale=flags=area of the 2160p master); settle with ffprobe on /clips in the bench pod, which this session cannot run", "load_bearing": false},
    {"id": "P7", "claim": "Rows are called plainly under set -euo pipefail, so a failing `$(item_id ...)` would abort the script; item_id must therefore be called directly with globals", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 46 and 695-698", "load_bearing": true},
    {"id": "P8", "claim": "The test's fake curl and ffprobe/ffmpeg stubs are in test_bench_intel.py and can be extended (auth check, -w, dims, argv log, xpsnr fail) without touching other tests' outcomes", "label": "VERIFIED", "check": "Read scripts/test_bench_intel.py lines 327-476 (FAKE_CURL, FFMPEG_STUB, FFPROBE_STUB, World)", "load_bearing": true},
    {"id": "P9", "claim": "No doc or other script depends on the X-Emby-Token string in bench-intel.sh", "label": "VERIFIED", "check": "Grep X-Emby-Token across the repo: only bench-intel.sh:364, the test mutant at line 576, and a comment in image-preload.js", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "HTTP status of an API call", "checked": "curl -w '%{http_code}' (already in the Intel image) wrapped by the existing api()", "verdict": "extend", "reason": "api() already holds the token-on-stdin handling; a new write-out flag is one argument"},
    {"need": "source clip dimensions", "checked": "ffprobe from the bundled jellyfin-ffmpeg, already used by src_fps and rows 10/12 via $FFPROBE", "verdict": "use", "reason": "same binary, same call shape as src_fps"}
  ]
}
```
