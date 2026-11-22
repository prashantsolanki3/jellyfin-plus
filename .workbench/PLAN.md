# Plan: address the maintainer's review of the fsr2-without-CUDA PR (round 1)

The base branch is the PR head (commit 449a361 on top of 1117833). Two things need a decision, and the nits follow.

1. **Hard error stays on NVIDIA.** In `ffmpeg/gu_inputs.h` `gu_inputs_init`, put the original behaviour back under `#ifndef GU_NO_NVOF`.
   A failed `cuda_load_functions`, `cuInit`, `cuDeviceGet`, retain or push returns the error again.
   Only a failed NVOFA init stays soft, as it was before the PR. The soft "no usable CUDA" path goes away and the `cuda_on` variable goes with it.
   The `GU_NO_NVOF` build is unchanged. The header comment at the `GU_NO_NVOF` define and the docs lose the "CUDA that will not start now degrades" sentence.
2. **fsr2 start-up probe on the Intel device.**
   - Copy `src/patcher/CachedProbe.cs` unchanged from the fork branch `worktree-agent-b7ff7a746bf4099c` (read with `git diff HEAD <branch> -- src/patcher/CachedProbe.cs`). It is already `Jellyfin.Plugin.Plus.Patcher`, and the harness csproj globs `src/patcher/**/*.cs`.
   - Add `Fsr2ProbeArgs()` in `UpscaleEngine`: one 128x72 `testsrc` frame through `format=gbrpf32le,fsr2=w=256:h=144:jitter=zero:reactive=none:depth=flat,format=yuv420p` into `-f null -`.
     - No `-init_hw_device`: `vf_fsr2.c` opens its own Vulkan instance and takes `devs[device_index]` (default 0), and `GameFilter` never sets `device=`, so the probe tests the device a session gets.
   - The probe is a `CachedProbe<Fsr2ProbeResult>` where `Ok == true` is conclusive and kept for the process.
     - A failed run is an answer with `Ok == false`, kept as the last value and retried after a 5-minute TTL.
     - ffmpeg missing, a timeout or a throw is null.
     - It runs the patched binary (`ShaderLibrary.PatchedFfmpegPath()`) with a 20 s kill, both pipes drained, as `VulkanDenoiseRunsOnPatchedBinary` does.
   - It is started from `PatcherHost.Apply` through `UpscaleEngine.StartFsr2Probe()`, only on an Intel path with the marker. NVIDIA, no-GPU and mixed boxes never spawn it.
     `Peek()` also kicks a retry once the TTL has passed, and never runs the probe on the request path.
   - `NoCudaGameNow()` becomes: Intel, detected, `NoCudaGameAvailable`, and `Peek()?.Ok == true`. Until the first probe lands, fsr2 stays withheld.
   - The withheld reason for fsr2 on an Intel box that has the marker but no passing probe says so (probe failed or pending), not "needs NVIDIA". It is a small `AddWithheld` change, so invariant 7 holds.
   - Test seam: an internal factory that takes the probe function, a synchronous scheduler and a clock, as `CachedProbe`'s ctor already allows.
