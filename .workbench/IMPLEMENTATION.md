# Implementation (round 1, test stage)

## Changes
- `UpscaleEngine.GuardAv1Intel` takes the plan's `DeviceInit` and passes it to `Av1QsvUsable`; it no longer calls `Devices()` itself. `GuardAv1` and `EncoderFor` take an optional `devices` argument and forward it.
- `UpscalePatches.cs`: the four call sites pass `plan.Devices` (from `ShouldAct`'s plan).
- `Av1QsvProbeSource` is now `Func<DeviceInit, bool?>` so the harness sees which device the probe ran on.
- Harness (`scripts/gpu-vendor-harness/Program.cs`): two new checks fail if `GuardAv1`/`EncoderFor` do not hand the plan's `DeviceInit` (reference-equal) to the AV1 probe; existing probe stubs updated to the new signature.
- `docs/architecture/GPU_VENDORS.md`: the "have not been run" sentence now says the checks have since been run and passed.

## Test results
- Harness (`scripts/test-gpu-vendor.sh <jfbin>`): NOT RUN here. The sandbox has no jfbin (the server's MediaBrowser.*.dll) and only allows `bash scripts/test-*.sh` with no arguments, so the script exits 2 ("'' is not a directory"). The 993/0 count is the earlier independent verification, not a result of this session.
- `pre-commit run`: no `.pre-commit-config.yaml` in this checkout. `scripts/sync_mcp_filesystem_docs.py`: not present.
- Reviewed the diff by hand: `Av1QsvProbeArgs(null)` still falls back to `Devices()` only when no plan exists; no other `Av1QsvProbeSource` callers use the old signature (Grep of all `.cs`).
- Live cluster checks: none apply (C# patcher and a doc line; nothing in the cluster depends on it).
