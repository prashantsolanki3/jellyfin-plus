# Implementation (round 1, test stage)

Changes on top of #23:
- `ShaderLibrary.PatchedFfmpegPath(UpscaleSettings cfg = null)` resolves beside the configured `NeuralModelDirectory`; `UpscaleEngine.OrtOpenVinoState` passes `Settings`.
- `UpscaleEngine.Decide` computes the ort device (and so starts the `-h filter=ort` probe) only when a neural level was requested or `NeuralMaxSourceHeight > 0` with neural allowed.
- Harness (`scripts/gpu-vendor-harness/Program.cs`): counting probe source (0 / 1 / 2 calls), slow stub (`sleep 5`) proving the cold-cache plan returns at once on `device=cpu` and flips to `openvino` after the probe lands.
- `docs/architecture/GPU_VENDORS.md` states both.
- `ShaderLibrary.AutoNeuralLevel` caches its pick per model directory for 30 s (TTL, not mtime: mtime validation needs a stat per plan). Seams: `WeightFileExistsSource`, `AutoNeuralClockSource`, `ResetAutoNeuralCache()`. Harness `AutoNeuralCache()` counts stats through the seam: 51 lookups stat once; a weight installed inside the TTL is not seen until it expires; an auto-picked `Decide` stats exactly as many files as one naming the level (20 repeats).

Test results:
- Full harness: NOT RUN. dotnet exists here but `$(JellyfinBin)` (MediaBrowser.*.dll) does not, so `dotnet run --project scripts/gpu-vendor-harness` fails with CS0246 before any test runs. No count exists for the new `AutoNeuralCache` checks; they are unexecuted.
- Static read of the diff (test stage, this session): the cache hit returns before any stat; `NeuralWeightsPresent` routes through `WeightFileExistsSource`; harness rigs use GUID directories so the 30 s cache cannot leak between sections; without the cache the 51-lookup check would fail. No defect found. The shell here has no `dotnet`, so the harness count (previous head: 1099/0) is not re-measured for this change.
- `pre-commit run`: fails with "`.pre-commit-config.yaml` is not a file" (no config on this branch).
- pre-commit / sync script: not available on this branch. No live-cluster check applies.

Verdict: BLOCKED on running the harness.
