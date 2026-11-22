# Plan: FSRCNNX licence check rejects the real igv file

The check at `scripts/install-shaders.sh:289` greps `'GNU Lesser General Public License'`. The real igv header wraps after "Public", so the line never contains "License". Every other LGPL check in the script (lines 138, 164, 194, 234) greps `'GNU Lesser General Public'`.

1. Test first. In `scripts/test_install_shaders.py`, change the `FSRCNNX` stub (line 74) to the real five-line wrapped header. It keeps the `Copyright (C) 2017-2021 igv` line and the `//!HOOK LUMA` body. Run the test and see "LGPL header missing" fail the install on today's code.
2. Fix. Change line 289 of `install-shaders.sh` to `grep -q 'GNU Lesser General Public'`. The test passes.
3. The existing mutations still bite on the wrapped stub. "GNU Lesser" becomes "GNU Lesser-less", which breaks the match. "igv" becomes "someone", which breaks the copyright match.

Anime4K stub check. I fetched the real `Anime4K_Upscale_CNN_x2_S.glsl` at commit 4029bf70. It has `// MIT License`, `// Copyright (c) 2019-2021 bloc97`, and `// Permission is hereby granted, free of charge, to any person obtaining a copy`. It also has `//!DESC Anime4K-v3.2-Upscale-CNN-x2-(S)-Conv-4x3x3x3` and a line-start `//!HOOK MAIN`. Every string the script greps for is present, and the stub contains the same strings. The stub layout is faithful enough, so no change is needed. The real file's licence blocks are blank-line separated, which does not affect the greps.

I could not fetch the real FSRCNNX file. GitHub's robots.txt blocks the fetch tool. The wrapped header therefore comes from the bead's evidence and is not independently verified. The sha256 pins already passed in the failing build.

Scope: two files, no workflow edits, and the build is not dispatched.

```yaml
{
  "goal": "install-shaders.sh accepts the real wrapped igv LGPL header in FSRCNNX files, proved by a test stub that uses that real header",
  "files": ["scripts/install-shaders.sh", "scripts/test_install_shaders.py"],
  "test_commands": ["python3 scripts/test_install_shaders.py"],
  "tdd": "required",
  "fast_path": false,
  "reuse_check": {"need": "licence header check", "checked": "existing grep 'GNU Lesser General Public' in install-shaders.sh lines 138/164/194/234", "verdict": "use", "reason": ""},
  "acceptance": [
    "install-shaders.sh line 289 greps 'GNU Lesser General Public'",
    "the FSRCNNX stub in scripts/test_install_shaders.py uses the real 5-line wrapped igv header",
    "the test fails on the old grep with the new stub and passes after the fix",
    "the FSRCNNX no-LGPL and no-igv mutations still refuse",
    "the Anime4K stub is confirmed to match the real header strings at commit 4029bf70"
  ],
  "premises": [
    {"id": "P1", "claim": "line 289 greps the unwrapped string 'GNU Lesser General Public License' while the other four LGPL checks grep 'GNU Lesser General Public'", "label": "VERIFIED", "check": "Grep of scripts/install-shaders.sh", "load_bearing": true},
    {"id": "P2", "claim": "the real igv FSRCNNX header wraps 'General Public' / 'License' across lines", "label": "INFERRED", "check": "bead evidence only; the GitHub release download is blocked by robots.txt for the fetch tool; settle with the Arc build's next run", "load_bearing": true},
    {"id": "P3", "claim": "the real Anime4K S file contains the licence text, the bloc97 copyright, the DESC line and a line-start //!HOOK MAIN, as the stub does", "label": "VERIFIED", "check": "mcp fetch of raw.githubusercontent.com/bloc97/Anime4K/4029bf70.../Anime4K_Upscale_CNN_x2_S.glsl", "load_bearing": false},
    {"id": "P4", "claim": "the existing test passes on today's code and its LGPL mutation still fails with the wrapped stub", "label": "VERIFIED", "check": "python3 scripts/test_install_shaders.py -> all checks passed; mutation replaces 'GNU Lesser' so the new grep fails on it", "load_bearing": false}
  ]
}
```
