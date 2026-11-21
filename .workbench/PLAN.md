# Plan: run fsr2 without CUDA on Intel (GPU vendors PR 6)

**Where this starts.** Fork PR #2's branch (`agent/workbench-6dbcd9d0dffa423d`, tip a264b08) is in this
checkout's object store. Its `.workbench/PLAN.md` is recoverable with `git diff d14571f 4841c1a`, and it
already holds a full implementation of that plan. `gh` has no credential here, so I read the branch with
`git diff 7855e15 agent/workbench-6dbcd9d0dffa423d`. This task re-does the PR on top of main (7855e15)
and follows the same design. I re-read it and found three defects to fix, listed below. No bead id was
given and `beads_list gu_inputs` found none, so the PR uses `no-bead`. The repo has no
`docs/agent-lessons.md` or `docs/automations.md`, so there was nothing to search there. The code deploys
to a Proxmox container, not the cluster, so `kubectl` cannot settle any premise. `kubectl get pods -A`
shows only unrelated workloads.

**Design (unchanged from the approved plan).**
1. `ffmpeg/gu_inputs.h`: a `GU_NO_NVOF` compile switch removes `ffnvcodec`, the Optical Flow headers, the
   CUDA context, the NVOFA session, and the flow and reactive code. Without it, a `cuda_load_functions`,
   `cuInit` or context-push failure logs a warning and carries on with `nvof_ready = 0` (zero flow, zero
   reactive, measured jitter, flat depth) instead of failing init. The successful CUDA call order is
   kept exactly (`gu_cuda_open` just wraps it).
2. `scripts/build-ffmpeg.sh`: `ORT_EP=openvino` plus `WITH_FSR2=1` is allowed. `WITH_OPTIX`, `WITH_DLSS`
   and `WITH_MAXINE_VSR` stay refused. A new standalone `ffmpeg/0016-add-fsr2-filter-to-build-standalone.patch`
   replaces 0004 on that path, and `-DGU_NO_NVOF` is added to CFLAGS. No `NVOF_SDK` is needed. After the
   `-filters` check passes, the script writes `<prefix>/fsr2-nocuda`. An OpenVINO build without fsr2
   removes the marker.
3. Plugin: `GpuCapability.NoCudaGameAvailable` is true only for a detected Intel GPU plus the marker beside
   the models directory's parent (same lookup as `OpenVinoProvider`). `NvidiaOnly("Game", level)` returns
   false for `fsr2` only when `NoCudaGameNow()` is true, and both the probe lists and the engine's
   `Withhold` go through it. `dlss` and `dlaa` stay "needs NVIDIA". `IsNvidiaOnlyGame` is not widened;
   `IsCudaFreeGame` is a separate predicate.
4. Honest reporting: `SessionRecord.GameMotionNote` appears only when a game level ran on Intel.

**Defects in the old branch, fixed here.**
- `0016` hunk 3 (the `libffxfsr2` require in `configure`) has 5 old context lines, but its header says
  `-7361,6 +7361,11`. `patch` would reject that as malformed. Fix: take the `}` context lines that 0015
  uses, then add a patch-application check to `test-gu-inputs.sh`. That check applies 0001, 0015 and 0016
  in order to the pristine FFmpeg 8.1.2 tarball that the script already extracts, and asserts that
  `ff_vf_fsr2`, `CONFIG_FSR2_FILTER` and `fsr2_filter_deps` land.
- Depth is flat on Intel, not "model (OpenVINO)" as the old docs say. `gu_depth_init` only ever appends the
  ORT CUDA execution provider (`gu_inputs.h` 616-631), and the OpenVINO ORT has none, so it falls back to
  flat. PR 5 added `device=openvino` to `vf_ort` only. The Spec commit rewrites section 5.5 to say depth is
  flat. `GameMotionNote` also says so, because the record would otherwise report `depth model`
  (the default) while flat ran, which is the overstatement AGENTS.md invariant 7 forbids.
- The old Spec text claimed "written with no compiler available". The new docs state only what actually
  ran (see Tests).

