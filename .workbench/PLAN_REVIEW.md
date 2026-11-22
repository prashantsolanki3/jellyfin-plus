# Plan review (round 2)

I ran each check myself, using Read, Grep and the fetch tool against api.github.com and raw.githubusercontent.com.

## Premises

- **P1 (installer never writes FSRCNNX/Anime4K): VERIFIED.**
  - I read `scripts/install-shaders.sh` in full. It writes RCAS-*, NVScaler, NVSharpen-0.25/0.65, SSimSuperRes, KrigBilateral, ravu-zoom-r3 and the four CuNNy files, and nothing else.
  - "FSRCNNX" appears there once, in the comment at line 213.
  - A repo-wide grep for `FSRCNNX_x2|Anime4K_Upscale` finds no fetch anywhere. It only finds consumers: `ShaderLibrary.cs`, `ShimBridge.cs`, the shim, `bench-intel.sh`, `compose.py`, docs, INSTALL.md and `docker/intel/Dockerfile:111`.
  - INSTALL.md lines 60-72 say the installer fetches FSRCNNX and Anime4K, so the doc is stale. This matches the plan's cause.
  - The Dockerfile final stage does `COPY --from=build /usr/share/jellyfin-shaders` (line 130), so the check at line 111 is the only gate.
- **P2 (plugin needs these files at runtime): VERIFIED.**
  - `ShaderLibrary.cs:44-48` maps fsrcnnx, fsrcnnx-heavy, fsrcnnx-max, anime4k-s and anime4k-m.
  - `shim/jellyfin-ffmpeg-upscale:62` and `scripts/bench-intel.sh:52` hard-code `FSRCNNX_x2_8-0-4-1.glsl`.
  - The full list of `.glsl` names in the C# is exactly the plan's set. Only the following are uninstalled:
    - the four fetched files the plan adds;
    - `CAS-*` (rollback files, which live in `shaders/` and are not installed by the script);
    - `FSRCNN_x2_r1_32-0-2` (the exempt set).
  - The exempt set in the test is therefore complete.
- **P3 (igv release 1.1 assets): VERIFIED.**
  - `api.github.com/repos/igv/FSRCNN-TensorFlow/releases/tags/1.1` lists exactly three assets: `checkpoints_params.7z`, `FSRCNNX_x2_16-0-4-1.glsl` (246868 B) and `FSRCNNX_x2_8-0-4-1.glsl` (71296 B).
  - All three have `digest: null`. There is no `FSRCNN_x2_r1_32-0-2` asset.
- **P4 (Anime4K pin): VERIFIED; the plan's INFERRED label can be dropped.**
  - `git/ref/tags/v4.0.1` returns object type `commit`, sha `4029bf701ecaa15f163cdc49cffe5501c1acf410`.
  - `contents/glsl/Upscale?ref=v4.0.1` lists `_M` (37685 B) and `_S` (18638 B) at the stated path.
  - Fetching `_S` at that commit returns the MIT header and `//!HOOK MAIN`. These give the marker lines for the header grep: "Permission is hereby granted, free of charge" and `//!HOOK MAIN`.
- **P5 (the two FSRCNNX sha256 values can be obtained in the implement stage): NOT VERIFIED, and probably false. Load-bearing.**
  - I cannot compute a sha256 from here. My shell has no hash tool, and external curl and ad-hoc python are refused.
  - The plan's own P5 text says the implementer shell has no `sha256sum` or external curl. It only allows `python3 scripts/test_*.py` and `sync_*.py`, so the proposed "throwaway `python3 scripts/...` that fetches and hashes" cannot be run there either.
  - The fetch tool cannot reach `github.com/.../releases/download`, because robots.txt blocks it. It returns no hash in any case.
  - No hash for these files exists anywhere in the repo or in the API (`digest: null`).
  - So the acceptance item "sha256-verified FSRCNNX" very likely cannot be met without the owner.
- **P6 (no docker in this session): VERIFIED.** My allowed shell has no docker. The plan correctly says the Docker listing acceptance is not met here.
- **P7 (CAS-* out of scope): VERIFIED.**
  - `ShaderLibrary.cs:197-199` references CAS-*.
  - The files are in `shaders/` and install-shaders.sh only mentions them in a comment.
  - A CAS reference in the Dockerfile `test -f` list would break the build, so keeping CAS in the exempt set is right.
- **P8 (extend the Python test rather than add `test-*.sh`): accepted.**
  - `scripts/test_install_shaders.py` is the existing stub-driven test. The stubs are driven by env overrides, which the plan extends to `FSRCNNX_DIR` and `ANIME4K_DIR`.
  - The plan flags the deviation from the bead's `test-*.sh` request, which is acceptable.

## Scope and TDD

- **Scope.** `files` is install-shaders.sh, the Intel Dockerfile, test_install_shaders.py and GPU_VENDORS.md. That is the whole change.
  - Optional: INSTALL.md lines 60-72 claim CAS install and an FSRCNNX/Anime4K fetch. The plan leaves them alone, which is fine, but I'd note them in the PR.
  - `tdd: required` is honest. The test is written first and fails today because FSRCNNX and Anime4K are absent from the output dir.
- **Test value.**
  - The "file referenced by ShaderLibrary.cs or a Dockerfile `test -f` must exist in the installer output" test catches this exact bug class, a check or consumer for a file the installer never writes.
  - The wrong-sha256 and wrong-header mutations catch a silently accepted wrong or moved upstream file.
  - These mutations only mean something if the stubs override the expected sha, as the NIS stubs already do. The FSRCNNX env override needs the same sha-override variables.

## Why BLOCKED

P5 is load-bearing. I cannot verify it, and the plan's own text indicates the implementer will also fail to get the hashes. The rest of the design is sound.

Either of these would unblock the round:

1. The owner supplies the two sha256 values. They come from `curl -fsSL https://github.com/igv/FSRCNN-TensorFlow/releases/download/1.1/FSRCNNX_x2_8-0-4-1.glsl | sha256sum`, and the same with `FSRCNNX_x2_16-0-4-1.glsl` (expected sizes 71296 and 246868 bytes). The plan is otherwise ready to implement.
2. The plan is amended so the implementer does everything except the two sha constants: pin the release URL, check the byte size and header markers, leave the sha values as an explicit `blocked_on_owner` item, and still add the test with env-overridden shas. Until the owner supplies real values, the installer must not ship invented hashes.

Docker listing acceptance remains impossible in this session (P6). The orchestrator has to run it.

VERDICT: BLOCKED
