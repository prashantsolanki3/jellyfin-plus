# Plan: address the upstream review of #9 (bench-intel.sh and the Intel image)

Scope is the review's four blockers and the nits, nothing else. Item 5 (the webinject `dpkg-query`
fallback) is already fixed on fork `main` by #11; the merge brings it, and the PR says so.

**Step 0, merge.** `git merge main` (merge commit, no rebase), then drop
`.github/workflows/build-arc-image.yml` and `.workbench` from the result. `git diff HEAD main` shows main is 9
files ahead: INSTALL.md, docker/intel/Dockerfile, GPU_VENDORS.md, install-shaders.sh, jellyfin-plus-webinject,
test_bench_intel.py (+160 lines: apt, local-libs and webinject tests), test_install_shaders.py, make-rcas.sh.
**This session's shell allows only `git status|log|diff`, so the merge cannot be run from here.** The trusted
process, or an implement stage with merge rights, has to do it first. The edits below assume the merged
`test_bench_intel.py` (it adds `test_webinject_without_dpkg_package`, which is the item 5 proof). If the merge is
not done, stop and report; do not hand-copy main's files.

**Step 1, tests first** (`scripts/test_bench_intel.py`, after the merge). Add a hermetic part that runs the real
`bench-intel.sh` against a fake `curl` (a small Python stub that logs argv and stdin and serves JSON by URL path), a
stub `date`, a stub ffmpeg and the real `jq`. Run it against the unfixed script and confirm every new check fails,
then fix.

