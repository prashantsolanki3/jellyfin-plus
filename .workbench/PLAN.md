# Plan: pin NVScaler/NVSharpen to the v1.0.2 gist revision

Root cause (checked against the GitHub gist API, not memory): the check pattern is right and the fetch
is the bug. `install-shaders.sh` fetches `.../7e89.../raw/NVScaler.glsl` with no revision, so it gets the
gist head. agyild pushed two revisions in the last two weeks (65bd0ef 2026-11-12, 9d270a4 2026-11-17);
head is now "NVIDIA Image Scaling v1.0.3". The v1.0.2 file is gist revision
`05f00864228871ffd157daa9beb2db8fa7412cfa` (2022-02-24), header `// NVIDIA Image Scaling v1.0.2 by NVIDIA`.

Do NOT just bump the check to v1.0.3. In head, NVSharpen.glsl drops `#define SHARPNESS` for
`//!PARAM Sharpness`, so the script's `sed s/^#define SHARPNESS .*/` would silently do nothing and
NVSharpen-0.25 and NVSharpen-0.65 would be identical (the "installed, never effective" failure this repo
keeps hitting). Rev 05f0086 matches every assumption in the script: NVSharpen has `#define SHARPNESS 0.25`
and a `//!WHEN` guard, NVScaler has `#define SHARPNESS 0.25`, and ShaderLibrary.cs describes v1.0.2.
Pinning to it keeps behaviour identical to what was benchmarked.

Changes:
1. `scripts/install-shaders.sh`: default `NIS_URL` becomes the revision-pinned
   `.../raw/05f00864228871ffd157daa9beb2db8fa7412cfa` (still overridable by `NIS_GLSL_URL`). Add
   expected sha256 for NVScaler.glsl and NVSharpen.glsl (overridable by env), verified with `sha256sum`
   on the fetched (network) path only; `NIS_DIR` local copies keep the existing marker checks. Tighten the
   version grep to the real header line (`^// NVIDIA Image Scaling v1\.0\.2 by NVIDIA`, dots escaped) and
   add a check that NVSharpen still contains `#define SHARPNESS`, so the sed cannot be a silent no-op.
