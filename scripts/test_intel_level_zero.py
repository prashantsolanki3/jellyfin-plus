#!/usr/bin/env python3
"""docker/intel/Dockerfile installs the Level Zero loader and GPU driver, and fails the build without them.

    python3 -I scripts/test_intel_level_zero.py

An earlier bench run printed level_zero_loader=MISSING on the Arc image. Needs no docker, GPU or network: it reads
the Dockerfile text and proves each rule with a mutant that must be caught. It does not build the image
and does not run OpenVINO's GPU device query (that needs the Arc card). Exit status: 0 every check
passed, 1 a check failed.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DOCKERFILE = os.path.join(REPO, "docker", "intel", "Dockerfile")
DOC = os.path.join(REPO, "docs", "intel-patched-ffmpeg-libplacebo.md")

VERSION = "26.35.39758.10-0"
SHA256 = "c19a641b953d55aebbf1d51bec364a84bf629f985e02fbbe6dc70224c0e88470"
PLUGIN = "/usr/lib/jellyfin-ffmpeg-oidn/ort/lib/libopenvino_intel_gpu_plugin.so"

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        FAILED += 1


def runs(text):
    return re.findall(r"^RUN (?:[^\n]*\\\n)*[^\n]*", text, re.M)


def failures(df):
    bad = []
    stages = df.split("\nFROM ")
    build, final = stages[-2], stages[-1]
    if not re.search(rf"^ARG LIBZE_VERSION={re.escape(VERSION)}$", build, re.M):
        bad.append("LIBZE_VERSION is not pinned to the compute-runtime release of the base's OpenCL driver")
    if not re.search(rf"^ARG LIBZE_DEB_SHA256={SHA256}$", build, re.M):
        bad.append("the deb's sha256 is not pinned")
    if not re.search(r"^ARG LIBZE_DEB_URL=https://github\.com/intel/compute-runtime/releases/download/\S*libze-intel-gpu1_\$\{LIBZE_VERSION\}_amd64\.deb$",
                     build, re.M):
        bad.append("the deb is not fetched from the intel/compute-runtime release")
    fetch = next((r for r in runs(build) if "$LIBZE_DEB_URL" in r), "")
    if not fetch:
        bad.append("the build stage does not download the deb")
    elif not re.search(r'echo "\$LIBZE_DEB_SHA256 +/out/libze-intel-gpu1\.deb" \| sha256sum -c -', fetch) \
            or fetch.find("sha256sum -c") < fetch.find("curl"):
        bad.append("the deb is not checked against its sha256 after the download")

    fr = runs(final)
    install = next((r for r in fr if "libze1" in r and "apt-get install" in r), "")
    if not install:
        bad.append("the final stage does not apt-get install libze1")
    else:
        if "--no-install-recommends" not in install or "/tmp/libze-intel-gpu1.deb" not in install:
            bad.append("the install is not --no-install-recommends of libze1 plus the staged deb")
        if "apt-get update" not in install or install.find("apt-get update") > install.find("apt-get install"):
            bad.append("the install RUN does not run its own apt-get update first")
        if "rm -rf /var/lib/apt/lists/*" not in install:
            bad.append("the install does not clean the apt lists")
        if "ldconfig" in install or "grep 'not found'" in install or "mesa-vulkan-drivers" in install:
            bad.append("the install RUN holds a string test_bench_intel.py keys on (ldconfig, grep 'not found', mesa-vulkan-drivers)")
        copy = final.find("COPY --from=build /out/libze-intel-gpu1.deb /tmp/libze-intel-gpu1.deb")
        driver = next((r for r in fr if "mesa-vulkan-drivers" in r), "")
        local = final.find("COPY --from=build /out/local-libs/")
        if not (0 <= copy < final.find(install)):
            bad.append("the deb is not copied into the final stage before the install")
        if not driver or final.find(driver) > final.find(install):
            bad.append("the install RUN is not after the driver RUN")
        if local < 0 or final.find(install) > local:
            bad.append("the install RUN is not before the local-libs COPY")

    gate = next((r for r in fr if "libze_loader.so.1" in r and "dpkg-query" in r), "")
    if not gate:
        bad.append("no acceptance RUN checks libze_loader.so.1")
    else:
        for need, why in [
            ("ldconfig -p", "reads the linker cache"),
            ("libze_intel_gpu.so.1", "names the driver library"),
            (PLUGIN, "names the OpenVINO GPU plugin by its full path"),
            ("libze-intel-gpu1", "reads the driver package version"),
            ("-f='${Version}' intel-opencl-icd)", "reads the base's OpenCL driver version"),
            ('[ "$ze" = "$ocl" ]', "compares the two versions"),
            ("exit 1", "exits non-zero on a miss"),
        ]:
            if need not in gate:
                bad.append(f"the acceptance RUN does not check what {why}")
        if len(re.findall(r"ldd ", gate)) < 2 or len(re.findall(r"grep 'not found'", gate)) < 2:
            bad.append("the acceptance RUN does not ldd the libraries and fail on 'not found'")
        if "/usr/share/vulkan/icd.d" in gate:
            bad.append("the acceptance RUN holds the ICD path test_bench_intel.py keys on")
        ldconfig_run = final.find("RUN ldconfig")
        if final.find(gate) < ldconfig_run or final.find(gate) < final.find(install):
            bad.append("the acceptance RUN runs before ldconfig or before the install")
    return bad


def main():
    df = open(DOCKERFILE).read()
    doc = open(DOC).read()
    check("the Dockerfile installs the Level Zero loader and driver and proves them", not failures(df), str(failures(df)))

    mutants = {
        "an unpinned version": df.replace(f"ARG LIBZE_VERSION={VERSION}", "ARG LIBZE_VERSION=26.35.0-0"),
        "a changed sha256": df.replace(SHA256, "0" * 64),
        "no sha256 check": df.replace("| sha256sum -c -", "| cat"),
        "a deb from elsewhere": df.replace("github.com/intel/compute-runtime", "example.invalid/x"),
        "no libze1": df.replace("install -y --no-install-recommends libze1 /tmp", "install -y --no-install-recommends /tmp"),
        "recommends allowed": df.replace("--no-install-recommends libze1", "libze1"),
        "no apt lists clean in the install": df.replace("rm -rf /var/lib/apt/lists/* /tmp/libze-intel-gpu1.deb", "rm -f /tmp/libze-intel-gpu1.deb"),
        "no apt-get update in the install": df.replace("    apt-get update; \\\n    apt-get install -y --no-install-recommends libze1", "    apt-get install -y --no-install-recommends libze1"),
        "ldconfig inside the install": df.replace("rm -rf /var/lib/apt/lists/* /tmp/libze-intel-gpu1.deb", "ldconfig; rm -rf /var/lib/apt/lists/* /tmp/libze-intel-gpu1.deb"),
        "no loader in the cache check": df.replace("for so in libze_loader.so.1 libze_intel_gpu.so.1", "for so in libze_intel_gpu.so.1"),
        "no driver in the cache check": df.replace("for so in libze_loader.so.1 libze_intel_gpu.so.1", "for so in libze_loader.so.1"),
        "no plugin ldd": df.replace(PLUGIN, "/x/libopenvino_intel_gpu_plugin.so"),
        "no OpenCL driver version read": df.replace("-f='${Version}' intel-opencl-icd)", "-f='${Version}' libze1)"),
        "no version match": df.replace('[ "$ze" = "$ocl" ]', '[ -n "$ocl" ]'),
        "no 'not found' gate": df.replace("if ldd \"$ov\" | grep 'not found'", "if ldd \"$ov\" | cat"),
    }
    for name, text in mutants.items():
        check(f"mutant: {name} is caught", text != df and bool(failures(text)))

    ov = df.partition("RUN set -eu; \\\n    cache=")[2].partition("\nCOPY ")[0]
    moved = df.replace("RUN set -eu; \\\n    cache=" + ov + "\n", "", 1)
    moved = moved.replace("\nRUN ldconfig\n", "\n" + "RUN set -eu; \\\n    cache=" + ov + "\nRUN ldconfig\n", 1)
    check("mutant: the acceptance before ldconfig is caught", moved != df and bool(failures(moved)))

    check("the doc says why the packages are in the image", "libze-intel-gpu1" in doc and "libze1" in doc)

    print("OK" if not FAILED else f"{FAILED} check(s) failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
