# Plan: GPU vendors PR 6, `gu_inputs` without CUDA (fsr2 on Intel)

The plan stage ran out of tool budget before every premise was read, so several premises below are INFERRED
and the implement stage must settle them first (listed in order of risk).

**Blocker to resolve first.** This worktree is on `agent/workbench-8962ce40064b4da4` at `e8ff4a3`, which is
`4f9b7d2` plus the workbench start commit. It does NOT contain the Intel series: `docs/architecture/GPU_VENDORS.md`,
`scripts/test-gpu-vendor.sh`, `scripts/gpu-vendor-harness/` and `src/patcher/Transcoding/GpuVendor.cs` are absent.
The base `worktree-agent-c50db4079c58fcc4` (tip `7855e15`, PR 5) is a local branch, and `git diff HEAD <base> --stat`
shows 38 files, +6058 lines. The brief says to cut the work branch from that tip. I cannot check it out (no git write
verbs), so the implement stage must run on a checkout whose HEAD is `7855e15`, named `worktree-agent-<16 hex>`. If
it cannot be, the correct report is `blocked_on_owner`: do not re-create the series files by copying them.

**Shape (two commits, as PR 2, 3 and 5 did).**

1. `Spec: GPU vendors PR 6, gu_inputs without CUDA (design note)`: rewrites section 5.5 of GPU_VENDORS.md, plus the
   table row, the header paragraph, open question 5 and the section 7 NVIDIA-identical statement. Section 5.5
   currently says "a flow provider interface in `gu_inputs.h` with NVOFA as one implementation and a non-CUDA one as
   another, until a provider measures well Intel offers no game-upscaler levels". The note must pick the fallback
   the brief names (zero motion vectors, the existing CPU phase-correlation jitter, the existing depth fake) and say
   why it is the smallest honest step, what picture it gives (FSR2 without motion vectors degrades to a temporal
   accumulator with no reprojection, so moving content ghosts or softens), and that no temporal measurement or Intel
   run exists, so the level ships labelled with that. Open question 5 becomes "measure it".
2. `GPU vendors: run gu_inputs without CUDA so fsr2 works on Intel (PR 6)`: tests first, then code, then docs and
   ENHANCEMENTS.md in the same commit.

**Code (to confirm by reading in the implement stage).**

- `ffmpeg/gu_inputs.h`: `gu_inputs_init` stops requiring libcuda. When libcuda or NVOFA is unavailable it enters a
  no-CUDA mode: motion vectors are a zeroed buffer, jitter keeps the phase-correlation path, depth keeps the existing
  fake when no model is given. The existing handling of missing NVIDIA optical flow is the model to extend. The CUDA
  path is not edited, only a branch added before it.
- `ffmpeg/vf_fsr2.c` (and `vf_dlss.c` only if it shares `gu_inputs_init` and would otherwise change behaviour; DLSS
  stays NVIDIA-only and must still fail or be withheld as today).
- The engine: `ShaderLibrary.cs` / `UpscaleEngine.cs` / `GpuVendor.cs`. Remove `fsr2` from the Intel-withheld
  NVIDIA-only list ONLY when the binary carries the no-CUDA path. "When that path is built" needs a run-time signal
  the engine can read, because the 0003/0004/0005 patch stack and `ORT_EP=openvino` refuse `WITH_FSR2` today.
  Candidate: a marker file beside the patched binary written by `scripts/build-ffmpeg.sh`, or a filter option or
  `-h filter=fsr2` string the existing filter probe can read. The implement stage picks one after reading how
  PR 3's withholding and the shim's per-invocation filter probe work. `dlss`, `dlaa`, `optix`, `dlpp-*` and
  `vsr-rtcuda` stay withheld. The record and probe must say fsr2 runs without motion vectors (invariant 7).
- `scripts/build-ffmpeg.sh`: lets an `ORT_EP=openvino` build include fsr2 without the CUDA SDK, or states that it
  cannot. If the FSR2 shader compiler dependency makes that unreasonable, say so in the Spec and keep the engine gate
  false, so the level stays withheld, which is still a correct and honest PR.

**Tests first.**

- `scripts/test-gpu-vendor.sh` / `scripts/gpu-vendor-harness/Program.cs`: with the no-CUDA marker absent, Intel still
  withholds `fsr2` exactly as before (probe, session record, summary); with it present, `fsr2` is offered and runs on
  Intel, `dlss`/`dlaa`/`optix`/`dlpp-*`/`vsr-rtcuda` still withheld, and the record names the zero-motion-vector
  fallback. NVIDIA, no-GPU and mixed goldens do not change: no golden string in the existing NVIDIA checks is edited,
  and the diff to those checks must be additions only.
- A C-side harness for `gu_inputs.h`, modelled on `scripts/test-vf-ort.sh` / `scripts/vf-ort-test/`: compile the
  header with `-Wall -Wextra -Werror` with `dlopen("libcuda.so.1")` failing, call `gu_inputs_init`, and assert it
  succeeds, the MV plane is all zero, jitter comes from phase correlation and depth is the fake; with CUDA available
  the call sequence is unchanged. This is new `scripts/test-vf-gu-inputs.sh` plus `scripts/vf-gu-inputs-test/`. The
  CI affected-tests job picks up only `scripts/test_*.py`; a bash harness needs a line in the PR description saying so.