| Item | Test that fails first |
|---|---|
| 1 octal | stub `date` prints 0830, 0900, 0158: a gated run must reach the gate decision (0830 and 0900 pass, 0158 is refused with `gate:`, rc 2). Before the fix: rc 1 "value too great for base". 1200 passes, 0130 and 1700 are refused. PSI is read from a new `BENCH_PSI_FILE` (default `/proc/pressure/memory`) so the test does not depend on the host. |
| 2 unguarded api | fake curl exits 22 for everything except `/System/Info/Public`. Rows 3, 4, 5, 6, 10, 12 must each print `result: row N FAIL`, the run must print `summary:` and exit 1. Before the fix: abort with no summary. |
| 3 row 6 | the stream URL must carry `neural=realesr-anime-x2` and never `fast`; `BENCH_NEURAL_LEVEL=fast` is refused. PASS needs the probe `Levels.NeuralMaxSourceHeight == 480` (set through the plugin configuration API when absent, and restored afterwards: the fake curl logs the POST bodies), `SourceHeight == 720`, `NeuralApplied == false` and `NeuralRouting` containing `NOT run: the source is 720p, taller than NeuralMaxSourceHeight 480`. A non-empty `NeuralRouting` of any other text (e.g. "was chosen") is FAIL. |
| 4 row 5 | the fake curl logs request order: the `master.m3u8` request with `neural=realesr-anime-x2&PlaySessionId=<sid>` must come before `Session/<sid>`, and the sid must be the stream's. `NeuralDevice` cpu is FAIL, openvino is MANUAL (fps are for a human), a failed stream is FAIL. |
| nit no-gates | `--no-gates` without `BENCH_CONFIRM_NO_GATES=1` exits 2; with it the run proceeds; `--help` names both. The existing stub-run test sets the variable. |
| nit run_fps | a stub ffmpeg that exits 1: row 11 (`BENCH_PLEX_TRANSCODING=1`) and row 2 must record FAIL with `ffmpeg failed`. Before the fix: `fps=0 under contention`, because `return 1` inside the `for ... done \| median` subshell is lost and the function ends on `rm -f`. |
| nit record | a server-supplied `NeuralRouting` containing `"`, `\` and a newline flows into a FAIL detail; every line of results.jsonl must `json.loads` and round-trip the detail. |
| nit row 1 scope | `BENCH_SHIM_LOG` seeded with a `nvenc` line before the run: row 1 PASS. The fake curl appends a `nvenc` line to the log when the probe is requested (that is "during this run"): row 1 FAIL `nvenc/cuda-in-log`. |
| nit token | the token is absent from every logged curl argv and present on the logged stdin (`--config -`). |
| nit shared meta | neither `proxmox-build.sh` nor `stage-plugin.sh` contains the literal GUID or `12.3.0.0`; both source `scripts/plugin-meta.sh`; its GUID equals `Plugin.cs` `Id` and its ABI equals `manifest.json`; the JSON it prints parses with those keys. |
| nit dockerignore | `Dockerfile.dockerignore` carries `**/.env*`, `**/*.pem`, `**/*.key`; the existing "keeps what the build needs" check still passes. |

Mutants for the main fixes (octal, run_fps, token on argv) go into `test_mutants` so a revert is caught.

**Step 2, fixes** (`scripts/bench-intel.sh`, smallest diff):
1. `hm=$((10#$(TZ=Australia/Melbourne date +%H%M)))` and decimal comparisons (100, 700, 1630, 1830).
2. `api` keeps `-f` but every caller handles failure: `item_id` returns empty, `api_stream` returns 1 and sets
   `STREAM_ERR` (and counts segments, 0 is a failure), and `shader_row`, `row_5`, `row_6`, `row_10`, `row_12` record
   FAIL and continue.
3. Row 6 as in the table. The level must be one of `off|realesr-anime-x2|realesr-anime-x4|realesr-general-x4`.
4. Row 5 starts a 360p stream with `neural=realesr-anime-x2` first, then reads that stream's `STREAM_SID`.
5. `--no-gates` documented in the header and `usage`, plus the `BENCH_CONFIRM_NO_GATES=1` requirement.
6. `run_fps` collects values in the main shell, returns 1 on a failed run, and ends with `median`.
7. `record` builds the JSONL with `jq -nc --argjson row --arg status --arg detail`.
8. Row 1 (and row 12's libplacebo grep) read only the shim log bytes after the offset taken at start.
9. `api` pipes `header = "X-Emby-Token: ..."` into `curl --config -`; extra curl args are passed through, and the
   plugin configuration POST sends its body from a temp file, so stdin stays free.
10. `scripts/plugin-meta.sh` (new, the `ffmpeg-vendor.sh` pattern) holds the GUID, name, target ABI, overview and a
    `plugin_first_meta <version>` that prints the first-install meta.json. `stage-plugin.sh` and `proxmox-build.sh`
    source it (proxmox-build.sh sources it late, after the migrate step, so `test_build_ffmpeg_vendor.py`'s stub repo,
    which stops at the migrate stub, is unaffected). `migrate-to-jellyfin-plus.sh` keeps its own copy: out of scope,
    named in the PR.
11. `Dockerfile.dockerignore`: add the three patterns.

**Docs**: GPU_VENDORS.md 5.8 (rows 5 and 6 as run, the `--no-gates` confirmation, `BENCH_PSI_FILE`, the test list)
and a `context-map.md` row for `plugin-meta.sh`. No `docs/agent-lessons.md` or `docs/automations.md` exists in this
checkout, so the PR's lesson line is `lesson: none (...)` with a reason, or names GPU_VENDORS.md.

**Not verifiable here**: nothing runs on an Arc card; the plugin configuration API, `NOT run` wording and session
record fields are checked against source and the fake API only, never a live Jellyfin. The PR says so. Upstream #9 is
not commented on.

```yaml
{
  "goal": "Fix the upstream review of #9: bench-intel.sh octal gate and unguarded api calls, rows 5 and 6, the nits (no-gates confirmation, run_fps, JSONL via jq, row 1 log scope, token off the command line, one shared meta.json source, dockerignore), with tests that fail first; merge fork main first (item 5 already fixed there).",
  "files": [
    "scripts/bench-intel.sh",
    "scripts/test_bench_intel.py",
    "scripts/plugin-meta.sh",
    "scripts/proxmox-build.sh",
    "docker/intel/stage-plugin.sh",
    "docker/intel/Dockerfile.dockerignore",
    "docs/architecture/GPU_VENDORS.md",
    "context-map.md",
    "INSTALL.md",
    "docker/intel/Dockerfile",
    "scripts/install-shaders.sh",
    "scripts/jellyfin-plus-webinject",
    "scripts/test_install_shaders.py",
    "shaders/make-rcas.sh"
  ],
  "test_commands": [
    "python3 scripts/test_bench_intel.py",
    "python3 scripts/test_build_ffmpeg_vendor.py",
    "python3 scripts/test_install_shaders.py",
    "python3 -m py_compile scripts/test_bench_intel.py",
    "pre-commit run --files scripts/bench-intel.sh scripts/test_bench_intel.py scripts/plugin-meta.sh scripts/proxmox-build.sh docker/intel/stage-plugin.sh docker/intel/Dockerfile.dockerignore docs/architecture/GPU_VENDORS.md context-map.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "fork main is merged into the branch (merge commit, no rebase) without .github/workflows/build-arc-image.yml and .workbench",
    "check_gates reads HHMM as decimal: 08:30, 09:00 and 01:58 Australia/Melbourne no longer abort with a base error; the windows 01:00-07:00 and 16:30-18:30 still refuse with rc 2",
    "a failing api call (404) in api_stream, shader_row, row_5, row_6, row_10 or row_12 records FAIL for that row and the run still prints summary: and exits 1",
    "row 6 sends a real neural level (realesr-anime-x2), sets or checks NeuralMaxSourceHeight=480 and restores it, and passes only when the record says the network was NOT run for the 720p source because of NeuralMaxSourceHeight 480",
    "row 5 starts a stream before reading Session/<its sid>, and fails when the device is not openvino",
    "item 5 (webinject dpkg-query fallback) is stated as already fixed by fork #11 and proven by the merged test_webinject_without_dpkg_package",
    "--no-gates is in the usage text and needs BENCH_CONFIRM_NO_GATES=1",
    "run_fps returns non-zero on a failed ffmpeg run and the FAIL branch is reached",
    "results.jsonl lines are valid JSON for details containing quotes, backslashes and newlines",
    "row 1 judges only shim-log lines written during this run",
    "the admin token never appears on a curl command line",
    "scripts/plugin-meta.sh is the only place the plugin GUID and target ABI are written for proxmox-build.sh and stage-plugin.sh",
    "Dockerfile.dockerignore excludes .env*, *.pem and *.key",
    "each new check fails against the unfixed script, and python3 scripts/test_bench_intel.py, test_build_ffmpeg_vendor.py and test_install_shaders.py pass after",
    "nothing posted on upstream #9; no other PR branch touched; the PR says what was not verified (no Arc card, no live Jellyfin)"
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "The merge of fork main cannot be performed from this session's shell",
      "label": "VERIFIED",
      "check": "the Bash hook refused `git branch -a` with 'only status|log|diff are allowed'; `git log HEAD..main` and `git diff HEAD main` work, main is 855408d, 9 files ahead",
      "load_bearing": true
    },
    {
      "id": "P2",
      "claim": "The octal bug is real: bash reads 0830, 0900 and 0158 as invalid octal inside [[ -ge ]]",
      "label": "VERIFIED",
      "check": "scripts/bench-intel.sh:239-240 uses `[[ $hm -ge 0100 && ... ]]` on `date +%H%M`; the failing test (stub date) will prove it at implement time",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "run_fps's FAIL branch is dead: `return 1` is inside the `for ... done | median` subshell and the function ends on `rm -f`",
      "label": "VERIFIED",
      "check": "scripts/bench-intel.sh:288-299 read in full; the stub-ffmpeg test will prove it",
      "load_bearing": false
    },
    {
      "id": "P4",
      "claim": "A 720p source with NeuralMaxSourceHeight=480 and an ONNX level asked for yields NeuralRouting 'neural <level> NOT run: the source is 720p, taller than NeuralMaxSourceHeight 480, so the shader path ran'; with no level asked it yields null; 'fast' is not a level",
      "label": "VERIFIED",
      "check": "src/patcher/Transcoding/UpscaleEngine.cs:2428-2446 (RouteNeuralBySourceHeight); ShaderLibrary.cs:593 _neuralMenu = off, realesr-anime-x2, realesr-anime-x4, realesr-general-x4; gpu-vendor-harness Program.cs:1583-1591",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "The probe (Session/<unknown id>) returns Levels with NeuralMaxSourceHeight only when the setting is above 0, and the session record carries NeuralDevice, NeuralApplied, NeuralRouting and SourceHeight",
      "label": "VERIFIED",
      "check": "src/patcher/PatcherHost.cs:222-225 and 285-358",
      "load_bearing": true
    },
    {
      "id": "P6",
      "claim": "Stock Jellyfin GET/POST /Plugins/{guid}/Configuration with an admin token applies a changed NeuralMaxSourceHeight live (Plugin.UpdateConfiguration reconfigures the patcher)",
      "label": "INFERRED",
      "check": "Plugin.cs:101-110 calls PatcherLoader.Configure; the stock endpoint itself is unverified here. Fallback if it fails: row 6 only checks the probe value and FAILs with the instruction to set it",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "docs/agent-lessons.md and docs/automations.md do not exist in this checkout",
      "label": "VERIFIED",
      "check": "Glob docs/{agent-lessons,automations}.md returned no files",
      "load_bearing": false
    },
    {
      "id": "P8",
      "claim": "proxmox-build.sh can source a new scripts/plugin-meta.sh after the migrate step without breaking test_build_ffmpeg_vendor.py, whose stub repo stops at a migrate stub (exit 42) and carries only ffmpeg-vendor.sh",
      "label": "VERIFIED",
      "check": "test_build_ffmpeg_vendor.py:271-294 and proxmox-build.sh:235-296; re-run the test after the change",
      "load_bearing": false
    },
    {
      "id": "P9",
      "claim": "The Dockerfile's webinject CONF_MD5 problem is fixed on main and the merge brings it",
      "label": "VERIFIED",
      "check": "`git diff HEAD main -- scripts/jellyfin-plus-webinject` shows `|| true` on the dpkg-query pipeline; this branch's copy (line 27) lacks it",
      "load_bearing": false
    },
    {
      "id": "P10",
      "claim": "jq and python3 are available to the test run, so the fake-API tests can use the real jq",
      "label": "INFERRED",
      "check": "run `python3 scripts/test_bench_intel.py` once; if jq is missing the new jq-dependent part must print a skip line instead of failing, and the plan falls back to a minimal python fake jq",
      "load_bearing": false
    }
  ],
  "reuse_check": [
    {
      "need": "keep the token off the curl command line",
      "checked": "curl --config - (stdin config), curl 7.x+ man page; the image's curl",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "valid JSONL for arbitrary detail text",
      "checked": "jq -n --arg (jq 1.6, already a hard dependency of bench-intel.sh via `need jq`)",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "one shared source for the plugin GUID and target ABI",
      "checked": "scripts/ffmpeg-vendor.sh (the repo's own pattern for a fact two scripts must agree on); assets/plugin-meta.json is merged over existing installs so putting guid/targetAbi there would change proxmox-build.sh behaviour on live servers",
      "verdict": "extend",
      "reason": ""
    },
    {
      "need": "a fake Jellyfin API for the hermetic tests",
      "checked": "the existing stub curl in test_bench_intel.py (exit 22 only); Python stdlib",
      "verdict": "extend",
      "reason": ""
    }
  ]
}
```
