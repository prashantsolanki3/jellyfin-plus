# Intel patched ffmpeg: why its libplacebo output is wrong

**Status (step 5):** `scripts/build-ffmpeg.sh` now builds libplacebo at jellyfin-ffmpeg's pinned commit
`cee9b076f2c6` (not tag `v7.351.0`: the patch's third hunk edits `map_vk_fmt()`, which that tag does not have),
applies `builder/patches/libplacebo/*.patch` (jellyfin's `0001`, libplacebo issue 349) and passes
`--buildtype=release -Dvk-proc-addr=enabled -Dglslang=disabled -Dtests=false -Dbench=false -Dfuzz=false`. The libplacebo
stamp now covers the ref, the patches and the flags. Not yet proven: that the patch applies to that commit and
that it fixes the output. Check after the next Arc image build: bench row 8 (fsr2) and a plain
`hwupload,libplacebo,hwdownload` chain on the patched binary must score close to stock (25.7 dB; patched was 4.73 dB),
`-filters` still lists `oidn ort fsr2`, and `ldd` shows no `not found`. The tables below describe the build as it
was before this change.

Bead rev-hxlit, step 4. Research only: no Dockerfile or script changed. It blocks moving-video fsr2,
because fsr2 exists only in the patched build (`/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg`) and bench row 8
(`scripts/bench-intel.sh:199`, `chain_fsr2`) ends in a plain `hwupload,libplacebo,hwdownload` tail.

## What was measured, and what was not

Measured in the Arc test pod (rev-hxlit comment of 2026-11-25 5:05 am IST; `vulkan=vk:0`, 96 frames, 360p to
1920x858, frame-index pairing; scoring the same files with either binary's `xpsnr` gives the same numbers):

| Binary | `hwupload,libplacebo=...custom_shader_path=FSRCNNX...:format=yuv420p,hwdownload` |
|---|---|
| stock `/usr/lib/jellyfin-ffmpeg/ffmpeg` | 25.70 dB (16-layer shader 25.55) |
| patched `/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg` | 4.73 dB (16-layer shader 4.73) |
| bicubic reference | 37.35 dB |

- The same 4.73 dB for two different shaders says the defect is not in shader compilation or the shader.
  It is in how libplacebo maps and writes frames on this build.
- Stock's 25.7 dB is itself below bicubic over the first frames (a start-of-clip dip, a separate open item),
  so "fixed" means matching stock, not reaching bicubic.
- The served shim is unaffected: plain libplacebo chains already run on the stock binary and only fsr2
  routes to the patched one (`scripts/test_shim_patched_path.py`, 16 PASS).
- NOT measured here: anything inside the test pod (no `kubectl exec` in this session), the exact libplacebo
  version inside the stock binary, and jellyfin-ffmpeg's shaderc and Vulkan-Headers pins. The `ldd` facts
  in the next section come from the brief and the Dockerfile's own `ldd` step, not from a fresh run.

## How the patched ffmpeg is built

`docker/intel/Dockerfile:75-78` runs `GPU_VENDOR=intel WITH_ORT=1 ORT_EP=openvino WITH_FSR2=1
PREFIX=/usr/lib/jellyfin-ffmpeg-oidn bash scripts/build-ffmpeg.sh` on the Jellyfin (Debian trixie) base,
with `libvulkan-dev` and no `libshaderc-dev` (`Dockerfile:43-45`). In `scripts/build-ffmpeg.sh`:

| Piece | What it does | Where |
|---|---|---|
| FFmpeg | vanilla 8.1.2 from the ffmpeg.org tarball | `:109`, `:675-678` |
| FFmpeg patches (Intel) | only `0008` and `0009` (generic hwmap format negotiation) plus the fsr2 patch; no `hwcontext_vulkan.c` or `vf_libplacebo.c` change | `:476`, `:500` |
| Vulkan headers | Vulkan-Headers `v1.3.280` copied into `/usr/local/include`, over the distro `libvulkan-dev` headers | `:546-548` |
| Vulkan loader | the distro `libvulkan.so.1` (`libvulkan-dev` is the only Vulkan package); nothing builds a loader | `Dockerfile:45` |
| shaderc | `v2026.4` built from source, shared, into `/usr/local/lib`; copied into the image as `libshaderc_shared.so.1` by the Dockerfile's `local-libs` step | `:282`, `:576-585`, `Dockerfile:85-88` |
| libplacebo | `v7.351.0`, static: `meson setup --prefix=/usr/local --libdir=lib/x86_64-linux-gnu --default-library=static -Dvulkan=enabled -Dshaderc=enabled -Ddemos=false` and nothing else | `:279`, `:600-604` |
| ffmpeg configure | `--enable-vulkan --enable-libplacebo --enable-libshaderc --enable-vaapi --enable-libvpl --enable-libdrm` | `:929-939`, `:480` |

The libplacebo clone is `--depth 1 -b v7.351.0 --recursive`, so its submodules (glad, the Vulkan registry,
fast_float, xxhash) are whatever that tag pins. Stamp files skip the rebuild when the tag is unchanged.

## How that differs from jellyfin-ffmpeg

Stock ships libplacebo as `libplacebo.so.360` with its own libvulkan and shaderc under
`/usr/lib/jellyfin-ffmpeg/lib` (from the brief). The soname number is libplacebo's API version, so
360 is newer than the patched build's 351 (inferred from the naming, not read out of the binary).

jellyfin-ffmpeg's builder script (`builder/scripts.d/50-libplacebo.sh` on the `jellyfin` branch, fetched
2026-11-25; the tag the base image ships was not separately checked) builds libplacebo differently:

| | patched build here | jellyfin-ffmpeg |
|---|---|---|
| libplacebo | tag `v7.351.0` | pinned commit `cee9b076f2c6` |
| libplacebo patches | none | `builder/patches/libplacebo/` (at the time of the fetch `0001`, "libav/utils: fix non-multiplane fmt bit_shift in pl_map_avframe*", libplacebo issue 349) |
| meson flags | `-Dvulkan -Dshaderc -Ddemos=false` | adds `--buildtype=release`, `-Dvk-proc-addr=enabled`, `-Dvulkan-registry=<vk.xml>`, `-Dglslang=disabled`, tests/bench/fuzz off |
| FFmpeg | vanilla 8.1.2 | 8.1.x with jellyfin's own Vulkan and libplacebo patches on top |
| libvulkan, shaderc | distro loader, shaderc v2026.4 in `/usr/local` | bundled beside the binary |
| Vulkan headers | v1.3.280 | not retrieved (the likely script names returned 404) |

## Most likely cause (hypothesis, not proven)

**The patched build's libplacebo plus vanilla FFmpeg Vulkan code map and write yuv420p Vulkan frames
wrongly on ANV, because it lacks the fixes jellyfin-ffmpeg carries.**

Pointing to it:
- It is the one area where the two builds clearly differ (older libplacebo, no jellyfin libplacebo patch,
  vanilla FFmpeg Vulkan), and the patch jellyfin carries is exactly a frame-mapping fix (`pl_map_avframe`).
- `docs/architecture/GPU_VENDORS.md` ("Chain tail") already records ANV returning wrong output for
  libplacebo's multi-planar YUV: green frames, later gone on a newer driver. The same area is fragile.
- The error is deterministic and independent of the shader (4.73 dB for both).

Pointing away, or still open:
- The shaderc and loader set differ too (second candidate), so a loader or shader-compiler problem is not excluded.
- The bench uses `vulkan=vk:0`, the served path `vulkan=vk@dr` (render node); a device difference is a confound
  for any comparison between the two.
- The stock number is also poor early in the clip, so some of the gap could be a shared timing effect.
  It cannot explain 25.7 against 4.7.

## Experiments that settle it (Arc test pod, one chain at a time, both binaries)

1. Round trip with no libplacebo: `format=yuv420p,hwupload,hwdownload,format=yuv420p`. If the patched
   binary is already wrong here, the fault is FFmpeg's Vulkan upload/download, not libplacebo.
2. The failing chain with the multi-planar path switched off (FFmpeg's Vulkan device option
   `disable_multiplane=1`, appended to the `-init_hw_device vulkan=` argument) and, separately, with
   `libplacebo=...:format=rgba` instead of `yuv420p`, to see whether only the multi-planar mapping is wrong
   (the RGBA download loss recorded in GPU_VENDORS.md is a separate open item).
3. Library swap: run the patched binary with `LD_LIBRARY_PATH=/usr/lib/jellyfin-ffmpeg/lib` so it loads
   stock's libvulkan and shaderc. A fix points at the loader or shaderc, not libplacebo.
4. `ldd` on both binaries and `strings ... | grep -i libplacebo` for the versions actually in use.
5. Run both binaries with `vulkan=vk@dr` as well as `vk:0`, to rule out the device-selection confound.

## Proposed fix, in order (a later PR; this one does not touch the Dockerfile)

1. Run experiments 1-5 first. Each is minutes, and they separate libplacebo from FFmpeg Vulkan from the loader.
2. If libplacebo is the cause (the likely result): in `scripts/build-ffmpeg.sh` build libplacebo at
   jellyfin-ffmpeg's pinned commit, apply its libplacebo patch, and use its meson flags (`vk-proc-addr`,
   `vulkan-registry`, `glslang=disabled`). Take the pin and patch from the jellyfin-ffmpeg tag the base image
   ships (so the two builds move together), and align the Vulkan-Headers and shaderc versions to that tag's
   builder scripts. Check `-filters` and re-run bench row 8 against the stock-binary baseline.
3. If that does not fix it, the cause is FFmpeg's own Vulkan code: carry jellyfin-ffmpeg's `hwcontext_vulkan`
   and `vf_libplacebo` patches into `ffmpeg/`, or use jellyfin-ffmpeg's source tree as the base.
4. Fallback that needs no rebuild: fsr2 hands system-memory frames on, so the shim could split the chain,
   with fsr2 on the patched binary and the libplacebo tail on stock. It costs one extra process and a pipe.

Whichever is chosen, `nlmeans_vulkan` must still compile on the patched build: the reason shaderc was built
from source (`scripts/build-ffmpeg.sh:551-559`), so do not drop to the distro shaderc to match stock.