3. **Nits.**
   - `build-ffmpeg.sh` ~920: `touch` the marker only when `WITH_FSR2=1` and `ORT_EP=openvino`, otherwise `rm -f` it on every build. Fix the stale "Not removed by it" header sentence.
   - Regenerate `0016`. By hand arithmetic against 0001 then 0015, the old-side starts and context already match (configure 2073, 4155, 7361; Makefile 420; allfilters 393).
     The `+` start numbers of hunks 2 and 3 are wrong (4155 should be 4156, 7361 should be 7363), and that is all I can find wrong. I cannot run `git apply` here, so the implementer must say so.
   - Add a runnable check: `scripts/test_patch_stack.py` reconstructs the post-0001+0015 line windows from those two patches and checks 0016's hunk counts, old-start context and new-start arithmetic.
     It also accepts `--tree DIR` (pristine configure, libavfilter/Makefile and allfilters.c) to apply 0001, 0015 and 0016 in order. Also add a `git apply --check` step in `scripts/test-gu-inputs.sh` against the extracted FFmpeg 8.1.2 files (network, not runnable here).
   - Fix the `NvidiaOnly` doc comment: it is a property of the level alone except fsr2 on a detected Intel GPU with the marker and a passing probe.
   - Fake-CUDA test: today's `f_retain` sets `*c` before it decides to fail, so a retain failure with `cu_ctx` unset is not modelled. Make the stub set `*c` only on success and add a "retain fails" scenario.
     Expected calls after uninit are `load,cuInit,cuDeviceGet,retain,free`: no push, pop or release of a half-open context.
   - Rework the scenarios to the hard-error contract: load, cuInit, retain and push failures now return a negative from init, then run uninit and check the freed state. The success scenario is unchanged.
   - PR body (the later stage writes it): mention the unrelated `if (sx<0)` brace fix and the `AllocatorFree` status release in `gu_depth_close`. Both are already in the PR diff (`git diff 1117833 449a361 -- ffmpeg/gu_inputs.h`).
   - Docs: GPU_VENDORS.md 5.5, ENHANCEMENTS.md PR 6 paragraph, FSR2.md, and the header comments of `test-gu-inputs.sh`, `no_cuda.c` and `test-gpu-vendor.sh`.

**Tests first.** The harness `Program.cs` NoCudaGame cases get the probe seam, then these cases:
- probe passes, so fsr2 is offered and runs;
- probe fails, so fsr2 is withheld, with no node and no motion note;
- probe null or pending, so fsr2 is withheld;
- an NVIDIA box and a no-GPU box never call the probe;
- `CachedProbe`: true is kept, a failure is retried only after the TTL (fake clock), a throw gives null;
- `Fsr2ProbeArgs()` has no `-init_hw_device` and names `fsr2`.

`no_cuda.c` is rewritten to the new C contract and `test_patch_stack.py` is written before the patch is touched.

**What this stage could and could not run.**
- The C tests (`scripts/test-gu-inputs.sh`) and the harness (`scripts/test-gpu-vendor.sh`) are bash and need network (FFmpeg tarball, ONNX Runtime header from nuget) or the dotnet SDK plus the server's DLLs. The workbench shell allows neither.
- So the C and harness counts cannot be produced here and later stages must say so rather than approve on silence.
- Runnable here: `python3 scripts/test_patch_stack.py` and `pre-commit run --files <changed>`.
- `docs/agent-lessons.md` and `docs/automations.md` do not exist in this repo, so there was nothing to search there. The PR's `lesson:` line must name a changed file or give a reason.
- No cluster premise applies: the change is code and docs, and this repo's Intel path is not deployed on the cluster I can query.

