# Plan: gu_inputs without CUDA (GPU vendors PR 6)

Stage note: my read budget ran out before I could open `UpscaleEngine.cs` around the withhold and
probe-list code (the `Withhold(...)` call at ~1573 and the `NvidiaOnly`/level-filter helpers at
~2208-2270), `docs/agent-lessons.md`, `docs/automations.md` and the harness `Program.cs`. Those are
INFERRED below. No bead id was supplied, so the PR uses `no-bead`. This repo deploys to a Proxmox
container, not the cluster, so no `kubectl` check can settle anything here.

**What is true today (read from the files).**
- `gu_inputs_init` fails hard when `cuda_load_functions` fails (`ffmpeg/gu_inputs.h` 836-839).
- A missing `libnvidia-opticalflow` is already survived (`nvof_ready = 0`): zero flow and a zero
  reactive mask. Depth falls back to flat 0.5 when ORT has no CUDA execution provider, and
  `gu_pc_init` is CPU only.
- `gu_inputs.h` includes `ffnvcodec/dynlink_loader.h` and the NVOF SDK headers unconditionally.
- `ORT_EP=openvino` refuses `WITH_FSR2` (`build-ffmpeg.sh` 289-293). Patch 0004 also cannot apply
  there: its configure context (`libnvoptix`) is added by 0002, which the openvino path skips.
- On Intel, `ShaderLibrary.IsNvidiaOnlyGame` withholds `fsr2`, `dlss` and `dlaa` unconditionally.
  `OpenVinoAvailable` is decided by a file next to the models directory (`GpuVendor.cs` 451, 467-481).

**Design (one decision made: RESOLVE the build refusal, do not leave it gated).**
1. *C*: `gu_inputs.h` gets a `GU_NO_NVOF` compile switch (default off, so NVIDIA builds are
   textually the same code). With it set, the NVOF headers and functions are not compiled. At run
   time, a failing `cuda_load_functions` (or `cuInit` and so on) no longer returns. It logs one
   warning, leaves `cu` NULL and continues with `nvof_ready = 0`. The existing branches then give
   zero flow, a zero reactive mask, phase-correlation jitter and flat depth. `gu_inputs_uninit`
   already tolerates `cu == NULL`. The CUDA-present path keeps its exact call order.
2. *Build*: `ORT_EP=openvino` plus `WITH_FSR2=1` is allowed. `WITH_OPTIX`, `WITH_DLSS` and
   `WITH_MAXINE_VSR` stay refused. A new standalone `ffmpeg/0016-add-fsr2-filter-to-build-standalone.patch`
   (fsr2 only, written against 0001 plus 0015, the same way 0015 was written against 0003) replaces
   0004 on that path. The script adds `-DGU_NO_NVOF` to the extra cflags and needs `FSR2_SDK` and
   `FSR2_LIB` but not `NVOF_SDK`. It writes a marker file `<prefix>/fsr2-nocuda`.
3. *Plugin*: a new `GpuCapability.NoCudaGameAvailable` is true when an Intel GPU was seen **and** the
   marker sits beside the models directory (same lookup as the OpenVINO provider file). `fsr2` leaves
   the Intel withheld list only when it is true, in both the probe list and the engine's `Withhold`.
   `dlss` and `dlaa` stay "needs NVIDIA". NVIDIA, `none` and Intel-without-marker output is unchanged.
   `IsNvidiaOnlyGame` stays as the name for "needs NVIDIA" and is not widened.
4. *Honest reporting*: when `fsr2` runs on Intel the session notes say motion vectors are zero (no
   NVOFA), so the report never claims flow.

**Process.** Spec commit first (GPU_VENDORS.md 5.5 rewritten; table row 6, section 2 row 55 and 53,
and open question 5 updated). Then one code commit, "GPU vendors: run gu_inputs without CUDA (PR 6)".
ENHANCEMENTS.md, docs/filters/FSR2.md and GPU_VENDORS.md change in that commit. The wrapper makes the
commits, so the PR description records the two-commit shape, and the plan assumes the implementer
sequences the edits that way.

