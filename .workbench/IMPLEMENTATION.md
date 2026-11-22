# Implementation - round 1 (test stage)

## Changed (uncommitted on top of 571b8d6)
- `src/patcher/Transcoding/UpscaleEngine.cs`: `HardwareDecodableSource` now assigns `codec = name` and `surfaceFormat`; new `HardwareSurfaceFormat(pixelFormat)` (yuv420p -> nv12, else null); `Plan.HardwareDecodeFormat` set in `Decide`, read by `BuildChain` and `HardwareDecodeTestChain`; the hard-coded "inferred" constant is removed.
- `scripts/gpu-vendor-harness/Program.cs`: literal hardware-decode plans carry the format; new checks for the map and for the decided/software plan.
- `docs/architecture/GPU_VENDORS.md`: matched.

## Test results
- Full dotnet harness: NOT RUN. No jfbin (MediaBrowser.*.dll) in the worktree, the shell allows `bash scripts/test-*.sh` only without arguments (`bash scripts/test-gpu-vendor.sh` -> exit 2, "'' is not a directory"), and dotnet beyond build|run is refused. Nothing here has been compiled. Verdict: BLOCKED, not approved.
- `python3 scripts/test-shim-device.py` -> 16 PASS, 0 failed.
- `pre-commit run --files ...` -> failed to start: no `.pre-commit-config.yaml` in the worktree.
- Live cluster: nothing the change depends on (Intel-GPU-gated plugin code).

## Not verified
- Harness count for this head (required: 0 failed).
- Real Arc: start-up test-decode, `-hwaccel_device va` with `vk@va`, that nv12 is what the Arc downloads.
