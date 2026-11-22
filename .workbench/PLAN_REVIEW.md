# Plan review: upstream #9 review fixes (round 1)

Premises checked by my own commands and reads in this session.

| id | verdict | what I ran |
|---|---|---|
| P1 | VERIFIED | `git merge --no-commit main` is refused by the hook ("only status\|log\|diff"). `git diff HEAD main --stat` lists 9 files ahead: the workflow file, INSTALL.md, docker/intel/Dockerfile, GPU_VENDORS.md, install-shaders.sh, jellyfin-plus-webinject, test_bench_intel.py, test_install_shaders.py, make-rcas.sh. `git status` is clean, so the merge has not happened. |
| P2 | VERIFIED | bench-intel.sh:239-240 compares `$hm -ge 0100` etc. on `date +%H%M`. In `[[ ]]` arithmetic a leading zero means octal, so 0830, 0900 and 0158 raise "value too great for base" and `set -e` aborts. |
| P3 | VERIFIED | bench-intel.sh:288-299. `echo 0; ...; return 1` sits inside `for ... done \| median`, a pipeline subshell. The function ends on `rm -f`, so it always returns 0 and the caller's FAIL branch is dead. |
| P4 | VERIFIED | UpscaleEngine.cs:2428-2446 produces exactly `neural <level> NOT run: the source is <h>p, taller than NeuralMaxSourceHeight <max>, so the shader path ran` for an ONNX level above the height. ShaderLibrary.cs:593-594 menu is off, realesr-anime-x2, realesr-anime-x4, realesr-general-x4, so `fast` is not a level. |
| P5 | VERIFIED | PatcherHost.cs:222-224 adds `Levels.NeuralMaxSourceHeight` only when it is above 0. The session JSON carries NeuralApplied, NeuralDevice, NeuralRouting and SourceHeight (lines 346-355). The unknown-id probe has `Known=false` and null routing/device. |
| P6 | INFERRED, non-load-bearing | The stock plugin configuration endpoint can't be exercised here. The plan has a fallback. |
| P7 | VERIFIED | Glob `docs/{agent-lessons,automations}.md` returned no files. |
| P8 | VERIFIED (by reading) | proxmox-build.sh migrate step is at line 235 and the first-install meta.json block at 258, so sourcing `plugin-meta.sh` after migrate leaves the stub repo (which exits at the migrate stub) untouched. I did not re-run test_build_ffmpeg_vendor.py, since nothing changed yet. |
| P9 | VERIFIED | `git diff HEAD main -- scripts/jellyfin-plus-webinject` adds `\|\| true` to the dpkg-query pipeline. This branch lacks it, so the merge brings the fix (item 5). |
| P10 | UNKNOWN | `jq` is not runnable from this shell. The existing test stubs jq (line 235). The plan's skip fallback covers it, but the new checks only mean something where real jq exists, so the implement stage must print the skip loudly. |

Baseline: `python3 scripts/test_bench_intel.py` prints "74 checks passed, 0 failed" on the unmerged branch.

## Scope and TDD
- `files` covers the whole change: the review items land in bench-intel.sh, test_bench_intel.py, plugin-meta.sh, proxmox-build.sh, stage-plugin.sh and Dockerfile.dockerignore. The other files are the 8 non-workflow files that the merge brings. The workflow file and .workbench are correctly excluded. `context-map.md` has a bench-intel row at line 161 to extend.
- `tdd: required` is honest. Each item has a named test that fails first against the unfixed script. Each answers a concrete bug: octal abort at 08:30, a lost summary on a 404, a bogus `neural=fast`, a stale session read, a dead FAIL branch, a broken JSONL line, a stale-log false FAIL on row 1, a token on argv, and drift between the GUID copies.

## Gaps to fix during implement (non-blocking)
1. proxmox-build.sh line 276 also writes the literal `12.3.0.0` (the sed that raises a 12.1 targetAbi) and matches `12.1.0.0`. The "no literal `12.3.0.0`" test would fail unless that line takes the value from `plugin-meta.sh` too.
2. Row 6 needs `NeuralAllowed` true as well as `NeuralMaxSourceHeight`: UpscaleEngine.cs:1521 skips routing otherwise. When routing is null, the FAIL detail should say that.
3. `api` will read its config from stdin (`--config -`), but `api_stream` calls it inside a `while read ... < <(awk)` loop. The curl stdin is its own pipe, so this is safe. A test should cover the segment loop, not only the first call.
4. The Dockerfile copies only bench-intel.sh and ffmpeg-vendor.sh into the image (line 125). bench-intel.sh must not source `plugin-meta.sh`; the plan doesn't, which is right.
5. The merge cannot be run in this shell, and the implement stage probably has the same restriction. The plan says to stop and report if the merge isn't done, and not to hand-copy main's files. Approval is conditional on that: if the merge is not in the branch when implementing starts, implement must stop. It must not edit the unmerged test_bench_intel.py, because main has 160 more lines there.

VERDICT: APPROVE
