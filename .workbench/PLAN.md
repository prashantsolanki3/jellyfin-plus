# Plan: GPU_VENDOR=intel build variant (GPU_VENDORS.md PR 8)

Base is 7855e15 (PR 5) plus the workbench start commit; the working tree is clean. This is a shell and docs change: no C#, no `ffmpeg/*.c`, nothing in `gu_inputs.h` or the fsr2 files (PR 6's ground). There is no live cluster or running Jellyfin behind it. The build and deploy scripts run on a Proxmox box, so "verify live" here means running the real scripts in plan mode and under fakes. A full ffmpeg build is NOT RUN.

**Shape of the change**

1. `scripts/ffmpeg-vendor.sh` (new, sourced by both scripts) holds the one per-vendor fact that must not drift: `ffmpeg_required_filters <vendor>`. It returns `oidn optix ort fsr2 dlss` for nvidia (the Maxine `vsr` is added by the caller as today) and `oidn ort` for intel. It also validates the vendor name.
2. `scripts/build-ffmpeg.sh` gets `GPU_VENDOR=nvidia|intel`, default `nvidia`, any other value refused.
   - The vendor block sits before the `WITH_*` defaults. On nvidia every default is today's value. On intel the defaults are `WITH_ORT=1` and `ORT_EP=openvino`.
   - Vendor-dependent steps are driven by arrays computed once: the apt package list, the vendor patch list, the hwaccel configure flags and the required filters. The NVIDIA arrays are copied from today's script in the same order, so the configure argv and the patch order are unchanged.
   - New `PLAN_ONLY=1` mode. It skips the root and disk checks, runs the full existing preflight, prints `plan:` lines (vendor, flags, apt extras, nv-codec yes/no, patch list in order, hwaccel flags, required filters, bridge test default) and exits 0 before any apt, download or write. The real run uses the same arrays, so the printed plan is what runs.
   - Intel refusals, each naming the variable:
     - `WITH_OPTIX`, `WITH_FSR2`, `WITH_DLSS`, `WITH_MAXINE_VSR`, `WITH_RTXDLPP` or `WITH_RTXVSR` set to 1;
     - `ORT_EP=cuda`;
     - `WITH_ORT=0` or `WITH_OIDN=0` (the intel required set is `oidn ort`);
     - `BRIDGE_TEST=1`.
     The existing `ORT_EP=openvino` refusals (`ORT_LIB`, a CUDA provider already in the prefix) still apply.
   - Intel skips these:
     - the nv-codec-headers clone;
     - `--enable-ffnvcodec --enable-cuda --enable-cuvid --enable-nvdec --enable-nvenc`;
     - the CUDA-llvm flags and the clang warning;
     - patches 0006, 0007, 0010, 0011, 0012, 0013 and 0014;
     - the `transpose_cuda` verify;
     - the bridge test.
   - Intel adds:
     - apt `libva-dev libvpl-dev libdrm-dev`;
     - `--enable-vaapi --enable-libvpl --enable-libdrm`;
     - a verify step that captures `-hwaccels` and `-encoders` into variables and then greps them, not `grep -q` on a pipe, which hits the SIGPIPE-under-pipefail trap `proxmox-build.sh` documents. It checks for `vaapi` and for `h264_qsv`, `hevc_qsv`, `av1_qsv`.
     `--enable-libdrm` is there because PR 3's Vulkan-from-render-node device (`-init_hw_device drm=... vulkan=vk@dr`) runs in this binary and needs libdrm compiled in.
3. **Decision on 0008 and 0009: they stay for Intel.**
   - Both touch only `vf_hwmap.c`. They need no CUDA header, SDK or `--enable-cuda`; 0009's `AV_PIX_FMT_CUDA` is an enum in `pixfmt.h`.
   - They change nothing unless an hwmap node is in the graph, and no Intel command line from PR 2 or PR 3 has one.
   - PR 7's VA-API to Vulkan chain is exactly an hwmap chain. 0009 pins `derive_device=vulkan` to the Vulkan format and 0008 keeps the frame format in step in chained maps, the two ENOSYS failures they were written for. They are format-negotiation fixes, not CUDA fixes. `derive_device=vaapi` falls through 0009 unchanged.
   - Dropping them would hand PR 7 a binary that differs from the PR 8 one for no gain.
   - The cost is a changed generic filter that has only been run on NVIDIA. That is stated as unverified, and the patches can be dropped from the intel list in one line.
   - FFmpeg's nv-codec-headers do not change hwmap at all. They go for Intel with the ffnvcodec/cuda/nvenc flags, as section 5.7 of the spec says.
