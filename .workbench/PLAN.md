# Plan: address upstream review of sanchitd5/jellyfin-plus#3

Scope is the Intel path of PR 3 only. NVIDIA and the default path stay byte-identical, because every change sits behind
`IsIntelPath()` or `Devices()` returning its default `DeviceInit`. Tests come first in
`scripts/gpu-vendor-harness/Program.cs`, the repo's existing convention. That harness compiles the real patcher sources
against the server's DLLs, so it cannot run in this sandbox (no dotnet, no jfbin). The one test that can run here is a
new static Python check for the config page. I will say so in the PR.

Review items, one commit each:

1. **Probe off the session path (review items 1 and 2).** `DrmVulkanStarts` stops running ffmpeg under a global lock.
   - A per-node entry holds `{Result, At, Running}`, behind a short lock that guards the entry only.
   - A miss, or an expired false/null, starts a single-flight `Task.Run` probe and returns immediately.
   - The caller gets the last known value, or "not known yet". Not known yet resolves to the untested node, with the
     record text "(test pending)". A cached null keeps today's "(not tested: ffmpeg could not be run)".
   - Only `true` is kept for the life of the process. False and null expire after a retry window of about 5 minutes.
     While an expired entry is re-tested, the stale value is served.
   - `UpscaleEngine.WarmDevices()` is called from `PatcherHost.Apply` after `Configure`. It calls `Devices()`, which is
     now non-blocking and kicks the background probe. It does nothing off Intel.
   - Test seams: `DrmVulkanProbeSource` now replaces only the runner. Cache, TTL and single-flight logic run under test.
     I add an internal clock, an "inline" switch to run the probe synchronously, and a cache reset. The existing
     harness blocks that flip the source true, false, null on one node call the reset.
   - New tests:
     - A session-path call returns without waiting for a blocked probe.
     - Two concurrent callers produce one probe.
     - `true` is never re-asked.
     - `false` and `null` are re-asked only after the window, and a later `true` replaces them.
     - The stale value is served during a re-test.
2. **Withhold the ONNX neural levels on Intel (review item 3).**
   - Add `ShaderLibrary.IsOnnxNeural(level)`. `IsNvidiaOnlyNeural` is unchanged, so the NVIDIA-only harness checks still
     hold.
   - Add `UpscaleEngine.IntelWithholdReason(axis, level)`, returning `NeedsNvidia`, a new `NotMeasuredOnIntel` reason for
     `realesr-*`, or null. `OfferedOnThisGpu`, `AddWithheld` and `Withhold` use it.
   - `Levels.Withheld` and `Status.Withheld` then carry the per-level reason. The Intel `Neural` list becomes `off`.
   - The session record and summary stop hard-coding "needs an NVIDIA GPU". `Plan` keeps (note, reason) pairs and
     `Summarise` and `WithheldNote` group them by reason.
   - The NVIDIA-only sentence stays byte-identical, because the harness asserts it.
   - **No opt-in setting.** An opt-in would need the 5-place settings checklist. Withholding is the smaller change and is
     reversible when #4 lands. The PR will say so.
   - Tests:
     - Intel probe `Neural` is `off`.
     - `realesr-*` is withheld with the new reason.
     - Withheld session summary wording is correct per reason.
     - NVIDIA probe and session are unchanged.
3. **Pin the QSV device to the detected node (review item 4).**
   - Drop the renderD128-135 "implicit" branch. On Intel with a QSV encoder, `QsvArgs` is always
     `-init_hw_device qsv=qs:hw_any,child_device=<node>` and `QsvText` is the node.
   - With no QSV encoder the arguments stay empty and the text stays "none".
   - Update the harness expectations for `HwaccelArgs`, `Av1QsvProbeArgs`, the record text and the two-GPU cases.
   - Add a mixed AMD+Intel case: AMD at renderD128, Intel at renderD129 gives `child_device=/dev/dri/renderD129`.
4. **Resolve `DeviceInit` once per plan (nit).**
   - Add `Plan.Devices`, set in `Decide` when the plan acts. `HwaccelArgs(plan)` and `Describe` use `plan.Devices ?? Devices()`.
   - `VulkanDenoiseProbeArgs` and `Av1QsvProbeArgs` take an optional `DeviceInit`. `Decide`, `VulkanDenoiseRunsOnPatchedBinary`
     and `Av1QsvUsable` resolve it once and pass it.
   - Test: change the probe answer after `Decide`, and the command and the record still agree.
5. **Shim `render_node` documentation (nit).** Docs only: `ENHANCEMENTS.md` and `GPU_VENDORS.md` section 5.3, plus one
   docstring line in `shim/jellyfin-ffmpeg-upscale`. The text says:
   - It is set by hand in `/etc/jellyfin-upscale.json`.
   - It is never test-started.
   - `ShimBridge.Sync` keeps it across a sync (it merges the existing keys) but never writes it.
6. **`withheldText` in `configPage.html` (nit).**
   - Render `Axis level (reason)` from the server's strings and drop the hard-coded "needs NVIDIA".
   - The new `scripts/test_config_page_withheld.py` extracts the function from the HTML and asserts it reads the reason
     and the axis, and contains no `needs NVIDIA` literal.

