# Plan: optional Intel hardware decode (GPU vendors PR 7)

No bead was supplied (`beads_list` for the feature returned nothing), so the PR will say `no-bead:`. Nothing outside the files below is needed, so there is no `blocked_on_owner`.

## Approach

1. Setting `IntelHardwareDecode` (default false), wired in the 5 places: `PluginConfiguration`, the `UpscaleSettings` mirror, a `configPage.html` checkbox plus load/save lines, the probe, and session/status reporting.
   - The probe key is added only when the setting is on, like `NeuralMaxSourceHeight`. With it off the probe is byte-identical.
2. Decision, made in `UpscaleEngine.Decide` and cached on the `Plan`. All of these must hold:
   - The setting is on.
   - `IsIntelPath()` is true.
   - The plan has no CPU-side node (deblock, atadenoise, neural ort, game fsr2/dlss, deinterlace, tonemap).
   - The source is 8-bit SDR and progressive.
   - The plan is not `UsesCudaNeural`.
   - The startup test-decode passes.

   VA-API frames cannot enter a CPU filter, so any other plan keeps software decode. The record says which condition failed.
3. Command when hardware decode is chosen, using the render node `Devices()` already detects:
   - `-init_hw_device vaapi=va:<node> -init_hw_device vulkan=vk@va -hwaccel vaapi -hwaccel_output_format vaapi -filter_hw_device vk`
   - The chain starts at `hwmap=derive_device=vulkan` with no `format=yuv420p` and no `hwupload`.
   - It ends in a `hwdownload` tail whose format must be confirmed (see the `nv12` premise).
4. `HardwareDecodeUsable()` mirrors `Av1QsvUsable`.
   - It is cached as a `bool?` and has a test hook, `IntelHwDecodeProbeSource`.
   - It runs a short ffmpeg decode into `hwmap=derive_device=vulkan` on the render node.
   - A timeout or inconclusive result means software decode.
   - It runs on the binary the session will use. The failure in the known fact was the stock binary, and 0008/0009 exist only in the patched one. The shim routes only by `PATCHED_FILTERS` names, so a hwmap-only chain goes to stock. A small shim rule is needed: send argv containing `-hwaccel_output_format vaapi` plus `derive_device=vulkan` to the patched binary. This is the one change beyond a pure settings wiring, and the PR will say so.
5. `UpscalePatches` postfixes for `GetInputVideoHwaccelArgs`/`GetHwaccelType`/`GetHardwareVideoDecoder` stop blanking Jellyfin's vaapi args when the plan chose hardware decode.
6. `SessionRecord` gets `HardwareDecode` (bool?) and `HardwareDecodeReason` (string). Both are null when the setting is off, so NVIDIA, default and no-GPU session JSON is unchanged. They are mapped in `PatcherHost.SessionJson`.
7. Tests first in `scripts/gpu-vendor-harness/Program.cs`:
   - Golden command with the setting on, Intel, and a passing hook.
   - A failing hook gives the software chain and a reason.
   - An inconclusive hook gives software.
   - A CPU-node plan gives software with a reason.
   - Setting off, NVIDIA, no-GPU and mixed strings are byte-identical to the existing goldens.
   - The probe key is absent when the setting is off.
8. Docs in the same change: `GPU_VENDORS.md` (header, section 2 Decode row, series row 7, 5.6, section 6, section 8), `ENHANCEMENTS.md`, `INSTALL.md`. Spec commit first, then the PR 7 commit.
9. `scripts/test-gpu-vendor.sh` requires a jfbin dir as `$1`. The tool refuses arguments, and none was found in the worktree. If an env fallback (`JFBIN`) is added, the harness still only runs where Jellyfin's DLLs exist. If none exist, report the harness as NOT RUN rather than claiming a pass count. C tests are NOT RUN (no gcc). No Arc run is possible, so the feature stays "not verified on hardware".

