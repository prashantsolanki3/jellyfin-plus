# Plan: keep scripts/ffmpeg-vendor.sh POSIX sh-safe

`scripts/ffmpeg-vendor.sh` is sourced by `/bin/sh` (dash) in the Arc image's acceptance step, but line 19 uses the
bash-only herestring `<<<"$filters"`. I read the whole 34-line file. That is the only bashism: the rest is
`case`, `[ ]`, `local` (fine in dash), `$(...)` and `echo`. Fix: `printf '%s\n' "$filters" | grep -qE '...' || return 0`.
Behaviour is unchanged. The pipeline's status is grep's, and `set -o pipefail` is not in play in dash.

The existing suite (166 checks, 29 mutants, all green on today's file; I ran it) never sees this, because every
check sources the file under bash (`BASH -n`, build-ffmpeg.sh's `source`, proxmox-build.sh's `.`). So the tests
come first, and they must fail on today's file:

- New `suite_posix` in `scripts/test_build_ffmpeg_vendor.py`, using `shutil.which("dash")`. If dash is absent it
  prints a clear SKIP line and registers no check.
- `dash -n scripts/ffmpeg-vendor.sh` passes.
- Under `dash -c '. "$1"; ffmpeg_vendor_switch_guard ...'`, with stub binaries from the existing `fake_ffmpeg`:
  - intel over a binary that lists optix: rc 1 and stderr names ALLOW_VENDOR_SWITCH;
  - intel over a binary without optix: rc 0;
  - nvidia over an optix binary: rc 0;
  - intel over an optix binary with ALLOW_VENDOR_SWITCH=1: rc 0;
  - intel over a missing path: rc 0.
- `ffmpeg_required_filters nvidia` prints `oidn optix ort fsr2 dlss`, and `intel` prints `oidn ort`.
- On today's file, dash dies with "Syntax error: redirection unexpected", so each of these fails.
- A text scan of the file for bashisms (`<<<`, `[[`, `&>`, `${x//}`, a `function` keyword, arrays), comments stripped.
- A mutant that reintroduces `<<<` in ffmpeg-vendor.sh, guarded so it is skipped when dash is absent.
- Update the module docstring.

Then make the one-line fix in ffmpeg-vendor.sh. Existing mutant "guard looks for the wrong filter" keys on the
regex string, which still occurs once, so it is unaffected.

No docs change: the behaviour is the same. Lesson: none, because there is no agent-lessons file in this repo and
the lesson is captured by the new dash tests. The PR stage must supply a `lesson: none (<reason>)` line of at
least 15 characters.

```yaml
{
  "goal": "Make scripts/ffmpeg-vendor.sh valid POSIX sh (replace the <<< herestring with printf|grep) with dash-based tests that fail on the old file",
  "files": ["scripts/ffmpeg-vendor.sh", "scripts/test_build_ffmpeg_vendor.py"],
  "test_commands": ["python3 scripts/test_build_ffmpeg_vendor.py", "python3 -m py_compile scripts/test_build_ffmpeg_vendor.py"],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "dash -n scripts/ffmpeg-vendor.sh passes (checked by the test)",
    "sourcing the file under dash and calling ffmpeg_vendor_switch_guard works: refuses intel over an optix stub, allows intel over a no-optix stub, nvidia, ALLOW_VENDOR_SWITCH=1 and a missing binary",
    "ffmpeg_required_filters nvidia/intel print the same lists under dash",
    "no other bashism remains in the file",
    "new dash checks fail on the unfixed file, then pass after the fix; suite ends with 0 failed and all mutants caught",
    "behaviour of the guard is unchanged under bash (existing 166 checks still pass)"
  ],
  "premises": [
    {"id": "P1", "claim": "Line 19 of scripts/ffmpeg-vendor.sh is the only bashism in the file", "label": "VERIFIED", "check": "Read the whole 34-line file: only case, [ ], local, $(), echo, and the <<< on line 19", "load_bearing": true},
    {"id": "P2", "claim": "The existing suite passes on the current file because it only sources under bash, so it cannot see the bug", "label": "VERIFIED", "check": "python3 scripts/test_build_ffmpeg_vendor.py -> checks: 166 passed, 0 failed; mutants: 29 caught of 29", "load_bearing": true},
    {"id": "P3", "claim": "dash is installed in the workbench image so the new tests can run", "label": "INFERRED", "check": "the brief says so; the shell allow list blocks running dash directly, so the test's own shutil.which('dash') settles it at implement time", "load_bearing": true},
    {"id": "P4", "claim": "fake_ffmpeg stub output (' T.. optix ...') matches the guard regex under grep -E in dash, so the optix/no-optix stubs discriminate", "label": "INFERRED", "check": "same stubs already drive the bash-run guard checks that pass; confirm by running the new checks", "load_bearing": false},
    {"id": "P5", "claim": "The Dockerfile that sources this file is not in this repo, so nothing else needs changing", "label": "VERIFIED", "check": "Grep for ffmpeg-vendor finds only scripts, docs, AGENTS.md and CLAUDE.md; no Dockerfile or workflow", "load_bearing": false},
    {"id": "P6", "claim": "The existing mutant that targets the regex string 'optix ' stays valid after the fix (target text still occurs exactly once)", "label": "VERIFIED", "check": "the fix keeps the quoted regex '^ *[TSC.]* *optix ' verbatim", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "check POSIX-shell validity of a sourced script", "checked": "dash -n and dash -c sourcing (Debian dash, the /bin/sh in the failing image); existing test helpers fake_ffmpeg/run/Suite in scripts/test_build_ffmpeg_vendor.py", "verdict": "extend", "reason": ""}
  ]
}
```
