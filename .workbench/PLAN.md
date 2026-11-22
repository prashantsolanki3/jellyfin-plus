# Plan: address review of sanchitd5/jellyfin-plus#4 (neural on OpenVINO, routed by source height)

The base branch is the head of upstream #4, so this PR targets it and touches nothing else. Tests come first for items 1 and 2. Nothing is posted upstream.

1. **`scripts/fetch-ort-header.py`.** `HttpFile.read` will check `resp.status == 206` and otherwise `sys.exit` with a plain message: the server ignored the Range request and said which HTTP status it sent. The first test is a new `scripts/test_fetch_ort_header.py`. It starts a stdlib `http.server` on 127.0.0.1 and loads the hyphenated module through `importlib`. A fake that answers 200 with the whole body must make `read()` exit with that message. A fake that honours Range with 206 must read the right bytes and open a small in-memory zip through `zipfile.ZipFile(HttpFile(url))`. The test turns proxies off so the loopback server is reached.

2. **Stale-binary guard for `device=openvino`.**
   - `vf_ort.c` already had `device=cuda|cpu` before this PR, and only `openvino` is new. So an Intel server whose binary does not list it can safely fall back to `device=cpu`. That path already exists for "provider file missing", and the routing code already refuses to auto-pick a level on `cpu`.
   - In `UpscaleEngine`, add a cached probe, `OrtOpenVinoOffered()`. It runs `<ShaderLibrary.PatchedFfmpegPath()> -hide_banner -h filter=ort`, drains both pipes with a timeout, and looks for the word `openvino` in the captured output. It is modelled on `RunFfmpegProbe`, `Av1QsvUsable` and `HasFilter`.
   - A true answer is cached for the process lifetime. A false or null answer is cached with a TTL (about 60 s) and then re-asked, because the shim picks up a swapped binary without a restart.
   - A request never runs the probe. `Decide` only reads the cache. If nothing is cached, or the entry has expired, it starts one guarded `Task.Run` probe and treats the answer as "not confirmed" meanwhile. That is the same rule `HasFilter` uses: unknown counts as missing, because an unknown option kills the stream.
   - A warm call from `PatcherHost.Configure`, run in a `Task.Run` and only if the Intel path with the provider file applies, means the answer is normally ready before the first session.
   - Test seams, in the style of `Av1QsvProbeSource`: `internal Func<bool?> OrtOpenVinoProbeSource`, `internal Task WarmOrtProbe()` and `internal ResetOrtProbe()`.
   - `NeuralDeviceNow` returns `("cpu","cpu", note)` when the provider file is there but the binary is not confirmed to list openvino. The note says the patched ffmpeg's ort filter has no openvino device, so rebuild with `ORT_EP=openvino`, or that the check is not confirmed yet. Neither case sends `device=openvino`.
   - The harness test uses a stub `ffmpeg` shell script written into `NeuralRig()`, which is the directory above the model directory where `PatchedFfmpegPath` looks. One variant prints an ort help without openvino and one prints it with. The test asserts the node is `:device=cpu` for the first and `:device=openvino` for the second, with the real probe and no seam.
   - Every existing Intel+provider harness case that expects openvino must set `OrtOpenVinoProbeSource = () => true`, and `ResetNeuralHarness` must clear it. NVIDIA and default expectations must stay byte-identical.

3. **Resolve once per `Decide`.**
   - `Decide` already computes `onIntel` once. Resolve the neural device once there too.
   - Split `NeuralDeviceNow()` into `NeuralDeviceFor(GpuCapability)` plus a thin wrapper. The capability comes from one `GpuDetector.Current` call, which also stats the provider file.
   - Pass `onIntel` and the device into `RouteNeuralBySourceHeight` and reuse the same device at the later `NeuralFilter` call. The two extra `IsIntelPath` and `NeuralDeviceNow` calls go away.
   - `AutoNeuralLevel` already runs at most once per plan, after the cheap exits. I read "resolve once" as once per `Decide` and will not add a cross-plan memo, which would make the harness weight-file cases order dependent. I will call this out in the PR.

4. **Nits.**
   - In `build-ffmpeg.sh` (line 899 here, not the 371 the review gives), capture `ort_help="$(... -h filter=ort 2>/dev/null || true)"` and then `grep -q openvino <<<"$ort_help"`, so `grep -q` cannot SIGPIPE ffmpeg under pipefail.
   - Next to the wheel pin (lines 215-226), add a comment that the soname `libopenvino.so.2541` (staged in the lib loop around line 527), the wheel URL and the sha256 must change together.
   - Config text (`configPage.html` `NeuralMaxSourceHeight`, plus the `PluginConfiguration.cs` summary) says two things. The setting applies on every GPU vendor, NVIDIA included. When the engine picks the level itself, Recent sessions shows requested `off` yet a network ran, and `NeuralRouting` says why. No behaviour change: `NeuralRequested` stays `off` on auto-pick, as `docs/architecture/GPU_VENDORS.md` and the existing harness assert.
   - Docs: the `GPU_VENDORS.md` device table gets the new "binary does not list openvino" row, and `NEURAL.md` and the `ENHANCEMENTS.md` ort-device bullet get a matching sentence. The `test-gpu-vendor.sh` header comment gets one line.

