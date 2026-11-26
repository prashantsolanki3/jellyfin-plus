# Plan: Intel Arc admin settings (IntelNeuralAllowed, IntelLowRes*)

Epic rev-7n4fe. Plugin code only; `scripts/bench-intel.sh` and `scripts/test_bench_intel.py` are not touched.

**1. `IntelNeuralAllowed`.** Replace the hardcoded `UpscaleEngine.IntelOnnxMeasured = false` (line 3136, read in
`IntelWithholdReason` at 3149) with `Settings?.IntelNeuralAllowed == true`. Add the property (default false) to
`PluginConfiguration` and to the patcher mirror `UpscaleSettings`. The two cross the ALC boundary as one JSON
serialisation of the whole configuration (`PatcherLoader.Serialize`), so names must match. When false nothing changes:
the same `NotMeasuredOnIntel` reason, the same `GpuWithheldUnmeasured`, the same probe `Withheld` list. When true, the
existing PR 5 path (OpenVINO device, provider file, weights present) is offered. `configPage.html` gets a checkbox
beside `NeuralAllowed`/`NeuralMaxSourceHeight`, added to the `data-gates` of `NeuralAllowed`. Its help text carries the
bench run22 numbers (about 1.4 fps at 360p, 0.5 fps at 720p on an A380, so it will stutter).

**2. Low-res route.** Add `IntelLowResMaxSourceHeight` (int, default 480, 0 = off) and `IntelLowResIntermediateScale`
(double, default 1.5) to both settings classes. In `Decide`, after `ShaderLibrary.Resolve` and before the plan is
returned, a route is set only when all of these hold:
- the GPU is a detected Intel one (`onIntel`);
- the upright source height is below the max;
- `plan.UpscaleApplied` and the resolved SR level is `fsrcnnx`, `fsrcnnx-heavy` or `fsrcnnx-max` (the SR bypass has
  already set it to off when that applies);
- no neural or game pass ran (fsr2/dlss hand libplacebo a target-size frame);
- the intermediate height, `round_even(sh*scale)` with scale clamped to [1.0, 2.0], is still below the target height.

The plan then carries `LowResMidWidth/Height` and a `LowResRoute` string, "low-res route: fsrcnnx to 1.5x then
bicubic". `AppendLibplaceboSegment` uses the mid size for `w=`/`h=` (not when `keepInputSize`). The two Intel tails in
`BuildChain` (software path `...format=yuv420p,hwdownload,format=yuv420p`, hw-decode path `hwdownload,format=nv12`) then
append `,scale=<W>:<H>:flags=bicubic,format=<fmt>`. That is the chain shape of `bench-intel.sh` `chain_shader_bicubic`.
Other vendors build identical strings.

The route is reported through a new `SessionRecord.LowResRoute` (JsonIgnore WhenWritingNull, so non-Intel JSON gains no
key), a summary part, and the flat session JSON in `PatcherHost` only when non-null. The NVIDIA probe and session keys
stay as they were.

**3. Config page.** Two number inputs after `SrMinScaleFactor`: max height (`min=0 max=4320`) and scale
(`min=1 max=2 step=0.05`, required). Native form validation blocks a bad save, and the engine clamps again on read
because the API accepts anything. Both go through the generic `data-config`/`data-kind`/`data-default` load/save, so
there are no extra script lines.

**4. Tests (TDD).** First write the new `scripts/test_config_page_intel.py`. It checks that each new input exists in
`configPage.html`, that its `data-config` names a `PluginConfiguration` property, that the defaults match the C#
defaults, that the scale input is bounded 1 to 2, that the checkbox defaults to false and carries the 1.4/0.5 fps help,
and that `UpscaleSettings` mirrors every name. Then extend the C# harness `scripts/gpu-vendor-harness/Program.cs`:
- Replace the five `UpscaleEngine.IntelOnnxMeasured = ...` uses with the setting (`IntelNeuralAllowed` on the
  `UpscaleSettings` built by `RigSettings`/`NeuralSettings`, or mutated on `rig`). Keep the existing assertions, and add
  that the default is false and the withhold text is unchanged.
