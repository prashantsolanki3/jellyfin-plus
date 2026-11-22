# Implementation (round 1, test stage)

Changes on top of #23:
- `ShaderLibrary.PatchedFfmpegPath(UpscaleSettings cfg = null)` resolves beside the configured `NeuralModelDirectory`; `UpscaleEngine.OrtOpenVinoState` passes `Settings`.
- `UpscaleEngine.Decide` computes the ort device (and so starts the `-h filter=ort` probe) only when a neural level was requested or `NeuralMaxSourceHeight > 0` with neural allowed.
- Harness (`scripts/gpu-vendor-harness/Program.cs`): counting probe source (0 / 1 / 2 calls), slow stub (`sleep 5`) proving the cold-cache plan returns at once on `device=cpu` and flips to `openvino` after the probe lands.
- `docs/architecture/GPU_VENDORS.md` states both.
- `AutoNeuralLevel` stat caching: declined (once per plan, routing branch only; mtime validation needs a stat).

Test results:
- Full harness: NOT RUN. No dotnet, Docker or Jellyfin DLLs in this session, so no pass/fail count exists for this head. The verifier's scratch run of the `PatchedFfmpegPath(Settings)` change alone gave 1092 passed / 0 failed.
- Static read of the diff: consistent, no defect found (see VERDICT.json).
- pre-commit / sync script: not available on this branch. No live-cluster check applies.

Verdict: BLOCKED on running the harness.
