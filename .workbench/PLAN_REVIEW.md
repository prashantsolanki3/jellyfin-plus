# Plan review: rev-q18n2 Level Zero loader + GPU driver (round 1)

The worktree has no diff yet (`git status` clean), so this reviews the plan and the files it depends on. I ran every check myself.

## Premises

- **P1 VERIFIED (not load-bearing).** `kubectl logs jellyfin-plus-bench-run22-vbb7n -n media` prints `ort_env: ep_libs_missing=none gpu_plugin_unresolved=none opencl_icd=present libOpenCL=present level_zero_loader=MISSING render_node=/dev/dri/renderD129 ... open=ok`. Only the loader is missing. The same log has `device=openvino` at 1.38 and 0.54 fps, and `session_withheld=neural ... not measured on Intel`.
- **P2 VERIFIED (load-bearing).** I fetched the GitHub releases API for intel/compute-runtime tag 26.35.39758.10. The asset `libze-intel-gpu1_26.35.39758.10-0_amd64.deb` has digest `sha256:c19a641b953d55aebbf1d51bec364a84bf629f985e02fbbe6dc70224c0e88470`, and the release body's sum list agrees. The same release holds `intel-opencl-icd_26.35.39758.10-0_amd64.deb` and `libigdgmm12_22.10.0_amd64.deb`. jellyfin-packaging `docker/Dockerfile` (master) pins `NEO_VER=26.35.39758.10` and installs `intel-opencl-icd_${NEO_VER}-0`, so the sha and the release match the base.
- **P3 VERIFIED (load-bearing).** packages.debian.org shows `libze1` 1.20.6-1 amd64 in trixie. The image's final stage already runs `apt-get install` from trixie (Dockerfile line 124). The new RUN must run its own `apt-get update`, because the lists are deleted after each RUN.
- **P4 INFERRED (load-bearing), cannot be run here.** It needs a real `docker build` of the image, and the sandbox has no Docker and no Arc device. What I could check:
  - The release's `build_l0_gpu_driver_deb.sh` shows the deb's Depends are `libigdgmm12 (>= build version)` and `intel-igc-core-2 (>= v, << v+~)`. The base image already installs `libigdgmm12` 22.10.0 and IGC 2.41.5 from the same release set, so these should be satisfied.
  - The driver RUN's `apt-get install libigdgmm12` will not downgrade 22.10.0.
  - A failure would be a loud build error, not a silent regression.
  - That is evidence, not a run.
- **P5 INFERRED (not load-bearing).** Needs `dpkg -L libze-intel-gpu1` in a built image. The gate on `libze_intel_gpu.so.1` and `libze_loader.so.1` in `ldconfig -p` is plausible. If the deb names its library differently, the build gate fails loudly.
- **P6 VERIFIED (not load-bearing).** The build stage's apt line (Dockerfile line 40) installs `curl` and `ca-certificates`. Lines 44-45 show the `sha512sum -c` pattern to copy.
- **P7 VERIFIED (load-bearing).** I read `local_libs_failures`, `driver_failures` and `shim_interpreter_failures` in `scripts/test_bench_intel.py` lines 1023-1183:
  - The order check uses the first `ldconfig` and the first `grep 'not found'` in the final stage.
  - The driver install is found by `apt-get install` plus `mesa-vulkan-drivers`.
  - The driver gate is the first RUN containing `/usr/share/vulkan/icd.d`.
  - The shim check uses the first `apt-get install` in the final stage.
  - The placement in the plan is compatible with all of these. The install RUN goes after the driver RUN (line 126) and before `COPY ... local-libs` (line 131), and contains neither `ldconfig` nor `grep 'not found'`. The acceptance RUN goes after line 145 and must not contain `/usr/share/vulkan/icd.d`.
  - Baseline runs before any edit: `python3 scripts/test_bench_intel.py` -> `328 checks passed, 0 failed`. `python3 scripts/test_build_ffmpeg_vendor.py` -> `checks: 186 passed, 0 failed` and `mutants: 34 caught of 34`.
- **P8 INFERRED (not load-bearing).** Whether the missing loader explains the low fps is unknown, and the plan says so. This is an image-half attempt only.
- **P9 INFERRED (not load-bearing).** `scripts/build-ffmpeg.sh` lines 666-684 unpack only the `onnxruntime/capi` libraries. A build has no `/dev/dri`. Skipping the device query is justified.

## Scope, tdd, tests

- `files` lists the Dockerfile, the doc and the new test. That is the whole change. The forbidden paths (workflows, `bench-intel.sh`, `test_bench_intel.py`, `src/`, `GPU_VENDORS.md`) are not touched.
- `tdd: required` is honest. The Dockerfile is code, so a new `scripts/test_intel_level_zero.py` is warranted because `test_bench_intel.py` is off limits.
- Each planned mutant must be real:
  - a changed or missing sha
  - an install RUN that is missing or placed after `COPY local-libs`
  - an acceptance RUN without the `libze_loader.so.1` / `libze_intel_gpu.so.1` gate or the version-equality gate
  Each would let the image build without the loader, which is the bug under repair.
- Implementation notes, not blockers:
  - In the image the plugin is at `/usr/lib/jellyfin-ffmpeg-oidn/ort/lib/libopenvino_intel_gpu_plugin.so`. The Dockerfile's `PREFIX` is `/usr/lib/jellyfin-ffmpeg-oidn`, so use the full path rather than `ort/lib/...`.
  - Run22's `gpu_plugin_unresolved=none` shows that `ldd` on this plugin is clean today, so the gate will not fail for an unrelated reason.

## Verdict

Every premise checks out except P4. P4 is load-bearing and can only be proven by a docker build, which is impossible here. By my rules, a load-bearing check that cannot be run is BLOCKED, not a pass. If the owner accepts P4 as an acknowledged PR-text caveat, because a bad dependency fails the build loudly and the PR states that no image was built, the plan is otherwise sound to implement as written.

VERDICT: BLOCKED
