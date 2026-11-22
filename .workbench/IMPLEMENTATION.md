# Implementation and test results (round 1)

## Changes (working tree on top of ddf93db)
- `src/Transcoding/GpuFdinfo.cs`: `DrmFdinfo.Compute` returns a null `BusyPercent` when any class was
  dropped as unmeasurable (i915 no capacity line over 110%, xe class with no elapsed cycles).
- `src/patcher/Transcoding/UpscaleEngine.cs`: `ResetEncoderProbe()`, `NotedEncoder()`; `GuardAv1Intel`
  swaps while the first AV1 test encode is pending (`Av1QsvUsable(out pending)`).
- `src/patcher/Transcoding/UpscalePatches.cs`: `PlanSource` test seam; `StartFfMpegStripPrefix` and
  `VideoEncoderPostfix` made `internal`.
- `scripts/gpu-vendor-harness/Program.cs`: cold-cache tests for `Capability()`, `GpuJson()` and the Intel
  `Gpu/Load` route; `StripPrefix`, `NullEncoderBranches`, `PatchWiring` groups; pending-AV1 expectations.
- `ENHANCEMENTS.md`, `docs/architecture/GPU_VENDORS.md`: null busy figure, swap-while-pending, harness scope.

## Test stage results
- Harness (`scripts/test-gpu-vendor.sh <jfbin>`): NOT RUN. This session has no way to run it: `dotnet` is
  limited to `build`/`run` without `-p:` flags (the harness build needs `-p:JellyfinBin=...`), there is no
  docker, and no copy of the server's MediaBrowser.*.dll exists in this checkout. No "0 failed" is claimed
  and no harness output exists to paste.
- Static review (no compiler): every identifier, accessor and signature the new harness code uses was
  checked against the sources (`PatcherHost.Apply/GpuJson`, `UpscaleEngine.WarmProbes/ResetEncoderProbe/
  NotedEncoder/Av1QsvRunner`, `UpscalePatches.PlanSource/ApplyIntelEncoder/VideoEncoderPostfix/
  StartFfMpegStripPrefix`, `Plan` properties, `StreamingRequestDto.PlaySessionId`). No compile error found.
  The i915 no-capacity fixture now yields a null headline; the xe fixtures still yield 50%.
- Mutation reading: reverting `KnownEncoders` to `AvailableEncoders`, dropping the warm-up, dropping
  `WarmProbes()` from `Apply`, dropping the Harmony hook or the strip call each make at least one new check
  fail, independent of `_encodersProbed`.
- Live checks: none apply (no running service is touched).

## Not verified
- Compilation and the whole harness, including `PatchWiring` (it installs the real Harmony patches
  against the server's DLLs and assumes the real `StartFfMpeg` carries `state` and `commandLineArguments`).
- A served segment on a real Intel Arc; the command-line fixture is reconstructed from Jellyfin's source.