4. `scripts/proxmox-build.sh` becomes vendor-aware.
   - `GPU_VENDOR` is read from the environment, default nvidia, and validated before the live binary is snapshotted.
   - It passes `GPU_VENDOR` through. The NVIDIA-only `WITH_*` defaults become 0 for intel and stay 1 for nvidia, so the environment handed to `build-ffmpeg.sh` is identical for nvidia.
   - `required_filters` comes from `ffmpeg_required_filters`. The success line stays "all five custom filters present" for nvidia and reads "all required filters present (oidn ort)" for intel.
   - A missing required filter still restores `.prev`.
   - The `--help` `sed -n '2,19p'` range is extended with the header lines that are added.
5. Pins. The Intel variant adds no downloaded URL. libva, libvpl and libdrm come from apt, signed by the distro archive, so there is nothing new to sha256-pin. The existing wheel and header pins are reused unchanged. The FFmpeg and OIDN tarballs are pre-existing and still unpinned. That is said plainly in the docs and the PR, not widened here.
6. Docs, in the same commit as the code.
   - `GPU_VENDORS.md`: header status, section 2 row, section 4 row 8, section 5.4's "still needs NVIDIA headers" bullet, section 5.7 rewritten as built with the 0008/0009 decision, the test paragraph and the open questions.
   - `NEURAL.md`: the "Building for Intel" bullets.
   - `ENHANCEMENTS.md`: the patched-binary paragraph and the PR 5 sentence about the nvcodec headers.
   - `docs/video-enhancement.md` and `docs/README.md`.
   - `AGENTS.md` invariant 10 and `CLAUDE.md`'s "all five must survive" lines, amended to a per-vendor required set. The spec calls this the maintainer's amendment. The brief asks for it, so it is made, and the PR body flags it.
   - The expected trivial doc conflict with PR 6 (same header paragraph of `GPU_VENDORS.md`) is noted in the PR.

**Tests first** (`bash scripts/test-build-ffmpeg-vendor.sh`, no arguments, which is the form the workbench shell allows). There is no existing test of the build script. The new one needs no root, GPU, network or jfbin.

- (A) The real `build-ffmpeg.sh` in `PLAN_ONLY=1` mode:
  - a hand-written golden of the NVIDIA plan, with `GPU_VENDOR` unset equal to `nvidia`;
  - the PR 5 mode `WITH_ORT=1 ORT_EP=openvino` unchanged;
  - the Intel plan: patches exactly `0008 0009`, no nv-codec step, hwaccel flags exactly the three, none of ffnvcodec/cuda/cuvid/nvdec/nvenc/cuda-llvm, required `oidn ort`, bridge test 0;
  - every refusal above with its message, plus an unknown vendor, and the existing NVIDIA refusals still firing.
- (B) The real `proxmox-build.sh` under fakes:
  - fake `id`, `dotnet` and `git` on PATH;
  - a stub repo whose `build-ffmpeg.sh` records its environment and writes a fake `ffmpeg` listing a chosen filter set;
  - the run stops at the missing `migrate` script, before anything under `/usr/lib` is touched.
  - Cases:
    - nvidia with 5 filters passes;
    - nvidia missing `dlss` is refused and `.prev` is restored;
    - intel with only `oidn ort` passes;
    - intel missing `ort` is refused and `.prev` is restored;
    - intel missing `oidn` is refused;
    - an unknown vendor is refused before the snapshot;
    - the environment seen by the stub is the NVIDIA one by default and has the NVIDIA-only flags at 0 for intel.