Docs updated in the same commits as the behaviour: `ENHANCEMENTS.md` (the device, withheld and fallback paragraphs) and
`docs/architecture/GPU_VENDORS.md` section 5.3, covering the background probe, the TTL and the QSV pin. They also record
that the ONNX levels are withheld until #4.

Not verified, to be said plainly in the PR. No dotnet or jfbin here, so the harness is not compiled or run, and the C#
is only reviewed by reading. No Intel box is reachable. Nothing is served or probed on live ffmpeg.
`-init_hw_device qsv=qs:hw_any,child_device=<renderD128>` on a real Arc node is taken from the documented `-qsv_device`
expansion, not from a run. The cluster's `media/jellyfin` pod is the stock deployment, and this plugin runs on CT114.

```yaml
{
  "goal": "Address the upstream maintainer's review of sanchitd5/jellyfin-plus#3: warm the render-node Vulkan test in the background with expiring false/null results, withhold the ONNX neural levels on Intel with a per-level reason, always pin the QSV child_device to the detected node, resolve DeviceInit once per plan, document the shim render_node, and make the dashboard withheld text use the server's reason and axis. NVIDIA and default paths unchanged.",
  "files": [
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/Transcoding/ShaderLibrary.cs",
    "src/patcher/PatcherHost.cs",
    "src/Configuration/configPage.html",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test_config_page_withheld.py",
    "shim/jellyfin-ffmpeg-upscale",
    "ENHANCEMENTS.md",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 scripts/test_config_page_withheld.py",
    "python3 -m py_compile scripts/test_config_page_withheld.py",
    "python3 -m py_compile shim/jellyfin-ffmpeg-upscale",
    "pre-commit run --files src/patcher/Transcoding/UpscaleEngine.cs src/patcher/Transcoding/ShaderLibrary.cs src/patcher/PatcherHost.cs src/Configuration/configPage.html scripts/gpu-vendor-harness/Program.cs scripts/test_config_page_withheld.py shim/jellyfin-ffmpeg-upscale ENHANCEMENTS.md docs/architecture/GPU_VENDORS.md",
    "scripts/test-gpu-vendor.sh <jfbin> (needs dotnet and the server DLLs; cannot run in this sandbox, so it is run by the orchestrator or the owner)"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "A first Intel session's command build never waits on an ffmpeg test start: DrmVulkanStarts returns at once (last known value, else the untested node) and runs the probe once in the background; WarmDevices at PatcherHost.Apply starts it at plugin start-up.",
    "Only a true render-node result is cached for the life of the process; false and null expire after a retry window and are re-tested in the background while the stale value is served.",
    "On Intel the ONNX neural levels (realesr-*) are absent from Levels.Neural and listed in Levels.Withheld and Status.Withheld with their own reason; a session that asks for one gets neural off and the record and summary say why. The NVIDIA-only wording and the NVIDIA probe, status and chain are byte-identical to before.",
    "On Intel with a QSV encoder the command always carries -init_hw_device qsv=qs:hw_any,child_device=<detected node>, including a mixed AMD+Intel box; with no QSV encoder it carries none.",
    "One DeviceInit per plan: HwaccelArgs(plan) and Describe use plan.Devices, so the record and the command cannot disagree; the probe argument builders take the resolved device.",
    "ENHANCEMENTS.md and GPU_VENDORS.md state that the shim's render_node is hand-set, never test-started and not written by ShimBridge (kept if present), and describe the background probe, the expiry and the QSV pin.",
    "configPage.html withheldText prints axis, level and the server's reason string, with no hard-coded 'needs NVIDIA'; scripts/test_config_page_withheld.py passes.",
    "The PR body maps each review item to a commit and a test, says how NVIDIA/default unchanged was checked, and lists what was not run (harness build, real Intel hardware)."
  ],
  "premises": [
    {"id": "P1", "claim": "DrmVulkanStarts runs RunFfmpegProbe(..., 20000) inside lock(_drmVulkanLock) and caches every result, including false and null, for the process lifetime.", "label": "VERIFIED", "check": "Read src/patcher/Transcoding/UpscaleEngine.cs lines 2216-2243.", "load_bearing": true},
    {"id": "P2", "claim": "Devices() is called independently from HwaccelArgs, Describe, VulkanDenoiseProbeArgs and Av1QsvProbeArgs, so a result that changes between calls can make the record and the command disagree.", "label": "VERIFIED", "check": "Grep of Devices() in src/patcher/Transcoding/UpscaleEngine.cs: lines 570, 2564, 2961 and 3232.", "load_bearing": true},
    {"id": "P3", "claim": "The ONNX levels (realesr-anime-x2/x4, realesr-general-x4) are not NVIDIA-only today, so on Intel they are offered and run on the CPU provider.", "label": "VERIFIED", "check": "ShaderLibrary.IsNvidiaOnlyNeural covers only CUDA-native levels and vsr (ShaderLibrary.cs 953-955); the harness expects Intel Neural to be 'off,realesr-anime-x2' (Program.cs line 898); the CPU wording is at UpscaleEngine.cs line 709.", "load_bearing": true},
    {"id": "P4", "claim": "The current Intel QSV device is left implicit for renderD128-135, so on a mixed AMD+Intel box the encoder may not use the detected node.", "label": "VERIFIED", "check": "UpscaleEngine.cs lines 2193-2198. GPU_VENDORS.md 5.3 asserts the implicit search picks the first Intel node. That is derived from source reading and was never run; the harness only has an AMD-at-128 case for Vulkan.", "load_bearing": true},
    {"id": "P5", "claim": "The shim's render_node can only be set by hand: ShimBridge.Sync merges existing keys and writes only plugin_patch_active, enabled, shader, upscaler, encoder and the numeric limits, and the shim has no probe.", "label": "VERIFIED", "check": "Read src/Transcoding/ShimBridge.cs lines 43-58 and shim/jellyfin-ffmpeg-upscale device_args (lines 440-450); no other writer of render_node (grep).", "load_bearing": false},
    {"id": "P6", "claim": "configPage.html's withheldText hard-codes 'needs NVIDIA' and prints only level names, although the server sends axis -> level -> reason.", "label": "VERIFIED", "check": "Read src/Configuration/configPage.html lines 812-821 and the Withheld shape built in UpscaleEngine.WithheldLevels/AddWithheld.", "load_bearing": false},
    {"id": "P7", "claim": "The test suite for this area is scripts/gpu-vendor-harness (dotnet, needs the server DLLs) and cannot run in this sandbox; no repo CI exists (.github is absent), so only the new Python check and py_compile/pre-commit run here.", "label": "VERIFIED", "check": "Glob scripts/test*; scripts/test-gpu-vendor.sh requires dotnet and a jfbin directory; Glob .github/** returns nothing; the shell allows only python3 scripts/test_<name>.py.", "load_bearing": true},
    {"id": "P8", "claim": "Every changed behaviour sits behind IsIntelPath()/an Intel-detected Devices(), so NVIDIA, none and default paths keep their exact strings; the existing NVIDIA harness blocks (byte-identical probe keys, vk:0, no Withheld key) will keep passing.", "label": "INFERRED", "check": "Needs scripts/test-gpu-vendor.sh <jfbin> to run on a machine with dotnet; by reading, Devices() returns the default DeviceInit before any new code when !IsIntelPath().", "load_bearing": true},
    {"id": "P9", "claim": "Warming from PatcherHost.Apply is safe: UpscaleEngine.Settings is set by Configure first, GpuDetector.Current is cheap, and the probe falls back to /usr/lib/jellyfin-ffmpeg/ffmpeg when _ffmpegPath is not yet noted (set only at the first transcode).", "label": "INFERRED", "check": "RunFfmpegProbe candidate list at UpscaleEngine.cs 2258-2265 and PatcherHost.Apply lines 22-27 were read; whether the stock ffmpeg is at that path on the live server is unverified (no Intel box reachable). A failed warm gives a null result that expires and is retried.", "load_bearing": false},
    {"id": "P10", "claim": "The live plugin host is CT114 on Proxmox, not this cluster; the cluster's media/jellyfin pod is the stock deployment, so nothing here can be verified live.", "label": "INFERRED", "check": "kubectl get pods -A -l app.kubernetes.io/name=jellyfin printed media/jellyfin-55c59fcb97-mmdvw Running; whether it carries the plugin or an Intel GPU was not inspected and is not needed for a code-only change.", "load_bearing": false},
    {"id": "P11", "claim": "docs/agent-lessons.md and docs/automations.md do not exist in this repo, so the PR's lesson line must name a changed file (docs/architecture/GPU_VENDORS.md) or 'none (reason)'.", "label": "VERIFIED", "check": "Glob docs/agent-lessons.md and docs/automations.md returned no files.", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "Single-flight, expiring cache for the render-node test", "checked": "Existing _drmVulkan dictionary plus the Av1QsvUsable pattern in UpscaleEngine.cs; BCL Task.Run and lock. The patcher assembly crosses the ALC boundary with primitives only (invariant 1), so no new package reference is added.", "verdict": "extend", "reason": "A permanent-true entry with a retry window and a Running flag is about 20 lines on the existing dictionary; MemoryCache has no 'true is permanent, others expire' semantic and would add a dependency."},
    {"need": "Per-level withheld reason", "checked": "Existing NvidiaOnly / OfferedOnThisGpu / AddWithheld / Withhold in UpscaleEngine.cs and the Levels.Withheld shape already carrying axis -> level -> reason.", "verdict": "extend", "reason": "The wire shape already carries a reason string; only its source changes from a constant to a function."},
    {"need": "Dashboard text for withheld levels", "checked": "Existing withheldText in configPage.html (plain JS, no library) and gpuText beside it.", "verdict": "extend", "reason": "Server already sends the reason; the client only has to print it."}
  ]
}
```
