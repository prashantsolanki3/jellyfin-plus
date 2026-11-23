# Plan: rev-f6v5q - end the Intel libplacebo chain in RGBA (shim + INSTALL.md)

The shim's `build_chain` always returns `format=yuv420p,hwupload,<libplacebo>,hwdownload,format=yuv420p`.
The shim has no vendor detection (GPU_VENDORS.md 5.3 says so); its only GPU input is the hand-set
`render_node`. I add a small `render_node_vendor`/`is_intel` helper that reads
`<DRM_ROOT>/<node>/device/vendor` (the same sysfs file and `0x8086` id the C# `GpuVendor.cs` uses) for a
node that already passed the `renderD<digits>` check. Only when that is Intel does `build_chain` append
`:format=rgba` to libplacebo and end the chain `hwdownload,format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p`.
Unset node, invalid node, unreadable sysfs, NVIDIA or any other vendor: the old string, byte for byte.
The shim cannot see source tags, so the matrix is bt709/tv (the bead's default); tagged-source handling is left out.

Tests first: new `scripts/test_shim_chain.py` (underscore name so I can run it; the existing
`test-shim-device.py` has a hyphen and is outside my allowed commands). It loads the shim the way
`test-shim-device.py` does, points `shim.DRM_ROOT` at a temp fake sysfs tree (set with `setattr`, so the
tests fail by count, not by crash, before the change), and pins: NVIDIA/default chain golden (with and
without shader files), vendor 0x10de and unreadable/invalid node unchanged, and the Intel chain
(contains `format=rgba`, the download and the CPU conversion, ends in `format=yuv420p`, still carries
libplacebo and the shader). I run it before the edit (Intel cases fail, NVIDIA cases pass), then after
(all pass), and show both counts. Also run `test-shim-device.py`'s equivalent is not possible; the
`rewrite_args` path is untouched.

Docs: INSTALL.md gets an Intel variant of the quick proof beside the NVIDIA one; GPU_VENDORS.md 5.3's
shim paragraph is updated (it now reads the render node's vendor, only to choose the tail) plus a
sentence on why (ANV multi-planar YUV output).

Out of scope, flagged in the PR: the plugin's C# `UpscaleEngine.BuildChain` Intel tail
(`,hwdownload,format=yuv420p`) has the same shape and is NOT in the bead's Where. When the plugin's
Harmony patches are live the shim passes through (`plugin_patch_active`), so this PR fixes the shim
fallback only; the real-transcode fix needs its own bead. I will say so rather than widen the diff.

```yaml
{
  "goal": "On Intel only (shim render_node whose sysfs vendor is 0x8086), end the shim's libplacebo chain in RGBA (libplacebo format=rgba, hwdownload,format=rgba, CPU scale to bt709/tv yuv420p) and document it in INSTALL.md; NVIDIA/default chains stay byte-for-byte identical.",
  "files": [
    "shim/jellyfin-ffmpeg-upscale",
    "INSTALL.md",
    "scripts/test_shim_chain.py",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 scripts/test_shim_chain.py",
    "python3 -m py_compile shim/jellyfin-ffmpeg-upscale",
    "python3 scripts/sync_mcp_filesystem_docs.py --check",
    "pre-commit run --files shim/jellyfin-ffmpeg-upscale INSTALL.md scripts/test_shim_chain.py docs/architecture/GPU_VENDORS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "scripts/test_shim_chain.py fails on the new Intel cases before the shim change (counts shown) and passes after, with the NVIDIA/default golden cases passing both before and after",
    "Intel chain contains libplacebo ...:format=rgba, then hwdownload,format=rgba, then scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
    "NVIDIA, unset render_node, invalid node, non-Intel vendor and unreadable sysfs produce the pre-change chain string byte for byte",
    "libplacebo is still in the Intel chain (not dropped)",
    "INSTALL.md shows the Intel RGBA chain; GPU_VENDORS.md shim paragraph no longer says the shim has no vendor detection",
    "Post-merge bench rows 2-4 and 8 (xpsnr_delta, UAVG/VAVG within 2 of master) are NOT verifiable in this PR; they need the cascade and rebuild"
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The shim has no vendor detection; render_node is its only GPU input, so Intel must be inferred from the node's sysfs vendor id.",
      "label": "VERIFIED",
      "check": "Read shim/jellyfin-ffmpeg-upscale (DEFAULTS, device_args, build_chain: no vendor logic) and docs/architecture/GPU_VENDORS.md line 451 ('it has no vendor detection')",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "The shim chain to change is build_chain's single return string, and rewrite_args keeps non-scaling nodes and appends the chain, so no other shim code needs to change.",
      "label": "VERIFIED",
      "check": "Read shim/jellyfin-ffmpeg-upscale lines 420-484",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "The existing shim chain test is scripts/test-shim-device.py (hyphen, not runnable by this session); there is no shim chain test, and the C# gpu-vendor-harness pins UpscaleEngine strings, not the shim's.",
      "label": "VERIFIED",
      "check": "Glob scripts/test*; Read scripts/test-shim-device.py; Grep scripts/gpu-vendor-harness/Program.cs for hwupload",
      "load_bearing": false
    },
    {
      "id": "P4",
      "claim": "The 0x8086 PCI vendor id read from <drm>/<node>/device/vendor is how this repo identifies Intel (so the shim can reuse the convention).",
      "label": "VERIFIED",
      "check": "src/patcher/Transcoding/GpuVendor.cs (PciIntel = 0x8086, reads device/vendor); GPU_VENDORS.md line 82",
      "load_bearing": false
    },
    {
      "id": "P5",
      "claim": "libplacebo's format=rgba on Vulkan with hwdownload,format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p yields correct colour on ANV (UAVG 116.2 / VAVG 150.4 vs master 116.2 / 150.5).",
      "label": "INFERRED",
      "check": "Taken from the bead's A380 measurement (bench run 10, build 16); not re-run here: this session cannot exec into the Intel pod (kubectl get|describe|logs only). Settled post-merge by the bench rows in Acceptance 2",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "The plugin's C# UpscaleEngine.BuildChain Intel tail has the same broken shape and is outside this bead's Where; with Harmony patches live the shim passes through, so the shim fix alone does not change real plugin transcodes.",
      "label": "VERIFIED",
      "check": "Read UpscaleEngine.cs lines 3922-3935 (',hwdownload,format=yuv420p' on Intel) and shim main() (plugin_patch_active -> passthrough)",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "NVIDIA-only/default shim chain strings are fully determined by build_chain's current return, so a golden test can pin them byte for byte.",
      "label": "VERIFIED",
      "check": "Read build_chain (line 435-438): string depends only on tw, th, upscaler and optional shader path",
      "load_bearing": false
    },
    {
      "id": "P8",
      "claim": "docs/agent-lessons.md and docs/automations.md do not exist in this checkout, so the PR's lesson line must be 'lesson: none (<reason>)' or name a changed doc.",
      "label": "VERIFIED",
      "check": "Glob docs/{agent-lessons,automations}.md returned no files",
      "load_bearing": false
    }
  ],
  "reuse_check": [
    {
      "need": "Detect that the shim's render node is an Intel GPU",
      "checked": "GpuVendors/GpuVendor.cs in src/patcher (C#, same repo, sysfs device/vendor == 0x8086)",
      "verdict": "none-fits",
      "reason": "The shim is a standalone Python script with no Harmony/plugin assemblies (it is the no-plugin fallback); only the sysfs convention can be reused, in about six lines"
    },
    {
      "need": "RGBA output from libplacebo and the CPU YUV conversion",
      "checked": "Built-in ffmpeg libplacebo 'format' option and swscale 'scale' filter (out_color_matrix, out_range) in the shipped jellyfin-ffmpeg",
      "verdict": "use",
      "reason": ""
    }
  ]
}
```
