# Plan: rev-8j46u, bench rows 3/4 XPSNR delta of -5.70 / -6.13 dB

## Cause (read from the code and libplacebo source; not yet run on the GPU)

The deltas come from a colour-matrix mismatch in the bench chain. FSRCNNX does not lose quality.

- The shader chain is `format=yuv420p,hwupload,libplacebo=...:format=rgba,hwdownload,format=rgba,format=yuv420p`
  (`scripts/bench-intel.sh:159-160`). The clips are untagged lossless masters.
- Head: libplacebo infers the YCbCr matrix from the frame size when it is untagged.
  `pl_color_system_guess_ycbcr` returns BT.709 for `width >= 1280 || height > 576`, else BT.601.
  Fetched from libplacebo `src/colorspace.c`.
  - The 360p clip is 640 wide, so it is decoded as BT.601.
  - The 720p and 1080p clips are 1280 and 1920 wide, so they are decoded as BT.709.
- Tail: the bench's plain `format=rgba,format=yuv420p` has no matrix pinned. swscale's default RGB-to-YUV matrix is
  BT.601 (INFERRED, not run).
- So only 360p round-trips with the same matrix on both ends. Rows 3 and 4 decode with 709 and encode with 601.
  That gives a deterministic luma and chroma error against the master that no model quality can offset. It caps
  PSNR at roughly 30-35 dB, which is why the delta is constant to the third decimal across runs 11-13.
- Row 2 still gains because a 360p bicubic is so soft that the 3 dB gain from FSRCNNX outweighs the cap. Bicubic is
  YUV to YUV and never touches the matrix, so it has no mismatch.
- Production differs in the tail. `UpscaleEngine.cs:5029` and the shim pin `scale=out_color_matrix=bt709:out_range=tv`.
  That matches HD sources, but an untagged source of 576 lines or fewer is decoded as 601 and encoded as 709.
  This is a follow-up and out of scope here: the shim cannot see tags, so there is no correct fix without them.
- AGENTS.md also records an unrelated ~-9/255 range shift on untagged clips. Pinning `range=tv` on the head removes
  it as a second suspect.

## Fix (bench only; no engine or revenant change)

1. `scripts/bench-intel.sh`
   - Tag the input before `hwupload`: add `setparams=colorspace=bt709:range=tv`.
     - `chain_shader`: before `format=yuv420p,hwupload`.
     - `chain_hwdec`: after `format=nv12`.
     - `chain_zerocopy`: extend the existing `setparams=range=tv` to `colorspace=bt709:range=tv`.
   - Make the shared `RGBA_END` the production tail with the explicit matrix:
     `format=rgba,hwdownload,format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p`.
   - Leave `chain_fsr2` on the old plain tail. It runs rows 7 and 8, which the bead does not cover, and its own
     gbrpf32le conversions would make a 709 tag a separate change. Give it a literal `RGBA_END_PLAIN` and a comment.
   - Fix the comment above `RGBA_END` ("what production will run").
   - Add a row 3/4 diagnostic: `xpsnr_y` of the bicubic output is already printed. Do not widen the output beyond that.
2. `scripts/test_bench_intel.py`
   - Update `shader()`, `RGBA_END`, `HWDEC_CHAIN` and `ZEROCOPY_CHAIN` to the new strings.
     Keep `FSR2_CHAIN` plain via a separate constant.
   - Update the `yuv-libplacebo-output` regex (line ~202) and the row 9 regex (line ~1476).
   - Add a plan check: every non-fsr2, non-ort libplacebo chain carries both `setparams=colorspace=bt709` before
     `hwupload` and `scale=out_color_matrix=bt709:out_range=tv` in the tail. Never one without the other.
   - Add mutants:
     - `shader-no-tag`: drop the `setparams` head.
     - `shader-plain-tail`: drop the `scale=out_color_matrix`.
     - `hwdec-no-tag`.
   - Each mutant must be caught by `plan_failures`.
3. `docs/architecture/GPU_VENDORS.md` (around lines 303-310)
   - Record the cause and the rule: the tail must invert the head, so pin both ends.
   - Note that the explicit-matrix tail has now run in the bench chain, once it has.
   - Note the open SD-source mismatch in production as a follow-up.

## Verification limits

- No ffmpeg, GPU or bench pod is reachable from this session. `jellyfin-plus-bench-run13` is gone, so its logs
  cannot be read. The pod exec and Job create verbs are refused.
- The cause stays INFERRED beyond the libplacebo guess rule. The proving commands go in the PR body for a bench Job
  re-run:
  - `ffprobe -show_entries stream=color_space,color_range` on each clip.
  - Run the shader chain with and without the tag/tail pair and score with `xpsnr`.
  - Compare signalstats `YAVG` of candidate against master. A matrix error shows as a luma/chroma offset.
