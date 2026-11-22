# Plan: Intel Vulkan ICD and a loadable iHD driver in the Arc image

The bench Job (`jellyfin-plus-bench-run3`, read with `kubectl logs`) prints rows 2, 3, 4 and 9 as FAIL, so the
symptom is live. The cause is that `runtime-packages.txt` is derived from `ldd` of the patched ffmpeg, which
never sees the drivers that libvulkan and libva `dlopen`. The fix is in the final stage of
`docker/intel/Dockerfile`, and `scripts/test_bench_intel.py` gets the tests first.

1. **Tests first** in `scripts/test_bench_intel.py`: a `driver_failures(df)` function in the existing style
   (a failures list, a `*_checks` wrapper, mutants), called from `dockerfile_checks`. It reads the final stage and requires:
   - an `apt-get install` that names `mesa-vulkan-drivers`, `intel-media-va-driver-non-free` and `libigdgmm12`;
   - the non-free component enabled for that one install only (a sources file written in the same RUN, removed
     again before the RUN ends, lists cleaned);
   - an acceptance RUN, after the install and after `RUN ldconfig`, that checks `/usr/share/vulkan/icd.d/*intel*.json`
     exists, runs `ldd` on `iHD_drv_video.so` and on the library named by the ICD JSON, and fails on `grep 'not found'`;
   - a comment containing `vainfo`/`vulkaninfo` and "GPU".

   Mutants: drop each package, drop the non-free scoping, drop the ICD check, drop an ldd check, put the check before the install.
   Run it first and confirm it FAILS (baseline is 189 passed, 0 failed), then pass.
2. **Dockerfile (final stage)**:
   - After the existing python3/jq install, add a RUN that writes a deb822 `non-free` source for trixie, runs
     `apt-get update`, installs the three packages with `--no-install-recommends`, then removes the source file and the lists.
   - After `RUN ldconfig`, add the acceptance RUN. It reads the library name from `intel_icd.json` with `jq`, which the
     first install already provides, resolves a bare name under `/usr/lib/x86_64-linux-gnu`, and runs `ldd` on it and on the iHD driver.
   - Comment that vainfo/vulkaninfo need a GPU, so the Arc bench is the real proof.
