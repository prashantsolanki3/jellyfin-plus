# Plan review: pin NVScaler/NVSharpen to gist rev 05f0086

Method: re-ran the plan's checks against the GitHub gist API through the fetch tool; read
`scripts/install-shaders.sh` and `scripts/test_install_shaders.py`; grepped for consumers.

## Premises

- **P1 (head is v1.0.3, so the v1.0.2 grep fails): VERIFIED.** The commits endpoint lists 9d270a4
  (2026-11-17) and 65bd0ef (2026-11-12) ahead of 05f0086 (2022-02-24). Head NVScaler has
  `// NVIDIA Image Scaling v1.0.3 by NVIDIA`. The script's grep is `NVIDIA Image Scaling v1.0.2`
  with no URL revision, so it fails on head. The plan's root cause is right.
- **P2 (rev 05f0086 has both files at v1.0.2 with `#define SHARPNESS 0.25`; NVSharpen has the //!WHEN guard): VERIFIED.**
  Rev NVScaler has `// NVIDIA Image Scaling v1.0.2 by NVIDIA`, `#define SHARPNESS 0.25` and a
  `//!WHEN` line. Rev NVSharpen has `// NVIDIA Image Scaling v1.0.2 by NVIDIA`,
  `//!WHEN OUTPUT.w OUTPUT.h * LUMA.w LUMA.h * / 1.0 > ! ... 1.0 < ! *` and `#define SHARPNESS 0.25`.
- **P3 (head NVSharpen has `//!PARAM Sharpness` and no `#define SHARPNESS`, so a bump to v1.0.3 would make the sed a silent no-op): VERIFIED.**
  Head NVSharpen has a `//!PARAM Sharpness` block, `//!WHEN ... 1.0 > !` and no `#define SHARPNESS`.
  Bumping the check to v1.0.3 is the wrong fix, and the plan correctly avoids it.
- **P4 (`.../raw/<revision-sha>/<file>` serves that revision): NOT VERIFIED (load-bearing).**
  I could not run it. `gist.githubusercontent.com` robots.txt blocks the fetch tool, and the
  shell refuses external curl. The API returns raw URLs in the blob-hash form for the rev
  (NVScaler: `.../raw/25c4f64f94374fe3700bcee0568a0e51aa141d6b/NVScaler.glsl`, size 26173). That
  form is verified as API output and is content-addressed. The commit-SHA form in the plan is
  only believed to work.
- **P5 (the sha256 of the pinned files can be computed in the implement stage): NOT VERIFIED (load-bearing).**
  The shell has no egress or hash tool, and the fetch tool returns truncated, JSON-escaped text,
  so a digest cannot be derived. The plan's own fallback is `blocked_on_owner`.
- **P6 (curl supports file://): not run (non-load-bearing).** The shell cannot run curl on a
  file:// URL. It is plausible, and the new test will show it.
- **P7 (no other consumer of NIS_GLSL_URL or the unpinned fetch): VERIFIED.** No hit for `NIS_`,
  `NVScaler` or `NVSharpen` in `docker/intel/Dockerfile`, `scripts/proxmox-build.sh`,
  `scripts/test_bench_intel.py` or `.github/`. The only NIS_ consumers are `install-shaders.sh`
  and `test_install_shaders.py` (lines 55-56, 92-93, 100).

## Scope and TDD

- `files` covers the change: `install-shaders.sh` and `test_install_shaders.py`. The existing
  test uses `NIS_DIR` stubs with a `v1.0.2` header and no `by NVIDIA`, so the tightened grep
  forces the stub update the plan lists.
- `tdd: required` is honest. The new tests catch real bugs: a v1.0.3 file accepted or refused by
  the version grep, an NVSharpen with no `#define SHARPNESS` producing identical 0.25/0.65
  outputs, and a digest mismatch not failing.

## Defects to fix before implementing

1. The task asks for a sha256 check. The plan may ship with no digests and does not say what the
   script does when the digest is empty. Fail-open skips the check and pins nothing. Fail-closed
   breaks the build until an owner fills the value. State which, and keep that outcome visible in the PR.
2. P4 is inferred. Use the raw URL the API returned for NVSharpen and NVScaler at the rev
   (blob-hash form, verifiable from API output), or label the commit-SHA form as unverified.
3. P5 is a plan dependency that cannot be met in this environment. Decide it now: ship the
   mechanism and report the digests as `blocked_on_owner`, with the exact one-line command for
   the owner to compute them.

VERDICT: BLOCKED