```yaml
{
  "goal": "Address the upstream review of the fsr2-without-CUDA PR: keep the hard CUDA error in the default NVIDIA build, gate fsr2 on Intel behind a background one-frame fsr2 probe (CachedProbe, true cached, failure retried after a TTL), and fix the marker cleanup, the 0016 patch headers, the NvidiaOnly comment and the fake-CUDA retain-failure test.",
  "files": [
    "ffmpeg/gu_inputs.h",
    "ffmpeg/0016-add-fsr2-filter-to-build-standalone.patch",
    "scripts/gu-inputs-test/no_cuda.c",
    "scripts/test-gu-inputs.sh",
    "scripts/build-ffmpeg.sh",
    "scripts/test-gpu-vendor.sh",
    "scripts/test_patch_stack.py",
    "scripts/gpu-vendor-harness/Program.cs",
    "src/patcher/CachedProbe.cs",
    "src/patcher/PatcherHost.cs",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "ENHANCEMENTS.md",
    "docs/filters/FSR2.md",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 scripts/test_patch_stack.py",
    "pre-commit run --files ffmpeg/gu_inputs.h ffmpeg/0016-add-fsr2-filter-to-build-standalone.patch scripts/gu-inputs-test/no_cuda.c scripts/test-gu-inputs.sh scripts/build-ffmpeg.sh scripts/test-gpu-vendor.sh scripts/test_patch_stack.py scripts/gpu-vendor-harness/Program.cs src/patcher/CachedProbe.cs src/patcher/PatcherHost.cs src/patcher/Transcoding/UpscaleEngine.cs ENHANCEMENTS.md docs/filters/FSR2.md docs/architecture/GPU_VENDORS.md",
    "python3 -m py_compile scripts/test_patch_stack.py",
    "scripts/test-gu-inputs.sh (NOT runnable in the workbench: bash, needs network; CI or a docker host runs it)",
    "scripts/test-gpu-vendor.sh <jfbin> (NOT runnable in the workbench: bash plus dotnet plus the server DLLs)"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "Default (NVIDIA) gu_inputs.h build: a failed cuda_load_functions, cuInit, cuDeviceGet, retain or push makes gu_inputs_init return a negative error again; only a failed NVOFA init stays soft. The GU_NO_NVOF build is unchanged.",
    "On Intel with the marker, fsr2 is offered and run only after a background one-frame fsr2 probe passed; a failed, null or pending probe withholds it with an honest reason, and a pass is cached for the process.",
    "A failed or null probe is retried only after the TTL, and the probe never runs on the request path; NVIDIA, no-GPU and mixed boxes never start it and their command lines and probe output are unchanged.",
    "build-ffmpeg.sh removes fsr2-nocuda on every build that is not the CUDA-free fsr2 one.",
    "0016 headers are regenerated against 0001+0015 and checked by scripts/test_patch_stack.py (and a git apply --check step in test-gu-inputs.sh, not runnable here).",
    "The NvidiaOnly doc comment names the fsr2 exception.",
    "The fake-CUDA test has a retain-failure scenario with cu_ctx unset and expects no push, pop or release in uninit.",
    "The PR body mentions the unrelated if (sx<0) brace fix and the AllocatorFree status release.",
    "Real output pasted for test_patch_stack.py and pre-commit; the harness and C test are stated as not run if the shell cannot run them."
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The PR changed default-build gu_inputs_init so a failed libcuda load, cuInit or context push now warns and continues with zero flow, where it used to return an error.",
      "label": "VERIFIED",
      "check": "git diff 1117833 449a361 -- ffmpeg/gu_inputs.h: the removed lines return ret / GU_CHECK_CU for cuda_load_functions, cuInit, cuDeviceGet, retain and push; the added gu_cuda_open result only sets cuda_on and logs a warning.",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "Before the PR only a failed gu_nvof_init was soft (warn, nvof_ready=0); everything before it was a hard error.",
      "label": "VERIFIED",
      "check": "Same diff, removed lines of gu_inputs_init.",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "vf_fsr2 creates its own Vulkan instance and uses vkEnumeratePhysicalDevices()[device_index] (default 0), and ShaderLibrary.GameFilter emits no device= option, so a probe that runs the same fsr2 node tests the device a session uses with no -init_hw_device.",
      "label": "VERIFIED",
      "check": "ffmpeg/vf_fsr2.c lines 162, 313-334 (device option, enumerate, pick); ShaderLibrary.cs GameFilter lines 1207-1260 build only w/h/jitter/reactive/depth/dmodel. Mixed NVIDIA+Intel boxes where index 0 is not Intel are a pre-existing gap this change does not widen.",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "A reusable CachedProbe<T> exists on the fork branch worktree-agent-b7ff7a746bf4099c as src/patcher/CachedProbe.cs, self-contained, not present on this base, and compiles into the harness through the src/patcher glob.",
      "label": "VERIFIED",
      "check": "git diff HEAD worktree-agent-b7ff7a746bf4099c -- src/patcher/CachedProbe.cs printed the whole file (new file there); GpuVendorHarness.csproj includes ../../src/patcher/**/*.cs.",
      "load_bearing": false
    },
    {
      "id": "P5",
      "claim": "NoCudaGameNow is the single gate for offering and running fsr2 on Intel (NvidiaOnly -> OfferedOnThisGpu, WithheldLevels, Withhold in Decide), so requiring the probe there changes all three together.",
      "label": "VERIFIED",
      "check": "Grep of UpscaleEngine.cs: NvidiaOnly (2231-2245) calls NoCudaGameNow; OfferedOnThisGpu 2281, AddWithheld 2310 and Decide 1588 all call NvidiaOnly.",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "The fake-CUDA stub f_retain assigns *c before step() decides to fail, so the current test never has cu_ctx unset on a retain failure, and no retain scenario exists.",
      "label": "VERIFIED",
      "check": "scripts/gu-inputs-test/no_cuda.c lines 163 (f_retain) and 257-266 (scenarios: load, default, cuInit, push).",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "gu_inputs_uninit guards the push, pop and release on cu_ctx being non-NULL, so with cu_ctx unset after a retain failure it only frees the loader (calls load,cuInit,cuDeviceGet,retain,free).",
      "label": "VERIFIED",
      "check": "ffmpeg/gu_inputs.h gu_inputs_uninit lines 833-856 (if (g->cu && g->cu_ctx) twice, cuda_free_functions after).",
      "load_bearing": false
    },
    {
      "id": "P8",
      "claim": "build-ffmpeg.sh removes the marker only when ORT_EP=openvino without WITH_FSR2, so a later CUDA or non-openvino build into the same PREFIX leaves a stale marker.",
      "label": "VERIFIED",
      "check": "scripts/build-ffmpeg.sh lines 920-931.",
      "load_bearing": false
    },
    {
      "id": "P9",
      "claim": "0016 old-side context and starts already match a tree with 0001 and 0015 applied; only the + starts of hunks 2 and 3 are off, so the review's git apply failure may come from real FFmpeg 8.1.2 context that differs from the tree 0001 was cut against.",
      "label": "INFERRED",
      "check": "Hand arithmetic over 0001 and 0015 hunk headers. Settle it with scripts/test_patch_stack.py --tree DIR using pristine 8.1.2 configure, libavfilter/Makefile and libavfilter/allfilters.c (fetch tool at raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1.2/...) or the git apply --check step in test-gu-inputs.sh.",
      "load_bearing": false
    },
    {
      "id": "P10",
      "claim": "The workbench shell cannot run scripts/test-gu-inputs.sh (bash, network to ffmpeg.org and nuget.org), scripts/test-gpu-vendor.sh (bash, dotnet, jfbin) or git apply, so real C and harness counts cannot be produced in this pipeline.",
      "label": "INFERRED",
      "check": "The tool rules allow only python3 scripts/test_<name>.py, sync_/hooks scripts, py_compile, pre-commit, kubectl get, git status/log/diff and curl to *.svc. The implementer must attempt nothing else and state not-run; a later stage must not approve the C or harness result without output.",
      "load_bearing": false
    },
    {
      "id": "P11",
      "claim": "docs/agent-lessons.md and docs/automations.md do not exist in this repo.",
      "label": "VERIFIED",
      "check": "Glob docs/agent-lessons.md and docs/automations.md: no files found.",
      "load_bearing": false
    }
  ],
  "reuse_check": [
    {
      "need": "Background, cached probe (true kept, failure/null retried after a TTL) that never runs on the request path",
      "checked": "CachedProbe<T> in src/patcher/CachedProbe.cs on fork branch worktree-agent-b7ff7a746bf4099c (upstream #1); this repo's own once-per-process probes (VulkanDenoiseRunsOnPatchedBinary, Av1QsvUsable) cache a failure for the life of the process and run inline",
      "verdict": "use",
      "reason": "Copy the file as is rather than writing a second caching shape."
    },
    {
      "need": "One-frame test run of a filter on the patched binary",
      "checked": "UpscaleEngine.VulkanDenoiseRunsOnPatchedBinary and VulkanDenoiseProbeArgs (process spawn, 20 s kill, both pipes drained) in this repo",
      "verdict": "extend",
      "reason": "Same process-handling shape, but wrapped in CachedProbe and run with the fsr2 node instead of nlmeans_vulkan."
    },
    {
      "need": "Check that a patch applies on top of earlier patches",
      "checked": "git apply --check (git, no network) inside test-gu-inputs.sh; no existing repo script checks the patch stack; GNU patch --dry-run cannot stack two patches without writing",
      "verdict": "extend",
      "reason": "Add the git apply step to the existing test-gu-inputs.sh, plus a small python stdlib checker that runs in the workbench."
    }
  ]
}
```
