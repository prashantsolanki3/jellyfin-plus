# Plan: Arc image step 13/13 checks a shader the installer never writes

**Cause (verified by reading, not by the build log).** `docker/intel/Dockerfile:111` asserts
`FSRCNNX_x2_8-0-4-1.glsl` after `install-shaders.sh`. That script has never fetched FSRCNNX or Anime4K:
it writes RCAS-*, NVScaler, NVSharpen-*, SSimSuperRes, KrigBilateral, ravu-zoom-r3 and CuNNy-* and
nothing else. INSTALL.md, AGENTS.md and the script's own header all say it fetches FSRCNNX and Anime4K,
so on a box where the files were installed by hand it never showed. The plugin does need the file at
runtime: `ShaderLibrary._srFiles["fsrcnnx"]` is the default SR level, `shim/jellyfin-ffmpeg-upscale:62`
and `scripts/bench-intel.sh:52` hard-code it, and `fsrcnnx-heavy`, `anime4k-s` and `anime4k-m` are menued
levels. So the fix is to make the installer fetch them, not to loosen the check.

**Change (smallest diff).**
1. `scripts/install-shaders.sh`: add a pinned fetch block in the style of `fetch_nis` for
   - `FSRCNNX_x2_8-0-4-1.glsl` and `FSRCNNX_x2_16-0-4-1.glsl` from igv's FSRCNN-TensorFlow release `1.1`
     (LGPL-3.0-or-later), sha256-checked, plus a header marker grep;
   - `Anime4K_Upscale_CNN_x2_S.glsl` and `_M.glsl` from bloc97/Anime4K, fetched by commit
     `4029bf701ecaa15f163cdc49cffe5501c1acf410` (tag v4.0.1, MIT) under `glsl/Upscale/`. The commit id is the
     content pin, as FSR2 already is in the Dockerfile; add a sha256 as well if it can be computed.
   - Each has a local-copy override env var (`FSRCNNX_DIR`, `ANIME4K_DIR`) like `NIS_DIR`/`CUNNY_DIR`, so the test needs no network.
   - A final self-check: every file the installer promises exists and is non-empty, else `exit 1` naming it.
2. `docker/intel/Dockerfile:110-111`: keep the `test -f` but cover every default/menued SR file
   (`FSRCNNX_x2_8`, `FSRCNNX_x2_16`, `Anime4K_..._S/_M`, `RCAS-*`, `NVScaler`), and fix the stale comment
   "no hash" now that these are pinned.
3. `scripts/test_install_shaders.py` (the existing covering test, runnable here and picked up by CI): after a
   stubbed install, assert that every `*.glsl` named in the Dockerfile's `test -f /usr/share/jellyfin-shaders/...`
   lines and every file `ShaderLibrary.cs` maps for sr/refine/chroma/deblur levels is present in the output
   directory, except an explicit exempt set with reasons (`CAS-*`: this repo's own rollback shaders, never installed;
   `FSRCNN_x2_r1_32-0-2`: API-only, no upstream release carries it). Also mutation cases: wrong FSRCNNX sha256 and a wrong
   Anime4K header are refused. Written first; it fails on today's tree (no FSRCNNX in the output).
4. `docs/architecture/GPU_VENDORS.md` "Known gaps": say the shader files are now pinned except the gist `/raw`
   ones that remain unpinned (FSR, SSimSuperRes, KrigBilateral, ravu, CuNNy), so the doc stays true.

**Deviation to flag.** The bead asks for `scripts/test-*.sh`. This repo's installer test is already
`scripts/test_install_shaders.py`, and the shell here can run `python3 scripts/test_*.py` but not a `.sh`, so the
test is extended there rather than adding a shell test nobody could run in this session.

**Cannot be done from this session (state plainly in the final report).**
- *The Docker run.* There is no `docker` in the allowed shell, so the "full install-shaders step in the same base
  image, paste the listing" acceptance is not met here. The stubbed Python run proves the file set and the failure
  mode; the real listing needs a networked Docker host (orchestrator).
- *The two FSRCNNX sha256 values.* GitHub release assets carry `digest: null`, so no published hash exists, and the shell
  has no `sha256sum` or external curl. Implementer: try to obtain them (a throwaway `python3 scripts/...` that fetches
  and hashes, if the sandbox has egress). If it cannot, do NOT invent values: stop and report
  `blocked_on_owner` with the exact `curl -fsSL <url> | sha256sum` commands to run. Known facts to check against:
  x2_8 is 71296 bytes, x2_16 is 246868 bytes.

**Do not:** dispatch the build workflow, touch `.github/workflows/`, vendor any shader, change `ShaderLibrary.cs`
level names, or widen into fetching CAS or `fsrcnnx-max`.

