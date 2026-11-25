# Intel patched ffmpeg: why its libplacebo output is wrong

**Root cause (proven in the Arc test pod, image 10a6474, 96 frames, 360p to 1920x858):** the system Vulkan loader
and/or the `/usr/local` shaderc v2026.4 that the patched binary links, not libplacebo and not FFmpeg. The same
`/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg` and the same libplacebo chain:

| Run | Score |
|---|---|
| patched binary as built | 4.73 dB |
| same binary, `LD_LIBRARY_PATH=/usr/lib/jellyfin-ffmpeg/lib` | 25.70 dB, identical to stock |
| upload and download only, no libplacebo | 37.35 dB (fine) |

**Fix:** `scripts/build-ffmpeg.sh` (Intel only) puts `/usr/lib/jellyfin-ffmpeg/lib` first in the binary's RUNPATH
(`STOCK_FFMPEG_LIB`, last `LDFLAGS` prepend), reads it back from `readelf -d` and dies if it is not first, and
`docker/intel/Dockerfile` requires `ldd` to resolve `libvulkan.so.1` and `libshaderc_shared.so.1` from there
whenever the base ships them. `scripts/test_build_ffmpeg_vendor.py` pins the wiring. Not yet proven: that a RUNPATH
is as good as the `LD_LIBRARY_PATH` of the experiment (RUNPATH is not transitive, so libraries the Vulkan loader
dlopens still resolve their own dependencies as before). Check after the next image build: bench row 8 (fsr2) and a
plain `hwupload,libplacebo,hwdownload` chain on the patched binary score close to stock, and the QSV and VAAPI rows
still pass (the bundled libva, libvpl and libdrm now win too). If the RUNPATH is not enough, the fallback is the
shim and the bench exporting `LD_LIBRARY_PATH` for the patched binary.

The libplacebo-version hypothesis below is therefore **not** the cause; it is kept as the record of what was
suspected. The `libplacebo` pin and patch from step 5 stay in the build, unproven as to any effect.