- Mutations (at least 3, each caught): (a) the fsr2 gate always true on Intel, (b) the gate removed from NVIDIA-only
  list unconditionally, (c) `gu_inputs_init` still requiring libcuda, (d) MV buffer left uninitialised, (e) an NVIDIA
  golden changed. Report the failing-check counts.

**Docs, same commit as the code.** ENHANCEMENTS.md, `docs/filters/FSR2.md`, GPU_VENDORS.md status paragraph, `docs/README.md`
and `context-map.md` only if a file is added (the new harness), INSTALL.md only if a build variable changes.

**Not verified (to state in the PR).** No Intel GPU run, no NVIDIA run, no served segment, no temporal measurement of
the zero-MV picture, no Vulkan run of the FSR2 shaders on Intel. The harness shows the plugin and filter logic only.

**Process notes.** Branch name `worktree-agent-<16 hex>` cut from `7855e15`, never rebase or force-push earlier
branches. The PR body is written to `.workbench/PR_BODY.md` with "Stacked on #4", What it adds, Testing, Not verified,
a `Refs`/`no-issue` line, the bead id or `no-bead`, and a `lesson:` line naming a changed file. In this shell only
`python3 scripts/test_*.py` style commands run, so `scripts/test-gpu-vendor.sh` (needs dotnet and the 12.4 DLLs) may
not be runnable in the implement session; if so the pass counts must be reported as NOT RUN, not claimed. I did not
find docs/workbench.md in this checkout (it is not in HEAD), so where the 12.4 DLLs come from is unverified.

