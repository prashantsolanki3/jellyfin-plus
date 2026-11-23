# Plan review: rev-f6v5q (round 1)

I re-ran each check myself with Read, Grep and Glob. I did not take the VERIFIED labels on trust.

## Premises

- **P1 (load-bearing): VERIFIED.** Grep of `shim/jellyfin-ffmpeg-upscale` finds `render_node` only in `DEFAULTS` (line 75) and `device_args`. It finds no vendor logic and no `DRM_ROOT`. `GPU_VENDORS.md` line 451 says "it has no vendor detection".
- **P2 (load-bearing): VERIFIED.** `build_chain` (lines 420-438) has a single return string, `format=yuv420p,hwupload,<placebo>,hwdownload,format=yuv420p`. `rewrite_args` (lines 454-484) keeps non-scaling nodes and appends `chain`, so it needs no change. `main()` passes through when `plugin_patch_active` is set (line 591).
- **P3: VERIFIED.** `scripts/test-shim-device.py` (hyphen) is the only shim test, and it only covers `rewrite_args` and the device arguments. `scripts/test-gpu-vendor.sh` calls it. `scripts/gpu-vendor-harness/Program.cs` pins `UpscaleEngine` strings (lines 351, 356, 718) and none of the shim's. A new `scripts/test_shim_chain.py` is therefore justified. The existing underscore test `scripts/test_config_page_withheld.py` runs here ("ok: ...").
- **P4: VERIFIED.** `GpuVendor.cs` has `PciIntel = "0x8086"` (line 26) and `FromPciId`, and it reads `device/vendor`. `GPU_VENDORS.md` line 82 agrees.
- **P5 (load-bearing): INFERRED, not verifiable by me.** The claim is that RGBA output plus a CPU bt709/tv conversion gives correct colour on ANV (A380). I have no way to run that: `kubectl` here is get/describe/logs only, with no exec. The plan is honest about it and defers it to the post-merge bench rows. Under the review rules a load-bearing premise I cannot run means BLOCKED, not APPROVE.
- **P6: VERIFIED.** `UpscaleEngine.cs` lines 3932-3934 end the Intel chain in `,hwdownload,format=yuv420p`. The shim passes through when the plugin patch is active. Flagging the C# half as out of scope is correct.
- **P7: VERIFIED.** The `build_chain` return depends only on `tw`, `th`, `upscaler` and the optional shader path, so a golden string test is feasible.
- **P8: VERIFIED.** Glob finds neither `docs/agent-lessons.md` nor `docs/automations.md`.

## Scope and TDD

- The `files` list (shim, INSTALL.md, `test_shim_chain.py`, GPU_VENDORS.md) covers the whole intended change. INSTALL.md line 45 holds the chain to add an Intel variant beside. I found no other shim chain string.
- `tdd: required` is honest. The plan runs the Intel cases before the edit and expects them to fail by count, with the NVIDIA/default golden cases passing both before and after. It uses `setattr` on `shim.DRM_ROOT` so the tests fail by count rather than crash.
- Each test catches a real bug:
  - the NVIDIA golden cases catch an accidental change to the default chain;
  - the invalid-node, unreadable and non-Intel cases catch the Intel path leaking to other hardware;
  - the Intel cases catch a missing `format=rgba`, a missing CPU conversion, or libplacebo being dropped.

## Defects to fix

1. `test_commands` lists `python3 scripts/sync_mcp_filesystem_docs.py --check`. No `scripts/sync_*` file exists in this checkout. The command would fail, so drop it, or state that it does not apply.
2. `test_commands` lists `pre-commit run --files ...`. There is no `.pre-commit-config.yaml` in the checkout, so this would also fail. Drop it, or state that it does not apply.
3. `python3 -m py_compile shim/jellyfin-ffmpeg-upscale` writes a `__pycache__` next to the shim. Make sure that stays out of the diff.
4. The bead says to find the tests in `scripts/test-*.sh`, and `test-gpu-vendor.sh` already calls `test-shim-device.py`. Consider noting in the PR that `test_shim_chain.py` is picked up by the affected-tests CI job but is not wired into `test-gpu-vendor.sh`.

VERDICT: BLOCKED
Reason: P5 is load-bearing and I cannot run it, because there is no Intel or ANV exec access from this session. P1-P4 and P6-P8 are VERIFIED by my own checks. If the owner accepts the bead's A380 measurement as the specification, the plan is sound once defects 1-2 are fixed.
