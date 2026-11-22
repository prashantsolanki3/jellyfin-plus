#!/usr/bin/env python3
"""scripts/install-shaders.sh derives the RCAS shaders through shaders/make-rcas.sh.

    python3 -I scripts/test_install_shaders.py

Needs no GPU, docker or network. Every fetch in install-shaders.sh is replaced by its local-copy
override, and each stub carries only the marker lines the script greps for. It proves:

  - the script finds make-rcas.sh (it once looked in scripts/ and died with "No such file");
  - RCAS-2.0 / 1.7 / 1.4 land in the output dir, non-empty, each with its own SHARPNESS;
  - each keeps the MIT attribution of the FSR.glsl it came from, and no EASUTEX reference is left;
  - two runs are byte-identical;
  - mutation: an FSR.glsl with no second //!HOOK block, or no licence text, is refused.

Exit status: 0 every check passed, 1 a check failed.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BASH = shutil.which("bash")
INSTALL = os.path.join(HERE, "install-shaders.sh")
MAKE_RCAS = os.path.join(REPO, "shaders", "make-rcas.sh")

MIT = "Permission is hereby granted, free of charge, to any person obtaining a copy"
FSR_HEADER = (
    "// FidelityFX FSR v1.0.2 by AMD\n"
    "// Copyright (c) 2021 Advanced Micro Devices, Inc. All rights reserved.\n"
    f"// {MIT}\n"
    "// of this software, subject to the conditions of the MIT licence.\n"
    "\n"
)
FSR_BODY = (
    "//!DESC FidelityFX Super Resolution v1.0.2 (EASU)\n"
    "//!HOOK LUMA\n"
    "//!BIND HOOKED\n"
    "//!SAVE EASUTEX\n"
    "//!WIDTH OUTPUT.w\n"
    "vec4 hook() { return HOOKED_tex(HOOKED_pos); }\n"
    "//!DESC FidelityFX Super Resolution v1.0.2 (RCAS)\n"
    "//!HOOK LUMA\n"
    "//!BIND EASUTEX\n"
    "//!WIDTH EASUTEX.w\n"
    "//!HEIGHT EASUTEX.h\n"
    "#define SHARPNESS 0.2 // lower is sharper\n"
    "vec4 hook() { return EASUTEX_tex(EASUTEX_pos); }\n"
)
FSR_STUB = FSR_HEADER + FSR_BODY

LGPL = "// GNU Lesser General Public License\n"
NIS_SCALER = f"// NVIDIA Image Scaling v1.0.2\n// {MIT}\n//!HOOK LUMA\nvoid hook() {{}}\n"
NIS_SHARPEN = (f"// NVIDIA Image Scaling v1.0.2\n// {MIT}\n//!HOOK LUMA\n"
               "//!WHEN OUTPUT.w LUMA.w / 1.0 > !\n#define SHARPNESS 0.5\nvoid hook() {}\n")
SSSR = LGPL + "// SSimSuperRes by Shiandow\n//!HOOK POSTKERNEL\n//!WHEN NATIVE_CROPPED.h OUTPUT.h <\n"
KRIG = LGPL + "// KrigBilateral by Shiandow\n//!HOOK CHROMA\n//!WHEN CHROMA.w LUMA.w <\n"
RAVU = (LGPL + "//!DESC RAVU-Zoom (luma, r3)\n//!HOOK LUMA\n"
        "//!WHEN HOOKED.w OUTPUT.w < HOOKED.h OUTPUT.h < *\n")
CUNNY = (LGPL + "// CuNNy (dp4a) by funnyplanter\n//!HOOK LUMA\n"
         "//!WHEN OUTPUT.w LUMA.w / 1.3 > OUTPUT.h LUMA.h / 1.3 > *\n")
CUNNY_FILES = ["CuNNy-fast-SOFT-Q.glsl", "CuNNy-4x16-SOFT-Q.glsl",
               "CuNNy-4x32-SOFT-Q.glsl", "CuNNy-4x16-DS-Q.glsl"]

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"  -- {detail}"))
    if not ok:
        FAILS.append(name)


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def stubs(root, fsr=FSR_STUB):
    """Write every stub input under root and return the env that points install-shaders.sh at them."""
    nis, cunny = os.path.join(root, "nis"), os.path.join(root, "cunny")
    os.makedirs(nis)
    os.makedirs(cunny)
    write(os.path.join(root, "FSR.glsl"), fsr)
    write(os.path.join(nis, "NVScaler.glsl"), NIS_SCALER)
    write(os.path.join(nis, "NVSharpen.glsl"), NIS_SHARPEN)
    for name in CUNNY_FILES:
        write(os.path.join(cunny, name), CUNNY)
    write(os.path.join(root, "sssr.glsl"), SSSR)
    write(os.path.join(root, "krig.glsl"), KRIG)
    write(os.path.join(root, "ravu.glsl"), RAVU)
    env = dict(os.environ)
    env.update(FSR_GLSL=os.path.join(root, "FSR.glsl"), NIS_DIR=nis, CUNNY_DIR=cunny,
               SSSR_GLSL=os.path.join(root, "sssr.glsl"), KRIG_GLSL=os.path.join(root, "krig.glsl"),
               RAVU_ZOOM_HOOK=os.path.join(root, "ravu.glsl"))
    return env


def install(env, out):
    return subprocess.run([BASH, INSTALL, out], env=env, capture_output=True, text=True, timeout=120)


def main():
    check("bash is available", BASH is not None)
    check("shaders/make-rcas.sh exists", os.path.isfile(MAKE_RCAS))
    if FAILS:
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        env = stubs(os.path.join(tmp, "in"))
        out1, out2 = os.path.join(tmp, "out1"), os.path.join(tmp, "out2")
        r = install(env, out1)
        check("install-shaders.sh exits 0 on stub inputs", r.returncode == 0, r.stderr.strip()[-300:])
        check("it does not look for make-rcas.sh in the wrong directory",
              "No such file" not in r.stderr, r.stderr.strip()[-300:])

        for s in ("2.0", "1.7", "1.4"):
            p = os.path.join(out1, f"RCAS-{s}.glsl")
            text = read(p) if os.path.isfile(p) else ""
            check(f"RCAS-{s}.glsl is installed and non-empty", bool(text.strip()))
            check(f"RCAS-{s}.glsl has SHARPNESS {s}", f"#define SHARPNESS {s} " in text)
            check(f"RCAS-{s}.glsl keeps the MIT attribution", MIT in text)
            check(f"RCAS-{s}.glsl keeps the FSR copyright line", "Copyright (c) 2021 Advanced Micro" in text)
            check(f"RCAS-{s}.glsl has no EASUTEX left", "EASUTEX" not in text)
            check(f"RCAS-{s}.glsl is the RCAS pass only", "(EASU)" not in text and "SAVE" not in text)
            check(f"RCAS-{s}.glsl reads HOOKED", "//!BIND HOOKED" in text and "HOOKED_tex" in text)

        r2 = install(env, out2)
        check("a second run also exits 0", r2.returncode == 0, r2.stderr.strip()[-300:])
        same = all(
            os.path.isfile(os.path.join(out1, f"RCAS-{s}.glsl"))
            and os.path.isfile(os.path.join(out2, f"RCAS-{s}.glsl"))
            and read(os.path.join(out1, f"RCAS-{s}.glsl")) == read(os.path.join(out2, f"RCAS-{s}.glsl"))
            for s in ("2.0", "1.7", "1.4"))
        check("two runs give byte-identical RCAS files", same)

        # mutation: an FSR.glsl that is not the shape make-rcas.sh expects must be refused.
        mut = os.path.join(tmp, "mut")
        easu_only = FSR_BODY[:FSR_BODY.index("//!DESC", 1)]
        env_m = stubs(os.path.join(mut, "in"), fsr=FSR_HEADER + easu_only)
        r = install(env_m, os.path.join(mut, "out"))
        check("mutation: no second //!HOOK block is refused", r.returncode != 0, f"rc={r.returncode}")

        nolic = os.path.join(tmp, "nolic")
        os.makedirs(nolic)
        write(os.path.join(nolic, "FSR.glsl"), FSR_STUB.replace(MIT, "no licence here"))
        r = subprocess.run([BASH, MAKE_RCAS, os.path.join(nolic, "FSR.glsl"), nolic],
                           capture_output=True, text=True, timeout=60)
        check("mutation: make-rcas.sh refuses an FSR.glsl with no MIT text", r.returncode != 0,
              f"rc={r.returncode}")

    print()
    print("FAILED: " + ", ".join(FAILS) if FAILS else "all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
