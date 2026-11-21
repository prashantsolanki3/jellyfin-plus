# Implementation and test results (PR 7, IntelHardwareDecode)

## Test stage, round 1

| command | result |
|---|---|
| `bash scripts/test-gpu-vendor.sh` | exit 2: `'' is not a directory`. No jfbin dir and no dotnet in the workbench. NOT RUN, no pass count, the 3 mutations were NOT produced. |
| `python3 scripts/test-shim-device.py` | 16 PASS, 0 failed (includes the new bridge-chain routing checks). |
| `python3 scripts/sync_mcp_filesystem_docs.py --check` | script does not exist in this checkout (exit 2). |
| `pre-commit run --files ...` | `.pre-commit-config.yaml` is not a file in this checkout (exit 1). |
| C tests | NOT RUN (no gcc). |

## Static review of the diff
- Setting present with default false in `PluginConfiguration`, the `UpscaleSettings` mirror and the config page checkbox (`data-config`/`data-kind`/`data-default`).
- Probe key and session keys are added only when the setting is on (`PatcherHost.Levels`, `SessionJson`).
- `DecideHardwareDecode` gates on: setting, Intel, no CPU-side node, upright 8-bit SDR progressive h264/hevc, render node, test-decode; the reason names the first failure.
- `HwaccelArgs`/`BuildChain` take the VA-API path only when `plan.HardwareDecode`; other paths unchanged.
- Shim routes `vulkan=vk@va` plus `derive_device=vulkan` to the patched binary only.
- Nothing under `.github/workflows/` touched.

## Live checks
`kubectl get pods -A` works; the Jellyfin and intel-gpu-plugin pods are Running. The change depends on an Intel Arc render node and the patched ffmpeg, neither observable from here, so nothing about the hardware path was verified live.

## Not verified
C# not compiled; harness not run; no Arc run; the nv12 tail, `vk@va` derive and 0008/0009 behaviour are inferred.
