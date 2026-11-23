# Plan: rev-ml3y9 - an Intel-only pod must not be called NVIDIA

Cause (read in `src/patcher/Transcoding/GpuVendor.cs`, `Build()` line 469): `nvidiaSeen = nvidiaNode != null || inv.NvidiaDriverLoaded || inv.NvidiaSmiLists`.
`NvidiaDriverLoaded` is `Directory.Exists("/proc/driver/nvidia")`, which is the host kernel's procfs, and `NvidiaSmiLists` runs `nvidia-smi -L`.
Neither says the container was handed an NVIDIA GPU. Auto mode checks NVIDIA first, so an Intel-only pod on a host that also has an RTX card resolves to nvidia.

Fix, the smallest diff:
- Add `bool NvidiaDeviceNode()` to `IGpuEnvironment`. The real one is true when `/dev/nvidiactl` or `/dev/nvidia0` exists in the container, under a new `nvidiaDevRoot` constructor parameter that defaults to `/dev` so the tests can use a temp dir.
- `NvidiaDriverLoaded()`, `NvidiaSmiLists()` and the `nvidia-smi` spawn leave the decision. Both members are removed from the interface and the inventory, and the `procNvidia` and `smi` constructor parameters go too.
- `nvidiaSeen = nvidiaNode != null || inv.NvidiaDeviceNode`. An NVIDIA render node (0x10de) still counts, so NVIDIA-with-render-node and NVIDIA-device-without-render-node (no nvidia_drm) behave as before. Precedence is unchanged: auto still checks NVIDIA first, so a box with both vendors' devices stays nvidia.
- `AnySupportedGpu` becomes `NvidiaDeviceNode || Devices.Any(vendor != null)`. The retry path is unchanged.
- Update the reason strings and `WhyNvidia` (`/dev/nvidia*, no render node`), and the doc comments in `GpuVendor.cs` that name the driver or nvidia-smi.
- Update the `PluginConfiguration.cs` doc comment, the `configPage.html` help text and `ENHANCEMENTS.md` line 149. All three say "with /proc/driver/nvidia or nvidia-smi as the tie-breaker", so leaving them would document the removed behaviour.
- Rewrite `docs/architecture/GPU_VENDORS.md` section 3.2 point 3 and the `auto` row in 3.3: the tie-breaker is an NVIDIA device node in the container, and `/proc/driver/nvidia` and `nvidia-smi` are host-wide, so they are not used.