**Status (step 5):** `scripts/build-ffmpeg.sh` now builds libplacebo at jellyfin-ffmpeg's pinned commit
`cee9b076f2c6` (not tag `v7.351.0`: the patch's third hunk edits `map_vk_fmt()`, which that tag does not have),
applies `builder/patches/libplacebo/*.patch` (jellyfin's `0001`, libplacebo issue 349) and passes
`--buildtype=release -Dvk-proc-addr=enabled -Dglslang=disabled -Dtests=false -Dbench=false -Dfuzz=false`. The libplacebo
stamp now covers the ref, the patches and the flags. Not yet proven: that the patch applies to that commit and
that it fixes the output. Check after the next Arc image build: bench row 8 (fsr2) and a plain
`hwupload,libplacebo,hwdownload` chain on the patched binary must score close to stock (25.7 dB; patched was 4.73 dB),
`-filters` still lists `oidn ort fsr2`, and `ldd` shows no `not found`. The tables below describe the build as it
was before this change.

## fsr2 without CUDA on moving content (bead rev-cpmuj, bench row 8: -38.10 dB)

With the libplacebo tail fixed (above), what is left is inside `fsr2`. Read from source; **not reproduced here** (no
Arc, no clips in this session), so the cause is the likely one, not a measured one.

**Likely cause: the no-CUDA build feeds FSR2 zero motion vectors, and a pan is nothing but motion.** What FSR2 needs
per frame, and what the Intel build supplies:

| FSR2 input | Needs | Intel build (`-DGU_NO_NVOF`, `scripts/build-ffmpeg.sh:507`) | Where |
|---|---|---|---|
| motion vectors | per-pixel, previous position of each pixel | **all zero, every frame**: `memset(g->flow, 0, ...)` | `ffmpeg/gu_inputs.h:996`, uploaded at `ffmpeg/vf_fsr2.c:690` |
| history reset | on a cut or first frame | only frames 0 and 1 (`frame_index <= 1`); no scene-cut reset | `vf_fsr2.c:715` |
| jitter | the sub-pixel offsets the renderer used | the fractional part of a global phase-correlation shift, which on a pan is the pan | `gu_inputs.h:959-964`, `vf_fsr2.c:705-706` |
| depth | real depth, for disocclusion and MV dilation | constant 0.5, so those passes are no-ops | `gu_inputs.h:1017` |
| reactive mask | where history is untrustworthy | all zero (it is built from NVOFA flow) | `gu_inputs.h:1005` |

With zero vectors FSR2 reprojects its history to the same pixel, so on a pan it blends each frame with the previous one
shifted by the pan. Nothing else (no depth, no reactive mask, no reset) tells it the history is wrong, so the output is
ghosted and smeared; the jitter it is also given (the pan's fractional part) is wrong in the same direction. A fixed
camera is the one case where zero vectors are true, which is why a still is fine. The score is also an inconsistency
to note: -38.10 dB below bicubic implies an absolute fsr2 score near zero if bicubic is in the usual 35-40 dB range,
which is more broken than ghosting alone usually reads (about 15-25 dB). The run-20 absolute scores are not in this
repo, so a second fault (frame pairing, range) is not excluded; the per-frame score below settles it.

**Row 7 does not show the still case is right.** It compares consecutive output frames of a looped still
(`tblend=difference`, `scripts/bench-intel.sh` `cmd_still`), so it passes for any output that is stable, correct or not.

**Recording the probe.** `scripts/bench-intel.sh` row 8 now keeps xpsnr's per-frame luma stats for each candidate
(`$BENCH_OUT/.../xpsnr-pan-fsr2.mkv.txt`) and a failing row 8 prints `frame1=X dB frames2+=Y dB`. Frame 1 has no history,
so a good `frame1` with a collapsed `frames2+` confirms the cause is temporal (zero vectors, history); a bad `frame1` too
means the fault is in the per-frame path (packing, colour range, pairing). `scripts/test_bench_intel.py` covers the
format. Not yet run on the Arc.

**Decision (bead rev-sr6bf): the shim no longer offers fsr2 on Intel, and bench row 8 is SKIP.** PR 47 found the cause
(zero motion vectors, above). `UpscaleEngine.NoCudaGameEnabled` is false and nothing in production sets it, so
`NoCudaGameBuilt()` is false on Intel and fsr2 is withheld ("needs NVIDIA") exactly as on a build with no marker.
`BENCH_FSR2=1 scripts/bench-intel.sh` brings row 8 and the fsr2 half of row 1 back for the experiment. Still open, owner
choice: remove `fsr2-nocuda` from the image itself. `docker/intel/Dockerfile:208` and
`.github/workflows/build-arc-image.yml` (the marker and `fsr2` checks) require both today, so that needs a workflow edit
by the owner. Not run: feeding `mestimate` (CPU), a Vulkan block-matching flow shader, or VA-API/QSV encoder motion
estimation (FEI, deprecated) into fsr2 on a 10 s fixture, which would report per-frame XPSNR and fps.

**Recommendation: ship the image without fsr2-nocuda** (the row's own threshold text says a miss means exactly this).
No fix is shipped here, because the one candidate is not small and cannot be shown to help:
a uniform motion field built from the phase-correlation shift (already measured, `gu_inputs.h:245`) would fix pans but
not local motion, and even exact vectors cannot reach the pass line: with a near-null jitter sequence FSR2 is a temporal
denoise plus a fixed Lanczos upsample (`vf_fsr2.c:40-46`), which is not expected to beat bicubic by itself. If someone
wants the experiment: in the `GU_NO_NVOF` branch write `(jitter_x, jitter_y)` into `g->flow` when `pcpeak` is high and
force `jitterOffset` to zero, then re-run row 8 and read `frames2+`. The served shim's stock path is untouched.

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

## Earlier hypothesis (superseded: libplacebo is not the cause, see the top of this page)

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

## Proposed fix, in order (superseded: experiment 3 settled it, the RUNPATH above is the fix)

1. Run experiments 1-5 first. Each is minutes, and they separate libplacebo from FFmpeg Vulkan from the loader.
   Experiment 3 and the no-libplacebo round trip were run: see the table at the top.
2. If libplacebo had been the cause: in `scripts/build-ffmpeg.sh` build libplacebo at
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