3. **Doc**: one short paragraph in `docs/architecture/GPU_VENDORS.md` 5.8 saying why (dlopen'd drivers are invisible to the ldd-derived list) and
   that the non-free component is enabled for `intel-media-va-driver-non-free` alone.

Non-free decision: the free `intel-media-va-driver` has "only a limited set of encoder" features (the HuC-based low-power encode that
Arc needs is non-free), and bench row 10 is QSV encode. `scripts/build-ffmpeg.sh` and GPU_VENDORS.md already name
`intel-media-va-driver-non-free` as the host requirement. So the plan uses the non-free package and says so in the PR. The image is private, so this adds no redistribution.

Risk to report, not fix: `mesa-vulkan-drivers` also drops `lvp_icd.json` and other ICDs. Row 2 uses `vulkan=vk@dr` (derived from the DRM
device), which should pick the Arc, but only the bench proves it. The image grows (anv pulls Mesa/LLVM-free deps; size not measured).
No build is dispatched.

Unverified by design: the apt sources layout of the base (the plan writes its own `.sources` file, so it does not depend on it), the non-free
package's `Conflicts`/`Replaces` against an iHD already in the base, and the final result on the GPU. Only the Arc bench settles these.
The workbench has no `kubectl exec`, so the in-image file state (empty `icd.d`, libigdgmm location) comes from the orchestrator's evidence.

```yaml
{
  "goal": "Ship the Intel anv Vulkan ICD and a loadable iHD VA driver (with libigdgmm on the loader path) in the Arc image, proved at build time by in-image checks and in the test suite.",
  "files": [
    "docker/intel/Dockerfile",
    "scripts/test_bench_intel.py",
    "docs/architecture/GPU_VENDORS.md"
  ],
  "test_commands": [
    "python3 scripts/test_bench_intel.py",
    "pre-commit run --files docker/intel/Dockerfile scripts/test_bench_intel.py docs/architecture/GPU_VENDORS.md"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "scripts/test_bench_intel.py fails before the Dockerfile change and passes after, with the counts pasted (baseline 189 passed, 0 failed)",
    "the final stage of docker/intel/Dockerfile installs mesa-vulkan-drivers, intel-media-va-driver-non-free and libigdgmm12 from Debian packages, not copied jellyfin-ffmpeg libs",
    "the non-free component is enabled for that single install only, and the PR says so",
    "a build-time RUN checks an Intel ICD JSON exists in /usr/share/vulkan/icd.d and that ldd of iHD_drv_video.so and of the ICD's library shows no 'not found'",
    "a comment says vainfo/vulkaninfo need a GPU, so the Arc bench is the real proof",
    "no build dispatched, nothing posted upstream"
  ],
  "premises": [
    {"id": "P1", "claim": "The bench rows 2, 3, 4 and 9 currently FAIL on the cluster", "label": "VERIFIED", "check": "kubectl logs jellyfin-plus-bench-run3-q5fc2 -n media printed 'result: row 2 FAIL ffmpeg failed', rows 3 and 4 likewise, 'row 9 FAIL hardware decode chain failed'", "load_bearing": false},
    {"id": "P2", "claim": "Debian trixie main ships mesa-vulkan-drivers with /usr/share/vulkan/icd.d/intel_icd.json and libvulkan_intel.so", "label": "VERIFIED", "check": "fetched https://packages.debian.org/trixie/amd64/mesa-vulkan-drivers/filelist", "load_bearing": true},
    {"id": "P3", "claim": "intel-media-va-driver (free, main) exists in trixie at 25.2.3, and the non-free build is a separate package in the non-free component", "label": "VERIFIED", "check": "fetched https://packages.debian.org/trixie/intel-media-va-driver (lists intel-media-va-driver-non-free as a similar package)", "load_bearing": true},
    {"id": "P4", "claim": "The free iHD build lacks the Arc low-power encode (HuC) features, so QSV encode (bench row 10) needs the non-free package", "label": "INFERRED", "check": "the package description says 'only a limited set of encoder'; scripts/build-ffmpeg.sh and GPU_VENDORS.md already name intel-media-va-driver-non-free; the Arc bench row 10 settles it", "load_bearing": false},
    {"id": "P5", "claim": "The pinned base image is Debian 13 trixie and apt there accepts a deb822 .sources file with Signed-By /usr/share/keyrings/debian-archive-keyring.pgp", "label": "INFERRED", "check": "the revenant e2e decision doc says production Jellyfin runs on Debian 13 (VERIFIED there by kubectl exec); the keyring path needs a build to confirm, and the Dockerfile acceptance would fail loudly if apt cannot read it", "load_bearing": true},
    {"id": "P6", "claim": "Adding libigdgmm12 via apt puts libigdgmm.so.12 on the default loader path (ldconfig trigger), so ldd of iHD_drv_video.so resolves it without LD_LIBRARY_PATH", "label": "INFERRED", "check": "the final-stage ldd check run with a clean environment; a docker build is needed and is not dispatched", "load_bearing": true},
    {"id": "P7", "claim": "No existing check in test_bench_intel.py breaks when a second apt-get install RUN and a second grep 'not found' RUN are added to the final stage, provided the install follows the python3/jq install and the ldd checks follow RUN ldconfig", "label": "VERIFIED", "check": "read shim_interpreter_failures (first apt-get install in the final stage) and local_libs_failures (first ldconfig, first grep 'not found'); baseline run: python3 scripts/test_bench_intel.py -> 189 checks passed, 0 failed", "load_bearing": false},
    {"id": "P8", "claim": "The ICD JSON's library_path is a bare soname (libvulkan_intel.so) resolvable under /usr/lib/x86_64-linux-gnu", "label": "VERIFIED", "check": "filelist shows /usr/lib/x86_64-linux-gnu/libvulkan_intel.so; the JSON content was not read, so the check handles an absolute path as well", "load_bearing": false},
    {"id": "P9", "claim": "mesa-vulkan-drivers also installs lvp and other ICDs that could be enumerated by libplacebo", "label": "VERIFIED", "check": "same filelist: lvp_icd.json, radeon_icd.json, intel_hasvk_icd.json present; whether device selection picks the Arc is only settled on the bench", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "Vulkan ICD for Intel Arc in the image", "checked": "Debian trixie mesa-vulkan-drivers (packages.debian.org, fetched 2026-11-22)", "verdict": "use", "reason": ""},
    {"need": "iHD VA-API driver and its gmm library", "checked": "Debian trixie intel-media-va-driver-non-free (non-free) with libigdgmm12 as its dependency; the free intel-media-va-driver is the alternative", "verdict": "use", "reason": ""},
    {"need": "A dlopen-path acceptance check in the Dockerfile and its test", "checked": "the existing ldd 'not found' acceptance RUN and the *_failures()/mutant pattern in scripts/test_bench_intel.py", "verdict": "extend", "reason": ""}
  ]
}
```
