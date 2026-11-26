# Implementation: Intel Arc admin settings (round 2)

Files changed: PluginConfiguration.cs, UpscaleSettings.cs, UpscaleEngine.cs, PatcherHost.cs, configPage.html,
gpu-vendor-harness/Program.cs, test_config_page_intel.py (new), GPU_VENDORS.md, ENHANCEMENTS.md.

## Round 2 fix

The wrapper's run of `scripts/test-gpu-vendor.sh /opt/jellyfin` failed 5 checks, all tagged `[neural]` in
`IntelHardwareDecode` ("a CPU-side step keeps software decode"). That block builds a plan with an ONNX neural level on
Intel using `HwSettings(true)`. It only passed before because `AutoNeuralCache` set the static `IntelOnnxMeasured = true`
and never reset it, so the flag leaked into the later test. With the static replaced by `IntelNeuralAllowed` on the
settings, that block saw the level withheld and kept hardware decode. Fix: the block's settings set
`IntelNeuralAllowed = true` (harness only; no engine change).

## Test order

1. `scripts/test_config_page_intel.py` (new, written first) - settings names and defaults in both classes, page controls,
   engine clamp.
2. C# harness: `IntelOnnxMeasured` assignments became the setting; new `LowResRoute()` section.
3. Engine, settings, page, docs.
4. Round 2: ran the harness for real (dotnet and `/opt/jellyfin` exist here), found the 5 failures, fixed the harness.

## Results

- `bash scripts/test-gpu-vendor.sh /opt/jellyfin` -> before the fix 1742 passed, 5 failed; after: `1747 passed, 0 failed`
  (the shim-device script 0 failed, the shim-chain script 17 passed, 0 failed)
- `python3 scripts/test_config_page_intel.py` -> ok
- `python3 scripts/test_config_page_withheld.py` -> ok
- `python3 scripts/test_bench_intel.py` -> 328 checks passed, 0 failed
- `python3 scripts/test_shim_chain.py` -> 17 passed, 0 failed
- `scripts/bench-intel.sh` and `scripts/test_bench_intel.py`: no diff.

## Not run

`scripts/sync_mcp_filesystem_docs.py` and `.pre-commit-config.yaml` do not exist in this checkout. The GPU, live ffmpeg
and browser checks (`test-gpu-resident.sh`, `test-gu-inputs.sh`, `test-hwmap-bridge.sh`, `test-vf-ort.sh`) were not run
(the wrapper reports them exit 2: tools, GPU or tarball missing). The config page was not opened in a browser.
`tdd: required`, followed.
