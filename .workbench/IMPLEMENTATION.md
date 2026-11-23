# Implementation: rev-ml3y9

## What changed

- `src/patcher/Transcoding/GpuVendor.cs`
  - `IGpuEnvironment`: `NvidiaDriverLoaded()` and `NvidiaSmiLists()` replaced by `NvidiaDevicePresent()`.
  - `SystemGpuEnvironment`: new `nvidiaDevRoot` (default `/dev`) replaces the `/proc/driver/nvidia` path and the smi
    delegate (constructor is now `(drmRoot, devRoot, nvidiaDevRoot)`). `NvidiaDevicePresent()` is true when
    `nvidiactl` or `nvidia[0-9]*` exists there; `nvidia-uvm` / `nvidia-caps` do not count. The nvidia-smi spawn and
    the unused `System.Diagnostics` using are gone.
  - `Inventory` carries `NvidiaDevice`; `nvidiaSeen = nvidiaNode != null || inv.NvidiaDevice`; reason strings and
    `WhyNvidia` follow. The "no NVIDIA GPU was found" wording is kept.
- `scripts/gpu-vendor-harness/Program.cs`: `FakeGpuEnvironment` has `NvidiaDevice` (driver/smi flags removed).
- `docs/architecture/GPU_VENDORS.md`: section 3.2 item 3, the `auto` row in 3.3, the 3.6 test list.

## Tests (written first)

Order: harness edited first, source second, docs last.

1. Intel-only node, no NVIDIA device (the host `/proc/driver/nvidia` is not a signal any more) -> intel, renderD128,
   detected, no CUDA-only levels, reason does not claim NVIDIA.
2. `NvidiaDevice` only -> nvidia, detected, no render node, CUDA-only levels, reason names `/dev/nvidia`.
3. `NvidiaDevice` + Intel node -> nvidia first, Intel named as unused, `intel` setting still wins; NVIDIA device +
   NVIDIA node -> the node is reported.
4. Real `SystemGpuEnvironment` on a temp tree: bare `/proc/driver/nvidia` dir, `nvidia-uvm`, `nvidia-caps` -> false;
   `nvidia0` -> true; `nvidiactl` -> true; back to none -> false; missing root -> false.
5. The old cases that encoded the bug (driver beats Intel, smi alone finds NVIDIA, smi call counts, proc-dir NVIDIA first)
   were removed or rewritten to the new contract.

## Not run

`scripts/test-gpu-vendor.sh` and `dotnet` are outside this session's allowed shell, so neither the before count (139/0
baseline from the bead) nor the after count was run here, and nothing was compiled. `pre-commit` has no
`.pre-commit-config.yaml` in this checkout. Run `scripts/test-gpu-vendor.sh <jfbin>` in
`mcr.microsoft.com/dotnet/sdk:10.0`.

## Test stage results (round 3)

- `python3 scripts/sync_mcp_filesystem_docs.py --check`: script does not exist in this checkout (no such file); nothing to sync.
- `pre-commit run --files ...`: fails with `.pre-commit-config.yaml is not a file`; no hooks configured here.
- `scripts/test-gpu-vendor.sh` / dotnet: not runnable from this shell (outside the allow list). Static review of the diff
  instead: no leftover references to `NvidiaDriverLoaded`, `NvidiaSmiLists`, `DriverLoaded`, `SmiLists`, `SmiCalls`; both
  `new SystemGpuEnvironment(...)` call sites in the harness use the 3-arg constructor; the two projects have `Nullable`
  disabled, so `Select(Path.GetFileName)` adds no warnings.
- Live: `kubectl get pods -n media` shows `jellyfin-55c59fcb97-7fghq` Running on `aihub`; `kubectl describe` shows it
  requests only `gpu.intel.com/i915: 1` (node selector `intel.feature.node.kubernetes.io/gpu=true`), no NVIDIA resource and no
  nvidia runtime class: the Intel-only pod from the bug. Whether `/dev/nvidia*` is absent in it was not checked
  (no `kubectl exec`).
- Left stale (outside the plan's files, not edited): `ENHANCEMENTS.md` line 149, the `GpuVendor` comment in
  `src/Configuration/PluginConfiguration.cs` line 168 and the help text in `src/Configuration/configPage.html` line 443 still
  describe `/proc/driver/nvidia` and `nvidia-smi` as tie-breakers.

`tdd`: required; red state is by construction (the new Intel-only case would resolve nvidia on the old source, and the
harness uses the new constructor and `NvidiaDevicePresent`, so the old source does not compile against it).