- The acceptance item "rows 3 and 4 show a positive delta after the fix" needs a bench re-run on the A380 through
  the revenant Job. That is outside this PR and outside the bead's `## Where`.
  The final report must say it was not verified.

```yaml
{
  "goal": "Make the Intel bench chains round-trip colour identically (pin the BT.709/tv matrix on both the libplacebo input and the RGBA tail), so rows 3/4 stop measuring a 709-decode/601-encode mismatch; document the cause with proving commands.",
  "files": [
    "scripts/bench-intel.sh",
    "scripts/test_bench_intel.py",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 -I scripts/test_bench_intel.py",
    "python3 scripts/test_shim_chain.py",
    "pre-commit run --files scripts/bench-intel.sh scripts/test_bench_intel.py docs/architecture/GPU_VENDORS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "reuse_check": [
    {
      "need": "pin the colour matrix and range on the libplacebo input and RGBA tail",
      "checked": "ffmpeg setparams and scale=out_color_matrix/out_range (already used in this repo: UpscaleEngine.cs:5029, the zero-copy chain's setparams=range=tv)",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "bench harness test",
      "checked": "scripts/test_bench_intel.py: golden plan lines, plan_failures, and the mutant table",
      "verdict": "extend",
      "reason": ""
    }
  ],
  "acceptance": [
    "A written cause with the ffmpeg commands that prove it, in the PR body and on the bead",
    "Rows 3 and 4 show a positive XPSNR delta after the fix, or the bead states why FSRCNNX genuinely loses at those heights (needs a bench re-run; unverifiable in this session)",
    "No revenant manifest or test Jellyfin image tag change; prod Jellyfin untouched"
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "libplacebo guesses BT.709 for untagged YCbCr with width>=1280 or height>576 and BT.601 below, so the 360p clip decodes as 601 and 720p/1080p as 709",
      "label": "VERIFIED",
      "check": "Fetched raw.githubusercontent.com/haasn/libplacebo/master/src/colorspace.c: pl_color_system_guess_ycbcr. The vf_libplacebo in jellyfin-ffmpeg 8.1.2 passes the frame's unspecified colourspace through (INFERRED).",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "The bench tail format=rgba,format=yuv420p encodes with swscale's default BT.601 matrix because no out_color_matrix is pinned",
      "label": "INFERRED",
      "check": "On a bench Job run the shader chain on one 720p frame with and without scale=out_color_matrix=bt709:out_range=tv and compare signalstats YAVG/UAVG/VAVG against the master. The GPU_VENDORS.md 5.8 A380 numbers (YAVG 93.88 vs 93.9) were for the plain form and are consistent with 601 round-tripping at SD.",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "Bench chains use the plain tail while production (UpscaleEngine.cs:5029, shim) uses the explicit bt709/tv tail",
      "label": "VERIFIED",
      "check": "Read scripts/bench-intel.sh:159 and src/patcher/Transcoding/UpscaleEngine.cs:5029; Grep of 'hwdownload,format=rgba' across the repo",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "The bench masters carry no colour tags and are produced by scale=flags=area from the 2160p master",
      "label": "INFERRED",
      "check": "ffprobe -show_entries stream=color_space,color_range,color_primaries /clips/tears_{360,720,1080,2160}p.mkv in the bench pod. The revenant decision doc says the prep step 'checks this with ffprobe'; the clip-prep Job log only shows hash skips.",
      "load_bearing": false
    },
    {
      "id": "P5",
      "claim": "Run13 logs and a bench pod are not reachable from this session, so the fix cannot be verified numerically here",
      "label": "VERIFIED",
      "check": "kubectl get jobs -A shows no jellyfin-plus-bench-* job (only jellyfin-plus-clip-prep, Complete); the shell allows no ffmpeg or kubectl exec",
      "load_bearing": false
    },
    {
      "id": "P6",
      "claim": "Changing the shared RGBA_END would also move rows 7/8 (fsr2) unless chain_fsr2 keeps a plain tail",
      "label": "VERIFIED",
      "check": "Read chain_fsr2 (bench-intel.sh:163): it uses $RGBA_END and runs row 7 (still) and row 8 (panning xpsnr_delta). The fsr2 head converts through gbrpf32le with its own default matrices, so a 709 tag there is a separate question.",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "hwupload keeps the frame colourspace/range from setparams into the Vulkan frame",
      "label": "INFERRED",
      "check": "The existing zero-copy chain already relies on setparams=range=tv before hwmap. Settle by running the new chain on a bench Job and checking the DC error against the master is about 0.",
      "load_bearing": true
    }
  ]
}
```
