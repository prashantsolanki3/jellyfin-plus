# Plan: rev-ml3y9, NVIDIA counts only with a container-visible NVIDIA device

`GpuDetector.Inventory.Gather` and `Build` (src/patcher/Transcoding/GpuVendor.cs) treat
`/proc/driver/nvidia` (the host kernel's procfs) and a working `nvidia-smi` as NVIDIA on their own, so
an Intel-only pod on a mixed host resolves `auto` to nvidia and ffmpeg dies on `hwmap=derive_device=cuda`.

Plan, tests first:

1. Harness (`scripts/gpu-vendor-harness/Program.cs`): add a `NvidiaDevice` bool and an
   `NvidiaDevicePresent()` method to `FakeGpuEnvironment`. The method is not on the interface yet, so
   the harness compiles against the unchanged source and the new cases FAIL on the real count
   (baseline 139/0). New cases: (1) Intel `renderD128` + `DriverLoaded=true` (and a variant with
   `SmiLists=true`) -> intel, renderD128, `Detected`, no CUDA-only levels; (2) `NvidiaDevice=true`, no
   node, no driver flag -> nvidia, `Detected`, `RenderNode` null; (3) Intel node + `NvidiaDevice` +
   driver flag -> nvidia first, the Intel GPU named as unused. Also (4) a real `SystemGpuEnvironment`
   on a temp tree: `/dev/nvidia0` and `/dev/nvidiactl` each count, `/dev/nvidia-uvm` and a bare
   `/proc/driver/nvidia` directory do not. The existing tie-breaker cases ("/proc/driver/nvidia beats
   an Intel node", "nvidia-smi alone finds NVIDIA", "nvidia-smi not run when the driver answered", the
   fake-sysfs "NVIDIA first" case that relies on the proc dir) encode the bug and are rewritten to the
   new contract.
2. Source: add `bool NvidiaDevicePresent()` to `IGpuEnvironment`. `SystemGpuEnvironment` gets a
   `/dev` root (default `/dev`) and returns true when `nvidiactl` or `nvidia[0-9]*` exists there.
   Remove `NvidiaDriverLoaded` and `NvidiaSmiLists` from the interface, the real environment, the
   constructor parameters and `Inventory` (they would be dead code). `nvidiaSeen = nvidiaNode != null ||
   inv.NvidiaDevice`. `AnySupportedGpu`, `WhyNvidia` and the three reason strings follow. The setting
   `nvidia` with no device keeps the "no NVIDIA GPU was found" wording the existing harness checks.
   NVIDIA behaviour with a device in the container is unchanged (device -> nvidia, precedence over
   Intel, `RenderNode` from the render node when one exists).
3. Docs: `docs/architecture/GPU_VENDORS.md` section 3.2 item 3 (tie-breaker becomes "NVIDIA device in
   the container"; /proc is the host's), the `auto` row in 3.3, and the 3.6 test list.

Verification limits: the shell here cannot run `scripts/test-gpu-vendor.sh` or dotnet (only the
allow-listed commands), so the before/after harness counts and the Arc-pod `/JellyfinPlus/Status`
check (needs a rebuild and deploy) are NOT verifiable in this session. Run
`scripts/test-gpu-vendor.sh <jfbin>` in `mcr.microsoft.com/dotnet/sdk:10.0` and report the counts.
The fix must then be carried up the stack by the orchestrator, per the bead.

```yaml
{
  "goal": "GpuDetector counts NVIDIA only when the container has /dev/nvidiactl, /dev/nvidia[0-9]* or an NVIDIA render node; host /proc/driver/nvidia and nvidia-smi alone no longer make the box NVIDIA, so an Intel-only pod resolves to intel with renderD128",
  "files": [
    "src/patcher/Transcoding/GpuVendor.cs",
    "scripts/gpu-vendor-harness/Program.cs",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "scripts/test-gpu-vendor.sh <jfbin>  (not runnable from this shell; run in mcr.microsoft.com/dotnet/sdk:10.0, expect baseline 139/0 -> new cases fail before the source change, all pass after)",
    "python3 scripts/sync_mcp_filesystem_docs.py --check",
    "pre-commit run --files src/patcher/Transcoding/GpuVendor.cs scripts/gpu-vendor-harness/Program.cs docs/architecture/GPU_VENDORS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "Intel-only /dev/dri (renderD128) with /proc/driver/nvidia present and/or nvidia-smi listing a GPU resolves auto to intel, RenderNode /dev/dri/renderD128",
    "An NVIDIA device node in the container (/dev/nvidia0 or /dev/nvidiactl) resolves auto to nvidia, unchanged",
    "NVIDIA device plus Intel node keeps today's precedence: nvidia first, Intel named as unused",
    "New harness cases fail on the unchanged source and pass after the change; no existing NVIDIA case regresses (baseline 139/0)",
    "docs/architecture/GPU_VENDORS.md section 3.2 describes the device-based rule and why /proc/driver is not used",
    "No NVIDIA behaviour change when an NVIDIA device is present; no revenant-side GpuVendor override added"
  ],
  "reuse_check": [
    {
      "need": "check whether an NVIDIA device is visible inside the container",
      "checked": "System.IO.Directory.EnumerateFileSystemEntries / File.Exists (BCL, .NET 10), already used by SystemGpuEnvironment.RenderNodes in src/patcher/Transcoding/GpuVendor.cs",
      "verdict": "extend",
      "reason": "same class and injectable-root pattern; no library needed"
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "GpuVendor.cs Build() sets nvidiaSeen = nvidiaNode != null || inv.NvidiaDriverLoaded || inv.NvidiaSmiLists, NvidiaDriverLoaded is Directory.Exists(/proc/driver/nvidia), and auto checks NVIDIA first",
      "label": "VERIFIED",
      "check": "Read src/patcher/Transcoding/GpuVendor.cs lines 258-268, 462-514, 583-601",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "IGpuEnvironment is implemented only by SystemGpuEnvironment and the harness FakeGpuEnvironment; SystemGpuEnvironment's 4-arg constructor is used only in the harness (2 call sites)",
      "label": "VERIFIED",
      "check": "Grep IGpuEnvironment|SystemGpuEnvironment across the repo (non-docs): only GpuVendor.cs and scripts/gpu-vendor-harness/Program.cs",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "Existing harness cases encode the bug (tie-breaker /proc/driver/nvidia beats Intel; nvidia-smi alone finds NVIDIA; fake sysfs tree expects nvidia via proc dir) and must be rewritten, not just supplemented",
      "label": "VERIFIED",
      "check": "Read scripts/gpu-vendor-harness/Program.cs lines 167-182 and 212-224",
      "load_bearing": false
    },
    {
      "id": "P4",
      "claim": "A container that can really use NVIDIA always has /dev/nvidiactl (nvidia container toolkit/device plugin) so dropping driver/smi signals does not regress working NVIDIA pods",
      "label": "INFERRED",
      "check": "kubectl exec is not allowed here; confirm on the live NVIDIA pod with ls /dev/nvidia* after deploy, or check the bead's bench notes",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "Baseline harness result is 139 passed / 0 failed and the sandbox cannot run it",
      "label": "INFERRED",
      "check": "Number comes from the bead brief; scripts/test-gpu-vendor.sh and dotnet are outside the allowed shell commands, so it must be run by the verifier in the dotnet SDK container",
      "load_bearing": false
    },
    {
      "id": "P6",
      "claim": "docs/agent-lessons.md and docs/automations.md do not exist in this checkout, so the PR lesson line should name a changed file or give a reasoned none",
      "label": "VERIFIED",
      "check": "Glob **/agent-lessons.md returned no files",
      "load_bearing": false
    }
  ]
}
```
