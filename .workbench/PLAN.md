# Plan: Intel image and Arc bench script (E2E, F2), stacked on #8

Bead: none supplied with the brief (`no-bead` in the PR). The decision doc the brief points at
(revenant `docs/decisions/jellyfin-plus-e2e.md`, sections 2-4) is not in the knowledge index and could not
be read. The bench rows 1-13 are therefore built from the brief's list plus `docs/architecture/GPU_VENDORS.md`
sections 5.4-5.7, 6 and 8. That is the main INFERRED premise; the owner should check the row list against the
decision doc at review.

## What gets built

1. `docker/intel/Dockerfile`, two stages.
   - Build stage: Debian trixie (the base of the pinned Jellyfin image), runs `scripts/build-ffmpeg.sh` with
     `GPU_VENDOR=intel WITH_ORT=1 ORT_EP=openvino WITH_FSR2=1`, then `dotnet publish` for `src` and
     `src/patcher` (the two commands `scripts/proxmox-build.sh` uses). The `-p:JellyfinBin` assemblies are
     copied from the pinned base image's `/jellyfin`, so the plugin is compiled against exactly the server
     it runs in.
   - Final stage: `FROM docker.io/jellyfin/jellyfin:12.4.20261026-211555@sha256:fea863d0...b771` (full digest
     from the brief). It adds the patched ffmpeg tree, the shim, the plugin and patcher, the `fsr2-nocuda`
     marker (built in the prefix by `build-ffmpeg.sh`), python3 for the shim, and the OpenVINO runtime libs.
   - Every download the Dockerfile itself adds is pinned by sha256 and checked with `sha256sum -c`. The FSR2
     SDK header and static lib are built out of tree (FSR2.md); the Dockerfile builds them from a
     sha256-pinned source archive. Downloads that `build-ffmpeg.sh` still makes unpinned (FFmpeg tarball,
     OIDN, Vulkan-Headers, shaderc, libplacebo) are not changed in this PR: that script is shared with the
     nvidia build and is out of scope. The PR body lists them as a known gap.
   - No `docker push`, no `COPY` of the binary out, no artifact upload: the binary is GPLv3-built and never
     leaves the image (the build script's rule). F3 owns CI.
2. `docker/intel/entrypoint.sh`, a small wrapper. The stock `ENTRYPOINT` is `/jellyfin/jellyfin` and the live
   pod keeps `/config` on a PVC, so a plugin baked into the image is invisible there. The wrapper copies the
   baked plugin dir into `$JELLYFIN_DATA_DIR/plugins/JellyfinPlus_1.0.0.0` (only when the content differs),
   then `exec`s `/jellyfin/jellyfin "$@"`. It never writes outside the data dir, so a read-only rootfs works. It
   does not touch other plugin dirs (the SSO init container writes there too).
3. `scripts/bench-intel.sh`, the matrix as a script. It prints a table and exits non-zero when any row's
   pass criterion fails. Modes: default (run), `--dry-run` (prints the `plan:` lines for every row with its
   command and criterion, runs nothing, needs no GPU), `--list`. Inputs are given by flags or env
   (`BENCH_CLIP_DIR`, `BENCH_FFMPEG`) and are never fetched by the script. The rows:
   1. vendor record (GPU, kernel driver, ffmpeg `-filters` against `ffmpeg_required_filters intel`)
   2. FSRCNNX 360p to 1080p fps
   3. FSRCNNX 720p to 1080p fps
   4. FSRCNNX 1080p to 2160p fps
   5. XPSNR of FSRCNNX against bicubic on open-content 4K masters (downscale, upscale, compare)
   6. neural on OpenVINO fps per source height
   7. routing by `NeuralMaxSourceHeight`
   8. fsr2 still-pixel stability, skipping warm-up frames
   9. fsr2 moving-content XPSNR
   10. hardware decode off
   11. hardware decode on (VA-API to Vulkan bridge)
   12. QSV encode
   13. GPU load from DRM fdinfo (same fields `src/Transcoding/GpuFdinfo.cs` reads: i915
       `drm-engine-<class>: N ns`, xe `drm-cycles-*` / `drm-total-cycles-*`)
   Thresholds come from the A380 baseline in GPU_VENDORS.md section 6 and are named variables at the top of
   the script, not buried in commands. Anything the decision doc may set differently is a one-line change.
4. `scripts/test_bench_intel.py`, modelled on `scripts/test_build_ffmpeg_vendor.py`:
   - `bash -n` on `scripts/bench-intel.sh`, `docker/intel/entrypoint.sh`;
   - `bench-intel.sh --dry-run` golden: exactly 13 `plan:` rows, ids 1..13 in order, each with a command and a
     criterion, exit 0, and no ffmpeg or GPU needed;
   - table and exit status logic: a fake `ffmpeg` on PATH (written to a temp dir by the test) that reports
     a fixed fps drives one row to pass and one to fail, and the script must exit non-zero on the fail;
   - fdinfo parser: fixtures for i915 and xe, same expected percentages as the C# parser;
   - static Dockerfile checks: the base ref is the exact digest from the brief, the build line carries all
     four env settings, every `curl`/`wget`/`ADD http` is followed by a sha256 check, no `docker push`,
     no `--push`, no `ENV`/`ARG` token or credential, the final `ENTRYPOINT` is the wrapper;
   - hadolint-style: runs `hadolint` if on PATH, otherwise reports SKIP and does not fail;
   - mutation phase like the template: drop the digest, drop a `sha256sum -c`, drop one env setting, and
     check that each makes a check fail.
5. Docs in the same commit (invariant 12): a new subsection in `docs/architecture/GPU_VENDORS.md` for the image
   and the bench, `INSTALL.md` a short pointer, `context-map.md` rows for the new files. No new doc file, so
   no `docs/README.md` row.

## Order of work

1. Spec commit: the GPU_VENDORS.md subsection with the 13 rows, thresholds and the image layout.
2. Test first (`scripts/test_bench_intel.py`, failing), then `bench-intel.sh`, then `Dockerfile` and
   `entrypoint.sh`, until green.
3. Run `python3 scripts/test_build_ffmpeg_vendor.py` (shared script untouched, so it must stay green) and
   `pre-commit run --files ...`.

## Not done here, stated up front

- The image is not built (F3 does that) and the bench is not run on an Arc card. Nothing here proves the
  image starts, the plugin loads, or any threshold holds. `--dry-run` and the static checks prove wiring only.
- Whether the plugin loader accepts a copied dir at first start, the stock image's user and `/jellyfin` layout
  at the 12.4.20261026 tag, and the FSR2 source pin are INFERRED and need the first F3 build to confirm.

```yaml
{
  "goal": "Add a stacked draft that gives the Intel Arc E2E a docker/intel image (patched ffmpeg GPU_VENDOR=intel WITH_ORT=1 ORT_EP=openvino WITH_FSR2=1, shim, plugin, patcher, fsr2-nocuda marker, OpenVINO libs on the pinned jellyfin 12.4.20261026 base, every download sha256-pinned, never published) and a scripts/bench-intel.sh that runs bench rows 1-13, prints a table and exits non-zero on a failed pass criterion, with a dry-run mode and a python test.",
  "files": [
    "docker/intel/Dockerfile",
    "docker/intel/entrypoint.sh",
    "scripts/bench-intel.sh",
    "scripts/test_bench_intel.py",
    "docs/architecture/GPU_VENDORS.md",
    "INSTALL.md",
    "context-map.md"
  ],
  "test_commands": [
    "python3 scripts/test_bench_intel.py",
    "python3 scripts/test_build_ffmpeg_vendor.py",
    "python3 -m py_compile scripts/test_bench_intel.py",
    "pre-commit run --files docker/intel/Dockerfile docker/intel/entrypoint.sh scripts/bench-intel.sh scripts/test_bench_intel.py docs/architecture/GPU_VENDORS.md INSTALL.md context-map.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "docker/intel/Dockerfile builds FROM the exact docker.io/jellyfin/jellyfin:12.4.20261026-211555@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771 reference and its build stage runs scripts/build-ffmpeg.sh with GPU_VENDOR=intel WITH_ORT=1 ORT_EP=openvino WITH_FSR2=1",
    "Every download made by the Dockerfile is followed by a sha256 check, and nothing in the Dockerfile or its checks pushes, uploads or exports the ffmpeg binary outside the image",
    "The final stage carries the patched ffmpeg, the shim, the plugin, the patcher with 0Harmony.dll outside the plugin dir, the fsr2-nocuda marker and the OpenVINO runtime, and its entrypoint places the plugin under $JELLYFIN_DATA_DIR/plugins without writing anywhere else",
    "scripts/bench-intel.sh --dry-run prints exactly 13 plan rows in order with a command and a pass criterion each, runs nothing and exits 0 on a machine with no GPU",
    "scripts/bench-intel.sh prints a results table and exits non-zero when any row's pass criterion fails, shown by a test using a fake ffmpeg",
    "bash -n passes for bench-intel.sh and entrypoint.sh, and python3 scripts/test_bench_intel.py exits 0 including its mutation phase",
    "python3 scripts/test_build_ffmpeg_vendor.py still passes and scripts/build-ffmpeg.sh and .github/workflows/ are untouched",
    "GPU_VENDORS.md documents the image and the bench rows with thresholds, and the PR states what was not verified: no image build, no Arc run, the decision doc unread"
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The live Jellyfin pod runs the image digest named in the brief (12.4.20261026-211555@sha256:fea863d0...).",
      "label": "VERIFIED",
      "check": "kubectl describe pod on the jellyfin pod showed that image and imageID earlier in this session",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "The data dir is /config (JELLYFIN_DATA_DIR) and the live plugins dir is /config/plugins on a PVC, so a plugin baked into the image must be copied there at start.",
      "label": "VERIFIED",
      "check": "kubectl describe pod (env and volume mounts) and the upstream jellyfin-packaging Dockerfile",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "build-ffmpeg.sh already supports GPU_VENDOR=intel, WITH_ORT=1, ORT_EP=openvino, WITH_FSR2=1, the fsr2-nocuda marker and PLAN_ONLY=1, so the Dockerfile only calls it.",
      "label": "VERIFIED",
      "check": "Read scripts/build-ffmpeg.sh and scripts/ffmpeg-vendor.sh in the repo; scripts/test_build_ffmpeg_vendor.py already covers the PLAN goldens",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "Repo test scripts are named scripts/test_<name>.py and are picked up by CI automatically, so the brief's scripts/test-*.py becomes scripts/test_bench_intel.py.",
      "label": "VERIFIED",
      "check": "Glob scripts/test_*.py and the task rules; scripts/test_build_ffmpeg_vendor.py is the template",
      "load_bearing": false
    },
    {
      "id": "P5",
      "claim": "The plugin and patcher are built with the two dotnet publish commands of proxmox-build.sh, the patcher dir stays outside the plugin dir with 0Harmony.dll, and the plugin dir is JellyfinPlus_1.0.0.0.",
      "label": "VERIFIED",
      "check": "Read scripts/proxmox-build.sh and scripts/jellyfin-plus-activate (PLUGIN_DIR, PATCH_DIR)",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "GPU load rows can mirror GpuFdinfo.cs: i915 prints drm-engine-<class>: N ns, xe prints drm-cycles-<class> and drm-total-cycles-<class>.",
      "label": "VERIFIED",
      "check": "Read src/Transcoding/GpuFdinfo.cs",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "The stock image ENTRYPOINT is /jellyfin/jellyfin, the server lives in /jellyfin, and the image is Debian-based, at the pinned 12.4.20261026 tag.",
      "label": "INFERRED",
      "check": "Read from the upstream jellyfin-packaging master Dockerfile, not from the pinned tag; the first F3 build (docker run --entrypoint) confirms it",
      "load_bearing": true
    },
    {
      "id": "P8",
      "claim": "Bench rows 1-13 and their pass thresholds match the decision doc revenant docs/decisions/jellyfin-plus-e2e.md sections 2-4.",
      "label": "INFERRED",
      "check": "The doc is not in the knowledge index (knowledge-get returned no indexed file); rows were rebuilt from the brief and GPU_VENDORS.md sections 5.4-5.7, 6, 8. Owner to compare at review",
      "load_bearing": true
    },
    {
      "id": "P9",
      "claim": "A baked plugin dir copied into /config/plugins at start is loaded by Jellyfin like a normally installed plugin.",
      "label": "INFERRED",
      "check": "Same layout as the live deploy (jellyfin-plus-activate); cannot be proved without running the image on a server",
      "load_bearing": true
    },
    {
      "id": "P10",
      "claim": "The FSR2 SDK header and libffx_fsr2_vk.a can be built from a sha256-pinned source archive inside the Docker build stage.",
      "label": "INFERRED",
      "check": "docs/filters/FSR2.md and ffmpeg/gen_perm.py describe an out-of-tree build; the exact source URL and sha256 are chosen when the Dockerfile is written and are checked by the static test, not by a build",
      "load_bearing": true
    },
    {
      "id": "P11",
      "claim": "hadolint may not be installed on the CI runner or here, so the hadolint-style check must skip rather than fail when absent.",
      "label": "INFERRED",
      "check": "The shell here cannot run which/hadolint; the test reports SKIP when shutil.which finds nothing",
      "load_bearing": false
    }
  ],
  "reuse_check": [
    {
      "need": "Build the Intel ffmpeg variant inside the image",
      "checked": "scripts/build-ffmpeg.sh, scripts/ffmpeg-vendor.sh",
      "verdict": "use",
      "reason": "The script already takes GPU_VENDOR, WITH_ORT, ORT_EP and WITH_FSR2 and checks the vendor's filter list; the Dockerfile only invokes it."
    },
    {
      "need": "Compile the plugin and patcher",
      "checked": "scripts/proxmox-build.sh dotnet publish lines",
      "verdict": "use",
      "reason": "Same two publish commands with -p:JellyfinBin pointed at the base image's assemblies; no new build path."
    },
    {
      "need": "Route sessions to the patched binary",
      "checked": "shim/jellyfin-ffmpeg-upscale (fixed paths, pick_binary, plugin_patch_active), src/Transcoding/ShimBridge.cs",
      "verdict": "use",
      "reason": "The shim is copied unchanged; the image only has to satisfy its fixed paths. Its writes under /etc and /var/log fail open on a read-only rootfs."
    },
    {
      "need": "Per-vendor required filter list for the vendor-record row",
      "checked": "scripts/ffmpeg-vendor.sh ffmpeg_required_filters",
      "verdict": "use",
      "reason": "The bench sources this function so the list has one definition (invariant 10)."
    },
    {
      "need": "Read GPU load from DRM fdinfo in bash",
      "checked": "src/Transcoding/GpuFdinfo.cs, GpuLoadController",
      "verdict": "none-fits",
      "reason": "The C# parser is internal to the plugin and cannot be called from a shell; the bench reimplements the same field names and the test pins them to the same expected percentages."
    },
    {
      "need": "Test harness for the new scripts",
      "checked": "scripts/test_build_ffmpeg_vendor.py, scripts/test-shim-device.py",
      "verdict": "extend",
      "reason": "Copy the Suite/check helper and mutation-phase structure of test_build_ffmpeg_vendor.py into a new test file; the shared script is not edited."
    },
    {
      "need": "Bench harness conventions (sha256-pinned inputs, uid 1000, supplementalGroups, gpu.intel.com/i915 limit)",
      "checked": "revenant scripts/ops/media/arc-upscale-bench.sh (knowledge index), repo scripts/*bench*",
      "verdict": "none-fits",
      "reason": "That precedent lives in another repo and runs as a cluster Job; this bench is a standalone script run inside the image. Its input-pinning rule is followed, its code is not copied."
    },
    {
      "need": "Place the plugin in /config/plugins on a read-only rootfs",
      "checked": "scripts/jellyfin-plus-activate, stock image entrypoint (upstream Dockerfile)",
      "verdict": "none-fits",
      "reason": "The activate script assumes systemd and /var/lib/jellyfin; the container needs a small entrypoint wrapper instead."
    }
  ]
}
```
