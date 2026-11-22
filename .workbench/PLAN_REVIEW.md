# Plan review (round 1)

- **P1 (line 19 is the only bashism): VERIFIED.** I read all 34 lines of `scripts/ffmpeg-vendor.sh`. The file uses `case`, `[ ]`, `local` (fine in dash), `$(...)` and `echo`. The only bashism is `<<<"$filters"` on line 19. There are no `[[`, arrays, `${x//}`, `function` keyword or `&>`. The plan's `printf '%s\n' "$filters" | grep -qE ... || return 0` keeps the behaviour.
- **P2 (existing suite is green and blind to this): VERIFIED.** I ran `python3 scripts/test_build_ffmpeg_vendor.py` and it printed `checks: 166 passed, 0 failed` and `mutants: 29 caught of 29`. The only syntax check on `ffmpeg-vendor.sh` is `bash -n`, and every source of the file runs under bash, so the suite cannot see a dash-only failure.
- **P3 (dash is available so the new tests run): NOT VERIFIED, load-bearing.** The shell allow list refuses running dash. Read, Glob and Grep refuse paths outside the worktree, so I could not check `/bin/dash` or `/usr/bin/dash`. The plan makes the dash suite skip when dash is absent. If dash were missing, the new tests would register nothing and the "fails on today's file" TDD evidence could not be produced. Settling this needs a run with dash.
- **P4 (stub output matches the guard regex): VERIFIED by reading.** The `fake_ffmpeg` stub (lines 125-133) echoes `' T.. optix            V->V       stub'`. This matches `^ *[TSC.]* *optix ` under grep -E. The bash-run guard checks use the same stubs and pass. Not load-bearing.
- **P5 (no Dockerfile in this repo): VERIFIED.** Grep for `ffmpeg-vendor` finds 7 files: the test, `proxmox-build.sh`, `ffmpeg-vendor.sh`, `build-ffmpeg.sh`, `GPU_VENDORS.md`, `CLAUDE.md` and `AGENTS.md`. None is a Dockerfile or workflow.
- **P6 (mutant target text unaffected): VERIFIED by reading.** The regex string stays in the file once after the fix. The existing mutant "guard looks for the wrong filter" keys on that string.

**Scope:** `files` (`scripts/ffmpeg-vendor.sh` and `scripts/test_build_ffmpeg_vendor.py`) covers the whole change. `git status` is clean, so nothing is implemented yet. `tdd: required` is honest because both files are code.

**Test value:** each planned check would catch a real regression. `dash -n` catches any bashism, and the source-and-call checks catch the guard misbehaving under dash. The text scan and the `<<<` mutant catch a reintroduced bashism if dash is absent from some CI image. The scan is a weak substitute for dash, but it is additive.

**Note for implement:** when dash is absent, print the SKIP line, and keep the text scan outside the dash gate so it still runs.

VERDICT: BLOCKED