```yaml
{
  "goal": "Make scripts/install-shaders.sh fetch, pin and verify the FSRCNNX and Anime4K shaders the plugin references, and make the Arc Dockerfile check a set the installer really writes, with a test that fails on the current mismatch.",
  "files": [
    "scripts/install-shaders.sh",
    "docker/intel/Dockerfile",
    "scripts/test_install_shaders.py",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 scripts/test_install_shaders.py",
    "python3 scripts/test_bench_intel.py",
    "pre-commit run --files scripts/install-shaders.sh docker/intel/Dockerfile scripts/test_install_shaders.py docs/architecture/GPU_VENDORS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "A test fails on today's tree: every shader file the Dockerfile test -f lines and ShaderLibrary.cs reference (minus the documented exempt set) must be in the installer output directory",
    "install-shaders.sh fetches FSRCNNX_x2_8-0-4-1, FSRCNNX_x2_16-0-4-1, Anime4K_Upscale_CNN_x2_S and _M, pinned by revision and verified (sha256 for FSRCNNX; commit id, plus sha256 if computable, for Anime4K), and exits non-zero on a hash or header mismatch",
    "The Dockerfile check fails loudly on a missing shader the plugin references",
    "The installer's own listing includes the FSRCNNX and Anime4K files",
    "Full install-shaders step run in the same base image in Docker with the listing pasted (NOT possible in this session: no docker, report it)",
    "The build workflow is not dispatched"
  ],
  "reuse_check": [
    {"need": "pinned fetch with checksum and licence/header verification", "checked": "existing fetch_nis / sha256_of helpers in scripts/install-shaders.sh (NVScaler fix, #13)", "verdict": "extend", "reason": ""},
    {"need": "content pin for Anime4K", "checked": "git commit id of bloc97/Anime4K tag v4.0.1 (4029bf70...), same convention as FSR2_COMMIT in docker/intel/Dockerfile", "verdict": "use", "reason": ""},
    {"need": "stub-driven installer test", "checked": "existing scripts/test_install_shaders.py stubs() and env overrides (NIS_DIR, CUNNY_DIR)", "verdict": "extend", "reason": ""}
  ],
  "premises": [
    {"id": "P1", "claim": "install-shaders.sh never writes any FSRCNNX or Anime4K file; FSRCNNX appears there only in a comment", "label": "VERIFIED", "check": "Read scripts/install-shaders.sh in full; Grep -i fsrcnn|anime4k over scripts/, shaders/, docker/ shows no fetch", "load_bearing": true},
    {"id": "P2", "claim": "The plugin needs FSRCNNX_x2_8-0-4-1.glsl at runtime (default SR level) and the other three menued SR files", "label": "VERIFIED", "check": "src/patcher/Transcoding/ShaderLibrary.cs:44-48, shim/jellyfin-ffmpeg-upscale:62, scripts/bench-intel.sh:52", "load_bearing": true},
    {"id": "P3", "claim": "igv release 1.1 serves FSRCNNX_x2_8-0-4-1.glsl (71296 B) and FSRCNNX_x2_16-0-4-1.glsl, and it has no FSRCNN_x2_r1_32-0-2 asset", "label": "VERIFIED", "check": "api.github.com/repos/igv/FSRCNN-TensorFlow/releases/tags/1.1 via the fetch tool: assets listed are checkpoints_params.7z, FSRCNNX_x2_16-0-4-1.glsl, FSRCNNX_x2_8-0-4-1.glsl", "load_bearing": true},
    {"id": "P4", "claim": "bloc97/Anime4K tag v4.0.1 resolves to commit 4029bf701ecaa15f163cdc49cffe5501c1acf410 and has glsl/Upscale/Anime4K_Upscale_CNN_x2_M.glsl (37685 B); the S file is at the same path", "label": "INFERRED", "check": "tag ref and M file listing verified via api.github.com; the S entry fell past the truncated listing, re-fetch ?ref=v4.0.1 from start_index 3500 to confirm", "load_bearing": false},
    {"id": "P5", "claim": "The sha256 of the two FSRCNNX files can be obtained in the implement stage", "label": "INFERRED", "check": "Release assets have digest null and the shell has no sha256sum or external curl; try a throwaway python3 scripts/ fetch-and-hash. If the sandbox has no egress, report blocked_on_owner with the curl|sha256sum commands rather than guessing", "load_bearing": true},
    {"id": "P6", "claim": "The Docker acceptance cannot be run in this session", "label": "VERIFIED", "check": "The allowed shell is kubectl get/describe/logs, git status/log/diff, cluster-internal curl, repo scripts; no docker. kubectl get pods -A shows no Arc build job to read from", "load_bearing": false},
    {"id": "P7", "claim": "CAS-low/medium/high are referenced by ShaderLibrary but also never installed by the script; this is a rollback path and out of scope", "label": "VERIFIED", "check": "Grep CAS-low in scripts/ shows only a comment in install-shaders.sh; ShaderLibrary.cs:197-199; files live in shaders/", "load_bearing": false},
    {"id": "P8", "claim": "Extending scripts/test_install_shaders.py satisfies CI and the acceptance's intent; a new scripts/test-*.sh is not required", "label": "INFERRED", "check": "CLAUDE.md says a new scripts/test_*.py is picked up by the affected-tests job; the bead names test-*.sh, so flag the deviation to the owner", "load_bearing": false}
  ]
}
```
