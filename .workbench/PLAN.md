# Plan: Arc image misses libshaderc_shared.so.1

**The brief's premise is false in this checkout.** It says the build stage links shaderc via
`libshaderc-dev` (fork #10). Here `docker/intel/Dockerfile` line 42 says "Not libshaderc-dev", the build
stage's apt line has no libshaderc, and `scripts/test_bench_intel.py:315` asserts that. `build-ffmpeg.sh`
builds shaderc `v2026.4` from source into `/usr/local` (it even `apt-get remove`s `libshaderc-dev libshaderc1`),
because the distro shaderc cannot compile FFmpeg 8's `nlmeans_vulkan` shader. So `libshaderc_shared.so.1`
is a source-built file under `/usr/local`. The build stage's `ldd`-to-`dpkg -S` step (which derives
`runtime-packages.txt`) finds no owning package for it, and the final stage, which never copies
`/usr/local`, fails with `not found`.

Installing Debian's `libshaderc1` in the final stage, as the brief suggests, would mean one of two things.
Either the distro build loads (shaderc compiles Vulkan shaders at run time, so this brings back the
`nlmeans_vulkan` failure), or it is shadowed and unused. I plan to do the following instead, and the PR will say
why it departs from the brief.

1. Build stage: in the existing `ldd` RUN, also copy every resolved library whose real path is under
   `/usr/local` into `/out/local-libs/`, saved under the soname the binary asks for. This is generic, so it
   covers any other source-built shared lib. It does not depend on shaderc's CMake libdir
   (`lib` or `lib/x86_64-linux-gnu`), which I could not confirm without a build.
2. Final stage: `COPY --from=build /out/local-libs/ /usr/local/lib/` and `RUN ldconfig`. `/usr/local/lib`
   is in Debian's default ld.so.conf and the cache is baked at build time, so the read-only pod rootfs is fine.
   The existing `ldd | grep 'not found'` acceptance check then gates it.
3. Audit of the -dev packages. `runtime-packages.txt` is `ldd`-derived, so every linked apt library is
   installed automatically. The pairs are:
   - libvulkan-dev -> libvulkan1
   - libva-dev -> libva2 (plus libva-drm2)
   - libvpl-dev -> libvpl2
   - libdrm-dev -> libdrm2
   - libx264-dev -> libx264-164
   - libxcb1-dev -> libxcb1
   - glslang-tools: build tool only, no runtime library
   - shaderc (source build): the new `/usr/local` copy, not a Debian package
   - libplacebo, the vulkan headers, FSR2: static or header-only
   - oidn and ort: rpathed under `$PREFIX`, which is already copied with the prefix

   Things loaded with `dlopen` rather than linked (the VA-API driver, `libmfx-gen`) never show in `ldd`.
   The stock `jellyfin-ffmpeg` already pulls those into the base, but I cannot verify that without an
   image build. This is stated as unverified in the PR.
4. Test first, in `scripts/test_bench_intel.py` (`build_apt_checks`). It adds a `DEV_RUNTIME` table
   mapping each build-stage -dev package to its runtime package (or `None` for a build tool), and a pure
   `unpaired_dev(pkgs, table)` helper. The check is that every `-dev` in the Dockerfile's build apt line
   and in the script's APT_PKGS has a table entry, so a new -dev without a runtime pair fails. The test also
   asserts three things. First, the final stage installs the `ldd`-derived list. Second, the build stage copies
   `/usr/local` libs to `/out/local-libs`. Third, the final stage copies them in and runs `ldconfig` before the
   `not found` check. Mutants for the helper (a -dev with no pair) and for a Dockerfile with the local-libs
   copy removed must be caught. I'll first run it against the unfixed Dockerfile to see the new checks go red.
5. Doc: one line in `docs/architecture/GPU_VENDORS.md` 5.8 about the source-built libs being carried.

The orchestrator dispatches the build; I do not.

```yaml
{
  "goal": "Make the Intel Arc runtime image carry the source-built libshaderc_shared.so.1 (and any other /usr/local shared lib the patched ffmpeg links), and make the test fail when a build-stage -dev package has no runtime pair",
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
  "reuse_check": [
    {"need": "carry a source-built shared library into the final image stage", "checked": "Docker multi-stage COPY --from + ldconfig (dockerfile:1.7, already used in this Dockerfile for /usr/lib/jellyfin-ffmpeg-oidn and runtime-packages.txt)", "verdict": "use", "reason": ""},
    {"need": "dev-package to runtime-package audit test", "checked": "scripts/test_bench_intel.py build_apt_checks (existing, compares the Dockerfile apt line with build-ffmpeg.sh APT_PKGS)", "verdict": "extend", "reason": ""}
  ],
  "acceptance": [
    "python3 scripts/test_bench_intel.py passes, and its new checks fail against the unfixed Dockerfile",
    "the final stage obtains libshaderc_shared.so.1 without installing the Debian shaderc packages (the distro build broke nlmeans_vulkan)",
    "every -dev package in the build stage and in build-ffmpeg.sh's intel APT_PKGS has a listed runtime pair, or is marked a build tool, and a new -dev without one fails the test",
    "the PR lists each dev/runtime pair and says the brief's libshaderc-dev premise did not hold in this checkout",
    "no build is dispatched"
  ],
  "premises": [
    {"id": "P1", "claim": "The build stage here does not install libshaderc-dev; shaderc is built from source into /usr/local by build-ffmpeg.sh", "label": "VERIFIED", "check": "Read docker/intel/Dockerfile:42-46, scripts/build-ffmpeg.sh:449-571, test_bench_intel.py:315", "load_bearing": true},
    {"id": "P2", "claim": "The final stage never copies /usr/local, so the source-built libshaderc_shared.so.1 is absent and dpkg -S finds no package for it", "label": "VERIFIED", "check": "Read Dockerfile:80-90 and 121-140: only /usr/lib/jellyfin-ffmpeg-oidn, /out/*, shaders and web are copied; matches the reported 'not found'", "load_bearing": true},
    {"id": "P3", "claim": "Installing Debian libshaderc1 would reintroduce the nlmeans_vulkan failure, since shaderc compiles shaders at run time", "label": "INFERRED", "check": "build-ffmpeg.sh:536-546 comments; settle by running nlmeans_vulkan on the built image", "load_bearing": false},
    {"id": "P4", "claim": "Where shaderc installs its lib (/usr/local/lib vs lib/x86_64-linux-gnu) is unknown, so the copy is taken from ldd output, not a fixed path", "label": "INFERRED", "check": "needs the build's ldd output; the generic approach does not depend on it", "load_bearing": false},
    {"id": "P5", "claim": "/usr/local/lib is in the base image's default ldconfig search path, and ldconfig runs as root in the final stage", "label": "INFERRED", "check": "Debian default (/etc/ld.so.conf.d/libc.conf); the final-stage ldd 'not found' gate settles it at build time", "load_bearing": true},
    {"id": "P6", "claim": "The other -dev packages are covered by the ldd-derived runtime-packages.txt; the dlopen'd VA-API/QSV drivers come from the base's stock jellyfin-ffmpeg dependencies", "label": "INFERRED", "check": "needs the image build's runtime-packages.txt output and a vainfo/QSV run on Arc; not checkable without a build", "load_bearing": false},
    {"id": "P7", "claim": "The existing test passes on the unmodified tree", "label": "VERIFIED", "check": "python3 scripts/test_bench_intel.py -> 99 checks passed, 0 failed", "load_bearing": false}
  ]
}
```