- (C) A mutation phase. Each mutant is a text replacement into a temp copy, with the original text asserted present so drift cannot make it vacuous. The suite must then fail. Mutants:
  1. the intel required set becomes the five;
  2. the intel refusal of `WITH_OPTIX` is removed;
  3. 0006 is added to the intel patch list;
  4. the NVIDIA patch order is swapped;
  5. the nv-codec step is un-gated for intel;
  6. the default vendor becomes intel.
- Pass counts and the mutants caught are reported from the actual run.

**Constraints on this stage's process**

- The brief's "Spec commit then the PR 8 commit" cannot be made from the workbench, which cannot commit. The spec text (the section 5.7 rewrite) is a separable doc change and is reported as such.
- `docs/agent-lessons.md` and `docs/automations.md` do not exist in this repo. The PR's lesson line therefore names `docs/architecture/GPU_VENDORS.md`, which changes.
- No bead id was given. `no-bead` goes in the PR body unless one is supplied.
- Not verified, to be said in the PR:
  - no ffmpeg build;
  - no Intel box;
  - libvpl, VA-API and QSV encoders in the built binary;
  - the host's QSV runtime packages (the script does not install them);
  - 0008/0009 behaviour on VA-API chains.

```yaml
{
  "goal": "Add GPU_VENDOR=nvidia|intel (default nvidia, byte-identical plan) to scripts/build-ffmpeg.sh with an Intel variant (VA-API, libvpl/QSV, libdrm, ORT_EP=openvino, no NVIDIA SDKs/CUDA patches/nv-codec-headers, 0008/0009 kept) and make proxmox-build.sh's required-filter check vendor-aware via a shared helper; tests first, docs and the invariant-10 amendment in the same change.",
  "files": [
    "scripts/build-ffmpeg.sh",
    "scripts/proxmox-build.sh",
    "scripts/ffmpeg-vendor.sh",
    "scripts/test-build-ffmpeg-vendor.sh",
    "docs/architecture/GPU_VENDORS.md",
    "docs/filters/NEURAL.md",
    "docs/video-enhancement.md",
    "docs/README.md",
    "ENHANCEMENTS.md",
    "AGENTS.md",
    "CLAUDE.md"
  ],
  "test_commands": [
    "bash scripts/test-build-ffmpeg-vendor.sh",
    "pre-commit run --files scripts/build-ffmpeg.sh scripts/proxmox-build.sh scripts/ffmpeg-vendor.sh scripts/test-build-ffmpeg-vendor.sh docs/architecture/GPU_VENDORS.md docs/filters/NEURAL.md docs/video-enhancement.md docs/README.md ENHANCEMENTS.md AGENTS.md CLAUDE.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "GPU_VENDOR unset and GPU_VENDOR=nvidia produce an identical PLAN_ONLY plan, equal to a hand-written golden of today's patch order (0006 0007 0008 0009 0010 0014 0011 0012 0013), configure hwaccel flags and apt list; the PR 5 mode WITH_ORT=1 ORT_EP=openvino is unchanged",
    "GPU_VENDOR=intel defaults to WITH_ORT=1 ORT_EP=openvino, applies only patches 0008 and 0009 from 0006-0014, skips the nv-codec-headers clone and the ffnvcodec/cuda/cuvid/nvdec/nvenc/cuda-llvm flags, enables --enable-vaapi --enable-libvpl --enable-libdrm, and defaults BRIDGE_TEST to 0",
    "Intel refuses, naming the variable: WITH_OPTIX/FSR2/DLSS/MAXINE_VSR/RTXDLPP/RTXVSR=1, ORT_EP=cuda, WITH_ORT=0, WITH_OIDN=0, BRIDGE_TEST=1; an unknown GPU_VENDOR is refused for both scripts",
    "proxmox-build.sh requires oidn optix ort fsr2 dlss on nvidia (unchanged text and restore of .prev) and oidn ort on intel, from one shared helper; an intel binary missing ort or oidn is refused and the previous binary restored",
    "The Intel variant adds no unpinned download: libva/libvpl/libdrm come from apt, existing sha256 pins are untouched, and the docs say the FFmpeg and OIDN tarballs are still unpinned",
    "The 0008/0009 decision for Intel is written in GPU_VENDORS.md 5.7 with its reasoning and its unverified status",
    "Docs and ENHANCEMENTS.md change in the same change; AGENTS.md invariant 10 and CLAUDE.md state the per-vendor required set and the PR body flags it as the maintainer's amendment",
    "bash scripts/test-build-ffmpeg-vendor.sh passes with counts reported and at least 3 mutants caught (6 planned); a full ffmpeg build is reported NOT RUN"
  ],
  "reuse_check": [
    {
      "need": "VA-API, QSV (oneVPL) and DRM libraries for the Intel ffmpeg configure",
      "checked": "Ubuntu noble apt packages libva-dev, libvpl-dev (2023.3.0-1build1, universe, per packages.ubuntu.com), libdrm-dev; FFmpeg 8.1.2 --enable-vaapi/--enable-libvpl/--enable-libdrm",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "Testing a root-only build script and a root-only deploy script without root, a GPU or network",
      "checked": "existing scripts/test-*.sh convention (test-vf-ort.sh, test-hwmap-bridge.sh) and bash itself; no bats/shunit2 is vendored in this repo and the workbench shell runs only bash scripts/test-<name>.sh",
      "verdict": "none-fits",
      "reason": "A plan-only mode plus fake id/dotnet/git on PATH is smaller than adding a test framework, and it exercises the real scripts, not copies of them"
    },
    {
      "need": "One source of truth for the per-vendor required-filter set shared by build-ffmpeg.sh and proxmox-build.sh",
      "checked": "scripts/ for an existing shared shell library (none; only the mixed-use scripts and gpu-vendor-harness)",
      "verdict": "none-fits",
      "reason": "A new sourced scripts/ffmpeg-vendor.sh of roughly 25 lines is smaller than duplicating the list in two scripts, which is the drift invariant 10 exists to prevent"
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "build-ffmpeg.sh applies 0006 0007 0008 0009 0010 0014 0011 0012 0013 unconditionally, in that order, and has no vendor concept today",
      "label": "VERIFIED",
      "check": "Read scripts/build-ffmpeg.sh lines 551-621 and Grep for GPU_VENDOR (no match in the script)",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "Patches 0008 and 0009 touch only libavfilter/vf_hwmap.c and need no CUDA header or configure flag, so keeping them on Intel does not require nv-codec-headers or --enable-cuda",
      "label": "VERIFIED",
      "check": "Grep of '+++ b/' across ffmpeg/0*.patch (0008, 0009 each name only vf_hwmap.c) and Read of both patches (0009 uses the AV_PIX_FMT_CUDA enum, defined in libavutil/pixfmt.h, only to pin derive_device=cuda)",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "Skipping 0006, 0007, 0010-0014 returns an Intel build to stock FFmpeg for those files and breaks nothing: they touch hwcontext_vulkan.c CUDA paths, the transpose_cuda filter and CUDA helper headers; 0001 and 0015 touch different hunks and 0015 was written against a tree with only 0001 applied",
      "label": "INFERRED",
      "check": "File list is VERIFIED by Grep of the patch headers; that 0001+0008+0009+0015 apply cleanly in that order needs patch --dry-run against the FFmpeg 8.1.2 tarball (a NOT RUN network step) or the first real Intel build",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "libvpl-dev exists on the build host's distro (Ubuntu noble, universe, 2023.3.0-1build1); libva-dev and libdrm-dev are in noble main",
      "label": "INFERRED",
      "check": "libvpl-dev VERIFIED by fetching packages.ubuntu.com/noble/libvpl-dev; the build host's release and whether universe is enabled are not checked (the script's own comment says noble); libva-dev and libdrm-dev not looked up. An apt failure is loud, not silent",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "FFmpeg 8.1.2's configure accepts --enable-vaapi --enable-libvpl --enable-libdrm and libvpl 2023.3.0 satisfies its minimum pkg-config version",
      "label": "INFERRED",
      "check": "raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1.2/configure is reachable via the fetch tool but the libvpl/libdrm lines were not located; settle by fetching and reading those lines at implement time, or by the configure step of a real build (NOT RUN)",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "The NVIDIA command line (apt list, configure argv) can be kept byte-identical by moving it into arrays, because every NVIDIA string is a literal in the current script",
      "label": "VERIFIED",
      "check": "Read scripts/build-ffmpeg.sh lines 401-403 (apt list) and 802-811 (configure argv); the empty-array expansions already in use (OPTIX_FLAGS and others on line 810) show set -u tolerates the pattern",
      "load_bearing": true
    },
    {
      "id": "P7",
      "claim": "proxmox-build.sh can run under fake id/dotnet/git on PATH against a stub repo and stops at the missing scripts/migrate-to-jellyfin-plus.sh before writing anything under /usr/lib or staging",
      "label": "VERIFIED",
      "check": "Read scripts/proxmox-build.sh lines 58-82 (root, dotnet, JELLYFIN_BIN, git checks), 94-191 (ffmpeg block) and 197-215 (dotnet publish, then the migrate call); PATCH_DIR is only written after step 4b",
      "load_bearing": true
    },
    {
      "id": "P8",
      "claim": "No existing test covers build-ffmpeg.sh or proxmox-build.sh's filter check, so the preflight and refusal tests are new",
      "label": "VERIFIED",
      "check": "Grep of build-ffmpeg|ORT_EP across scripts/: matches only in build-ffmpeg.sh, proxmox-build.sh, build-ffmpeg-debug.sh, fetch-ort-header.py and comments/hashes in test-vf-ort.sh (a vf_ort.c provider test); Glob scripts/test* lists five tests, none for the build script",
      "load_bearing": false
    },
    {
      "id": "P9",
      "claim": "The workbench shell can run `bash scripts/test-<name>.sh` with no arguments, so the new test must take no arguments and use no jfbin",
      "label": "VERIFIED",
      "check": "A bash --version call was refused with: only `bash scripts/test-<name>.sh` (no arguments, no flags) may run in this repo",
      "load_bearing": true
    },
    {
      "id": "P10",
      "claim": "The base is the fork's main 7855e15 (PR 5) and the tree is clean; this branch adds only the workbench start commit",
      "label": "VERIFIED",
      "check": "git log -2 --format='%h %s' printed b6fc2c1 chore(workbench) over 7855e15 GPU vendors PR 5; git status --short printed nothing. That #4 is the upstream draft is not checkable here (gh has no credential)",
      "load_bearing": false
    },
    {
      "id": "P11",
      "claim": "The spec asks for a required set per vendor and an amendment of invariant 10 in the same commit, and calls it the maintainer's decision",
      "label": "VERIFIED",
      "check": "Read docs/architecture/GPU_VENDORS.md section 5.7 lines 603-617",
      "load_bearing": true
    },
    {
      "id": "P12",
      "claim": "The shim and the probe already degrade when the binary lacks a filter, so an Intel binary without optix/fsr2/dlss does not break sessions",
      "label": "INFERRED",
      "check": "AGENTS.md invariant 10 text and ShaderLibrary.cs:1656 (a `patched` filter list) say so; the shim probe itself was not read, and PR 3 already withholds those levels on Intel. Read shim/ at implement time",
      "load_bearing": false
    },
    {
      "id": "P13",
      "claim": "docs/agent-lessons.md and docs/automations.md do not exist, so there is no lesson or automation page to search or extend; no live cluster is involved",
      "label": "VERIFIED",
      "check": "Read of docs/agent-lessons.md returned does not exist; Glob docs/*.md lists only README.md and video-enhancement.md; the change touches shell scripts and docs, so kubectl has nothing to check",
      "load_bearing": false
    },
    {
      "id": "P14",
      "claim": "Running the full ffmpeg build to prove -filters, -hwaccels and -encoders on an Intel binary is out of reach here (root, apt, network, 12G, an Intel box for any real check)",
      "label": "INFERRED",
      "check": "Cannot run the build in the narrow workbench shell; reported as NOT RUN and listed under Not verified",
      "load_bearing": false
    }
  ]
}
```