5. **Not verifiable here.**
   - The workbench has no dotnet, so the C# harness (`scripts/test-gpu-vendor.sh <jfbin>`) and a build cannot run. I will say so in the PR and review the C# by reading it.
   - No Jellyfin Plus host, patched ffmpeg or Intel GPU is reachable from the cluster. `kubectl get pods -A` shows only a stock `jellyfin` pod and a `jellyfin-plus-test` pod in CrashLoopBackOff, which I will not touch. The probe's behaviour against a real binary is therefore unproven, and only the stub-binary test covers it.
   - Repo checks that can run: `python3 scripts/test_fetch_ort_header.py` and `python3 -m py_compile scripts/fetch-ort-header.py scripts/test_fetch_ort_header.py`. There is no `.pre-commit-config.yaml` and no `scripts/sync_*.py`, so those two commands do not apply.

PR body notes: the task gave no beads id, so use `no-bead: <reason>` unless one turns up. `Refs` points at upstream #4. `docs/agent-lessons.md` does not exist in this repo, so the `lesson:` line is either a path in the diff or `lesson: none (<reason>)`.

```yaml
{
  "goal": "Address upstream #4 review: require HTTP 206 in fetch-ort-header.py, guard device=openvino with a cached off-request-path `ffmpeg -h filter=ort` probe (fall back to device=cpu), and fix the build-ffmpeg.sh SIGPIPE, wheel-pin comment, config text and per-Decide resolution nits, tests first, NVIDIA/default paths unchanged.",
  "files": [
    "scripts/fetch-ort-header.py",
    "scripts/test_fetch_ort_header.py",
    "scripts/build-ffmpeg.sh",
    "src/patcher/Transcoding/UpscaleEngine.cs",
    "src/patcher/PatcherHost.cs",
    "scripts/gpu-vendor-harness/Program.cs",
    "scripts/test-gpu-vendor.sh",
    "src/Configuration/configPage.html",
    "src/Configuration/PluginConfiguration.cs",
    "docs/architecture/GPU_VENDORS.md",
    "docs/filters/NEURAL.md",
    "ENHANCEMENTS.md"
  ],
  "test_commands": [
    "python3 scripts/test_fetch_ort_header.py",
    "python3 -m py_compile scripts/fetch-ort-header.py",
    "python3 -m py_compile scripts/test_fetch_ort_header.py"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "A fake server that ignores Range and returns 200 makes HttpFile.read exit with a clear message naming the status and the missing 206; a Range-honouring fake still reads correctly (scripts/test_fetch_ort_header.py passes).",
    "NeuralDeviceNow/Decide never emits device=openvino unless the patched ffmpeg's `-h filter=ort` output lists openvino; a stub ffmpeg without it yields device=cpu with a note, a stub with it yields device=openvino (harness cases written; run needs dotnet and jfbin, not run here).",
    "The probe never runs on the request path: Decide only reads the cache and starts at most one guarded background probe; true is cached for the process, false/null expire after a TTL; a startup warm exists.",
    "On every server that is not a detected Intel one, and on Intel without the provider file, the ort node and Plan fields are byte-identical to before (existing harness expectations unchanged apart from setting the probe seam on Intel+provider cases).",
    "build-ffmpeg.sh captures `-h filter=ort` output before grepping; a comment next to the wheel pin says soname libopenvino.so.2541, URL and sha256 change together.",
    "Config text for NeuralMaxSourceHeight says it applies on every vendor including NVIDIA and that an auto-picked level shows requested off with NeuralRouting explaining; NeuralRequested behaviour is unchanged.",
    "GpuDetector.Current is called once per Decide for the neural device/vendor decision (onIntel and device resolved once and passed to RouteNeuralBySourceHeight and the NeuralFilter call).",
    "ENHANCEMENTS.md, GPU_VENDORS.md and NEURAL.md describe the openvino-listed check in the same change; nothing posted upstream, no other branch touched."
  ],
  "reuse_check": [
    {
      "need": "Ranged HTTP reads and a loopback fake server for the 200-vs-206 test",
      "checked": "Python stdlib urllib.request and http.server (already what fetch-ort-header.py uses); no new dependency",
      "verdict": "extend",
      "reason": "A status check inside the existing HttpFile.read is the whole fix"
    },
    {
      "need": "Cached ffmpeg capability probe with a test seam",
      "checked": "UpscaleEngine.RunFfmpegProbe, Av1QsvUsable/Av1QsvProbeSource, HasFilter/AvailableFilters and VulkanDenoiseRunsOnPatchedBinary in src/patcher/Transcoding/UpscaleEngine.cs",
      "verdict": "extend",
      "reason": "Follow the same process-spawn, drain-both-pipes, timeout and probe-source pattern, adding a TTL for false/null and a background start, which the existing probes lack"
    },
    {
      "need": "Fallback device when openvino is not listed",
      "checked": "Existing NeuralDeviceNow cpu branch and OpenVinoMissingNote, which RouteNeuralBySourceHeight already treats as 'do not auto-pick'",
      "verdict": "use",
      "reason": "device=cpu pre-exists in vf_ort.c (git diff 3325599 7855e15 -- ffmpeg/vf_ort.c shows only openvino is new), so the cpu path is safe on a stale binary"
    },
    {
      "need": "Resolve GPU capability once per Decide",
      "checked": "Decide's existing `bool onIntel = IsIntelPath()` and GpuDetector.Current (inventory cached, provider file stat per call)",
      "verdict": "extend",
      "reason": "Pass the already-resolved values into RouteNeuralBySourceHeight instead of calling Current again"
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "HttpFile.read in scripts/fetch-ort-header.py never inspects the HTTP status of its Range request, so a 200 full-body reply would desync the zip read.",
      "label": "VERIFIED",
      "check": "Read scripts/fetch-ort-header.py lines 46-55: urlopen(req).read() with no status check",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "NeuralDeviceNow returns openvino purely from cap.OpenVinoAvailable (provider .so exists) with no check of the ffmpeg binary's ort options.",
      "label": "VERIFIED",
      "check": "Read UpscaleEngine.cs 2114-2132 and GpuVendor.cs 451/467-481 (OpenVinoProvider is only env.FileExists)",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "device=cpu and device=cuda exist in vf_ort.c on every binary built before this PR; only device=openvino is new, so cpu is a safe fallback on a stale binary.",
      "label": "VERIFIED",
      "check": "git diff 3325599 7855e15 -- ffmpeg/vf_ort.c: the option table's cuda/cpu constants are pre-existing lines, openvino and the range extension are the additions",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "The ort filter lives only in the patched ffmpeg, whose path is ShaderLibrary.PatchedFfmpegPath() (directory above NeuralModelDirectory + /ffmpeg), so that is the binary to probe, and a harness stub can be placed there.",
      "label": "VERIFIED",
      "check": "Read ShaderLibrary.cs 1630-1647 and Program.cs NeuralSettings/NeuralRig (NeuralModelDirectory = NeuralRig()/models or /empty)",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "RouteNeuralBySourceHeight already declines to auto-pick when the device name is cpu, so falling back to cpu on an unconfirmed binary needs no routing change.",
      "label": "VERIFIED",
      "check": "Read UpscaleEngine.cs 2193-2199",
      "load_bearing": false
    },
    {
      "id": "P6",
      "claim": "Per Decide, the Intel/device decision hits GpuDetector.Current several times (IsIntelPath at 1419, IsIntelPath at 2176, NeuralDeviceNow at 2193 and 1549), and onIntel is already available in Decide for passing down.",
      "label": "VERIFIED",
      "check": "Grep of GpuDetector.Current/IsIntelPath/NeuralDeviceNow call sites in UpscaleEngine.cs and reading 1419 and 1473",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "The `-h filter=ort | grep -q openvino` line is at build-ffmpeg.sh:899 under `set -euo pipefail` (line 89), not ~371; the wheel pin and soname are at 215-226 and 527.",
      "label": "VERIFIED",
      "check": "Read build-ffmpeg.sh 208-232 and 890-907; Grep for pipefail and filter=ort",
      "load_bearing": false
    },
    {
      "id": "P8",
      "claim": "PatcherHost.Configure runs early enough, and often enough, to host an idempotent background warm of the probe.",
      "label": "INFERRED",
      "check": "Read PatcherHost.Configure (settings JSON applied, called by the plugin) and the plugin's call site in src/Hosting/PatcherLoader.cs; the lazy kick inside NeuralDeviceFor is the fallback if the warm is late",
      "load_bearing": false
    },
    {
      "id": "P9",
      "claim": "A real patched ffmpeg's `-h filter=ort` output lists `openvino` for an ORT_EP=openvino build and does not for a stale one, exits cleanly, and loads without a GPU.",
      "label": "INFERRED",
      "check": "Needs the real binary on the Jellyfin host (CT114 or an Intel box), which is not reachable from this cluster; `kubectl get pods -A` shows no Jellyfin Plus host. The stub-binary harness test and the build script's own existing `-h filter=ort | grep openvino` check are the evidence available.",
      "load_bearing": false
    },
    {
      "id": "P10",
      "claim": "The C# harness cannot be run in the workbench (no dotnet, no jfbin), so the item-2 and item-3 tests are written but unrun here.",
      "label": "VERIFIED",
      "check": "The allowed command list has no dotnet or test-gpu-vendor.sh; only python3 scripts/test_*.py and py_compile run, and scripts/test_fetch_ort_header.py is the one runnable new test",
      "load_bearing": false
    },
    {
      "id": "P11",
      "claim": "docs/agent-lessons.md, docs/automations.md, .pre-commit-config.yaml and scripts/sync_*.py do not exist in this repo, so the lesson line and the pre-commit and sync checks cannot use them.",
      "label": "VERIFIED",
      "check": "Glob for each returned no files",
      "load_bearing": false
    }
  ]
}
```
