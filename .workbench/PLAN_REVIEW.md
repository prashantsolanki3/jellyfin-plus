# Plan review, round 1

No code exists yet (`git status` is clean), so this reviews the plan only.

## Premises

- P1 VERIFIED. `kubectl describe pod jellyfin-55c59fcb97-mmdvw -n media` printed
  `Image: docker.io/jellyfin/jellyfin:12.4.20261026-211555@sha256:fea863d0...b771` and the same `Image ID`.
- P2 VERIFIED. The same describe shows `JELLYFIN_DATA_DIR=/config`, `JELLYFIN_CONFIG_DIR=/config/config` and
  `/config` mounted from the PVC `jellyfin-config`. The sso-plugin init container writes `/config/plugins/SSO-Auth_*`,
  so a plugin baked into the image would be hidden by the PVC. The wrapper is needed.
- P3 VERIFIED. `scripts/build-ffmpeg.sh` reads GPU_VENDOR, WITH_ORT, ORT_EP, WITH_FSR2 and PLAN_ONLY. It
  accepts intel with ORT_EP=openvino and FSR2. It also requires `FSR2_SDK` and `FSR2_LIB` (lines 382-386), which
  the Dockerfile must provide: the plan covers this through the out-of-tree FSR2 build (see P10).
- P4 VERIFIED. Only `scripts/test_build_ffmpeg_vendor.py` exists as a template in the Glob. The `scripts/test_*.py`
  naming holds.
- P5 VERIFIED. `scripts/jellyfin-plus-activate` sets `PLUGIN_DIR=.../plugins/JellyfinPlus_1.0.0.0` and
  `PATCH_DIR=/usr/lib/jellyfin-plus`. `0Harmony.dll` and the patcher DLL live in PATCH_DIR, outside the plugin dir.
  `src/Jellyfin.Plugin.Plus.csproj` targets **net10.0** (see gap 2 below).
- P6 VERIFIED. `src/Transcoding/GpuFdinfo.cs` uses `drm-engine-<class>`, `drm-engine-capacity-<class>`,
  `drm-cycles-<class>` and `drm-total-cycles-<class>`.
- P7 INFERRED, not confirmed. The upstream `jellyfin-packaging` master Dockerfile (fetched) is Debian trixie, has
  `JELLYFIN_FFMPEG=/usr/lib/jellyfin-ffmpeg/ffmpeg`, `JELLYFIN_DATA_DIR=/config` and a server in `/jellyfin`.
  `shim/jellyfin-ffmpeg-upscale` uses `/usr/lib/jellyfin-ffmpeg/ffmpeg`, which matches. This is master, not the
  pinned 12.4.20261026 tag, and I cannot pull the image here.
- P8 NOT VERIFIABLE. `revenant:docs/decisions/jellyfin-plus-e2e.md` is not in the knowledge index. `knowledge-get`
  returned "no indexed file", and searches for the name and the row keywords found no copy. This is load-bearing:
  the 13 rows and their thresholds are the deliverable, and the plan rebuilds them from the brief's list plus
  GPU_VENDORS.md. The orchestrator must supply the doc, or confirm that the rebuilt rows are acceptable.
- P9 INFERRED. It cannot be proved without running the image. It is plausible: the layout matches the live deploy.
- P10 INFERRED. `docs/filters/FSR2.md` describes the out-of-tree build (git clone at HEAD `1680d1ed`,
  `ffmpeg/gen_perm.py`, `glslang-tools`, `g++ ... -DFFX_GCC`, `ar`). The plan picks "a sha256-pinned source
  archive", but this session has no way to compute a sha256 of an external download: no external curl, and `fetch`
  returns text. A hash written into the Dockerfile would therefore be unverified, and a wrong one fails the first
  F3 build. This needs a stated plan, such as the owner supplying the hash or the PR naming it as unverified.
- P11 INFERRED, not load-bearing. A skip-if-absent check is the right behaviour.

## Scope and TDD

- `files` covers every change the plan describes: Dockerfile, entrypoint, bench script, test, GPU_VENDORS.md,
  INSTALL.md, context-map.md. `scripts/build-ffmpeg.sh` and `.github/workflows/` are untouched.
- `tdd: required` is honest: the test is written first, and the test file covers new behaviour.
- The tests would catch real bugs: row count and order drift, a fail row that does not set the exit code, a dropped
  digest or `sha256sum -c`, a parser that diverges from the C# one. The mutation phase exercises the Dockerfile
  checks. The weak spot is that the static checks only test text, so a wrong hash or a wrong file path passes. The
  PR must say so.

## Gaps to fix before implementing

1. The brief says "Pin every download by sha256". `scripts/build-ffmpeg.sh` still makes unpinned downloads
   (`git clone` of Vulkan-Headers, shaderc, libplacebo and nv-codec-headers by tag only, the OIDN tarball at
   line 606 and the FFmpeg tarball at line 661, none followed by `sha256sum -c`), and the plan leaves them as
   "a known gap". The Dockerfile's own `RUN` steps can't pin what the script fetches. The owner should decide
   whether a gap is acceptable or the script gets a pinned-hash change in this PR, which the plan currently
   excludes.
2. The plugin targets net10.0, but the plan never says where the .NET 10 SDK comes from. It has to be a digest-pinned
   base image or a sha256-checked tarball. The Debian build-stage base (`debian:trixie`) also needs a digest pin
   to meet the brief.
3. The final stage needs the extra Jellyfin-side runtime pieces (VA-API and QSV runtime: `libvpl`/`libmfx-gen`,
   `intel-media-va-driver`, Intel compute-runtime for OpenCL). The upstream image installs the compute runtime
   from unpinned debs. The plan lists only "OpenVINO runtime libs". State which of these the image carries and how
   each is pinned.

VERDICT: BLOCKED