**Process.** Two commits, made by the wrapper. (1) "Spec: GPU vendors PR 6, ..." changes only
`docs/architecture/GPU_VENDORS.md` section 5.5: the decision of no new flow source, the input table
(motion vectors zero, reactive empty, jitter measured, depth flat), the build, the marker and the plugin
rule. It also changes table rows 6, section 2's `gu_inputs.h` row, section 4's Game row, and open
question 5. (2) "GPU vendors: run fsr2 without CUDA on Intel (PR 6)" carries the code, tests, FSR2.md,
ENHANCEMENTS.md and the status paragraph in GPU_VENDORS.md. I cannot commit, so the PR body states the
two-commit shape. Fork PR #2 cannot be closed from here (no `gh` credential), so that is a hand-off line
in the PR body. Nothing is done to sanchitd5's repo.

**Tests, written before the code.**
- `scripts/gpu-vendor-harness/Program.cs` gets `NoCudaGame()` (add-only; no existing golden is edited).
  It checks the capability for NVIDIA, no-GPU, mixed and Intel with and without the marker, across the
  `GpuVendor` settings. It checks the probe: Intel without the marker is unchanged (`Withheld.Game` is
  `fsr2,dlss,dlaa`), and Intel with the marker offers `fsr2` and withholds `dlss,dlaa`. It checks the
  engine plan: `fsr2` runs, has a chain node and no CUDA hwaccel, and carries `GameMotionNote`; `dlss`
  and `dlaa` stay withheld; NVIDIA with the marker file is byte-identical, and `GameMotionNote` is null.
  New NVIDIA golden: the fsr2 chain string on an NVIDIA box is asserted before and after.
- `scripts/gu-inputs-test/no_cuda.c` and `scripts/test-gu-inputs.sh`, modelled on `test-vf-ort.sh`
  (libavutil from the pinned tarball, the pinned ORT header). They compile `gu_inputs.h` with
  `-DGU_NO_NVOF -Werror`, then run init and frames: zero flow and reactive, flat depth, jitter measured.
  They also compile and run the default variant against stub `ffnvcodec` and Optical Flow headers and a
  fake CUDA. The scenarios are a failing load, `cuInit` and push, and the unchanged call order on success.
  New: the patch-application check above.
- `scripts/test-gpu-vendor.sh`: header comment only (it already runs the whole harness).

**What I can and cannot run here (checked).**
- `bash scripts/test-gu-inputs.sh` needs `gcc` and `make`, plus network to ffmpeg.org and nuget.org.
  `bash scripts/test-vf-ort.sh` printed `test-vf-ort: gcc not found` and exited 2, so the C harness
  **cannot run in this sandbox**. The shell allow-list names no compiler.
- `bash scripts/test-gpu-vendor.sh` needs a directory of Jellyfin server assemblies as `$1`.
  `dotnet build` of the harness restored, then failed with CS0246 on `MediaBrowser.*`. No such DLLs exist
  in the worktree, and `kubectl` cannot copy them from the pod. Whether NuGet's `Jellyfin.*` packages can
  stand in is unverified, and `MediaBrowser.MediaEncoding` may not be published.
- So the pass counts and the 3 mutations asked for cannot be produced unless the assemblies and gcc
  turn up. The implement stage tries once. If it cannot, it says "NOT RUN" with these reasons, does not
  invent counts, and lists the mutations as predicted catches only. The owner should run:
  `scripts/test-gpu-vendor.sh <jfbin>` (1082 checks before this PR, plus the new ones) and
  `scripts/test-gu-inputs.sh`.
- Mutations, each to be reverted straight after it is run:
  (M1) drop `intelSeen &&` from `NoCudaGameAvailable`, caught by "NVIDIA with the marker file: no NoCudaGameAvailable";
  (M2) make `IsCudaFreeGame` also accept `dlss`, caught by "dlss is still withheld" and the Withheld-list check;
  (M3) make `gu_inputs_init` return the error when `gu_cuda_open` fails again, caught by the "CUDA fails to load: init succeeds" scenario.
  (M4, spare) drop `-DGU_NO_NVOF` from the build script's CFLAGS: nothing in the harness catches it (the
  C test sets the flag itself), so I add a `grep` assertion in `test-gu-inputs.sh` on the script text.

