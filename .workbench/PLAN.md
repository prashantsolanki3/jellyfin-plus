# Plan: rev-sr6bf — drop fsr2-nocuda from the Intel image, bench row 8 becomes SKIP

The plugin offers fsr2 on Intel only when the build left the `fsr2-nocuda` marker (GpuVendor.cs `NoCudaGameMarkerFile` -> `NoCudaGameAvailable` -> `UpscaleEngine.NoCudaGameNow()` -> `NvidiaOnly("Game","fsr2")`) and a one-frame probe passed. So the smallest correct change is image-side: `docker/intel/Dockerfile` stops building fsr2 (no `WITH_FSR2=1`, no `FSR2_SDK`/`FSR2_LIB`, the out-of-tree libffx_fsr2_vk build stage goes, the acceptance `fsr2` filter and marker checks go). `scripts/build-ffmpeg.sh` already deletes a stale marker on every non-fsr2 build and the plugin already withholds fsr2 with no marker, so no C# change is needed. The C# harness (`scripts/test-gpu-vendor.sh`) cannot be run here, so I avoid touching C#. The `ffmpeg/` patches and the stock libplacebo/FSRCNNX path stay untouched. The mestimate experiment is out of scope.

`scripts/bench-intel.sh`: row 8 records `SKIP` with the reason (zero motion vectors without CUDA, docs/intel-patched-ffmpeg-libplacebo.md; the image ships without fsr2). Its plan lines, its row-8-only threshold text and the dead `FAIL "ship the image without fsr2-nocuda"` branch go. Necessary knock-on, kept to small hunks (the bench would otherwise die or FAIL on the new image, and the owner asked for the diff to stay near row 8, so I will merge main before finishing and keep these hunks minimal to ease conflicts with the other agents): the required-filter loops (`plan: required filters`, line ~1076) stop requiring `fsr2`; row 1 stops requiring "fsr2 offered" and the marker and instead fails if fsr2 IS offered; row 7 SKIPs when the patched binary has no fsr2 filter. `scripts/test_bench_intel.py` follows: the row 8 plan goldens, the Dockerfile needle list (drop `WITH_FSR2=1`, `FSR2_SDK=`, `FSR2_LIB=`, `fsr2-nocuda`), the stub-binary filter list, the row-8 cases (collapse, swap, pos, FFV1, stock-binary mutants, xpsnr-fail rows) are rewritten to a SKIP assertion, and the stub run's skip count stays 8. FSR2 commit pin checks: the pin and `ffmpeg/gen_perm.py` COPY go with the stage, so those checks go too (decide at edit time whether to keep the `ARG FSR2_COMMIT` for the next experiment; planned: remove it, the doc keeps the commit id).

Docs: `docs/architecture/GPU_VENDORS.md` section 5.8 (build line, acceptance, row 1/7/8 table) and a one-line status in `docs/intel-patched-ffmpeg-libplacebo.md`, plus `context-map.md` if it names the stage.

**Blocked on owner (cannot be done by this session):** `.github/workflows/build-arc-image.yml` acceptance requires the `fsr2` filter and the `fsr2-nocuda` marker (lines 101, 109-113, 165). After this PR the Arc image CI would fail until the owner removes those checks. I will say so in the PR body. Not editing the workflow.

Not verified: no Docker build, no Arc run, no C# build/harness (no dotnet access); the live `jellyfin-plus-test` pod (media ns, Running) still runs the previous image and is not touched.

```yaml
{
  "goal": "Remove fsr2 (fsr2-nocuda, zero motion vectors) from the Intel image so the plugin/shim never offers fsr2 on Intel, and make bench-intel row 8 report SKIP with that reason instead of FAIL.",
  "files": [
    "docker/intel/Dockerfile",
    "scripts/bench-intel.sh",
    "scripts/test_bench_intel.py",
    "docs/architecture/GPU_VENDORS.md",
    "docs/intel-patched-ffmpeg-libplacebo.md",
    "context-map.md"
  ],
  "test_commands": [
    "python3 -I scripts/test_bench_intel.py",
    "python3 scripts/test_build_ffmpeg_vendor.py",
    "python3 scripts/sync_mcp_filesystem_docs.py --check"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "The Intel image no longer builds fsr2 or leaves the fsr2-nocuda marker, so the plugin withholds fsr2 on Intel (never offered).",
    "bench-intel.sh row 8 records SKIP (not FAIL) with the zero-motion-vector reason; a bench run on an fsr2-less binary does not die or FAIL rows 1/7 because of it.",
    "scripts/test_bench_intel.py passes with row 8 asserted as SKIP and the Dockerfile checks updated.",
    "The stock-ffmpeg libplacebo/FSRCNNX path and the ffmpeg/ patches are unchanged; production Jellyfin untouched; mestimate experiment not run."
  ],
  "reuse_check": {
    "need": "n/a",
    "checked": "removal of an existing build option; no new library or feature",
    "verdict": "use",
    "reason": "n/a"
  },
  "premises": [
    {"id": "P1", "claim": "The plugin offers fsr2 on Intel only when the build left the fsr2-nocuda marker (plus a passing probe), so dropping WITH_FSR2=1 from the image makes fsr2 unoffered with no C# change.", "label": "VERIFIED", "check": "Read GpuVendor.cs:541/555 and UpscaleEngine.cs NvidiaOnly/NoCudaGameBuilt/NoCudaGameNow (2957-3012); ShaderLibrary.IsCudaFreeGame", "load_bearing": true},
    {"id": "P2", "claim": "build-ffmpeg.sh already removes a stale marker when WITH_FSR2 is not set.", "label": "VERIFIED", "check": "scripts/build-ffmpeg.sh:1071-1077 (rm -f FSR2_NOCUDA_MARKER before the conditional touch) and GPU_VENDORS.md:726", "load_bearing": false},
    {"id": "P3", "claim": "Dropping fsr2 from the image breaks bench-intel.sh beyond row 8 (required-filter die at ~1076, row 1 'fsr2-not-offered/marker-missing', row 7 fsr2 chain), so those need small tolerant hunks.", "label": "VERIFIED", "check": "Read bench-intel.sh lines 304, 690-691, 855-861, 1076", "load_bearing": true},
    {"id": "P4", "claim": "The Arc image CI workflow requires the fsr2 filter and marker and would fail after this change; agents cannot edit .github/workflows, so the owner must.", "label": "VERIFIED", "check": "Read .github/workflows/build-arc-image.yml:101-113,165", "load_bearing": false},
    {"id": "P5", "claim": "test_bench_intel.py asserts row 8 PASS/FAIL, the plan goldens and Dockerfile needles WITH_FSR2=1/FSR2_SDK=/FSR2_LIB=/fsr2-nocuda, so it must be rewritten alongside.", "label": "VERIFIED", "check": "Grep of scripts/test_bench_intel.py (lines 136,160-163,1086-1087,1440,1551-1574,1765,2012)", "load_bearing": true},
    {"id": "P6", "claim": "The C# gpu-vendor harness and the Docker build cannot be run in this session, so correctness there is by reading only.", "label": "INFERRED", "check": "Shell allowlist permits only python3 scripts/test_*.py, no dotnet/docker; confirm by the final test run list", "load_bearing": false},
    {"id": "P7", "claim": "The live jellyfin-plus-test pod is running and unaffected by this PR (it needs a new image + revenant values bump later).", "label": "VERIFIED", "check": "kubectl get pods -A -l app.kubernetes.io/name=jellyfin-plus-test -> media/jellyfin-plus-test-f5cd4cb99-8d8x2 1/1 Running", "load_bearing": false}
  ]
}
```