**Tests, written first, NOT RUN (bead rev-ql69o: no bash or dotnet in this shell).**
- `scripts/gpu-vendor-harness/Program.cs`: new cases for `NoCudaGameAvailable` and the game-level
  withholding. NVIDIA, `none`, mixed and Intel-without-marker goldens are only added, never edited.
  Intel with the marker keeps `fsr2` and withholds `dlss` and `dlaa`.
- `scripts/test-gpu-vendor.sh`: runs the new cases and asserts the new probe lists.
- `scripts/test-gu-inputs.sh` plus `scripts/gu-inputs-test/no_cuda.c`, modelled on `test-vf-ort.sh`
  (libavutil from the pinned FFmpeg tarball, the pinned ORT header, a stub `ffnvcodec` header). It
  compiles `gu_inputs.h` with `-DGU_NO_NVOF -Wall -Wextra -Werror` and with the switch off against a
  stub NVOF header. It runs init with a failing `cuda_load_functions`, expecting success, zero flow,
  zero reactive, flat depth 0.5 and a measured jitter on a shifted synthetic frame. It also runs init
  with the fake CUDA succeeding and checks the call order is unchanged.

**Risks.** `0016` and the `-DGU_NO_NVOF` plumbing cannot be exercised here (no build, no Intel GPU).
If implementing it shows the standalone patch cannot be written safely against the 0015 context, fall
back to gating: keep the refusal, leave `fsr2` withheld on Intel, and say so in the PR. The C and
plugin halves stay valid either way. I have not run anything on an Intel GPU, so the temporal
measurement AGENTS.md asks for (still-pixel variation) is out of scope and goes under "Not verified".