**Risks.** Nothing is compiled or run on Intel here. Whether FSR2 with zero motion vectors looks
acceptable is unmeasured. AGENTS.md says per-frame metrics cannot judge it, and the still-pixel temporal
measurement has not been made. Both go in "Not verified". NVIDIA behaviour changes in one way: a CUDA that
will not start now degrades to zero flow instead of failing the filter. The PR says so.

```yaml
{
  "goal": "Let gu_inputs run without CUDA (zero motion vectors, empty reactive mask, measured jitter, flat depth) so the fsr2 level is offered and runs on an Intel GPU only when the patched ffmpeg left the fsr2-nocuda marker; dlss and dlaa stay NVIDIA-only; NVIDIA command lines stay byte-identical.",
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
    "bash scripts/test-gu-inputs.sh",
    "bash scripts/test-gpu-vendor.sh <dir holding the Jellyfin server assemblies>",
    "bash scripts/test-vf-ort.sh"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "Spec commit first: GPU_VENDORS.md section 5.5 rewritten (no new flow source; zero vectors, empty reactive, measured jitter, flat depth; the build; the fsr2-nocuda marker; the plugin rule), with table row 6, the section 2 and 4 rows and open question 5 updated",
    "Second commit 'GPU vendors: run fsr2 without CUDA on Intel (PR 6)' carries the code, tests, FSR2.md, ENHANCEMENTS.md and GPU_VENDORS.md status in the same commit",
    "gu_inputs_init succeeds with no usable CUDA: zero flow, zero reactive mask even with react=flow, flat depth, jitter measured; the CUDA-present call order is unchanged (scripts/test-gu-inputs.sh)",
    "ORT_EP=openvino WITH_FSR2=1 is accepted without NVOF_SDK; WITH_OPTIX, WITH_DLSS and WITH_MAXINE_VSR are still refused; ffmpeg/0016 applies after 0001 and 0015 on the pristine FFmpeg 8.1.2 tree (asserted by the test); the marker is written only after the -filters check",
    "fsr2 is offered and runs on Intel only with a detected Intel GPU plus the marker; dlss and dlaa stay 'needs NVIDIA'; without the marker the Intel probe and plan are as PR 3 left them",
    "NVIDIA, none and mixed-box probes and command lines are byte-identical: NVIDIA goldens are only added to in the harness, never edited",
    "The session record on Intel says the motion vectors are zero and the depth is flat, so reporting never overstates",
    "Test results are stated as run or NOT RUN with the reason; if gcc or the Jellyfin assemblies are unavailable, no pass counts are invented and the 3 mutations are listed as not run",
    "PR body has 'Stacked on #4', What it adds, Testing, Not verified (no Intel GPU run), Refs/no-issue, no-bead, a lesson: line, and a hand-off line to close fork PR #2 as superseded; sanchitd5's repo is untouched"
  ],
  "premises": [
    {"id": "P1", "claim": "The fork PR #2 branch is readable in this checkout and holds a full implementation of the approved plan; the plan text is recoverable from git history", "label": "VERIFIED", "check": "git log agent/workbench-6dbcd9d0dffa423d; git diff d14571f 4841c1a -- .workbench/PLAN.md; git diff --stat 7855e15 agent/workbench-6dbcd9d0dffa423d (13 files, +818/-39)", "load_bearing": false},
    {"id": "P2", "claim": "gu_inputs_init fails hard when cuda_load_functions fails, while a missing NVOFA already degrades to zero flow", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h 836-850 and the branch diff of the same hunk", "load_bearing": true},
    {"id": "P3", "claim": "ORT_EP=openvino currently refuses WITH_FSR2, WITH_FSR2/WITH_DLSS already require WITH_ORT=1, and the source tree is the pristine FFmpeg 8.1.2 tarball with 0001 applied first", "label": "VERIFIED", "check": "Read scripts/build-ffmpeg.sh 283-319, 546-553, 646", "load_bearing": true},
    {"id": "P4", "claim": "Both the probe lists and the engine's Withhold reach NvidiaOnly(axis, level) / IsNvidiaOnlyGame, so one capability-aware predicate changes both without moving NVIDIA output", "label": "VERIFIED", "check": "Read UpscaleEngine.cs 1573 and 2205-2292; Grep shows IsNvidiaOnlyGame has one call site (1573) plus NvidiaOnly (2220)", "load_bearing": true},
    {"id": "P5", "claim": "The old 0016 patch has a malformed third hunk (header -7361,6 but 5 old lines) and would be rejected by patch", "label": "VERIFIED", "check": "Counted the hunk lines in git diff 7855e15 agent/workbench-6dbcd9d0dffa423d -- ffmpeg/0016-*.patch, against 0015's equivalent hunk (-7358,6 with 6 old lines); the new test applies the patch to prove the fix", "load_bearing": true},
    {"id": "P6", "claim": "Depth on the OpenVINO ONNX Runtime is flat, because gu_depth_init appends only the CUDA execution provider and falls back to flat when it fails", "label": "VERIFIED", "check": "Read ffmpeg/gu_inputs.h 616-631 and 857-860; PR 5 only touched vf_ort.c for OpenVINO", "load_bearing": true},
    {"id": "P7", "claim": "vf_fsr2.c reaches CUDA only through gu_inputs.h, so -DGU_NO_NVOF removes every ffnvcodec and Optical Flow dependency of the fsr2 filter", "label": "INFERRED", "check": "Grep of vf_fsr2.c showed includes of gu_inputs.h, vulkan.h and ffx_fsr2.h only; the compile of a real fsr2 filter under GU_NO_NVOF is not run here (no gcc, no FidelityFX SDK) and is the WITH_FSR2=1 ORT_EP=openvino build on the Intel box", "load_bearing": true},
    {"id": "P8", "claim": "The C harness and the vendor harness cannot run in this sandbox: gcc is missing and no Jellyfin assemblies are present", "label": "VERIFIED", "check": "bash scripts/test-vf-ort.sh -> 'test-vf-ort: gcc not found' (exit 2); dotnet build scripts/gpu-vendor-harness -> restore ok, CS0246 on MediaBrowser.*; bash scripts/test-gpu-vendor.sh -> needs a bin dir", "load_bearing": true},
    {"id": "P9", "claim": "Nothing in the live cluster is involved: the plugin deploys to a Proxmox container", "label": "VERIFIED", "check": "CLAUDE.md 'Where the work actually happens'; kubectl get pods -A lists no jellyfin-plus deployment, only unrelated workloads", "load_bearing": false},
    {"id": "P10", "claim": "No bead exists for this work, and the repo has no docs/agent-lessons.md or docs/automations.md to update", "label": "VERIFIED", "check": "beads_list query gu_inputs (include_closed) -> []; Grep on both paths -> path does not exist", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "Standalone build patch for fsr2 without the OptiX context", "checked": "ffmpeg/0015-add-ort-filter-to-build-standalone.patch in this repo (the same technique for ort) and 0004's fsr2 hunks", "verdict": "extend", "reason": "Same three files and same technique as 0015, written against 0001 + 0015"},
    {"need": "Marker-file detection of what the binary carries", "checked": "GpuDetector.OpenVinoProvider / OpenVinoProviderFile in src/patcher/Transcoding/GpuVendor.cs", "verdict": "extend", "reason": "Same directory lookup, one more file name (shared FileBesideModels helper)"},
    {"need": "C test scaffolding for a header-only module", "checked": "scripts/test-vf-ort.sh and scripts/vf-ort-test/provider_calls.c, scripts/fetch-ort-header.py (pinned FFmpeg 8.1.2 tarball, pinned ORT header)", "verdict": "extend", "reason": "Same downloads and structure; only the harness C differs"},
    {"need": "A non-NVOFA motion vector source", "checked": "Existing behaviour: zero flow when nvof_ready is 0 (gu_inputs.h 944-946); no CPU block matcher or optical-flow network is added", "verdict": "use", "reason": "The brief asks for zero vectors; a real flow source needs the temporal measurement AGENTS.md prescribes and is a later PR"}
  ]
}
```
