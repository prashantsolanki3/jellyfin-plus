# Plan review (round 1)

Premises re-run by me, not taken from the labels.

- **P1 (load-bearing): VERIFIED.** `docker/intel/Dockerfile:42-45` says "Not libshaderc-dev" and the apt line has no libshaderc. `scripts/test_bench_intel.py:315` asserts `"libshaderc" not in pkgs`. `scripts/build-ffmpeg.sh:519` removes the distro `libshaderc-dev libshaderc1`. `build-ffmpeg.sh:556-570` builds shaderc from source with `CMAKE_INSTALL_PREFIX=/usr/local`, then runs `ldconfig`. The brief's "build stage links libshaderc-dev (fork #10)" is false in this checkout. Departing from the brief's `libshaderc1` suggestion is justified.
- **P2 (load-bearing): VERIFIED.** The final stage (Dockerfile:121-140) copies only `/usr/lib/jellyfin-ffmpeg-oidn`, `/out/*`, the shaders and the web root. It never copies `/usr/local`. The `ldd | awk | dpkg -S` step (83-90) emits nothing for a library with no owning package. This matches the reported `libshaderc_shared.so.1 => not found`.
- **P3: INFERRED, not load-bearing.** The build-ffmpeg.sh comments (536-546) support it. I did not run it, and nothing in the plan's approach depends on it.
- **P4: INFERRED, not load-bearing.** The cmake call sets no libdir. Debian multiarch could place the library in `lib/x86_64-linux-gnu`, and libplacebo is told to do exactly that at line 587. The plan's ldd-driven copy does not depend on the path, so this is sound.
- **P5 (load-bearing): NOT VERIFIED.** The plan copies the libraries to `/usr/local/lib` and relies on `/usr/local/lib` being in the base image's ld.so search path (Debian `libc.conf`). I could not check this here. There is no docker or image in this session, the repo has no evidence either way, and the fetch tool was refused by sources.debian.org and salsa robots.txt. It is very likely true, and the final-stage `ldd | grep 'not found'` gate would catch a miss, but only after a full Arc build. That is expensive, and it is a load-bearing claim without a first-hand check.
- **P6: INFERRED, not load-bearing.** The dev/runtime pair audit is derived from the ldd-driven `runtime-packages.txt`. dlopen'd drivers cannot be checked without a build. The plan says so.
- **P7: VERIFIED.** `python3 scripts/test_bench_intel.py` printed `99 checks passed, 0 failed` on the unmodified tree.

Scope and TDD:
- `files` (Dockerfile, `test_bench_intel.py`, `GPU_VENDORS.md`) looks like the whole change. `SPEC` in the test is `GPU_VENDORS.md`, so the doc line is covered by the existing 5.8 check. `build-ffmpeg-debug.sh` has the same shaderc build, but it does not feed the image, so it can stay out of scope.
- `tdd: required` is honest. The plan runs the new checks against the unfixed Dockerfile first and includes mutants. The checks catch these real bugs: a -dev package added with no runtime pair, and a source-built library that never reaches the final stage.
- Suggestion: have the test also assert the `ldconfig` step and the destination directory. Make the destination independent of P5 by copying into `/usr/lib/x86_64-linux-gnu/` instead of `/usr/local/lib/`. That directory is a trusted default directory that every glibc searches, so P5 stops being load-bearing. Alternatively, state the `libc.conf` fact with a source the implementer has read.

VERDICT: BLOCKED
