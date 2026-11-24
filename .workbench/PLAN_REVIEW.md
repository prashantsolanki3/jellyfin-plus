# Plan review: rev-8j46u (round 1)

## Premises

- **P1 (libplacebo guesses 709 for width>=1280 or height>576, else 601): HALF-VERIFIED, load-bearing.**
  I fetched `libplacebo/src/colorspace.c`. `pl_color_system_guess_ycbcr` is exactly
  `width >= 1280 || height > 576 -> BT_709, else BT_601`. The second half is still unproved. That half is that
  jellyfin-ffmpeg's `vf_libplacebo` hands an UNSPECIFIED colourspace through and the renderer applies this guess
  to the Vulkan frame. I did not read `vf_libplacebo.c` or `renderer.c`, and there is no ffmpeg here.
- **P2 (the bare `format=rgba,format=yuv420p` tail encodes with swscale's default BT.601): INFERRED, load-bearing.**
  I cannot run it: the shell has no ffmpeg, GPU or bench pod. Nothing in the repo or in the revenant doc
  measures it. The existing A380 figures in `GPU_VENDORS.md` (YAVG 93.88 vs 93.9, +3.05 dB) are consistent with it
  but do not prove it. They match the 360p case (row 2 is +3.06 dB), which is the one clip the hypothesis says is
  matrix-matched.
- **P3 (bench uses the plain tail, production uses the explicit one): VERIFIED.**
  `scripts/bench-intel.sh:159` has `RGBA_END="format=rgba,hwdownload,format=rgba,format=yuv420p"`.
  `UpscaleEngine.cs:5029` is the only `out_color_matrix` in `src/`, and it has `scale=out_color_matrix=bt709:out_range=tv`.
  Grep of the old tail finds only the bench script, the test (lines 52, 60, 61, 202, 1476) and `GPU_VENDORS.md:307`,
  so `files` covers the whole change.
- **P4 (masters untagged): INFERRED, not load-bearing.** Not runnable here. Agreed.
- **P5 (no bench Job or logs reachable): VERIFIED.** `kubectl get jobs -A` shows only `media/jellyfin-plus-clip-prep`
  (Complete). There is no `jellyfin-plus-bench-*`.
- **P6 (chain_fsr2 shares `$RGBA_END`, rows 7/8): VERIFIED.** `bench-intel.sh:163`. Not load-bearing.
- **P7 (hwupload keeps the setparams colourspace/range into the Vulkan frame): INFERRED, load-bearing.**
  It is not runnable here. If `hwupload` drops the tag, the head fix is a no-op and the plan's whole result
  claim falls.

## Other observations

- The revenant doc (section 8, runs 10 to 12) shows that row 8 (fsr2, 1080p panning, plain tail) also scores
  -14.88 dB, constant across runs. The plan leaves it on the plain tail. If the matrix hypothesis is right,
  row 8 is confounded by the same 709-decode/601-encode, and the bead's own re-run will not be able to tell
  whether fsr2 is really worse. This is a scope choice, not an error, but the PR body should say so.
- Competing suspects the plan does not exclude, but should list: the untagged range shift (AGENTS.md line 271,
  the plan's `range=tv` covers it), and PTS pairing in `xpsnr` (AGENTS.md line 268).
  A constant delta to the third decimal across runs does not point at any one of them, because any deterministic
  chain repeats.
- `tdd: required` is honest. The planned mutants (`shader-no-tag`, `shader-plain-tail`, `hwdec-no-tag`) catch a real
  regression: a chain that reintroduces the unpinned head or tail.
  They test the plan string, not the colour result, and the PR must say that.
- `GPU_VENDORS.md` must word the cause as INFERRED until a bench re-run, not as fact.
- The acceptance item "rows 3/4 positive after the fix" cannot be met in this session.
  That is an honest, declared gap.

## Result

The diff scope and the TDD plan are sound. The cause stays a hypothesis: P2 and P7 are load-bearing and I could
not run either check, so I cannot mark them VERIFIED. This is an environment limit, not a plan fault.
A plan that ships the bench change plus a proving-commands list, and states plainly that the numeric result is
unverified, is acceptable to proceed on. Strict approval is withheld because of the load-bearing INFERRED premises.

VERDICT: BLOCKED
