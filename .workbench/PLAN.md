# Plan: Level Zero loader + GPU driver in the Intel image (rev-q18n2, rows 5/6, image only)

Bench run22 (read with `kubectl logs jellyfin-plus-bench-run22-vbb7n -n media`) printed
`ort_env: ... opencl_icd=present libOpenCL=present level_zero_loader=MISSING render_node=/dev/dri/renderD129 open=ok`,
so only the Level Zero loader is missing. The base image's Intel stack is not from Debian: the
jellyfin-packaging Dockerfile installs `intel-opencl-icd_26.35.39758.10-0` from intel/compute-runtime GitHub
release debs. That same release also ships `libze-intel-gpu1_26.35.39758.10-0_amd64.deb`
(sha256 c19a641b953d55aebbf1d51bec364a84bf629f985e02fbbe6dc70224c0e88470, from the release API's `digest`
and the release body). The loader is `libze1` 1.20.6-1 in Debian trixie main, which the image's apt already serves.

Change (docker/intel/Dockerfile only, plus a doc line and a new test script):
1. Build stage: `ARG` for the version and sha256, `curl` the one deb into `/out`, `sha256sum -c` (the build stage
   already has curl and the DOTNET sha pattern to copy).
2. Final stage: new RUN after the driver RUN and before the `COPY --from=build /out/local-libs` line. It installs
   `libze1` plus the staged deb with `apt-get install --no-install-recommends` (apt resolves the deb's Depends from
   trixie and the base's libigdgmm12 22.10.0 / IGC), then cleans the apt lists and deletes the deb. It must not
   contain the word `ldconfig` or `grep 'not found'`, and it must not contain `mesa-vulkan-drivers`
   (test_bench_intel.py's order and `install` lookups key on those strings).
3. Acceptance RUN after the existing driver acceptance: `ldconfig -p` must list `libze_loader.so.1` and
   `libze_intel_gpu.so.1`; `ldd` of both and of `ort/lib/libopenvino_intel_gpu_plugin.so` must show no `not found`;
   `dpkg-query` versions of `libze-intel-gpu1` and `intel-opencl-icd` must be equal (fails the build if the base
   moves its compute runtime and the pin does not follow). Any miss exits non-zero.
4. The OpenVINO device-query check is skipped: the onnxruntime-openvino wheel ships libraries only (no
   `benchmark_app`/python openvino), and listing GPU needs the Arc device, which a build does not have. The bench
   `ort_env` line and row 5 are the runtime proof.
5. One line in docs/intel-patched-ffmpeg-libplacebo.md, "How the patched ffmpeg is built" section, saying why the
   two packages are in the image.
6. New `scripts/test_intel_level_zero.py` (the tdd item, because test_bench_intel.py is off limits): reads the
   Dockerfile and asserts the pinned sha/version, the sha check, the install RUN placement, and the acceptance
   RUN, each with a mutant that must be caught. It is picked up by the affected-tests job automatically.

Not claimed: no image was built and no Arc hardware is reachable from this sandbox. It is INFERRED, not proven, that
the missing loader is why OpenVINO ran slowly. The ORT log line already says `device=openvino` and the bench shows
`session_withheld=neural ... not measured on Intel`, a plugin/src-side gate other tasks own. If row 5 is still slow
after this image, the cause is elsewhere (e.g. OpenVINO 2025.4 GPU plugin using OpenCL only). The bead's wider
Acceptance (no FAIL rows, Argo Healthy, run19 log) cannot be met by an image-only change; this attempt only supplies
the image half of rows 5/6.

```yaml
{
  "goal": "Make docker/intel/Dockerfile install the Level Zero loader (libze1) and the matching Intel Level Zero GPU driver (libze-intel-gpu1 26.35.39758.10-0, sha256-pinned, same release as the base's intel-opencl-icd) and fail the build if libze_loader.so.1, libze_intel_gpu.so.1 or a version match is missing",
  "files": [
    "docker/intel/Dockerfile",
    "docs/intel-patched-ffmpeg-libplacebo.md",
    "scripts/test_intel_level_zero.py"
  ],
  "test_commands": [
    "python3 scripts/test_intel_level_zero.py",
    "python3 scripts/test_bench_intel.py",
    "python3 scripts/test_build_ffmpeg_vendor.py"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "docker/intel/Dockerfile downloads libze-intel-gpu1_26.35.39758.10-0_amd64.deb from the intel/compute-runtime release and checks its sha256 before install",
    "the final stage installs libze1 and that deb with apt-get --no-install-recommends and cleans /var/lib/apt/lists",
    "an acceptance RUN fails the build when libze_loader.so.1 or libze_intel_gpu.so.1 is absent from ldconfig -p, when ldd shows not found for either or for libopenvino_intel_gpu_plugin.so, or when libze-intel-gpu1 and intel-opencl-icd versions differ",
    "scripts/test_bench_intel.py Dockerfile checks still pass unchanged (order of COPY local-libs, ldconfig and the not-found gate; driver install RUN untouched)",
    "scripts/test_intel_level_zero.py passes and each of its mutants is caught",
    "one line in docs/intel-patched-ffmpeg-libplacebo.md says why the packages are there",
    "nothing under .github/workflows, scripts/bench-intel.sh, scripts/test_bench_intel.py, src/ or docs/architecture/GPU_VENDORS.md changes",
    "PR text states no image was built in the sandbox and that OpenVINO's GPU device query was not run"
  ],
  "reuse_check": [
    {
      "need": "Level Zero loader (libze_loader.so.1)",
      "checked": "Debian trixie main package libze1 1.20.6-1 amd64 (packages.debian.org search, fetched this session); the image already apt-installs from trixie main",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "Intel Level Zero GPU driver matching the base's compute runtime 26.35",
      "checked": "intel/compute-runtime release 26.35.39758.10 asset libze-intel-gpu1_26.35.39758.10-0_amd64.deb with published digest (GitHub releases API, fetched this session); the base image gets intel-opencl-icd from the same release per jellyfin-packaging docker/Dockerfile (NEO_VER=26.35.39758.10). Debian trixie's own libze-intel-gpu1 is 25.x and would mismatch.",
      "verdict": "use",
      "reason": ""
    },
    {
      "need": "sha256-checked download in a Dockerfile",
      "checked": "the DOTNET_SDK_SHA512 / sha512sum -c pattern already in docker/intel/Dockerfile",
      "verdict": "extend",
      "reason": ""
    }
  ],
  "premises": [
    {
      "id": "P1",
      "claim": "On run22 the only missing item in ort_env is the Level Zero loader (OpenCL ICD, libOpenCL and render node open are fine)",
      "label": "VERIFIED",
      "check": "kubectl logs jellyfin-plus-bench-run22-vbb7n -n media: 'opencl_icd=present libOpenCL=present level_zero_loader=MISSING ... open=ok'",
      "load_bearing": false
    },
    {
      "id": "P2",
      "claim": "libze-intel-gpu1_26.35.39758.10-0_amd64.deb exists in the same compute-runtime release as the base's intel-opencl-icd, sha256 c19a641b953d55aebbf1d51bec364a84bf629f985e02fbbe6dc70224c0e88470",
      "label": "VERIFIED",
      "check": "fetch https://api.github.com/repos/intel/compute-runtime/releases/tags/26.35.39758.10 (asset digest and release-body sums); jellyfin-packaging docker/Dockerfile NEO_VER=26.35.39758.10",
      "load_bearing": true
    },
    {
      "id": "P3",
      "claim": "libze1 is available from trixie main for the image's apt",
      "label": "VERIFIED",
      "check": "fetch packages.debian.org search libze1 suite=trixie: 1.20.6-1 amd64; the image already runs apt-get from trixie (Dockerfile mesa-vulkan-drivers install)",
      "load_bearing": true
    },
    {
      "id": "P4",
      "claim": "apt can install the local libze-intel-gpu1 deb and resolve its Depends (libigdgmm12 22.10.0 already in base; IGC dlopen'd) without a conflict with the base's Intel packages",
      "label": "INFERRED",
      "check": "a real docker build of docker/intel/Dockerfile (not possible in this sandbox); the jellyfin base itself installs these debs together with `apt-get install -f ./*.deb`",
      "load_bearing": true
    },
    {
      "id": "P5",
      "claim": "The Level Zero loader finds the driver by the default name libze_intel_gpu.so.1 shipped in libze-intel-gpu1, so a ldconfig -p check for both names is a valid build-time gate",
      "label": "INFERRED",
      "check": "dpkg -L libze-intel-gpu1 in a built image; Level Zero loader's built-in driver list",
      "load_bearing": false
    },
    {
      "id": "P6",
      "claim": "The build stage has curl (it installs curl explicitly), so the deb can be fetched there and copied to the final stage, which may not have curl",
      "label": "VERIFIED",
      "check": "docker/intel/Dockerfile build-stage apt-get install line 40 names curl",
      "load_bearing": false
    },
    {
      "id": "P7",
      "claim": "New RUNs can be added without breaking scripts/test_bench_intel.py's Dockerfile checks (first 'ldconfig' after COPY local-libs and before the first \"grep 'not found'\"; the driver install RUN is the first containing mesa-vulkan-drivers; python3 install is the first apt-get install in the final stage)",
      "label": "VERIFIED",
      "check": "read local_libs_failures, driver_failures and shim_interpreter_failures in scripts/test_bench_intel.py lines 1023-1183; will be re-proved by running that test",
      "load_bearing": true
    },
    {
      "id": "P8",
      "claim": "The missing loader is why OpenVINO ran at 1.38/0.54 fps (i.e. never used the Arc GPU)",
      "label": "INFERRED",
      "check": "a bench run on the rebuilt image with ort_env level_zero_loader=present and row 5 fps; the ORT line already says device=openvino, the session record says neural withheld on Intel, and OpenVINO's GPU plugin mainly uses OpenCL, so this may not move the fps",
      "load_bearing": false
    },
    {
      "id": "P9",
      "claim": "An OpenVINO device-query build check cannot run without the Arc device, and the onnxruntime-openvino wheel carries no query tool",
      "label": "INFERRED",
      "check": "scripts/build-ffmpeg.sh lines 666-684 unpack only libonnxruntime*, the provider, libopenvino.so.2541 and the GPU plugin; a build has no /dev/dri",
      "load_bearing": false
    }
  ]
}
```