- Add a low-res section: default on and off, Intel only (NVIDIA, no GPU and `GpuVendor=nvidia` give a byte-identical
  chain), the chain shape (`w=540:h=...`, then `scale=1920:1080:flags=bicubic`), the threshold boundary (479/480),
  non-FSRCNNX levels, a ratio-agnostic level, scale clamping, and record/summary text.

**5. Docs.** `GPU_VENDORS.md` (the paragraph at 517-523 and the row 2 section at 1040-1069), plus one clause in
`ENHANCEMENTS.md` line 229.

**Verification limits.** My shell cannot run `dotnet` or `test-gpu-vendor.sh`, so the C# changes and harness additions
cannot be compiled or run here. I will say so in the PR and list them as unverified. I can run the Python tests. The
cluster is not involved: this plugin runs on the Jellyfin box over SSH, not in Kubernetes.

```yaml
{
  "goal": "Make Intel neural (IntelNeuralAllowed, default false) and the low-res FSRCNNX-to-1.5x-then-bicubic route (IntelLowResMaxSourceHeight=480, IntelLowResIntermediateScale=1.5) admin settings, Intel-only, with record/summary reporting, config page controls, harness tests and docs",
  "files": [
    "src/Configuration/PluginConfiguration.cs",
    "src/patcher/Transcoding/UpscaleSettings.cs",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/PatcherHost.cs",
    "src/Configuration/configPage.html",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test_config_page_intel.py",
    "docs/architecture/GPU_VENDORS.md",
    "ENHANCEMENTS.md"
  ],
  "test_commands": [
    "python3 scripts/test_config_page_intel.py",
    "python3 scripts/test_config_page_withheld.py",
    "python3 scripts/test_bench_intel.py",
    "python3 scripts/test_shim_chain.py",
    "python3 scripts/sync_mcp_filesystem_docs.py --check",
    "scripts/test-gpu-vendor.sh <jfbin>  (C# harness; needs dotnet and the server's DLLs, not runnable in this sandbox)"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "IntelNeuralAllowed defaults to false in PluginConfiguration and UpscaleSettings; with it false an Intel session asking for an ONNX level is withheld exactly as before (same reason text, GpuWithheldUnmeasured, probe Withheld list)",
    "With IntelNeuralAllowed true on Intel the ONNX levels are offered and run when the OpenVINO provider and weights are present; the config page has the checkbox with the 1.4 fps / 0.5 fps help text",
    "IntelLowResMaxSourceHeight (default 480, 0 = off) and IntelLowResIntermediateScale (default 1.5, validated 1.0 to 2.0 on the page and clamped in the engine) exist in both settings classes and on the config page next to SrMinScaleFactor",
    "On Intel only, a source below the max height with an fsrcnnx SR level builds libplacebo at round_even(source*scale) followed by scale=W:H:flags=bicubic; NVIDIA/no-GPU chains are byte-identical to before",
    "The session record and summary say 'low-res route: fsrcnnx to 1.5x then bicubic' when the route ran, and nothing new appears for other sessions",
    "Existing harness checks that flipped IntelOnnxMeasured are adapted to the setting; new harness checks cover both settings (default, Intel-only, chain shape, boundaries)",
    "GPU_VENDORS.md describes both settings and their defaults; scripts/bench-intel.sh and scripts/test_bench_intel.py are not modified",
    "PR body states that the C# was not compiled or run in the sandbox if that remains true"
  ],
  "reuse_check": [
    {
      "need": "Chain shape for FSRCNNX to an intermediate size then bicubic",
      "checked": "scripts/bench-intel.sh chain_shader_bicubic / alt_setup (repo, read-only) and the existing AppendLibplaceboSegment/BuildChain Intel tail in UpscaleEngine.cs",
      "verdict": "extend",
      "reason": "Same libplacebo node with mid w/h plus one swscale bicubic node; no new library"
    },
    {
      "need": "Settings plumbing across the ALC boundary and config page load/save",
      "checked": "PatcherLoader.Serialize (whole PluginConfiguration as JSON) and the generic data-config/data-kind/data-default handling in configPage.html",
      "verdict": "use",
      "reason": "New properties need only matching names and defaults; no new script lines"
    },
    {
      "need": "Test harness for the engine",
      "checked": "scripts/gpu-vendor-harness (Program.cs, runs through scripts/test-gpu-vendor.sh)",
      "verdict": "extend",
      "reason": "It is the existing C# test project and already holds every Intel withhold/chain check"
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "IntelOnnxMeasured is a static bool at UpscaleEngine.cs:3136, read only in IntelWithholdReason (3149), and set by five places in the gpu-vendor harness (Program.cs lines 1255, 1260, 2076, 2251, 2261, 2334)",
      "label": "VERIFIED",
      "check": "Grep IntelOnnxMeasured across the repo; Read UpscaleEngine.cs 3100-3180",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "Settings cross the plugin/patcher boundary as one JSON serialisation of PluginConfiguration deserialised into UpscaleSettings, so matching property names are enough and UpscaleEngine.Settings is readable from IntelWithholdReason",
      "label": "VERIFIED",
      "check": "PatcherLoader.cs:296 Serialize(PluginConfiguration); PatcherHost.cs:44 Deserialize<UpscaleSettings>; harness sets UpscaleEngine.Settings before Decide",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "The Intel libplacebo tail is ':format=yuv420p,hwdownload,format=yuv420p' (software decode) and the hw-decode tail is 'hwdownload,format=<nv12>', so a CPU 'scale=W:H:flags=bicubic' node can be appended after either, matching bench chain_shader_bicubic",
      "label": "VERIFIED",
      "check": "Read UpscaleEngine.cs BuildChain 4923-5042 and scripts/bench-intel.sh 195-221",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "FSRCNNX's WHEN guard (1.3x) fires at an intermediate 1.5x, and the SrMinScaleFactor bypass (1.6x on the target) does not drop the level for the targets the route applies to (360p to 1080p is 3x)",
      "label": "INFERRED",
      "check": "SrMinScaleFactor bypass is computed on plan.Height/sh (UpscaleEngine.cs 1461-1482), and the guard is documented in AGENTS.md; the real shader files are not in this checkout, so the 1.5x behaviour rests on bench run22 (+1.08 dB), which the task states. A live ffmpeg run on an Arc would settle it.",
      "load_bearing": false
    },
    {
      "id": "P5",
      "claim": "Adding a nullable SessionRecord.LowResRoute with WhenWritingNull, and adding the flat session key only when non-null, leaves NVIDIA/no-GPU JSON and the probe key set unchanged",
      "label": "INFERRED",
      "check": "SessionRecord already uses JsonIgnore WhenWritingNull for HardwareDecodeReason (UpscaleEngine.cs:265); the flat dictionary construction in PatcherHost ~375-410 needs a conditional add. The harness ProbeKeys golden (Program.cs:472) covers the probe, not the session record, so the implementer must grep the harness for session-key goldens before finishing.",
      "load_bearing": false
    },
    {
      "id": "P6",
      "claim": "Config page needs no extra script for new fields: applyConfig/collectConfig are driven by data-config/data-kind/data-default, and native form validation (min/max/required) blocks a bad save",
      "label": "INFERRED",
      "check": "configPage.html inputs such as SrMinScaleFactor (line 89) use only those attributes and the submit handler is a form submit event; the applyConfig/collectConfig bodies (~lines 900-950) will be read before editing to confirm.",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "The C# cannot be built or the harness run in this sandbox (no dotnet, no jfbin, shell limited to python3 scripts)",
      "label": "VERIFIED",
      "check": "The allowed command list contains no dotnet or .sh runner; scripts/test-gpu-vendor.sh needs dotnet and a jfbin directory (lines 62-63)",
      "load_bearing": false
    },
    {
      "id": "P8",
      "claim": "Editing GPU_VENDORS.md cannot break scripts/test_bench_intel.py, which reads it only to confirm the base image digest",
      "label": "VERIFIED",
      "check": "test_bench_intel.py:1187-1190 is the only SPEC use; re-run the test after the edit",
      "load_bearing": false
    }
  ]
}
```
