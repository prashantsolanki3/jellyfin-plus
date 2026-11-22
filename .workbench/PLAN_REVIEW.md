# Plan review, round 1

I re-ran each check myself. No docker, no `kubectl exec` and no GPU exist in this session, so anything that needs an image build is stated as such.

## Premises

- **P1 (rows 2, 3, 4, 9 FAIL): VERIFIED.** `kubectl logs jellyfin-plus-bench-run3-q5fc2 -n media` printed `row 2 FAIL ffmpeg failed`, rows 3 and 4 the same, and `row 9 FAIL hardware decode chain failed: bench: maxrss=15744KiB`. Rows 1, 5-8 and 10-12 are SKIP, row 13 is MANUAL. The log does not contain the underlying `[VAAPI] Failed to initialise` line; that comes from the orchestrator's evidence only.
- **P2 (mesa-vulkan-drivers ships intel_icd.json and libvulkan_intel.so): VERIFIED.** I fetched the trixie amd64 filelist. It lists `/usr/share/vulkan/icd.d/intel_icd.json` and `/usr/lib/x86_64-linux-gnu/libvulkan_intel.so`. The ICD JSON content was not read.
- **P3 (free intel-media-va-driver in main; non-free build is a separate package): VERIFIED.** The fetched page `packages.debian.org/trixie/intel-media-va-driver-non-free` shows `25.2.3+ds1-1 [non-free]`. It depends on `libigdgmm12 (>= 22.7.2+ds1)` and `libva-driver-abi-1.22`, and lists the free package as a similar package.
- **P4 (free build lacks Arc encode): INFERRED, not load-bearing.** Accepted. The task's "if non-free is needed" is a judgement call, and the plan's reason (row 10 is QSV encode, and the repo already names the non-free package as the host requirement) is reasonable. The PR must say plainly that the non-free component is enabled.
- **P5 (base is trixie; deb822 source with `/usr/share/keyrings/debian-archive-keyring.pgp`): VERIFIED enough to proceed.**
  - `jellyfin-packaging` master Dockerfile builds on `debian:trixie-slim` (`OS_VERSION=trixie`). The revenant e2e doc records Debian 13 from `kubectl exec` on the production pod.
  - The trixie `debian-archive-keyring` filelist contains `/usr/share/keyrings/debian-archive-keyring.pgp`.
  - Not verified: the pinned digest's own apt layout. The plan writes its own `.sources` file, and the acceptance RUN fails the build if apt cannot read it.
- **P6 (libigdgmm12 via apt puts libigdgmm.so.12 on the loader path): NOT VERIFIED, and the premise behind it is contradicted. Not blocking.**
  - Upstream `jellyfin-packaging` `docker/Dockerfile` installs `libigdgmm12_${GMMLIB_VER}_amd64.deb` (22.10.0, from Intel's GitHub release) with `apt-get install -f ./*.deb` on amd64. That is a system package, so `/usr/lib/x86_64-linux-gnu/libigdgmm.so.12` should already be on the loader path.
  - The revenant doc, verified by `kubectl exec`, also says the runtime image has `libigdgmm`.
  - So the orchestrator's "libigdgmm only in jellyfin-ffmpeg/lib" does not match the packaging source. The real cause of the row 9 libva failure may differ.
  - The same Dockerfile means `apt-get install libigdgmm12` will be a no-op, because the installed 22.10.0 is newer than Debian's 22.7.2 and satisfies the non-free package's `>= 22.7.2+ds1`. That is harmless.
  - It cannot be settled here: no `kubectl exec`, no image. The plan's in-image `ldd` gate is the right instrument and is part of the deliverable, so I do not treat it as a precondition. The PR body must not assert "libigdgmm was missing" as the root cause. It should say the cause is unproven until the Arc bench runs.
- **P7 (existing checks survive a second apt RUN and a second `grep 'not found'` RUN): VERIFIED.** `python3 scripts/test_bench_intel.py` printed `189 checks passed, 0 failed`.
  - `shim_interpreter_failures` takes the first `apt-get install` in the final stage, which is the existing python3/jq one.
  - `local_libs_failures` uses `final.find("ldconfig")` and the first `grep 'not found'`. The new acceptance RUN must come after `RUN ldconfig`, and no new comment before the `COPY ... local-libs` line may contain the word "ldconfig", or the order check breaks.
  - The build-stage apt check (line 591) reads only the build stage.
  - Only `scripts/test_bench_intel.py` and `scripts/bench-intel.sh` reference the Dockerfile, so the file scope is complete.
- **P8 (ICD library_path is a bare soname): not checked, not load-bearing.** The plan's check also handles an absolute path, which is right.
- **P9 (extra ICDs lvp, radeon, hasvk, nouveau, virtio, gfxstream installed): VERIFIED** from the same filelist. Device selection on the Arc is only settled by the bench.

## Corrections the implementer must apply

1. The plan's size note, "anv pulls Mesa/LLVM-free deps", is wrong. `mesa-vulkan-drivers` depends on `libllvm19` plus about 80 MB installed (trixie package page). The PR must say the image grows by that, and must not say it is LLVM-free.
2. Do not state the libigdgmm root cause as fact (see P6).
3. Keep the non-free source scoped to one RUN: write it, install, then remove it and the lists. This matches the acceptance criterion.

## Scope and TDD

- `files` (Dockerfile, `scripts/test_bench_intel.py`, GPU_VENDORS.md) covers the whole change. The doc is read by the test as the spec page, so adding a paragraph is consistent.
- `tdd: required` is honest. Baseline is 189 passed, 0 failed. The new `driver_failures` checks must be added first and shown failing, then passing, with the counts pasted.
- The planned mutants catch real regressions: a dropped package, dropped non-free scoping (which would leave the repo permanently enabled), a missing ICD check, a missing `ldd` on either library, and a check placed before the install (which would validate nothing). The `vainfo`/`vulkaninfo` comment check is a documentation check and should stay small.

## Not verified

The GPU outcome, the build itself, the base image's real apt layout, Conflicts/Replaces between the non-free iHD and any iHD already in the base, and the actual image size. Only a build and the Arc bench settle these.

VERDICT: APPROVE