```yaml
{
  "goal": "Add an optional, default-off IntelHardwareDecode setting that, on a detected Intel GPU with a passing startup test-decode and a plan free of CPU-side nodes, builds the VA-API to Vulkan (derive_device=vulkan via vk@va) command from the detected render node, falls back to software decode otherwise, records why in the session record, and leaves NVIDIA and default command lines byte-identical.",
  "files": [
    "src/Configuration/PluginConfiguration.cs",
    "src/patcher/Transcoding/UpscaleSettings.cs",
    "src/Configuration/configPage.html",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/Transcoding/UpscalePatches.cs",
    "src/patcher/PatcherHost.cs",
    "shim/jellyfin-ffmpeg-upscale",
    "scripts/test-shim-device.py",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test-gpu-vendor.sh",
    "docs/architecture/GPU_VENDORS.md",
    "ENHANCEMENTS.md",
    "INSTALL.md",
    "context-map.md"
  ],
  "test_commands": [
    "bash scripts/test-gpu-vendor.sh",
    "python3 scripts/test-shim-device.py",
    "python3 scripts/sync_mcp_filesystem_docs.py --check",
    "pre-commit run --files src/patcher/Transcoding/UpscaleEngine.cs docs/architecture/GPU_VENDORS.md ENHANCEMENTS.md INSTALL.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "IntelHardwareDecode exists with default false in PluginConfiguration, the UpscaleSettings mirror and the configPage checkbox (data-config/data-kind/data-default plus load/save), and appears in the probe only when on.",
    "With the setting on, an Intel GPU detected, a passing test-decode and a plan with no CPU nodes, the built command contains -init_hw_device vaapi=va:<detected render node>, -init_hw_device vulkan=vk@va, -hwaccel vaapi, -hwaccel_output_format vaapi, -filter_hw_device vk and hwmap=derive_device=vulkan, asserted as a golden string in the harness.",
    "With a failing or inconclusive test-decode, or a plan containing a CPU node, the command is the existing software-decode string and the session record carries a reason; asserted in the harness.",
    "NVIDIA, no-GPU, mixed-vendor and setting-off command lines, probe JSON and session JSON are byte-identical to the existing goldens.",
    "The session record and SessionJson report whether hardware decode ran and why not, as null when the setting is off.",
    "The shim sends a vaapi-decode plus derive_device=vulkan chain to the patched binary, covered by scripts/test-shim-device.py.",
    "GPU_VENDORS.md, ENHANCEMENTS.md and INSTALL.md are updated in the same change; the doc still says no Arc run was done.",
    "bash scripts/test-gpu-vendor.sh pass count is reported with 3 mutations shown to be caught, or the harness is reported NOT RUN with the reason if no jfbin directory exists. C tests are reported NOT RUN (no gcc).",
    "Nothing under .github/workflows/ and nothing in sanchitd5's repo is touched."
  ],
  "ui_candidates": [
    {
      "name": "existing jellyfin-plus config page markup (reuse)",
      "licence": "same as this repo (already shipped)",
      "maintenance": "owned in this repo; GpuVendor and NeuralMaxSourceHeight use the same pattern",
      "dark_mode": "yes, inherits the Jellyfin dashboard theme the page already renders in",
      "coverage": "full: one checkbox with data-config/data-kind/data-default plus the load/save lines",
      "size": "a few lines of markup and script, no new asset",
      "chosen": true
    },
    {
      "name": "Jellyfin's built-in emby-checkbox web component",
      "licence": "GPL-2.0, ships with Jellyfin web",
      "maintenance": "upstream Jellyfin; its internals change between server versions",
      "dark_mode": "yes, themed by Jellyfin",
      "coverage": "full for a checkbox, but the config page does not use it for its other settings",
      "size": "no extra asset, but a second markup style on one page",
      "chosen": false
    },
    {
      "name": "a plain HTML input",
      "licence": "none (browser built-in)",
      "maintenance": "none",
      "dark_mode": "no, unstyled against the dashboard theme",
      "coverage": "full for a checkbox",
      "size": "smallest, but looks out of place beside the other settings",
      "chosen": false
    },
    {
      "name": "pulling in any new UI library",
      "licence": "unknown until picked",
      "maintenance": "a new dependency to track",
      "dark_mode": "would need theming",
      "coverage": "far more than one checkbox needs",
      "size": "large for one setting; the page is an embedded resource in the plugin DLL",
      "chosen": false,
      "rejected": "not needed for one checkbox"
    }
  ],
  "ui_note": "Nothing new is added: no library, component or asset. The setting is one checkbox in the plugin's existing configuration page markup.",
  "reuse_check": [
    {
      "need": "Cached, hookable startup test-decode with fail-soft null result",
      "checked": "src/patcher/Transcoding/UpscaleEngine.cs Av1QsvUsable (~3194) and DrmVulkanStarts (~2444), read in this checkout at fork main 449a361",
      "verdict": "extend",
      "reason": "Add HardwareDecodeUsable on the same pattern and reuse RunFfmpegProbe and Devices(); a different command and binary, same shape."
    },
    {
      "need": "Render node and Vulkan/VA device init args",
      "checked": "UpscaleEngine.Devices()/DeviceInit (~2376) and HwaccelArgs (~3449), read in this checkout",
      "verdict": "extend",
      "reason": "Devices() already yields the node; add the vaapi/vk@va variant beside VulkanArgs rather than re-detecting."
    },
    {
      "need": "A conditional probe key and flat session-record fields",
      "checked": "PatcherHost.Levels (NeuralMaxSourceHeight, ~222) and SessionJson (~325-381), read in this checkout",
      "verdict": "use",
      "reason": "The existing pattern gives a key present only when the setting is on and nullable record fields."
    },
    {
      "need": "Test scaffolding for goldens, fake sysfs and probe hooks",
      "checked": "scripts/gpu-vendor-harness/Program.cs and GpuVendorHarness.csproj (1082 passing checks at baseline, per GPU_VENDORS.md header)",
      "verdict": "extend",
      "reason": "Add sections beside the existing per-feature ones; a new harness would duplicate the fake sysfs and hooks."
    },
    {
      "need": "Making VA-API frames reach libplacebo on the stock binary",
      "checked": "ffmpeg/0008-hwmap-chain-format.patch and 0009-hwmap-query-formats.patch, read in this checkout; revenant docs/agent-lessons/apps.md A380 lesson (stock 12.4 hwmap=derive_device=vulkan gave 'Conversion failed!')",
      "verdict": "none-fits",
      "reason": "The stock binary fails by the recorded measurement; only the patched binary carries 0008/0009, so a routing rule in the shim is needed, not an existing option."
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The edit points are HwaccelArgs, BuildChain, Devices(), Decide and the three hwaccel Postfixes in UpscalePatches, and the five settings places are PluginConfiguration, UpscaleSettings, configPage.html, PatcherHost.Levels and SessionRecord/SessionJson.",
      "label": "VERIFIED",
      "check": "Read src/patcher/Transcoding/UpscaleEngine.cs, UpscalePatches.cs, PatcherHost.cs and the config files in this checkout; the code graph was indexed at an older commit and returned no nodes.",
      "load_bearing": false
    },
    {
      "id": "P2",
      "claim": "The shim chooses the patched binary only when the command names one of PATCHED_FILTERS (oidn, optix, ort, fsr2, dlss), so a libplacebo/hwmap-only chain runs on the stock binary where the known failure occurred.",
      "label": "VERIFIED",
      "check": "Read shim/jellyfin-ffmpeg-upscale pick_binary and PATCHED_FILTERS in this checkout.",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "Patches 0008 and 0009 make hwmap=derive_device=vulkan work straight off raw VA-API decode output on the patched binary.",
      "label": "INFERRED",
      "check": "The patch text and its comment aim at exactly this shape, but only an Arc run on the patched binary proves it; there is none here, so the feature ships off and the PR says not verified.",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "The pinned jellyfin-ffmpeg derives a Vulkan device from a VA-API device (-init_hw_device vulkan=vk@va).",
      "label": "INFERRED",
      "check": "hwcontext_vulkan.c vulkan_map_to handles AV_PIX_FMT_VAAPI under CONFIG_VAAPI with the DRM-modifier extension (fetched from upstream n8.1 source); the derive case in vulkan_device_derive was in a truncated part of the fetch and is not confirmed. The startup test-decode is the runtime guard.",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "Frames out of hardware decode are VA surfaces, so no CPU node (deblock, atadenoise, neural ort, game fsr2/dlss, deinterlace, tonemap) can precede hwmap; hardware decode must be gated off for such plans.",
      "label": "INFERRED",
      "check": "Follows from -hwaccel_output_format vaapi and the CPU nodes listed in BuildChain; confirm in the harness by asserting each CPU node forces software decode.",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "The tail after libplacebo for hardware decode is hwdownload,format=nv12 (not yuv420p), because the Vulkan frames keep the decoder's sw format.",
      "label": "INFERRED",
      "check": "Cannot be settled without an Arc run; the implementation will make the format a single constant and the docs will say it is unmeasured.",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "A jfbin directory with Jellyfin's DLLs exists where the implementation stage runs, so bash scripts/test-gpu-vendor.sh can run and give a pass count.",
      "label": "INFERRED",
      "check": "bash scripts/test-gpu-vendor.sh with no arguments exited 2 (no dir given, none found in the worktree); the tool refused an argument and dotnet --list-sdks. If none exists, report the harness NOT RUN instead of a count.",
      "load_bearing": true
    },
    {
      "id": "P8",
      "claim": "Software decode into libplacebo remains the working default on the A380, and the fallback keeps it unchanged.",
      "label": "VERIFIED",
      "check": "knowledge-get revenant:docs/agent-lessons/apps.md (rev-jyqze): every libplacebo number was software decode plus hwupload, hwmap=derive_device=vulkan failed with 'Conversion failed!'.",
      "load_bearing": false
    }
  ]
}
```
