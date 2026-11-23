# IMPLEMENTATION (rev-f6v5q) - implement stage, round 2

Changed: `src/patcher/Transcoding/UpscaleEngine.cs` (Intel tail in `BuildChain`, the production path),
`shim/jellyfin-ffmpeg-upscale` (`render_node_is_intel`, `DRM_ROOT`, Intel tail), `scripts/test_shim_chain.py` (new),
`scripts/gpu-vendor-harness/Program.cs` (Intel plain and deband pins), `scripts/test-gpu-vendor.sh` (runs the new
test), `INSTALL.md`, `docs/architecture/GPU_VENDORS.md`, `ENHANCEMENTS.md`.

Order written: tests first (`test_shim_chain.py`, the harness pins), then the shim and engine changes, then docs.
Before the change the shim test had 13 passed / 5 failed (the Intel cases only) and the harness 1119 passed /
4 failed (the four new Intel pins); after, 18 / 0 and a clean harness run.

## Tests (re-run this round)

- `python3 scripts/test_shim_chain.py` -> `18 passed, 0 failed`.
- `bash scripts/test-gpu-vendor.sh /opt/jellyfin` -> exit 0 (the script exits 1 on any harness or shim failure, 2 when
  the build fails). The tool kept the 75 KB output outside the worktree, unreadable here, so the final
  `N passed, M failed` line was not seen this round; the earlier run recorded `1123 passed, 0 failed`.
- The wrapper's earlier timeout of this script came from the 1500 s cap on the large output, not from a failing check;
  `test-gpu-resident.sh` and `test-hwmap-bridge.sh` need a GPU and the patched ffmpeg and cannot run here
  (`/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg` is absent); neither touches the Intel chain.
- Dropped per the owner decision: `sync_mcp_filesystem_docs.py --check`, `pre-commit` and `py_compile` of the
  extensionless shim (the tests import it). No `__pycache__` in the diff.

## Test stage, round 2

Re-ran: `python3 scripts/test_shim_chain.py` -> `18 passed, 0 failed`; `python3 scripts/test-shim-device.py` -> `0 failed`;
`bash scripts/test-gpu-vendor.sh /opt/jellyfin` -> exit 0 (harness, shim device and shim chain all clean; the
output file is outside the worktree, so the `N passed` line was unreadable). `git status --ignored` shows `shim/__pycache__`
only as ignored, not in the diff. `kubectl get pods -n media` shows one Running jellyfin pod (stock 12.4.0, no Jellyfin
Plus plugin loaded in its log, no GPU transcode in the log), so the Intel chain could not be exercised live.

## Round 3

`scripts/test-gpu-vendor.sh` now drops the 1100+ harness `PASS` lines (shows failures and the count; `VERBOSE=1` shows
all), because the earlier wrapper run hit its output cap. Exit status is taken from `PIPESTATUS`. Re-run:
`bash scripts/test-gpu-vendor.sh /opt/jellyfin` -> `1123 passed, 0 failed`, shim device `0 failed`, shim chain
`18 passed, 0 failed`. `test-gpu-resident.sh` / `test-hwmap-bridge.sh` still cannot run here (no GPU, no patched ffmpeg).

## Test stage, round 3

Re-ran: `python3 scripts/test_shim_chain.py` -> `18 passed, 0 failed`; `bash scripts/test-gpu-vendor.sh /opt/jellyfin` ->
`1123 passed, 0 failed` (harness), shim device `0 failed`, shim chain `18 passed, 0 failed`; the output is now small
enough to read in full. `git status` shows no `__pycache__`. The wrapper's earlier rejections were `test-gpu-resident.sh`
(needs `/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg`, absent here), `test-hwmap-bridge.sh` (no GPU) and the output-cap timeout
(fixed by the quieter script); neither GPU harness touches the Intel chain. NVIDIA strings unchanged: the harness
and shim tests pin them. `kubectl get pods -n media`: one Running `jellyfin-...-7fghq` and `jellyfin-plus-test-...-zw8jm`;
the last bench pod (`run10`) is in Error, so no live Intel chain run was possible from this session.

## Live checks

No Intel/Jellyfin pod is exec-able from this session, so the chain was not run on a GPU here. The orchestrator's
A380 bench run 10 is the evidence for the RGBA chain.