2. `scripts/test_install_shaders.py`: add a `NIS_SCALER_BAD` (v1.0.3 header) stub, a mutation expecting a
   non-zero exit with the "is not NVIDIA Image Scaling v1.0.2" message, a stub whose NVSharpen lacks
   `#define SHARPNESS` (refused), and the sha path exercised through `NIS_GLSL_URL=file://...` (curl
   supports file://) with a matching and a mismatching digest passed via the override env vars. Update the
   stub header to the real `... v1.0.2 by NVIDIA` line. TDD: write the new tests first, see them fail.
3. Doc touch only if a sentence says the fetch tracks upstream head (video-enhancement.md lines ~127/337
   say "v1.0.2" already; likely no change).

NOT VERIFIED / risk: the sha256 values. I have no network from the shell and no way to hash a file here
(the allowed commands are only repo scripts), and the fetch tool returns JSON-escaped text, so I cannot
produce the digest by hand. The implement stage must try to obtain them (a throwaway uncommitted
`scripts/test_*.py` using hashlib/urllib on the pinned raw URLs, if egress exists). If no egress, the
digests cannot be filled honestly: report `blocked_on_owner` for the two constants rather than commit
guessed values, and ship the revision pin + stricter checks + the env-configurable sha check (the test
proves the mechanism). Also not verified: that the arc image build then passes the rest of the script.

```yaml
{
  "goal": "Pin NVScaler.glsl/NVSharpen.glsl in install-shaders.sh to gist revision 05f0086 (v1.0.2) with sha256 checks and a precise version check, so upstream gist head drift cannot break the build or silently neuter NVSharpen.",
  "files": ["scripts/install-shaders.sh", "scripts/test_install_shaders.py"],
  "test_commands": ["python3 scripts/test_install_shaders.py", "pre-commit run --files scripts/install-shaders.sh scripts/test_install_shaders.py"],
  "tdd": "required",
  "fast_path": false,
  "reuse_check": [
    {"need": "immutable fetch of a gist file", "checked": "gist raw URL with revision SHA path segment (GitHub gist API, commits endpoint, revision 05f00864228871ffd157daa9beb2db8fa7412cfa)", "verdict": "use", "reason": ""},
    {"need": "sha256 verification of a fetched file", "checked": "coreutils sha256sum -c, same idiom as scripts/build-ffmpeg.sh ORT_OPENVINO_SHA256 and scripts/test-vf-ort.sh", "verdict": "use", "reason": ""}
  ],
  "acceptance": [
    "install-shaders.sh fetches NVScaler.glsl and NVSharpen.glsl from a URL containing the gist revision SHA, not the moving head",
    "a sha256 is checked on the fetched NVScaler.glsl and NVSharpen.glsl and a mismatch exits non-zero",
    "the version check matches the real header '// NVIDIA Image Scaling v1.0.2 by NVIDIA' of the pinned file; a v1.0.3 file is refused with the 'is not NVIDIA Image Scaling v1.0.2' message",
    "test_install_shaders.py has a correct-header stub that passes and a wrong-header stub that fails, and python3 scripts/test_install_shaders.py passes",
    "NVSharpen without '#define SHARPNESS' is refused instead of silently producing identical 0.25/0.65 files"
  ],
  "premises": [
    {"id": "P1", "claim": "Gist head NVScaler.glsl is now 'NVIDIA Image Scaling v1.0.3' (revisions 2026-11-12 and 2026-11-17), so the v1.0.2 grep fails", "label": "VERIFIED", "check": "fetch of api.github.com/gists/7e8951915b2bf24526a9343d951db214/9d270a44... shows header 'v1.0.3 by NVIDIA'; commits endpoint lists the two new revisions", "load_bearing": true},
    {"id": "P2", "claim": "Revision 05f00864228871ffd157daa9beb2db8fa7412cfa holds NVScaler.glsl and NVSharpen.glsl both headed v1.0.2, both with '#define SHARPNESS 0.25', NVSharpen with a //!WHEN guard", "label": "VERIFIED", "check": "fetch of api.github.com/gists/7e89.../05f00864... (content fields read)", "load_bearing": true},
    {"id": "P3", "claim": "Head NVSharpen uses //!PARAM Sharpness and no '#define SHARPNESS', so tracking head would silently break the sed", "label": "VERIFIED", "check": "head revision content read via the gist API", "load_bearing": true},
    {"id": "P4", "claim": "The raw URL form https://gist.githubusercontent.com/agyild/<id>/raw/<revision-sha>/<file> serves that revision", "label": "INFERRED", "check": "curl of the URL from a networked host (the API's own raw_url fields use the blob-hash variant of this form); the implement stage should use whichever form the API returned as raw_url if it can only verify that", "load_bearing": true},
    {"id": "P5", "claim": "The sha256 of the two pinned files can be computed in the implement stage", "label": "INFERRED", "check": "needs egress plus hashlib; the shell here has no network and no hashing command. If unavailable, report blocked_on_owner for the digests", "load_bearing": true},
    {"id": "P6", "claim": "curl supports file:// URLs so the test can exercise the fetch+sha path offline", "label": "INFERRED", "check": "run the new test; curl is built with file protocol by default", "load_bearing": false},
    {"id": "P7", "claim": "No other file consumes NIS_GLSL_URL or depends on the unpinned fetch (workflow, docs)", "label": "VERIFIED", "check": "Grep for NIS_GLSL_URL and install-shaders across the repo: only install-shaders.sh, proxmox-build.sh, docs references; build-arc-image.yml does not mention it", "load_bearing": false}
  ]
}
```