```yaml
{
  "goal": "Let gu_inputs run without CUDA (zero motion vectors, phase-correlation jitter, flat depth) so the fsr2 level is offered on an Intel GPU, only when the binary carries that path; NVIDIA command lines stay byte-identical.",
  "files": [
    "docs/architecture/GPU_VENDORS.md",
    "docs/filters/FSR2.md",
    "ENHANCEMENTS.md",
    "ffmpeg/gu_inputs.h",
    "ffmpeg/0016-add-fsr2-filter-to-build-standalone.patch",
    "scripts/build-ffmpeg.sh",
    "scripts/test-gpu-vendor.sh",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test-gu-inputs.sh",
    "scripts/gu-inputs-test/no_cuda.c",
    "src/patcher/Transcoding/GpuVendor.cs",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/Transcoding/ShaderLibrary.cs"
  ],
  "test_commands": [
    "bash scripts/test-gu-inputs.sh   # NOT RUN here (bead rev-ql69o); orchestrator runs it",
    "bash scripts/test-gpu-vendor.sh   # NOT RUN here (bead rev-ql69o); orchestrator runs it",
    "pre-commit run --files docs/architecture/GPU_VENDORS.md docs/filters/FSR2.md ENHANCEMENTS.md scripts/build-ffmpeg.sh scripts/test-gpu-vendor.sh scripts/test-gu-inputs.sh"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "gu_inputs_init succeeds with no libcuda: flow zero, reactive zero, depth flat 0.5, jitter by phase correlation (C harness, NOT RUN here)",
    "The CUDA-present path keeps its call order, and the NVIDIA, none, mixed and Intel-without-marker goldens in the vendor harness are unchanged (only added to, NOT RUN here)",
    "fsr2 is offered on Intel only when the capability reports the no-CUDA path (marker file present); dlss and dlaa stay 'needs NVIDIA'",
    "build-ffmpeg.sh: ORT_EP=openvino with WITH_FSR2=1 is accepted without NVOF_SDK, and WITH_OPTIX, WITH_DLSS and WITH_MAXINE_VSR are still refused; the PR says which of resolve or gate was chosen",
    "A Spec commit updating GPU_VENDORS.md 5.5 comes first, then one code commit 'GPU vendors: run gu_inputs without CUDA (PR 6)' that also carries the docs and ENHANCEMENTS.md",
    "PR body has 'Stacked on #4', What it adds, Testing, Not verified (no Intel GPU run); the tests are marked NOT RUN; sanchitd5's repo is not touched"
  ],
  "premises": [
    {"id": "P1", "claim": "gu_inputs_init returns an error when cuda_load_functions fails, and a missing NVOFA already degrades to zero flow", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h lines 836-850 and 944-946", "load_bearing": true},
    {"id": "P2", "claim": "ORT_EP=openvino refuses WITH_FSR2, and patch 0004 cannot apply without 0002's context", "label": "VERIFIED", "check": "Read scripts/build-ffmpeg.sh lines 289-293 and ffmpeg/0004-add-fsr2-and-dlss-filters-to-build.patch (context line libnvoptix)", "load_bearing": true},
    {"id": "P3", "claim": "On Intel every game level is withheld through IsNvidiaOnlyGame, and OpenVinoAvailable is decided by a marker file next to the models directory", "label": "VERIFIED", "check": "Read src/patcher/Transcoding/ShaderLibrary.cs 960-965 and GpuVendor.cs 437-481", "load_bearing": true},
    {"id": "P4", "claim": "The probe level list and the engine's Withhold both go through UpscaleEngine.NvidiaOnly(axis, level) and can take the capability as a second input without changing NVIDIA output", "label": "INFERRED", "check": "Read UpscaleEngine.cs ~1573 and ~2208-2270 and the harness Program.cs before editing (grep hits seen, bodies not read)", "load_bearing": true},
    {"id": "P5", "claim": "A standalone fsr2-only build patch (0016) can be written against the tree with 0001 and 0015 applied, and nv-codec-headers (needed by gu_inputs.h and fsr2_filter_deps) are installed unconditionally by the script", "label": "INFERRED", "check": "Read 0015 and build-ffmpeg.sh lines 620-700 and the nv-codec-headers step; if it cannot be written safely, gate instead (keep refusal, fsr2 stays withheld) and say so in the PR", "load_bearing": true},
    {"id": "P6", "claim": "Depth on Intel stays flat, because the CUDA EP append fails and gu_depth_init falls back to flat; no OpenVINO depth is added", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h lines 616-631 and 857-860", "load_bearing": false},
    {"id": "P7", "claim": "Nothing in the live cluster is involved: the plugin is deployed to a Proxmox container, so kubectl and the MCP tools cannot settle any premise", "label": "INFERRED", "check": "CLAUDE.md 'Where the work actually happens'; no cluster command was run", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "Standalone build patch for fsr2 without the OptiX context", "checked": "ffmpeg/0015-add-ort-filter-to-build-standalone.patch in this repo (the same technique for ort)", "verdict": "extend", "reason": "Copy 0015's approach for vf_fsr2 rather than invent a new mechanism"},
    {"need": "Marker-file detection of what the binary carries", "checked": "GpuVendor.OpenVinoProvider / OpenVinoProviderFile in src/patcher/Transcoding/GpuVendor.cs", "verdict": "extend", "reason": "Same directory lookup, one more file name"},
    {"need": "C harness for a header-only module", "checked": "scripts/test-vf-ort.sh and scripts/vf-ort-test/provider_calls.c (fake OrtApi, libavutil from the pinned FFmpeg 8.1.2 tarball)", "verdict": "extend", "reason": "Same scaffolding and the same pinned downloads; only the harness C differs"},
    {"need": "A non-NVOFA motion vector source", "checked": "Existing behaviour: zero flow when nvof_ready is 0 (gu_inputs.h 944-946); no CPU block matching or optical flow network is added", "verdict": "use", "reason": "The brief asks for zero vectors; a better flow source is a later measurement-driven PR"}
  ]
}
```