Tests first, in `scripts/gpu-vendor-harness/Program.cs` (the existing C# harness, run by `scripts/test-gpu-vendor.sh <jfbin>`):
- `FakeGpuEnvironment` gets a `NvidiaDevice` flag in place of `DriverLoaded` and `SmiLists`.
- New: an Intel node plus a host `/proc/driver/nvidia` or `nvidia-smi` and no NVIDIA device gives intel on `/dev/dri/renderD128`. The fake no longer models the host signals, so the real reader is covered by a fake-tree case: a `proc/driver/nvidia` dir exists and `SystemGpuEnvironment.NvidiaDeviceNode()` is false.
- New: an Intel node plus an NVIDIA device (no render node) gives nvidia with `RenderNode == null` and `Detected`. This replaces the old "driver beats Intel" case.
- New: both an NVIDIA render node and an Intel node still gives nvidia on the NVIDIA node (existing precedence case kept).
- Changed: the nvidia-smi-alone case becomes "no signal, none". The `SysfsReader` fake tree gains `dev/nvidiactl` for the NVIDIA-first check, and a case with the file absent.
- Run the red tests before the fix, then green after it.

Limits:
- The shell here only allows the repo's python scripts, not `dotnet` or bash scripts, so I cannot run the harness in this session. The implementation stage has to run `scripts/test-gpu-vendor.sh` somewhere that has the jellyfin DLLs and the dotnet SDK 10 (the verifiers' `mcr.microsoft.com/dotnet/sdk:10.0` container). If that is not possible, the PR must say the counts were not produced.
- Acceptance 2 (live `/JellyfinPlus/Status` on the Arc pod reports vendor=intel, plus bench rows 3-4) needs a rebuild and deploy. I cannot do that, so it is not verified here. The pod could not be found with `kubectl get pods -A -l app=jellyfin-plus-test` (no match; the Arc pod is not labelled that way and the full pod list was too large to scan within the workbench policy).
- The upstream stack (PR #1 branch worktree-agent-b7ff7a746bf4099c, then #2..#9) is merged by the orchestrator. This checkout already contains `GpuVendor.cs`, so I work here. Removing interface members may conflict with later stack PRs that call them. A grep of `src/` shows only `GpuVendor.cs` uses them.
- Out of scope per "Do not": no revenant-side override, no change to NVIDIA behaviour when a device is present.
- `docs/agent-lessons.md` and `docs/automations.md` do not exist in this checkout. The PR `lesson:` line will point at the changed `docs/architecture/GPU_VENDORS.md`.

```yaml
{
  "goal": "GpuDetector counts NVIDIA only when the container has an NVIDIA device (render node 0x10de, /dev/nvidiactl or /dev/nvidia0); host-wide /proc/driver/nvidia or host nvidia-smi alone no longer makes an Intel-only pod 'nvidia'. NVIDIA behaviour with a device present is unchanged.",
  "files": [
    "src/patcher/Transcoding/GpuVendor.cs",
    "scripts/gpu-vendor-harness/Program.cs",
    "docs/architecture/GPU_VENDORS.md",
    "src/Configuration/PluginConfiguration.cs",
    "src/Configuration/configPage.html",
    "ENHANCEMENTS.md",
    "scripts/test-gpu-vendor.sh"
  ],
  "test_commands": [
    "scripts/test-gpu-vendor.sh <jfbin> (in mcr.microsoft.com/dotnet/sdk:10.0 with the jellyfin DLLs; paste the 'N passed, M failed' line)",
    "python3 scripts/sync_mcp_filesystem_docs.py --check",
    "pre-commit run --files src/patcher/Transcoding/GpuVendor.cs scripts/gpu-vendor-harness/Program.cs docs/architecture/GPU_VENDORS.md ENHANCEMENTS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "C# harness passes with new cases: Intel-only /dev/dri plus host /proc/driver/nvidia (or nvidia-smi) and no NVIDIA device => vendor intel, render node /dev/dri/renderD128",
    "NVIDIA device present (render node, or /dev/nvidiactl or /dev/nvidia0 without a render node) => nvidia, unchanged",
    "NVIDIA and Intel both present => auto still picks nvidia on the NVIDIA node, precedence unchanged",
    "docs/architecture/GPU_VENDORS.md section 3.2 (and the 3.3 auto row) describe the device-node rule",
    "After landing and a rebuild (NOT verifiable in this session): /JellyfinPlus/Status on the Arc pod says vendor=intel node=/dev/dri/renderD128 and bench rows 3-4 e2e return a segment"
  ],
  "premises": [
    {"id": "P1", "claim": "Build() sets nvidiaSeen from nvidiaNode, NvidiaDriverLoaded (Directory.Exists /proc/driver/nvidia) or NvidiaSmiLists, and auto checks NVIDIA first", "label": "VERIFIED", "check": "Read src/patcher/Transcoding/GpuVendor.cs lines 258-268, 462-514, 583-601", "load_bearing": true},
    {"id": "P2", "claim": "The only users of NvidiaDriverLoaded/NvidiaSmiLists/SystemGpuEnvironment ctor are GpuVendor.cs and scripts/gpu-vendor-harness/Program.cs, so removing them is safe", "label": "VERIFIED", "check": "Grep NvidiaDriverLoaded|NvidiaSmiLists|GpuDetector across src/ and the repo", "load_bearing": true},
    {"id": "P3", "claim": "The existing C# unit tests are scripts/gpu-vendor-harness/Program.cs run by scripts/test-gpu-vendor.sh <jfbin>, and the FakeGpuEnvironment there is where the new cases go", "label": "VERIFIED", "check": "Read scripts/test-gpu-vendor.sh and Program.cs", "load_bearing": true},
    {"id": "P4", "claim": "A container given an NVIDIA GPU always has /dev/nvidiactl (and /dev/nvidia0), so keying on them keeps working installs working", "label": "INFERRED", "check": "INSTALL.md line 35 says /dev/nvidia* is passed through; confirm on a live NVIDIA CT/pod with `ls /dev/nvidia*` once one is reachable", "load_bearing": true},
    {"id": "P5", "claim": "The dotnet harness cannot be run from this workbench shell", "label": "VERIFIED", "check": "The allowed shell commands are limited to python scripts, git status/diff/log, kubectl get/describe/logs, curl and pre-commit; there is no dotnet or bash script runner", "load_bearing": false},
    {"id": "P6", "claim": "The Arc pod reports vendor=nvidia today because /proc/driver is host procfs", "label": "INFERRED", "check": "Bead states it as verified on 2026-11-23 (bench run 8); I did not find the pod via kubectl get pods -A -l app=jellyfin-plus-test; re-check with curl /JellyfinPlus/Status once the pod is named", "load_bearing": false},
    {"id": "P7", "claim": "docs/agent-lessons.md and docs/automations.md are absent here, so the lesson line cites GPU_VENDORS.md", "label": "VERIFIED", "check": "Glob **/{agent-lessons,automations}.md returned no files", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "Detect whether the container was given an NVIDIA GPU", "checked": "Existing detection in GpuVendor.cs: render-node vendor read (/sys/class/drm + /dev/dri filter) already uses the 'device file must exist in the container' rule; /dev/nvidia* uses the same rule", "verdict": "extend", "reason": "Reuse the existing device-file-exists rule for NVIDIA control nodes; no library involved"}
  ]
}
```
