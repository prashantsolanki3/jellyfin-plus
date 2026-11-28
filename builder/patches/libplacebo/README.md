# libplacebo patches

`scripts/build-ffmpeg.sh` applies every `*.patch` here to its libplacebo checkout with `patch -p1`, in name
order, before `meson setup`.

| Patch | Source | Why |
|---|---|---|
| `0001-libav-utils-fix-non-multiplane-fmt-bit_shift-in-pl_m.patch` | copied unmodified from jellyfin-ffmpeg, `builder/patches/libplacebo/` on the `jellyfin` branch (https://github.com/jellyfin/jellyfin-ffmpeg), by nyanmisaka | fixes libplacebo issue 349 (https://github.com/haasn/libplacebo/issues/349): wrong `bit_shift` for non-multiplane formats in `pl_map_avframe*`. Without it the patched Intel ffmpeg's libplacebo chain scored 4.7 dB against 25.7 dB on stock (`docs/intel-patched-ffmpeg-libplacebo.md`). |

The patch applies to the commit jellyfin-ffmpeg pins (`LIBPLACEBO_REF` in the build script), not to tag
`v7.351.0`. Move the two together.
