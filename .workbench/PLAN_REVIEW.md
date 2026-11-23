# Plan review: rev-ml3y9

Working tree is clean (`git status` empty): the plan is not implemented yet, so this reviews the plan against the current source.

## Premises

- **P1: VERIFIED (own run).** I read GpuVendor.cs.
  - `Build()` line 469 is `nvidiaSeen = nvidiaNode != null || inv.NvidiaDriverLoaded || inv.NvidiaSmiLists`.
  - `SystemGpuEnvironment.NvidiaDriverLoaded()` is `Directory.Exists(_procNvidia)` (258-268). The default path is `/proc/driver/nvidia` (198).
  - The `auto` branch tests `nvidiaSeen` before `intelSeen` (497-509).
  - `Inventory.Gather` runs nvidia-smi only when nothing else answered (597-599).
  - The bug mechanism is as the plan describes.
- **P2: VERIFIED (own Grep).** `IGpuEnvironment` and `SystemGpuEnvironment` appear only in GpuVendor.cs and `scripts/gpu-vendor-harness/Program.cs`.
  - Harness: `FakeGpuEnvironment` implements the interface, and the 4-arg `SystemGpuEnvironment` constructor is called at lines 212 and 226.
  - Production uses the no-arg constructor at 356 and 412.
  - Removing the driver and smi members and the constructor parameters is therefore contained in the two files in `files`.
  - `files` is the whole change. The docs sections named (3.2 item 3, the 3.3 `auto` row, 3.6 tests) all exist; GPU_VENDORS.md lines 78-81, 93 and 163 mention the old signals.
- **P3: VERIFIED.** Harness lines 167-182 and 212-224 encode the bug.
  - Cases: "tie-breaker /proc/driver/nvidia beats an Intel node", "nvidia-smi alone finds NVIDIA", the `NvidiaDriverLoaded()` assertions at 218/220, and "fake sysfs tree: NVIDIA first", which relies on the proc dir.
  - They must be rewritten, as the plan says.
- **P4: VERIFIED for the cluster's own contract, not by a live exec (load-bearing).**
  - `kubectl exec` is not available here.
  - revenant `charts/app/README.md` ("GPU") says `runtimeClassName: nvidia` injects `/dev/nvidia*` and the driver libs. A pod without it runs on CPU anyway, so the device check loses nothing.
  - `kubectl describe pod jellyfin-55c59fcb97-7fghq -n media` shows the live Jellyfin pod requests `gpu.intel.com/i915: 1` only. It has no `nvidia.com/gpu` and no nvidia runtime class, and it is scheduled on `aihub`.
  - The README says `aihub` is the node with the RTX 3060. So this pod is Intel-only on a mixed host, which is the bug's setup.
  - After deploy, `ls /dev/nvidia*` on a real NVIDIA pod is still worth confirming. It is not a blocker.
- **P5: INFERRED, non-load-bearing.** The 139/0 baseline comes from the brief.
  - `scripts/test-gpu-vendor.sh` exists, but dotnet and that script are outside the allow-listed shell. The implementer cannot run it and must say so rather than claim before/after counts.
  - The TDD claim "new cases fail before the change" can only be justified by construction. `NvidiaDevicePresent` is not on the interface yet, so harness cases using it would not compile against the old source. The plan's wording ("compiles against the unchanged source") is wrong on that point: a harness that adds a method to `FakeGpuEnvironment` still compiles, but any case that calls `GpuDetector` with `NvidiaDevice=true` and no node fails on the old source. This is fine as long as it is stated accurately. `tdd: required` is honest.
- **P6: VERIFIED.** Glob `**/agent-lessons.md` found nothing. Use `lesson: none (<reason of 15+ chars>)`, or name a changed file such as `docs/architecture/GPU_VENDORS.md`.

## Other findings

- **The plan's test command 2 does not exist.** `scripts/sync_mcp_filesystem_docs.py` is absent in this checkout (my run: "No such file"), and the only `scripts/test*`/`sync_*` files are `test-gpu-vendor.sh` and `test-gpu-resident.sh`. Drop that command and run `pre-commit run --files ...` instead. This is non-blocking.
- **The planned tests answer real bugs.**
  - Intel node + driver flag -> intel is the incident itself.
  - The device -> nvidia and both -> nvidia-first cases guard against regressing working NVIDIA pods and the precedence.
  - The temp-tree `/dev/nvidia0` / `/dev/nvidiactl` case (counts) versus `/dev/nvidia-uvm` and a bare proc dir (does not count) checks the real filesystem reader, which the fake cannot.
  - Keep the "nvidia chosen but absent" wording check ("no NVIDIA GPU was found").
- **Scope.** There is no revenant-side override, no NVIDIA behaviour change when a device is present, and no GUI or config change. That fits the bead.
- **Open limit to report.** The Arc-pod `/JellyfinPlus/Status` check needs a rebuild and deploy, and the fix must be carried up the stack. Neither can be done in this session.

VERDICT: APPROVE