```yaml
{
  "goal": "Run gu_inputs without CUDA (zero motion vectors, phase-correlation jitter, existing depth fake) so the fsr2 level can run on an Intel GPU, and un-withhold fsr2 on Intel only when a binary carrying that path is present; NVIDIA command lines stay byte-identical.",
  "files": [
    "docs/architecture/GPU_VENDORS.md",
    "ENHANCEMENTS.md",
    "docs/filters/FSR2.md",
    "docs/README.md",
    "context-map.md",
    "INSTALL.md",
    "ffmpeg/gu_inputs.h",
    "ffmpeg/vf_fsr2.c",
    "scripts/build-ffmpeg.sh",
    "scripts/test-gpu-vendor.sh",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test-vf-gu-inputs.sh",
    "scripts/vf-gu-inputs-test/gu_inputs_nocuda.c",
    "src/patcher/Transcoding/GpuVendor.cs",
    "src/patcher/Transcoding/ShaderLibrary.cs",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/PatcherHost.cs"
  ],
  "test_commands": [
    "scripts/test-gpu-vendor.sh <jfbin with Jellyfin 12.4 MediaBrowser.*.dll>",
    "scripts/test-vf-gu-inputs.sh",
    "scripts/test-vf-ort.sh",
    "python3 scripts/test-shim-device.py",
    "pre-commit run --all-files"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "Two commits on a branch named worktree-agent-<16 hex> cut from 7855e15: first 'Spec: GPU vendors PR 6, ...' updating GPU_VENDORS.md section 5.5, then one code commit 'GPU vendors: <imperative> (PR 6)'.",
    "gu_inputs_init no longer requires libcuda: with libcuda absent it succeeds with zero motion vectors, phase-correlation jitter and the existing depth fake, proven by a C harness compiled -Wall -Wextra -Werror.",
    "fsr2 is removed from the Intel-withheld list only when the binary carries the no-CUDA path; with it absent, Intel withholds fsr2 exactly as before.",
    "dlss, dlaa, optix, dlpp-* and vsr-rtcuda stay withheld on Intel.",
    "NVIDIA, no-GPU and mixed-machine command lines are byte-identical: the existing goldens in scripts/test-gpu-vendor.sh are not edited, only added to.",
    "The record, probe and summary say fsr2 runs without motion vectors on Intel (reporting never overstates).",
    "Docs and ENHANCEMENTS.md change in the same code commit.",
    "Harness pass counts reported, with at least 3 mutations each caught by a named check; anything not run is labelled NOT RUN.",
    "The PR body says plainly: no Intel GPU run, no served segment, no temporal measurement of the zero-motion picture.",
    "The sanchitd5 repo is not touched; no earlier branch is rebased or force-pushed."
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The checked-out worktree HEAD (e8ff4a3) does not contain the Intel series; the base branch worktree-agent-c50db4079c58fcc4 (tip 7855e15, PR 5) does.",
      "label": "VERIFIED",
      "check": "Glob for docs/architecture/GPU_VENDORS.md and scripts/test-gpu-vendor.sh returned nothing; `git diff --stat HEAD worktree-agent-c50db4079c58fcc4` listed 38 files including both; `git log --oneline -12 worktree-agent-c50db4079c58fcc4` shows 7855e15 on top of the series.",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "GPU_VENDORS.md section 5.5 describes PR 6 as a flow-provider interface in gu_inputs.h and says Intel offers no game-upscaler levels until a provider measures well; the PR table row 6 depends on PR 5 and is proved by temporal measurements.",
      "label": "VERIFIED",
      "check": "Read via `git diff -U12 3325599 7855e15 -- docs/architecture/GPU_VENDORS.md` (the section 5.5 opening and table row 6 appear as context).",
      "load_bearing": false
    },
    {
      "id": "P3",
      "claim": "scripts/test-gpu-vendor.sh builds scripts/gpu-vendor-harness against a jfbin directory of MediaBrowser.*.dll, runs the dotnet harness, and then runs python3 scripts/test-shim-device.py; its header documents the NVIDIA byte-identical goldens.",
      "label": "VERIFIED",
      "check": "Read in full via `git diff HEAD worktree-agent-c50db4079c58fcc4 -- scripts/test-gpu-vendor.sh`.",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "gu_inputs_init in ffmpeg/gu_inputs.h requires libcuda today, already tolerates missing NVIDIA optical flow, and the repo already has phase-correlation jitter and a depth fake that can be reused without CUDA.",
      "label": "INFERRED",
      "check": "Read ffmpeg/gu_inputs.h and ffmpeg/vf_fsr2.c (this stage's tool budget ended before they were read); the task brief states it. Also `code_graph` on the filter sources if indexed.",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "vf_fsr2 can run (Vulkan/FSR2 shaders, no CUDA frames) once gu_inputs no longer needs CUDA, i.e. vf_fsr2.c itself does not hold a second hard CUDA dependency (hw frames context, cuda device, CUDA-resident input).",
      "label": "INFERRED",
      "check": "Read ffmpeg/vf_fsr2.c, the filter's query_formats / config_props / frame handling, and docs/filters/FSR2.md for what the filter takes as input today.",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "An ORT_EP=openvino ffmpeg build refuses WITH_FSR2, because patches 0004 and 0005 stack on 0003 and need the FSR2 shader compiler and NVIDIA headers; so 'when that path is built' needs either a build-script change or a gate that stays false until one exists.",
      "label": "INFERRED",
      "check": "The PR 5 spec text states the refusal (read). Read scripts/build-ffmpeg.sh and the ffmpeg/0004*/0005* patches on the base branch to see whether fsr2 can be built without the CUDA SDK, and how the engine can learn the binary carries the no-CUDA path (marker file or filter probe).",
      "load_bearing": true
    },
    {
      "id": "P7",
      "claim": "The Intel-withheld NVIDIA-only level list (optix, dlpp-*, vsr-rtcuda, fsr2, dlss, dlaa) lives in the patcher's ShaderLibrary/UpscaleEngine and is applied to the probe, the session and the summary; removing fsr2 conditionally touches the same place in each.",
      "label": "INFERRED",
      "check": "Grep for the withheld list and GpuWithheld in src/patcher/Transcoding on the base branch (the PR 3 spec text in section 5.3 says so).",
      "load_bearing": true
    },
    {
      "id": "P8",
      "claim": "The dotnet harness can be built and run in the implement session: the .NET 10 SDK is in the image and Jellyfin 12.4 MediaBrowser DLLs can be obtained from docs/workbench.md or another documented source.",
      "label": "INFERRED",
      "check": "docs/workbench.md is not in this checkout's HEAD; check it on the base branch and look for a jfbin directory. The implement shell allows only python3 scripts, `pre-commit`, git and kubectl reads, so running dotnet/bash harnesses may be impossible; if so pass counts are NOT RUN and that goes in the PR.",
      "load_bearing": true
    },
    {
      "id": "P9",
      "claim": "A bash C-harness for gu_inputs.h modelled on scripts/test-vf-ort.sh is the right test vehicle, and CI's affected-tests job only auto-runs scripts/test_*.py.",
      "label": "INFERRED",
      "check": "Read scripts/test-vf-ort.sh and scripts/vf-ort-test/ on the base branch; the task statement says new scripts/test_*.py are picked up automatically.",
      "load_bearing": false
    }
  ],
  "reuse_check": [
    {
      "need": "CPU jitter, depth and flow inputs for FSR2 without CUDA",
      "checked": "ffmpeg/gu_inputs.h in this repo (phase-correlation jitter and the depth fake already present per the brief); not read in this stage",
      "verdict": "extend",
      "reason": "The brief names the existing jitter and depth fake; add a zero-MV branch rather than a new optical-flow provider."
    },
    {
      "need": "Compile-and-call harness for a C filter header against fakes",
      "checked": "scripts/test-vf-ort.sh and scripts/vf-ort-test/ (PR 5, base branch)",
      "verdict": "extend",
      "reason": "Same pattern: compile with -Wall -Wextra -Werror and call the setup against a fake dlopen/OrtApi."
    },
    {
      "need": "Per-vendor level withholding and its probe/record reporting",
      "checked": "PR 3 code in src/patcher/Transcoding (ShaderLibrary, UpscaleEngine, GpuVendor) on the base branch",
      "verdict": "extend",
      "reason": "Make the fsr2 entry conditional on a no-CUDA capability flag instead of adding a second mechanism."
    }
  ]
}
```
