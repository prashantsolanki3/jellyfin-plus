#!/usr/bin/env python3
"""The Intel image and the 13-row matrix (docs/architecture/GPU_VENDORS.md, 5.8).

    python3 -I scripts/test_bench_intel.py

Needs no GPU, ffmpeg, docker, network or Jellyfin. It runs the real scripts/bench-intel.sh and proves:

  - --dry-run prints the whole plan from hand-written goldens: 13 rows in order, each row's must-pass
    line, the thresholds, and the exact commands of rows 2, 7 and 9; it runs nothing and writes nothing;
  - the plan has no NVIDIA-only string in any command and follows the env overrides (node, title, clips);
  - each refusal exits 2 (bad row, unknown argument, BENCH_RUNS=0, no BENCH_ASSERT_QUIET);
  - a hermetic run against stub ffmpeg/curl: row 13 is MANUAL, every row lacking an input is SKIP
    and never PASS, and results.jsonl is written;
  - runs against a fake Jellyfin (a stub curl serving JSON by path; real jq, or a stand-in when absent):
    the API rows PASS on a good server and FAIL on a failing one, the token stays off curl's command
    line, rows 1 and 12 read only the shim-log lines of this run, row 6 sets and restores the plugin
    configuration, row 5 reads the device of its own stream, the gates read HHMM as decimal;
  - a row scores only files its own run wrote (rev-hxlit): a fresh run-<id> directory per run, a used id
    refused, any failed write a FAIL, a missing or old scored file a FAIL;
  - scripts/plugin-meta.sh holds the plugin GUID and target ABI once (Plugin.cs and manifest.json agree);
  - docker/intel/Dockerfile keeps its pins (base digest, SDK sha512), builds the Intel
    variant without fsr2, carries the acceptance checks, and its digest matches the spec page;
  - mutation: each deliberate breakage of bench-intel.sh is caught by the plan checks.

It does not build the image and does not run a row against a GPU. Exit status: 0 every check passed,
1 a check failed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

WORKERS = 8

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BASH = shutil.which("bash")
BENCH = os.path.join(HERE, "bench-intel.sh")
VENDOR = os.path.join(HERE, "ffmpeg-vendor.sh")
DOCKERFILE = os.path.join(REPO, "docker", "intel", "Dockerfile")
SPEC = os.path.join(REPO, "docs", "architecture", "GPU_VENDORS.md")

FF = "/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg"
NODE = "/dev/dri/renderD128"
SHADER = "/usr/share/jellyfin-shaders/FSRCNNX_x2_8-0-4-1.glsl"
INIT = "-init_hw_device vulkan=vk:0 -filter_hw_device vk"
OUT = "/clips/bench-out"
RUN = f"{OUT}/run-plan"  # a dry run's fresh per-run directory (BENCH_RUN_ID=plan, see run())


def shader(w, h):
    # libplacebo writes yuv420p and the frame is downloaded as is: the RGBA tail it replaced cost ~18 dB (rev-8j46u)
    return (f"format=yuv420p,hwupload,libplacebo=w={w}:h={h}:upscaler=ewa_lanczos:custom_shader_path={SHADER}:"
            f"{YUV_END}")


def bicubic(w, h):
    return f"scale={w}:{h}:flags=bicubic,format=yuv420p"


YUV_END = "format=yuv420p,hwdownload,format=yuv420p"


def ewa(w, h):
    """Row 2's `ewa` alternative: libplacebo's ewa_lanczos, no shader."""
    return f"format=yuv420p,hwupload,libplacebo=w={w}:h={h}:upscaler=ewa_lanczos:{YUV_END}"


def shader_bicubic(mw, mh, w, h):
    """Row 2's `x2-bicubic` / `lowtarget` alternatives: FSRCNNX to mw x mh, then bicubic to the master's size w x h."""
    return (f"format=yuv420p,hwupload,libplacebo=w={mw}:h={mh}:upscaler=ewa_lanczos:custom_shader_path={SHADER}:"
            f"{YUV_END},scale={w}:{h}:flags=bicubic,format=yuv420p")


# the 360p fixture is not on disk in a dry run, so the plan takes the nominal 640x360: 2x is 1280x720, 1.5x is 960x540
ALTS = ("ewa", "x2-bicubic", "lowtarget")
# the tail the bench and production used until rev-8j46u: every chain that carries it is a regression
RGBA_TAIL = "format=rgba,hwdownload,format=rgba"
FSR2_CHAIN = ("format=yuv420p,format=gbrpf32le,fsr2=w=1920:h=1080,format=yuv420p,hwupload,"
              f"libplacebo=w=1920:h=1080:{YUV_END}")
# the working VA-API path: decode on the GPU, download, upload to Vulkan, libplacebo to yuv420p, download
HWDEC_CHAIN = (f"hwdownload,format=nv12,hwupload,libplacebo=w=1920:h=1080:upscaler=ewa_lanczos:"
               f"custom_shader_path={SHADER}:{YUV_END}")
# the zero-copy path, tried once: it fails on this ANV with VK_ERROR_DEVICE_LOST
ZEROCOPY_CHAIN = (f"setparams=range=tv,hwmap=derive_device=vulkan,libplacebo=w=1920:h=1080:upscaler=ewa_lanczos:"
                  f"custom_shader_path={SHADER}:{YUV_END}")
STOCK = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
H264 = f"{OUT}/tears_1080p_h264.mkv"
PREP = f"{STOCK} -hide_banner -nostdin -y -i /clips/tears_1080p.mkv -c:v libx264 -crf 12 -preset fast -an {OUT}/tears_1080p_h264.part.mkv"


def hw_cmd(clip, chain, tail):
    return (f"{FF} -hide_banner -nostdin -y -benchmark -stats -init_hw_device vaapi=va:{NODE} "
            f"-init_hw_device vulkan=vk:0 -hwaccel vaapi -hwaccel_output_format vaapi -filter_hw_device vk "
            f"-i {clip} -vf {chain} -an {tail}")


def run_cmd(clip, chain, out=None, ff=FF):
    tail = f"-c:v ffv1 {out}" if out else "-f null -"
    return f"{ff} -hide_banner -nostdin -y -benchmark -stats {INIT} -i {clip} -vf {chain} -an {tail}"


# the pairing is by frame index: a pts offset or a dropped frame in a written file cannot shift the comparison
PAIR = "[0:v]settb=1/1000,setpts=N[c];[1:v]settb=1/1000,setpts=N[m];[c][m]xpsnr=stats_file="


def xpsnr_cmd(cand, master, run=RUN, ff=FF):
    return f"{ff} -hide_banner -nostdin -i {cand} -i {master} -lavfi {PAIR}{run}/xpsnr.txt -f null -"


TITLES = {
    1: "Vendor record",
    2: "FSRCNNX 360p to 1080p, software decode",
    3: "FSRCNNX 720p to 1080p",
    4: "FSRCNNX 1080p to 2160p",
    5: "Neural (ORT on OpenVINO), 360p and 720p to 1080p",
    6: "Routing by source height",
    7: "fsr2 without CUDA, still pixels",
    8: "fsr2 without CUDA, moving content",
    9: "Hardware decode off/on",
    10: "QSV encode",
    11: "Contention with one Plex hardware transcode",
    12: "Client, headless",
    13: "Client, human",
}

# A fragment each row's must-pass line has to carry (the figures the owner's decision names).
MUST = {
    1: ["vendor=intel", "Vulkan vk:0", "no nvenc/cuda string"],
    2: ["fps>=100", "bicubic+0.5dB", "chroma xpsnr>=25dB", "low-res route"],
    3: ["fps>=1.2x source fps", "bicubic+0.3dB", "chroma xpsnr>=25dB"],
    4: ["scaler-only fps>=34", "QSV encode >=1.2x", "bicubic+0.3dB", "chroma xpsnr>=25dB"],
    5: ["device=openvino", "slower than real time is a valid result"],
    6: ["NeuralMaxSourceHeight=480", "shader path"],
    7: ["ceil(8*scale^2)", "<=0.5", "<=16 in 99%", "control exactly 0"],
    8: ["panning cut >= bicubic", "not a blocker"],
    9: ["H.264", "-hwaccel vaapi", "hwmap=derive_device=vulkan", "Conversion failed!", ">=30% lower", ">=50dB"],
    10: ["h264_qsv/hevc_qsv", "encoderSessions", "+-10 points", "visible to the bench pod"],
    11: ["fps>=36", "Plex hardware transcode"],
    12: ["target height", "QSV codec"],
    13: ["window.__jellyfinPlus.installed===true", "no stall over 2s"],
}

THRESHOLD = {
    2: "plan: row 2 threshold: fps>=100 xpsnr>=+0.5",
    3: "plan: row 3 threshold: e2e>=1.2x xpsnr>=+0.3",
    4: "plan: row 4 threshold: fps>=34 e2e>=1.2x xpsnr>=+0.3",
    5: "plan: row 5 threshold: device=openvino; largest NeuralMaxSourceHeight holding >=1.2x",
    7: "plan: row 7 threshold: mean<=0.5 peak<=16 in 99% control==0",
    8: "plan: row 8 threshold: xpsnr(fsr2)>=xpsnr(bicubic)",
    9: "plan: row 9 threshold: cpu gain>=30% xpsnr(on,off)>=50dB no 'Conversion failed!'",
    10: "plan: row 10 threshold: |load - fdinfo| <= 10 points",
    11: "plan: row 11 threshold: fps>=36 with BENCH_PLEX_TRANSCODING=1",
}

EXACT = [
    "plan: bench-intel: rows=1 2 3 4 5 6 7 8 9 10 11 12 13 runs=3 mode=dry-run",
    "plan: required filters: oidn ort",
    # the deciding chain is the plugin's Intel low-res route: FSRCNNX to the 640x360 source x 1.5 = 960x540, then bicubic
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader_bicubic(960, 540, 1920, 1080), ff=STOCK),
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader_bicubic(960, 540, 1920, 1080), f"{RUN}/r2-shader.mkv", ff=STOCK),
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", bicubic(1920, 1080), f"{RUN}/r2-bicubic.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-shader.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-bicubic.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    # the old direct chain, informational: FSRCNNX straight to the target
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader(1920, 1080), ff=STOCK),
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader(1920, 1080), f"{RUN}/r2-direct.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-direct.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    "plan: row 2 route check: a 360p stream with sr=fsrcnnx, then Session/<its PlaySessionId> Record.Summary must carry 'low-res route: fsrcnnx to 1.5x then bicubic'",
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", ewa(1920, 1080), f"{RUN}/r2-alt-ewa.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-alt-ewa.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader_bicubic(1280, 720, 1920, 1080), f"{RUN}/r2-alt-x2-bicubic.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-alt-x2-bicubic.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    "plan: row 2 cmd: " + run_cmd("/clips/tears_360p.mkv", shader_bicubic(960, 540, 1920, 1080), f"{RUN}/r2-alt-lowtarget.mkv", ff=STOCK),
    "plan: row 2 cmd: " + xpsnr_cmd(f"{RUN}/r2-alt-lowtarget.mkv", "/clips/tears_1080p.mkv", ff=STOCK),
    "plan: row 4 cmd: " + run_cmd("/clips/tears_1080p.mkv", shader(3840, 2160), ff=STOCK),
    "plan: row 4 cmd: " + run_cmd("/clips/tears_1080p.mkv", shader(3840, 2160), f"{RUN}/r4-shader.mkv", ff=STOCK),
    "plan: row 4 cmd: " + xpsnr_cmd(f"{RUN}/r4-bicubic.mkv", "/clips/tears_2160p.mkv", ff=STOCK),
    "plan: row 7 cmd: " + (f"{FF} -hide_banner -nostdin -y {INIT} -loop 1 -framerate 24 -t 10 -i /clips/still_720p.png "
                           f"-vf {FSR2_CHAIN},tblend=all_mode=difference,signalstats,"
                           f"metadata=print:key=lavfi.signalstats.YAVG:file={RUN}/still-fsr2.txt -an -f null -"),
    "plan: row 7 skip frames: 3x=72 2x=32 1.5x=18",
    "plan: row 9 prep cmd: " + PREP,
    "plan: row 9 cmd: " + hw_cmd(H264, HWDEC_CHAIN, f"-c:v ffv1 {RUN}/hwdec-on.mkv"),
    "plan: row 9 cmd: " + run_cmd(H264, shader(1920, 1080), f"{RUN}/hwdec-off.mkv"),
    # the CPU figure comes from these two (no encoder in either), not from the ffv1 writes above
    "plan: row 9 cmd: " + hw_cmd(H264, HWDEC_CHAIN, "-f null -"),
    "plan: row 9 cmd: " + run_cmd(H264, shader(1920, 1080)),
    "plan: row 8 cmd: " + run_cmd("/clips/pan_720p.mkv", FSR2_CHAIN, f"{RUN}/pan-fsr2.mkv"),
    "plan: row 8 cmd: " + run_cmd("/clips/pan_720p.mkv", bicubic(1920, 1080), f"{RUN}/pan-bicubic.mkv"),
    "plan: row 8 cmd: " + xpsnr_cmd(f"{RUN}/pan-fsr2.mkv", "/clips/pan_1080p.mkv"),
    "plan: row 8 cmd: " + xpsnr_cmd(f"{RUN}/pan-bicubic.mkv", "/clips/pan_1080p.mkv"),
    "plan: row 9 zero-copy cmd: " + hw_cmd(H264, ZEROCOPY_CHAIN, "-f null -"),
    "plan: row 13: MANUAL, the owner's browser check over a port-forward",
]

FAILS = []
PASSES = 0


LOCK = threading.Lock()


def check(name, ok, detail=""):
    global PASSES
    with LOCK:
        if ok:
            PASSES += 1
        else:
            FAILS.append(name)
            print(f"FAIL {name} {detail}".rstrip())


def clean_env(extra=None, path=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BENCH_")}
    env["PATH"] = (path + os.pathsep if path else "") + os.environ.get("PATH", "/usr/bin:/bin")
    env["LC_ALL"] = "C"
    # no render node in sight: the script falls back to renderD128, so the plan goldens do not depend on the host's GPUs
    env["BENCH_SYSFS_DRM"] = "/nonexistent/sys-class-drm"
    env["BENCH_DRI_DIR"] = "/nonexistent/dev-dri"
    env.update(extra or {})
    return env


def run(script, args, env=None):
    env = dict(env or clean_env())
    # the plan prints the run directory: a fixed id keeps its goldens fixed. A real run keeps the script's own
    # unique default, so repeated runs in one BENCH_OUT do not collide.
    if "--dry-run" in args or "--plan" in args:
        env.setdefault("BENCH_RUN_ID", "plan")
    p = subprocess.run([BASH, script, *args], capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


def title_rows(lines):
    """The row numbers of the `plan: row N: <title>` lines (not the MANUAL line of row 13)."""
    return [int(m.group(1)) for m in (re.match(r"plan: row (\d+): (?!MANUAL)", l) for l in lines) if m]


def plan_failures(text):
    """Everything wrong with a default dry-run output, as a list of names (empty = the golden holds)."""
    bad = []
    lines = text.splitlines()
    for want in EXACT:
        if want not in lines:
            bad.append("missing:" + want[:70])
    rows = title_rows(lines)
    if rows != list(range(1, 14)):
        bad.append(f"rows:{rows}")
    for n, title in TITLES.items():
        if f"plan: row {n}: {title}" not in lines:
            bad.append(f"title:{n}")
    for n, frags in MUST.items():
        got = [l for l in lines if l.startswith(f"plan: row {n} must pass: ")]
        if len(got) != 1 or not all(f in got[0] for f in frags):
            bad.append(f"must:{n}")
    for n, want in THRESHOLD.items():
        if want not in lines:
            bad.append(f"threshold:{n}")
    for l in lines:
        if " cmd: " in l and re.search(r"nvenc|cuda|cuvid|nvdec", l, re.I):
            bad.append("nvidia-in-cmd")
        # every libplacebo chain (not the ort ones, row 5) writes yuv420p and downloads it; the RGBA tail loses ~18 dB
        if " cmd: " in l and "libplacebo" in l and "ort=" not in l and (
                RGBA_TAIL in l or not re.search(
                    r"libplacebo=[^ ]*:format=yuv420p,hwdownload,format=yuv420p(,| |$)", l)):
            bad.append("libplacebo-tail:" + l[-80:])
    if "vulkan=vk:0 " not in text:
        bad.append("no-vulkan-index-device")
    if re.search(r"vk@|drm=dr:", text):
        bad.append("vulkan-derived-from-drm")
    return bad


def mutant_dir(tmp, name, old, new):
    src = open(BENCH).read()
    assert old in src, f"mutant {name}: pattern not in script"
    d = os.path.join(tmp, name)
    os.makedirs(d)
    shutil.copy(VENDOR, d)
    path = os.path.join(d, "bench-intel.sh")
    open(path, "w").write(src.replace(old, new, 1))
    return path


def test_plan_and_refusals(tmp):
    rc, out, err = run(BENCH, ["--dry-run"])
    check("dry-run exits 0", rc == 0, err)
    check("dry-run plan matches the golden", not plan_failures(out), str(plan_failures(out)))
    check("--plan is --dry-run", run(BENCH, ["--plan"])[1] == out)

    rc, out2, _ = run(BENCH, ["--dry-run"], clean_env({"BENCH_FFMPEG": "/nonexistent/ffmpeg", "BENCH_OUT": os.path.join(tmp, "never")}))
    check("dry-run needs no binary", rc == 0 and "/nonexistent/ffmpeg" in out2)
    check("dry-run creates nothing", not os.path.exists(os.path.join(tmp, "never")))

    rc, out3, _ = run(BENCH, ["--dry-run", "--rows", "2,7"])
    got = title_rows(out3.splitlines())
    check("--rows limits the plan", rc == 0 and got == [2, 7], str(got))

    env = clean_env({"BENCH_RENDER_NODE": "/dev/dri/renderD129", "BENCH_TITLE": "anim", "BENCH_CLIPS": "/data"})
    rc, out4, _ = run(BENCH, ["--dry-run"], env)
    check("env overrides reach the commands",
          "vaapi=va:/dev/dri/renderD129" in out4 and "vulkan=vk:0 " in out4 and "/data/anim_360p.mkv" in out4 and "renderD128" not in out4 and "/clips" not in out4)

    clips = os.path.join(tmp, "plan-clips")
    os.makedirs(clips)
    open(os.path.join(clips, "tears_1080p_h264.mkv"), "w").close()
    rc, out6, _ = run(BENCH, ["--dry-run", "--rows", "9"], clean_env({"BENCH_CLIPS": clips}))
    check("an H.264 clip in the clips dir is the row 9 source and nothing is cut",
          rc == 0 and "row 9 prep cmd" not in out6 and f"-i {clips}/tears_1080p_h264.mkv " in out6 and "tears_1080p.mkv " not in out6, out6)

    rc, out5, _ = run(BENCH, ["--list"])
    check("--list names 13 rows", rc == 0 and len(out5.splitlines()) == 13)

    for name, args, extra in [
        ("a row past 13", ["--dry-run", "--rows", "14"], None),
        ("an unknown argument", ["--frobnicate"], None),
        ("BENCH_RUNS=0", ["--dry-run"], {"BENCH_RUNS": "0"}),
        ("a real run without BENCH_ASSERT_QUIET", ["--rows", "13"], None),
    ]:
        rc, _, err = run(BENCH, args, clean_env(extra))
        check(f"{name} exits 2", rc == 2, f"rc={rc} {err}")
    rc, _, err = run(BENCH, ["--rows", "13"])
    check("the gate refusal says gate", "gate:" in err, err)


JQ = shutil.which("jq")
REAL_DATE = shutil.which("date")
REAL_TIMEOUT = shutil.which("timeout")
TOKEN = "tok-SECRET-4242"

# Used only when this machine has no jq: it answers the exact filters bench-intel.sh sends and nothing
# else, so a filter the script changes is a failure here, not a silent pass. Real jq is preferred.
FAKE_JQ = r'''#!@PY@
import json, re, sys

args = sys.argv[1:]
raw = compact = null_in = False
named, rest, i = {}, [], 0
while i < len(args):
    a = args[i]
    if a in ("-r", "--raw-output"):
        raw = True
    elif a in ("-c", "--compact-output"):
        compact = True
    elif a in ("-n", "--null-input"):
        null_in = True
    elif a in ("-nc", "-cn"):
        null_in = compact = True
    elif a == "--arg":
        named[args[i + 1]] = args[i + 2]
        i += 2
    elif a == "--argjson":
        named[args[i + 1]] = json.loads(args[i + 2])
        i += 2
    else:
        rest.append(a)
    i += 1
flt = rest[0]
data = None
if not null_in:
    text = sys.stdin.read()
    if not text.strip():
        sys.exit(0)
    data = json.loads(text)


def path(d, p):
    for k in [k for k in p.split(".") if k]:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def emit(v):
    if raw and isinstance(v, str):
        print(v)
    else:
        print(json.dumps(v, separators=(",", ":")) if compact else json.dumps(v))


m_alt = re.fullmatch(r"(\.[A-Za-z.]+) // (.+)", flt)
m_ne = re.fullmatch(r"(\.[A-Za-z.]+) != false", flt)
m_path = re.fullmatch(r"\.[A-Za-z.]+", flt)
if flt == "{row:$row,status:$status,detail:$detail}":
    emit({"row": named["row"], "status": named["status"], "detail": named["detail"]})
elif flt == '[.Levels.Denoise[]?, .Levels.Neural[]?, .Levels.Game[]?] | join(" ")':
    lv = data.get("Levels") or {}
    emit(" ".join(lv.get("Denoise", []) + lv.get("Neural", []) + lv.get("Game", [])))
elif flt == 'first(.Items[] | select(.Path | endswith("/" + $n)) | .Id) // empty':
    for it in data.get("Items", []):
        if it["Path"].endswith("/" + named["n"]):
            emit(it["Id"])
            break
elif flt == 'first(.[] | select(.Name == "Jellyfin Plus") | .Id) // empty':
    for it in data:
        if it["Name"] == "Jellyfin Plus":
            emit(it["Id"])
            break
elif flt == ".NeuralMaxSourceHeight = 480 | .NeuralAllowed = true":
    data["NeuralMaxSourceHeight"] = 480
    data["NeuralAllowed"] = True
    emit(data)
elif m_ne:
    emit(path(data, m_ne.group(1)) is not False)
elif m_alt:
    v, d = path(data, m_alt.group(1)), m_alt.group(2).strip()
    if v is None or v is False:
        if d != "empty":
            emit(json.loads(d))
    else:
        emit(v)
elif m_path:
    emit(path(data, flt))
else:
    sys.stderr.write("fake jq: unsupported filter: %s\n" % flt)
    sys.exit(3)
'''

# The filter list is followed by thousands of lines, as the real binary's is: a `| grep -q` that quits at
# its match then SIGPIPEs the writer, and under pipefail that reads as "no such filter".
FFMPEG_STUB = r'''#!/bin/sh
[ -z "${FAKE_FFMPEG_LOG:-}" ] || echo "$*" >> "$FAKE_FFMPEG_LOG"
case "$*" in
*-filters*)
    for f in oidn ort fsr2 libplacebo xpsnr; do
        # FAKE_NO_FSR2=1: the Intel image's binary, which carries no fsr2 filter
        [ "$f" = fsr2 ] && [ "${FAKE_NO_FSR2:-}" = 1 ] && continue
        echo " T.. $f  V->V stub"
    done
    i=0; while [ $i -lt 4000 ]; do echo " T.. pad$i  V->V padding so the pipe fills"; i=$((i + 1)); done
    exit 0;;
*xpsnr=*)
    [ "${FAKE_XPSNR_FAIL:-}" = 1 ] && exit 1
    # FAKE_XPSNR_FAIL_ON: a substring of a scored file's path whose score fails (one row 2 alternative missing its score)
    if [ -n "${FAKE_XPSNR_FAIL_ON:-}" ]; then case "$*" in *"$FAKE_XPSNR_FAIL_ON"*) exit 1;; esac; fi
    [ "${FAKE_XPSNR_HANG:-}" = 1 ] && exec sleep 4
    # real ffmpeg 9 prints two spaces after XPSNR and between the planes: `XPSNR  y: 14.4167  u: 30.1  v: 30.2`
    # FAKE_XPSNR_SWAP=1: the candidates (shader, fsr2) score below their bicubic control, a negative delta
    y=30
    case "$*" in
    *hwdec-on.mkv*) y=55;;
    *shader.mkv* | *pan-fsr2.mkv* | *r2-direct.mkv*) [ "${FAKE_XPSNR_SWAP:-}" = 1 ] && y=30 || y=40;;
    *) [ "${FAKE_XPSNR_SWAP:-}" = 1 ] && y=40 || y=30;;
    esac
    # FAKE_XPSNR_ROUTED / FAKE_XPSNR_DIRECT: the luma score of row 2's deciding (r2-shader.mkv) and informational (r2-direct.mkv) chain
    [ -z "${FAKE_XPSNR_ROUTED:-}" ] || case "$*" in *r2-shader.mkv*) y=$FAKE_XPSNR_ROUTED;; esac
    [ -z "${FAKE_XPSNR_DIRECT:-}" ] || case "$*" in *r2-direct.mkv*) y=$FAKE_XPSNR_DIRECT;; esac
    # FAKE_XPSNR_ALT="ewa=34 lowtarget=36": the luma score of a row 2 alternative (r2-alt-<name>.mkv), whole dB
    for kv in ${FAKE_XPSNR_ALT:-}; do
        case "$*" in *"r2-alt-${kv%%=*}.mkv"*) y=${kv#*=};; esac
    done
    # FAKE_XPSNR_GREEN=1: the shader outputs keep their luma score but have lost their chroma, as ANV's green frames did
    c=$y
    case "$*" in *shader.mkv*) [ "${FAKE_XPSNR_GREEN:-}" = 1 ] && c=3;; esac
    # the per-frame stats file (`n:    1  XPSNR y: ...`); FAKE_XPSNR_COLLAPSE=1: the fsr2 pan scores well on frame 1 and badly after
    all="$*"; sf=${all##*stats_file=}; sf=${sf%% *}
    : > "$sf"
    n=1; while [ $n -le 4 ]; do
        fy=$y
        case "$*" in *pan-fsr2.mkv*) [ "${FAKE_XPSNR_COLLAPSE:-}" = 1 ] && [ $n -gt 1 ] && fy=10;; esac
        echo "n:    $n  XPSNR y: $fy.0000  XPSNR u: $c.0000  XPSNR v: $c.0000" >> "$sf"
        n=$((n + 1))
    done
    # FAKE_XPSNR_INF=1: an identical pair, which ffmpeg scores as infinity
    if [ "${FAKE_XPSNR_INF:-}" = 1 ]; then
        echo "[Parsed_xpsnr_0 @ 0x1] XPSNR  y: inf  u: inf  v: inf" >&2
        exit 0
    fi
    echo "[Parsed_xpsnr_0 @ 0x1] XPSNR  y: $y.0000  u: $c.0000  v: $c.0000" >&2
    exit 0;;
*libx264*)
    # the H.264 cut of row 9: FAKE_PREP_FAIL=1 fails it, else it writes its last argument (the output)
    [ "${FAKE_PREP_FAIL:-}" = 1 ] && { echo "tears_1080p.mkv: No such file or directory" >&2; exit 1; }
    for a in "$@"; do last="$a"; done
    : > "$last"
    exit 0;;
*hwmap=derive_device=vulkan*)
    # the zero-copy attempt of row 9: FAKE_ZEROCOPY_FAIL=1 dies with the ANV device loss, =plain with a reason that has no VK_ code
    case "${FAKE_ZEROCOPY_FAIL:-}" in
    1) echo "[Parsed_libplacebo_2 @ 0x1] vkQueueSubmit failed: VK_ERROR_DEVICE_LOST (-4)" >&2; echo "Conversion failed!" >&2; exit 1;;
    plain) echo "Error initializing filter 'libplacebo' with args 'w=1920'" >&2; echo "Conversion failed!" >&2; exit 1;;
    esac
    printf 'frame=  240 fps=120\rbench: utime=0.5s stime=0.1s rtime=2.0s\nbench: maxrss=26176KiB\n' >&2
    exit 0;;
*-benchmark*)
    [ "${FAKE_FFMPEG_FAIL:-}" = 1 ] && exit 1
    [ "${FAKE_FFMPEG_HANG:-}" = 1 ] && exec sleep 4
    # FAKE_ORT_WARN: what the ort filter says on stderr when the OpenVINO provider is not there (vf_ort.c append_openvino)
    # FAKE_ORT_NOISE: three OpenVINO lines before the warning; FAKE_ORT_DEVICE: the device line vf_ort config_input prints
    # FAKE_RTIME_360 / FAKE_RTIME_720: the wall seconds an ort run over that clip reports (240 frames: fps = 240 / seconds)
    rt=2.0
    case "$*" in *ort=*)
        case "$*" in *tears_360p*) rt=${FAKE_RTIME_360:-2.0};; *tears_720p*) rt=${FAKE_RTIME_720:-2.0};; esac
        [ -z "${FAKE_ORT_NOISE:-}" ] || for n in 1 2 3; do echo "[Parsed_ort_3 @ 0x1] OpenVINO runtime note $n" >&2; done
        [ -z "${FAKE_ORT_WARN:-}" ] || echo "[Parsed_ort_3 @ 0x1] $FAKE_ORT_WARN" >&2
        [ -z "${FAKE_ORT_DEVICE:-}" ] || echo "[Parsed_ort_3 @ 0x1] onnxruntime 1.20.1: /m/model.onnx, 640x360 -> 1280x720 (x2), device=$FAKE_ORT_DEVICE" >&2;; esac
    # an ffv1 write leaves its output (the last argument) as ffmpeg does. FAKE_FFV1_FAIL / _SKIP / _STALE hold a substring
    # of the output path they apply to: _FAIL exits 1 (a full disk), _SKIP exits 0 and writes nothing, _STALE writes it
    # with a year-2000 mtime, as a leftover of an earlier run would be.
    case "$*" in
    *"-c:v ffv1"*)
        for a in "$@"; do last="$a"; done
        if [ -n "${FAKE_FFV1_FAIL:-}" ]; then
            case "$last" in *"$FAKE_FFV1_FAIL"*) echo "Error writing trailer: No space left on device" >&2; exit 1;; esac
        fi
        skip=""
        if [ -n "${FAKE_FFV1_SKIP:-}" ]; then
            case "$last" in *"$FAKE_FFV1_SKIP"*) skip=1;; esac
        fi
        if [ -z "$skip" ]; then
            # the file says what was asked of it (size from the chain, 480 frames like the masters), so ffprobe can report it.
            # FAKE_FFV1_SHORT / _BADSIZE hold a substring of the output path: one frame short, or a 1280x572 picture.
            # a trailing scale (the shader-then-bicubic alternatives) is the final size; else the libplacebo one
            dims=$(echo "$*" | sed -n -e 's/.*scale=\([0-9]*\):\([0-9]*\):.*/\1 \2/p' -e t -e 's/.*libplacebo=w=\([0-9]*\):h=\([0-9]*\).*/\1 \2/p')
            n=480
            if [ -n "${FAKE_FFV1_SHORT:-}" ]; then
                case "$last" in *"$FAKE_FFV1_SHORT"*) n=479;; esac
            fi
            if [ -n "${FAKE_FFV1_BADSIZE:-}" ]; then
                case "$last" in *"$FAKE_FFV1_BADSIZE"*) dims="1280 572";; esac
            fi
            echo "ffv1-stub $dims $n" > "$last"
            if [ -n "${FAKE_FFV1_STALE:-}" ]; then
                case "$last" in *"$FAKE_FFV1_STALE"*) touch -t 200001010000 "$last";; esac
            fi
        fi;;
    esac
    # CPU seconds: the VA-API decode run uses about half the software one's user time (FAKE_ON_STIME: more system time,
    # which a user-time-only figure would miss; FAKE_ON_SEQ: the user time of each -f null VA-API run in turn, so one
    # run can spike). An ffv1 write costs both paths the same FAKE_FFV1_CPU extra user seconds.
    case "$*" in
    *-hwaccel*)
        ut=0.5; stt=${FAKE_ON_STIME:-0.1}
        case "$*" in
        *"-f null -"*)
            if [ -n "${FAKE_ON_SEQ:-}" ]; then
                k=$(cat "$FAKE_FFMPEG_LOG.seq" 2>/dev/null || echo 0); k=$((k + 1)); echo $k > "$FAKE_FFMPEG_LOG.seq"
                ut=$(echo "$FAKE_ON_SEQ" | cut -d' ' -f$k)
            fi;;
        esac;;
    *) ut=1.0; stt=0.1;;
    esac
    case "$*" in *"-c:v ffv1"*) ut=$(awk -v a="$ut" -v b="${FAKE_FFV1_CPU:-1.0}" 'BEGIN { printf "%.2f", a + b }');; esac
    # FAKE_NO_BENCH: an ffmpeg that prints no bench: lines at all
    [ -z "${FAKE_NO_BENCH:-}" ] || exit 0
    printf 'frame=  240 fps=120\rbench: utime=%ss stime=%ss rtime=%ss\nbench: maxrss=26176KiB\n' "$ut" "$stt" "$rt" >&2;;
*-loop*)
    [ "${FAKE_FFMPEG_HANG:-}" = 1 ] && exec sleep 4;;
esac
exit 0
'''

# Width and height per clip: the tears masters are letterboxed (1920x858 at 1080p, 3840x1714 at 2160p: NOT the
# 1080p shape doubled, which is 3840x1716), wide_ is a 16:9 title, any other clip is unreadable.
FFPROBE_STUB = r'''#!/bin/sh
case "$*" in
*r_frame_rate*) echo "${FAKE_SRC_FPS:-24/1}";;
*packet=pts_time*) echo 0.000000; echo 0.041667; echo 0.083333;;
*nb_read_packets*)
    # a file the ffmpeg stub wrote says its own size and frame count; a master is 480 frames of its clip's size
    for a in "$@"; do last="$a"; done
    if [ -f "$last" ] && read -r tag w h n < "$last" && [ "$tag" = ffv1-stub ]; then :
    else
        n=480
        case "$last" in
        *tears_360p*) w=640; h=286;;
        *tears_720p* | *pan_720p*) w=1280; h=572;;
        *tears_1080p* | *pan_1080p*) w=1920; h=858;;
        *tears_2160p*) w=3840; h=1714;;
        *wide_360p*) w=640; h=360;;
        *wide_1080p*) w=1920; h=1080;;
        *) exit 1;;
        esac
    fi
    echo "width=$w"; echo "height=$h"; echo pix_fmt=yuv420p; echo color_range=tv; echo start_time=0.000000; echo "nb_read_packets=$n";;
*stream=width,height*)
    case "$*" in
    *tears_360p*) echo 640,286;;
    *tears_720p* | *pan_720p*) echo 1280,572;;
    *tears_1080p* | *pan_1080p*) echo 1920,858;;
    *tears_2160p*) echo 3840,1714;;
    *wide_360p*) echo 640,360;;
    *wide_1080p*) echo 1920,1080;;
    *) exit 1;;
    esac;;
*height,codec_name*) echo h264,1080;;
*)
    # FAKE_CODEC: what the segment holds (row 10); FAKE_CODEC_EXTRA=1 adds a second stream line after it
    echo "${FAKE_CODEC:-h264}"
    [ -z "${FAKE_CODEC_EXTRA:-}" ] || echo hevc;;
esac
'''

# `date +%H%M` is the only thing it fakes (FAKE_HM); everything else is the real date. With FAKE_TZ_LOG set
# it appends the TZ the script called it with, so a test can see which zone the gate asked for.
DATE_STUB = ('#!/bin/sh\nfor a in "$@"; do\n  if [ "$a" = "+%H%M" ] && [ -n "${FAKE_HM:-}" ]; then\n'
             '    [ -z "${FAKE_TZ_LOG:-}" ] || echo "${TZ:-}" >> "$FAKE_TZ_LOG"\n    echo "$FAKE_HM"; exit 0\n  fi\n'
             'done\nexec "$REAL_DATE" "$@"\n')

# `sleep 5` (row 10's measuring window) is the only thing it fakes: with FAKE_VANISH set to a directory, the second of the two
# 5 s sleeps (the load reader's and the fdinfo sampler's, in whichever order they start) removes it, so a stand-in /proc
# entry vanishes mid-window as an exited transcode does. mkdir of FAKE_VANISH_LOCK is the atomic "first caller" test.
SLEEP_STUB = ('#!/bin/sh\n[ -z "${FAKE_SLEEP_LOG:-}" ] || echo "$1" >>"$FAKE_SLEEP_LOG"\n'
              'if [ -n "${FAKE_VANISH:-}" ] && [ "$1" = 5 ]; then\n'
              '  mkdir "$FAKE_VANISH_LOCK" 2>/dev/null || rm -rf "$FAKE_VANISH"\nfi\n'
              # row 10's 1/2/5 s waits run at a tenth of their length; the fake plugin's 5 s cache window shrinks with them
              'if [ -n "${FAKE_FAST_WAIT:-}" ]; then\n  case "$1" in 1) set -- 0.1;; 2) set -- 0.2;; 5) set -- 0.5;; esac\nfi\n'
              'exec @SLEEP@ "$@"\n')

# A fake Jellyfin: serves JSON by URL path from $FAKE_DIR/scenario.json, logs every call (argv, stdin,
# POST body) to $FAKE_DIR/log.jsonl, and keeps the plugin configuration and started sessions in state.json.
FAKE_CURL = r'''#!@PY@
import json, os, re, sys, time, urllib.parse

d = os.environ["FAKE_DIR"]
argv = sys.argv[1:]
sc = json.load(open(os.path.join(d, "scenario.json")))
sp = os.path.join(d, "state.json")
st = json.load(open(sp)) if os.path.exists(sp) else {"config": sc["config"], "sessions": {}}
stdin = ""
if "--config" in argv and argv[argv.index("--config") + 1] == "-":
    stdin = sys.stdin.read()
url = next((a for a in argv if a.startswith("http")), "")
body = ""
for i, a in enumerate(argv):
    if a in ("-d", "--data", "--data-binary") and argv[i + 1].startswith("@"):
        body = open(argv[i + 1][1:]).read()
with open(os.path.join(d, "log.jsonl"), "a") as f:
    f.write(json.dumps({"argv": argv, "stdin": stdin, "url": url, "body": body, "t": time.time()}) + "\n")
u = urllib.parse.urlparse(url)
path, q = u.path, urllib.parse.parse_qs(u.query)


want_code = "-w" in argv


def out(obj, raw=False):
    sys.stdout.write(obj if raw else json.dumps(obj))
    if want_code:
        sys.stdout.write("\n200")
    sys.exit(0)


def fail(code=404):
    # curl -f writes no body on an HTTP error, but -w still prints its code
    if want_code:
        sys.stdout.write("\n%d" % code)
    sys.stderr.write("curl: (22) The requested URL returned error: %d\n" % code)
    sys.exit(22)


def shimlog(text):
    p = os.environ.get("FAKE_SHIM_LOG")
    if p and text:
        open(p, "a").write(text + "\n")


def save():
    json.dump(st, open(sp, "w"))


if path == "/System/Info/Public":
    out({"ServerName": "fake"})
# Jellyfin 12.4 has the legacy X-Emby-Token header off: only this Authorization header is a login
m = re.search(r'^header = "((?:[^"\\]|\\.)*)"$', stdin, re.M)
header = re.sub(r"\\(.)", r"\1", m.group(1)) if m else ""
if header != 'Authorization: MediaBrowser Token="%s"' % os.environ["FAKE_TOKEN"]:
    fail(401)
if path == "/System/Info":
    if sc.get("info_status"):
        fail(sc["info_status"])
    out({"Version": "12.4.0"})
if sc.get("fail_all"):
    fail()
cfg_max = st["config"].get("NeuralMaxSourceHeight", 0)
if path == "/Plugins":
    out([{"Name": "Other", "Id": "0000"}, {"Name": "Jellyfin Plus", "Id": "plugin-guid"}])
if path.startswith("/Plugins/") and path.endswith("/Configuration"):
    if "-X" in argv and argv[argv.index("-X") + 1] == "POST":
        st["config"] = json.loads(body)
        save()
        sys.exit(0)
    out(st["config"])
if path == "/JellyfinPlus/Status":
    out({"Gpu": {"Vendor": "intel", "RenderNode": "/dev/dri/renderD128"}})
if path == "/JellyfinPlus/Gpu/Load":
    val = sc.get("load", 12)
    if sc.get("load_needs_prime"):
        # GpuLoadController.IntelLoadNow: a figure is computed over the time since the last computation and
        # cached for 5 s. The first call has no baseline, and a call inside the window gets the cached figure.
        tp = os.path.join(d, "loadt.json")
        now = time.time()
        last = json.load(open(tp)) if os.path.exists(tp) else None
        if last is None:
            json.dump({"t": now, "v": None}, open(tp, "w"))
            val = None
        elif now - last["t"] < (0.5 if os.environ.get("FAKE_FAST_WAIT") else 5):
            val = last["v"]
        else:
            json.dump({"t": now, "v": val}, open(tp, "w"))
    out({"gpuBusyPercent": val})
if path == "/Items":
    if sc.get("items_status"):
        fail(sc["items_status"])
    if sc.get("items_empty"):
        out({"Items": []})
    out({"Items": [{"Id": "id%d" % h, "Path": "/media/tears_%dp.mkv" % h} for h in (360, 720, 1080, 2160)]})
if path.startswith("/JellyfinPlus/Session/"):
    sid = path.rsplit("/", 1)[1]
    if sid == "bench-probe":
        shimlog(sc.get("probe_logs"))
    if sc.get("session_fail") and sid != "bench-probe":
        fail(500)
    s = None if sc.get("no_record") else st["sessions"].get(sid)
    if s is None:
        levels = {"Denoise": ["off", "nlmeans"], "Neural": ["off", "realesr-anime-x2"], "Game": sc.get("game_levels", ["off", "fsr2"])}
        if cfg_max > 0 and sc.get("config_applies", True):
            levels["NeuralMaxSourceHeight"] = cfg_max
        out({"Known": False, "Levels": levels})
    routing = None
    if cfg_max > 0 and s["neural"] and s["src_height"] > cfg_max:
        routing = "neural %s NOT run: the source is %dp, taller than NeuralMaxSourceHeight %d, so the shader path ran" % (
            s["neural"], s["src_height"], cfg_max)
    rec = {"Known": True, "SourceHeight": s["src_height"], "NeuralApplied": sc.get("applied", False), "Encoder": sc.get("encoder"),
           "NeuralDevice": sc.get("device", "openvino"), "NeuralRouting": sc["routing"] if "routing" in sc else routing}
    if "requested" in sc:
        rec["NeuralRequested"] = sc["requested"]
    if "withheld" in sc:
        rec["Record"] = {"GpuWithheldUnmeasured": sc["withheld"]}
    # the plugin's summary carries the low-res route part when the stream asked for fsrcnnx on a source under 480p;
    # scenario `summary` replaces it (null: no summary at all)
    if "summary" in sc:
        summary = sc["summary"]
    elif s["sr"] == "fsrcnnx" and s["src_height"] < 480:
        summary = "Intel Arc, 360p to 1080p, low-res route: fsrcnnx to 1.5x then bicubic"
    else:
        summary = None
    if summary is not None:
        rec.setdefault("Record", {})["Summary"] = summary
    out(rec)
if path.endswith("/master.m3u8"):
    if sc.get("fail_master"):
        fail()
    sid = q["PlaySessionId"][0]
    st["sessions"][sid] = {"src_height": int(path.split("/")[2][2:]), "neural": q.get("neural", [""])[0],
                          "sr": q.get("sr", [""])[0]}
    save()
    shimlog(sc.get("stream_logs"))
    out("#EXTM3U\nmain.m3u8?PlaySessionId=" + sid + "\n", raw=True)
if path.endswith("/main.m3u8"):
    out("#EXTM3U\n" + "".join("hls1/main/%d.mp4\n" % i for i in range(7)), raw=True)
if "/hls1/main/" in path:
    if sc.get("fail_segments"):
        fail()
    out("segment-bytes", raw=True)
fail()
'''

JSON_ROW = re.compile(r"^result: row (\d+) (\w+) ?(.*)$", re.M)


def results(out):
    """row number -> (status, detail) from the `result:` lines of a run."""
    return {int(n): (s, d) for n, s, d in JSON_ROW.findall(out)}


def stub_bin(tmp, fake_api=False):
    """Stub tools; jq is the real one (the script writes JSONL with it). fake_api swaps curl for FAKE_CURL."""
    d = os.path.join(tmp, "stubs")
    os.makedirs(d)
    bodies = {
        "ffmpeg": FFMPEG_STUB,
        "ffprobe": FFPROBE_STUB,
        "date": DATE_STUB,
        "sleep": SLEEP_STUB.replace("@SLEEP@", shutil.which("sleep") or "/bin/sleep"),
        "curl": FAKE_CURL.replace("@PY@", sys.executable) if fake_api else "#!/bin/sh\nexit 22\n",
    }
    for name, body in bodies.items():
        p = os.path.join(d, name)
        open(p, "w").write(body)
        os.chmod(p, 0o755)
    if JQ:
        os.symlink(JQ, os.path.join(d, "jq"))
    else:
        p = os.path.join(d, "jq")
        open(p, "w").write(FAKE_JQ.replace("@PY@", sys.executable))
        os.chmod(p, 0o755)
    open(os.path.join(d, "fsr2-nocuda"), "w").close()
    return d


class World:
    """One hermetic bench run: stub tools, a clips dir, a fake Jellyfin with a scenario, a shim log."""

    def __init__(self, tmp, name, scenario=None, extra=None):
        self.root = os.path.join(tmp, name)
        self.runs = 0
        os.makedirs(self.root)
        self.stubs = stub_bin(self.root, fake_api=True)
        self.clips = os.path.join(self.root, "clips")
        self.out = os.path.join(self.root, "out")
        self.fake = os.path.join(self.root, "fake")
        self.shim = os.path.join(self.root, "shim.log")
        os.makedirs(self.clips)
        os.makedirs(self.fake)
        open(self.shim, "w").close()
        sc = {"config": {"NeuralAllowed": True, "NeuralMaxSourceHeight": 0, "Keep": "me"}}
        sc.update(scenario or {})
        self.config0 = dict(sc["config"])
        json.dump(sc, open(os.path.join(self.fake, "scenario.json"), "w"))
        ff = os.path.join(self.stubs, "ffmpeg")
        self.env = clean_env(dict({
            "BENCH_FFMPEG": ff, "BENCH_STOCK_FFMPEG": ff, "BENCH_CLIPS": self.clips, "BENCH_OUT": self.out,
            "BENCH_JELLYFIN_URL": "http://jf.test", "BENCH_JELLYFIN_TOKEN": TOKEN, "BENCH_SHIM_LOG": self.shim,
            "BENCH_RUNS": "1", "BENCH_CONFIRM_NO_GATES": "1", "FAKE_DIR": self.fake, "FAKE_SHIM_LOG": self.shim,
            "FAKE_TOKEN": TOKEN, "FAKE_FFMPEG_LOG": os.path.join(self.root, "ffmpeg.log"),
            "FAKE_FAST_WAIT": "1", "REAL_DATE": REAL_DATE or "date"}, **(extra or {})), path=self.stubs)

    def run(self, args, script=BENCH, **extra):
        # the quiet-box gates read the real clock and /proc: the gate tests call run() themselves
        # every call writes its own run directory unless the test names one (BENCH_RUN_ID=...)
        self.runs += 1
        env = dict(self.env, BENCH_RUN_ID=f"w{self.runs}")
        env.update(extra)
        return run(script, ["--no-gates", *args], env)

    def log(self):
        p = os.path.join(self.fake, "log.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def urls(self):
        return [e["url"] for e in self.log() if e["url"]]

    def posts(self):
        return [json.loads(e["body"]) for e in self.log() if "POST" in e["argv"]]

    def jsonl(self):
        p = os.path.join(self.out, "results.jsonl")
        return open(p).read().splitlines() if os.path.exists(p) else []


def test_stub_run(tmp):
    stubs = stub_bin(tmp)
    clips = os.path.join(tmp, "clips")
    out = os.path.join(tmp, "out")
    os.makedirs(clips)
    env = clean_env({"BENCH_FFMPEG": os.path.join(stubs, "ffmpeg"), "BENCH_STOCK_FFMPEG": os.path.join(stubs, "ffmpeg"),
                     "BENCH_CLIPS": clips, "BENCH_OUT": out, "BENCH_CONFIRM_NO_GATES": "1"}, path=stubs)
    rc, so, err = run(BENCH, ["--no-gates", "--rows", "1,5,6,7,8,10,11,12,13"], env)
    status = dict(re.findall(r"^result: row (\d+) (\w+)", so, re.M))
    check("stub run exits 0", rc == 0, f"rc={rc} {err}")
    check("row 13 is MANUAL", status.get("13") == "MANUAL", so)
    skipped = [r for r in "1 5 6 7 8 10 11 12".split() if status.get(r) == "SKIP"]
    check("rows without an input are SKIP, never PASS", len(skipped) == 8, f"{status}")
    check("no PASS without evidence", "PASS" not in status.values())
    jl = os.path.join(out, "results.jsonl")
    check("results.jsonl has one line per row", os.path.isfile(jl) and len(open(jl).read().splitlines()) == 9)

    # a binary lacking a required filter is refused, not run
    bare = os.path.join(tmp, "bare")
    os.makedirs(bare)
    p = os.path.join(bare, "ffmpeg")
    open(p, "w").write("#!/bin/sh\nexit 0\n")
    os.chmod(p, 0o755)
    for n in ("ffprobe", "curl"):
        shutil.copy(os.path.join(stubs, n), bare)
    env2 = dict(env, BENCH_FFMPEG=p, BENCH_STOCK_FFMPEG=p, PATH=bare + os.pathsep + env["PATH"])
    rc, _, err = run(BENCH, ["--no-gates", "--rows", "13"], env2)
    check("a binary without the Intel filters is refused", rc == 2 and "not the Intel build" in err, f"rc={rc} {err}")


def quick_stale(script, tmp, tag):
    return stale_output_failures(script, tmp, tag, full=False)


def test_mutants(tmp):
    for name, old, new in [
        ("threshold-fps", "FPS_R2=100", "FPS_R2=10"),
        ("threshold-peak", "STILL_PEAK_MAX=16", "STILL_PEAK_MAX=32"),
        ("nvidia-device", 'INIT=(-init_hw_device "vulkan=vk:$VK_DEV"', 'INIT=(-init_hw_device "cuda=cu:0"'),
        ("vulkan-from-drm", 'INIT=(-init_hw_device "vulkan=vk:$VK_DEV"', 'INIT=(-init_hw_device "drm=dr:$NODE" -init_hw_device "vulkan=vk@dr"'),
        ("row9-vulkan-from-va", '-init_hw_device "vulkan=vk:$VK_DEV" -hwaccel vaapi', '-init_hw_device "vulkan=vk@va" -hwaccel vaapi'),
        ("row-dropped", "ALL_ROWS=(1 2 3 4 5 6 7 8 9 10 11 12 13)", "ALL_ROWS=(1 2 3 4 5 6 7 8 10 11 12 13)"),
        ("skip-frames", "v = 8 * s * s", "v = 4 * s * s"),
        ("row-order", "ALL_ROWS=(1 2 3 4 5 6 7 8 9 10 11 12 13)", "ALL_ROWS=(2 1 3 4 5 6 7 8 9 10 11 12 13)"),
        ("title", '"Vendor record"', '"Vendor"'),
        ("shader-rgba-end", 'custom_shader_path=$SHADER:$YUV_END"; }', 'custom_shader_path=$SHADER:format=rgba,hwdownload,format=rgba,format=yuv420p"; }'),
        ("fsr2-rgba-end", 'libplacebo=w=$1:h=$2:$YUV_END"; }\n# chain_hwdec', 'libplacebo=w=$1:h=$2:format=rgba,hwdownload,format=rgba,format=yuv420p"; }\n# chain_hwdec'),
        ("shader-no-yuv-format", 'custom_shader_path=$SHADER:$YUV_END"; }', 'custom_shader_path=$SHADER,hwdownload,format=yuv420p"; }'),
        ("hwdec-rgba-end", 'hwupload,libplacebo=w=$1:h=$2:upscaler=ewa_lanczos:custom_shader_path=$SHADER:$YUV_END"; }\n# chain_zerocopy', 'hwupload,libplacebo=w=$1:h=$2:upscaler=ewa_lanczos:custom_shader_path=$SHADER:format=rgba,hwdownload,format=rgba,format=yuv420p"; }\n# chain_zerocopy'),
        ("chroma-floor-zero", "CHROMA_XPSNR_MIN=25", "CHROMA_XPSNR_MIN=0"),
        ("hwdec-no-download", 'echo "hwdownload,format=nv12,hwupload,libplacebo', 'echo "hwupload,libplacebo'),
        ("hwdec-ffv1-plan", 'cmd_run_hw "$(hwdec_clip)" "$(chain_hwdec 1920 1080)"', 'cmd_run_hw "$(clip 1080)" "$(chain_hwdec 1920 1080)"'),
        ("alt-no-final-resize", '$YUV_END,scale=$1:$2:flags=bicubic,format=yuv420p"; }', '$YUV_END"; }'),
        ("alt-shader-at-target", "libplacebo=w=$ALT_MID_W:h=$ALT_MID_H:", "libplacebo=w=$1:h=$2:"),
        ("alt-low-is-x2", "ALT_LOW=1.5", "ALT_LOW=2"),
        ("alt-ewa-with-shader", 'chain_ewa() { echo "format=yuv420p,hwupload,libplacebo=w=$1:h=$2:upscaler=ewa_lanczos:$YUV_END"; }',
         'chain_ewa() { echo "format=yuv420p,hwupload,libplacebo=w=$1:h=$2:upscaler=ewa_lanczos:custom_shader_path=$SHADER:$YUV_END"; }'),
        ("alts-not-planned", "                plan_alts 2 360 1080 1920 1080\n", ""),
        ("alts-dropped-from-plan", 'ROW2_ALTS="ewa x2-bicubic lowtarget"', 'ROW2_ALTS="ewa x2-bicubic"'),
    ]:
        path = mutant_dir(tmp, name, old, new)
        rc, out, _ = run(path, ["--dry-run"])
        check(f"mutant {name} is caught", rc != 0 or bool(plan_failures(out)))

    behaviour = []
    for name, old, new, fn in [
        ("octal-hhmm", 'hm=$((10#$(TZ="$BLOCK_TZ" date +%H%M)))', 'hm=$(TZ="$BLOCK_TZ" date +%H%M)', gate_failures),
        ("octal-window-start", "lo=$((10#${item%-*}))", "lo=$((${item%-*}))", gate_failures),
        ("octal-window-end", "hi=$((10#${item#*-}))", "hi=$((${item#*-}))", gate_failures),
        ("window-end-inclusive", "hm < BLOCK_HI[i]", "hm <= BLOCK_HI[i]", gate_failures),
        ("tz-hard-coded", 'TZ="$BLOCK_TZ" date', "TZ=Australia/Melbourne date", window_failures),
        ("windows-ignored", 'BLOCK_WINDOWS="${BENCH_BLOCK_WINDOWS:-0100-0700,1630-1830}"', 'BLOCK_WINDOWS="0100-0700,1630-1830"', window_failures),
        ("malformed-accepted", "^[0-9]{4}-[0-9]{4}(,[0-9]{4}-[0-9]{4})*$", "^[0-9-]+(,[0-9-]+)*$", window_failures),
        ("run-fps-lost-failure", '|| { rc=$?; keep_log "$log"; echo 0; rm -f "$log"; return "$rc"; }', "|| true", runfps_failures),
        ("token-on-argv", 'curl -fsS --config - "$@" "$JF_URL$p"',
         'curl -fsS -H "Authorization: MediaBrowser Token=\\"$JF_TOKEN\\"" "$@" "$JF_URL$p"', token_failures),
        ("legacy-header", 'Authorization: MediaBrowser Token=\\\\"%s\\\\"', "X-Emby-Token: %s", token_failures),
        ("xpsnr-zero", 'c=$(xpsnr_y "$1" "$3") || return $?\n    b=$(xpsnr_y "$2" "$3") || return $?',
         'c=$(xpsnr_y "$1" "$3" || echo 0); b=$(xpsnr_y "$2" "$3" || echo 0)', xpsnr_failures),
        ("item-error-swallowed", 'ITEM_ERR="item lookup HTTP $API_CODE"; return 1;', "return 0;", item_failures),
        ("dims-nominal", "NF >= 2 && $1 > 0 && $2 > 0", "0", size_failures),
        ("no-timeout", 'timeout --kill-after=10 "$RUN_TIMEOUT" "${CMD[@]}"', '"${CMD[@]}"', timeout_failures),
        ("row5-full-length", "ORT_FRAMES=48", "ORT_FRAMES=", row5_budget_failures),
        ("neural-skip-on-error", "nc_fail() { NC_STATUS=FAIL;", "nc_fail() { NC_STATUS=SKIP;", neural_class_failures),
        ("neural-skip-when-fast", "NC_STATUS=MANUAL", "NC_STATUS=SKIP", neural_class_failures),
        ("neural-fps-missing-from-reason", "measured $NM_F360/$NM_F720 fps below", "measured fps below", neural_class_failures),
        ("neural-only-360p-judged", '! ge "$NM_F720" "$s720" || r720=1', 'r720=$r360', neural_class_failures),
        ("neural-only-720p-judged", '! ge "$NM_F360" "$s360" || r360=1', 'r360=$r720', neural_class_failures),
        ("neural-mixed-is-skip", 'elif [[ "$r360$r720" == 10 ]]; then', 'elif false; then', neural_class_failures),
        ("neural-mixed720-is-skip", 'elif [[ "$r360$r720" == 01 ]]; then', 'elif false; then', neural_class_failures),
        ("neural-src-fps-unchecked", 'if ! ge "${s360:-0}" 0.01 || ! ge "${s720:-0}" 0.01; then', 'if false; then', neural_class_failures),
        ("row5-cpu-overwritten", 'if [[ "$dev" == cpu ]]; then\n            # the session itself', 'if false; then\n            # the session itself', row5_cpu_failures),
        ("row6-shader-check-after-withheld", 'if [[ "$applied" == true || ( -n "$routing" && "$routing" != *"NOT run"* ) ]]; then record 6 FAIL "$failnote"; return; fi', ':', row6_shader_failures),
        ("row6-chosen-not-checked", '( -n "$routing" && "$routing" != *"NOT run"* ) ]]; then record 6 FAIL', '( -n "" ) ]]; then record 6 FAIL', row6_shader_failures),
        ("row6-applied-not-checked", 'if [[ "$applied" == true || ( -n', 'if [[ "$applied" == never || ( -n', row6_shader_failures),
        ("neural-cpu-fallback-skipped", '[[ "$NM_ST360$NM_ST720" != *CPU_FALLBACK* ]] ||', ": ||", neural_class_failures),
        ("chroma-unchecked", 'ge "$cu" "$CHROMA_XPSNR_MIN" || status=FAIL', ':', chroma_failures),
        ("signed-dropped", 'printf "%+.2f\\n", c - b', 'printf "%.2f\\n", c - b', signed_failures),
        ("signed-plus-prefix", 'printf "%+.2f\\n", c - b', 'printf "+%.2f\\n", c - b', signed_failures),
        ("row9-ffv1-source", 'cmd_run_hw "$HWDEC_SRC" "$(chain_hwdec 1920 1080)"', 'cmd_run_hw "$(clip 1080)" "$(chain_hwdec 1920 1080)"', row9_failures),
        ("row9-control-ffv1", 'cmd_run "$HWDEC_SRC" "$(chain_shader 1920 1080)"', 'cmd_run "$(clip 1080)" "$(chain_shader 1920 1080)"', row9_failures),
        ("row9-cut-every-run", '[[ -f "$HWDEC_SRC" ]] && return 0', ':', row9_failures),
        ("row9-no-zerocopy", 'zc=$(zerocopy_note)', 'zc=""', row9_failures),
        ("row9-zerocopy-fatal", 'zc=$(zerocopy_note)', 'zc=$(zerocopy_note); [[ "$zc" != zerocopy=FAIL* ]] || status=FAIL', row9_failures),
        ("row9-cpu-user-only", "t = u + s", "t = u", row9_failures),
        ("row9-cpu-last-bench-line", "/^bench: utime=/ {", "/^bench:/ {", row9_failures),
        ("row9-cpu-ffv1-sink", 'hw) cmd_run_hw "$HWDEC_SRC" "$(chain_hwdec 1920 1080)" ;;',
         'hw) cmd_run_hw "$HWDEC_SRC" "$(chain_hwdec 1920 1080)" "$RUN_OUT/cpu-hw.mkv" ;;', row9_failures),
        ("row9-cpu-not-median", """printf '%s\\n' "${cpu[@]}" | median""", """printf '%s\\n' "${cpu[@]}" | sort -n | tail -1""", row9_failures),
        ("row9-cpu-off-hwaccel", '*)  cmd_run "$HWDEC_SRC" "$(chain_shader 1920 1080)" ;;', '*)  cmd_run_hw "$HWDEC_SRC" "$(chain_shader 1920 1080)" ;;', row9_failures),
        ("row10-redirect-order", """{ tr '\\0' ' ' <"$p/cmdline"; } 2>/dev/null | grep""", """tr '\\0' ' ' <"$p/cmdline" 2>/dev/null | grep""", row10_reason_failures),
        ("row10-encoder-ignored", 'want=""', 'want=""; enc=""', row10_reason_failures),
        ("row10-codec-whole-output", "| head -n 1 | tr -d '[:space:]'", "| tr -d '[:space:]'", row10_reason_failures),
        ("row10-vanished-counted", '[[ -d "$PROC/$pid" ]] || { rm -f "$RUN_OUT/r10-fd"; echo gone >"$RUN_OUT/r10-gone"; }', ':', row10_reason_failures),
        ("row10-fail-unnamed", "fail=load-vs-fdinfo", "fail=x", row10_reason_failures),
        ("run-dir-shared", 'RUN_OUT="$OUT/run-$RUN_ID"', 'RUN_OUT="$OUT"', quick_stale),
        ("run-dir-reuse-allowed", '[[ ! -e "$RUN_OUT" ]] || die', ': || die', quick_stale),
        ("run-id-fixed", "printf -v RUN_ID '%(%Y%m%dT%H%M%S)T-%d' -1 \"$$\"", "RUN_ID=run", quick_stale),
        ("run-id-unchecked", '[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||', ': ||', quick_stale),
        ("write-failure-swallowed", 'return "$rc"\n}\n# fresh_output', 'timed_out "$rc" && return "$rc"; return 0\n}\n# fresh_output', quick_stale),
        ("fresh-check-removed", 'fresh_output() { [[ -s "$1" ]] && ! [[ "$RUN_OUT/.run-start" -nt "$1" ]]; }', 'fresh_output() { :; }', quick_stale),
        ("fresh-mtime-unchecked", 'fresh_output() { [[ -s "$1" ]] && ! [[ "$RUN_OUT/.run-start" -nt "$1" ]]; }', 'fresh_output() { [[ -s "$1" ]]; }', quick_stale),
        ("xpsnr-pairing-removed", '-lavfi "[0:v]settb=1/1000,setpts=N[c];[1:v]settb=1/1000,setpts=N[m];[c][m]xpsnr=stats_file=$3"',
         '-lavfi "xpsnr=stats_file=$3"', argv_failures),
        ("xpsnr-inputs-swapped", '-nostdin -i "$1" -i "$2"\n', '-nostdin -i "$2" -i "$1"\n', argv_failures),
        ("commands-log-removed", """printf 'row %s: %s\\n' "${CUR_ROW:-?}" "${CMD[*]}" >>"$RUN_OUT/commands.log\"""", ":", argv_failures),
        ("plan-sizes-nominal", 'read -r ow oh <<<"$(master_dims "$master" "$4" "$5")"', 'ow=$4 oh=$5', argv_failures),
        ("inputs-file-not-written", 'rep="$RUN_OUT/r$1-inputs.txt"', "rep=/dev/null", argv_failures),
        ("rows-2-4-patched", "        2|3|4) echo \"$STOCK_FFMPEG\" ;;", "        2|3|4) echo \"$PATCHED_FFMPEG\" ;;", binary_failures),
        ("row-8-stock", "        *)     echo \"$PATCHED_FFMPEG\" ;;", "        *)     echo \"$STOCK_FFMPEG\" ;;", binary_failures),
        ("frame-count-unchecked", ' || ("$mn" != "?" && "$n" != "$mn") ]]', " ]]", inputs_failures),
        ("size-unchecked", '("$mw" != "?" && "$w" != "$mw") || ("$mh" != "?" && "$h" != "$mh") || ', "", inputs_failures),
        ("inf-read-as-zero-y", "print (v ~ /^[+-]?[Ii]nf/ ? 999 : v + 0)", "print v + 0", inputs_failures),
        ("inf-read-as-zero-chroma", "return x ~ /^[+-]?[Ii]nf/ ? 999 : x + 0", "return x + 0", inputs_failures),
        ("alts-dropped", 'ROW2_ALTS="ewa x2-bicubic lowtarget"', 'ROW2_ALTS=""', alt_failures),
        ("alt-fails-the-row", 'detail+="$ALT_DETAIL"', 'detail+="$ALT_DETAIL"; [[ "$ALT_DETAIL" != *FAIL* ]] || status=FAIL', alt_failures),
        ("alt-tie-goes-late", 'if gt "$d" "$best_d"; then best="$name"', 'if ge "$d" "$best_d"; then best="$name"', alt_failures),
        ("alt-not-lined-up", 'if ! why_not=$(lines_up "$out" "$master"); then', 'if ! why_not=$(:); then', alt_failures),
        ("alts-after-failed-score", 'if [[ "$alts" == 1 && "$xrc" == 0 ]]; then', 'if [[ "$alts" == 1 ]]; then', alt_failures),
        ("alts-on-row-3", 'row_3() { shader_row 3 720 1920 1080 0 "$XPSNR_R3" 1; }', 'row_3() { shader_row 3 720 1920 1080 0 "$XPSNR_R3" 1 1; }', alt_failures),
        ("primary-reverts-to-direct", 'row_2() { shader_row 2 360 1920 1080 "$FPS_R2" "$XPSNR_R2" 0 1 1; }',
         'row_2() { shader_row 2 360 1920 1080 "$FPS_R2" "$XPSNR_R2" 0 1 0; }', lowres_failures),
        ("route-check-dropped", 'if [[ "$routed" == 1 ]]; then\n        lowres_route_check', 'if false; then\n        lowres_route_check', lowres_failures),
        ("route-part-unchecked", '[[ "$sumry" == *"$want"* ]]', '[[ -n "$sumry" ]]', lowres_failures),
        ("route-low-is-x2", "ALT_LOW=1.5", "ALT_LOW=2", lowres_failures),
        ("unverified-counts-as-pass", '[[ "$ROUTE_STATE" != FAIL ]] || status=FAIL',
         '[[ "$ROUTE_STATE" != FAIL ]] || status=FAIL; [[ "$ROUTE_STATE" != unverified ]] || status=PASS', lowres_failures),
        ("unverified-is-fail", 'ROUTE_DETAIL=" lowres_route=unverified(no Jellyfin API)"', 'ROUTE_STATE=FAIL; ROUTE_DETAIL=" lowres_route=unverified(no Jellyfin API)"', lowres_failures),
        ("direct-decides-the-row", 'detail+="$DIRECT_DETAIL"', 'detail+="$DIRECT_DETAIL"; [[ "$DIRECT_DETAIL" != *FAIL* ]] || status=FAIL', lowres_failures),
        ("direct-not-printed", 'detail+="$DIRECT_DETAIL"', ':', lowres_failures),
        ("trace-silent","""printf 'trace: row %s: %s\\n' "${CUR_ROW:-?}" "${CMD[*]}" >&9;""", ":;", trace_failures),
        ("row10-no-prime",'sleep 2; api /JellyfinPlus/Gpu/Load >/dev/null 2>&1 || true', 'sleep 2', row10_prime_failures),
        ("row10-short-wait", "LOAD_WAIT=5", "LOAD_WAIT=1", row10_prime_failures),
        ("row10-wait-4s", "LOAD_WAIT=5", "LOAD_WAIT=4", row10_prime_failures),
        ("row10-fdinfo-none", 'fdnote="fdinfo=not visible from the bench pod"', 'fdnote="fdinfo_busy=none"', row10_prime_failures),
        ("row10-stock-unmatched", "jellyfin-ffmpeg(-oidn)?/ffmpeg", "jellyfin-ffmpeg-oidn/ffmpeg",
         lambda sc, t, g: row10_fdinfo_failures(sc, t, g, ("stock",))),
        ("row10-oidn-unmatched", "jellyfin-ffmpeg(-oidn)?/ffmpeg", "jellyfin-ffmpeg/ffmpeg",
         lambda sc, t, g: row10_fdinfo_failures(sc, t, g, ("oidn",))),
    ]:
        path = mutant_dir(tmp, name, old, new)
        behaviour.append((name, path, fn))

    # each behaviour check runs the script for seconds (some wait out a stub hang): in parallel, each in its own directory
    def caught(item):
        name, path, fn = item
        sub = os.path.join(tmp, "b-" + name)
        os.makedirs(sub)
        return name, bool(fn(path, sub, "mut-" + name))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for name, ok in pool.map(caught, behaviour):
            check(f"mutant {name} is caught by its behaviour check", ok)


def build_apt_checks(df):
    """The build stage's apt line carries every dev package build-ffmpeg.sh installs for the Intel build.

    A header missing there (vulkan/vulkan.h) failed the first CI build.
    """
    stage = re.search(r"^FROM \S+ AS build\n(.*?)(?=^FROM )", df, re.M | re.S)
    check("the Dockerfile has a build stage", bool(stage))
    body = stage.group(1) if stage else ""
    apt = re.search(r"apt-get install[^\n]*?--no-install-recommends(.*?)&&\s*rm -rf", body, re.S)
    pkgs = apt.group(1).replace("\\", " ").split() if apt else []
    check("the build stage has an apt-get install line", bool(pkgs))
    for pkg in ["libvulkan-dev", "libva-dev", "libvpl-dev", "libdrm-dev", "zlib1g-dev", "glslang-tools"]:
        check(f"the build stage installs {pkg}", pkg in pkgs, str(pkgs))

    script = open(os.path.join(HERE, "build-ffmpeg.sh")).read()
    base = re.search(r"^APT_PKGS=\(([^)]*)\)", script, re.M)
    intel = re.search(r"^if \[\[ \"\$GPU_VENDOR\" == \"intel\" \]\]; then\n\s+APT_PKGS\+=\(([^)]*)\)", script, re.M)
    check("build-ffmpeg.sh APT_PKGS and its intel addition are found", bool(base) and bool(intel))
    wanted = (base.group(1).split() if base else []) + (intel.group(1).split() if intel else [])
    check("the script's apt list is not empty", len(wanted) >= 15, str(wanted))
    missing = [p for p in wanted if p not in pkgs]
    check("the build stage installs everything build-ffmpeg.sh apt-installs for intel", not missing, str(missing))

    check("the build stage fetches no FSR2 source", "git fetch" not in body and "FidelityFX" not in body)
    check("the distro shaderc is not installed (it broke nlmeans_vulkan)", "libshaderc" not in " ".join(pkgs))
    check("build-ffmpeg.sh apt-installs zlib1g-dev for intel (png needs zlib)", "zlib1g-dev" in (intel.group(1).split() if intel else []))
    check("build-ffmpeg.sh passes --enable-zlib for intel", bool(re.search(r"HWACCEL_FLAGS=\([^)]*--enable-zlib", script.split("else\n    VENDOR_PATCHES=(")[0])))
    check("the nvidia branch of build-ffmpeg.sh is left without zlib changes", "--enable-zlib" not in script.split("else\n    VENDOR_PATCHES=(")[1].split("\nfi\n")[0])

    check("every -dev package has a runtime pair listed", not unpaired_dev(pkgs + wanted, DEV_RUNTIME),
          str(unpaired_dev(pkgs + wanted, DEV_RUNTIME)))
    check("mutant: a -dev package with no runtime pair is caught", unpaired_dev(pkgs + ["libnew-dev"], DEV_RUNTIME) == ["libnew-dev"])


# Each -dev package of the build stage and the runtime package the final stage gets for it. The final
# stage installs the ldd-derived runtime-packages.txt, so these arrive by themselves; None is a build tool.
DEV_RUNTIME = {
    "libvulkan-dev": "libvulkan1",
    "libva-dev": "libva2",
    "libvpl-dev": "libvpl2",
    "libdrm-dev": "libdrm2",
    "libx264-dev": "libx264-164",
    "libxcb1-dev": "libxcb1",
    "zlib1g-dev": "zlib1g",
    "glslang-tools": None,
}


def unpaired_dev(pkgs, table):
    return sorted({p for p in pkgs if p.endswith("-dev") and p not in table})


def local_libs_failures(df):
    """What keeps a source-built shared library (shaderc, under /usr/local) out of the final image.

    dpkg -S finds no package for it, so runtime-packages.txt cannot name it: the build stage must copy it
    and the final stage must put it where ldconfig looks, before the ldd 'not found' check.
    """
    bad = []
    build, _, final = df.partition("AS build")[2].rpartition("\nFROM ")
    if not re.search(r"/usr/local/\*\).*?/out/local-libs/", build, re.S):
        bad.append("the build stage does not copy /usr/local libraries to /out/local-libs")
    # stdout of that loop is the package list: a stray echo would land in runtime-packages.txt and break apt
    if re.search(r'echo "local: [^"]*"', build) and not re.search(r'echo "local: [^"]*" >&2', build):
        bad.append("the local-lib message goes to stdout, into runtime-packages.txt")
    copy = final.find("COPY --from=build /out/local-libs/ /usr/local/lib/")
    ldconfig = final.find("ldconfig")
    gate = final.find("grep 'not found'")
    if copy < 0:
        bad.append("the final stage does not copy /out/local-libs to /usr/local/lib")
    if ldconfig < 0:
        bad.append("the final stage does not run ldconfig")
    if not 0 <= copy < ldconfig < gate:
        bad.append(f"order must be COPY, ldconfig, then the not-found check ({copy} {ldconfig} {gate})")
    return bad


def local_libs_checks(df):
    check("the source-built /usr/local libraries reach the final image", not local_libs_failures(df), str(local_libs_failures(df)))
    no_copy = df.replace("COPY --from=build /out/local-libs/ /usr/local/lib/", "")
    check("mutant: no copy of the local libraries is caught", bool(local_libs_failures(no_copy)))
    no_ldconfig = df.replace("ldconfig", "true")
    check("mutant: no ldconfig is caught", bool(local_libs_failures(no_ldconfig)))
    late = df.replace("RUN ldconfig\n", "") + "\nRUN ldconfig\n"
    check("mutant: ldconfig after the not-found check is caught", bool(local_libs_failures(late)))
    stdout_echo = df.replace('echo "local: $n" >&2', 'echo "local: $n"')
    check("mutant: a message on stdout (into runtime-packages.txt) is caught", bool(local_libs_failures(stdout_echo)))
    no_build = df.replace("/out/local-libs/", "/out/x/")
    check("mutant: a build stage that copies nothing is caught", bool(local_libs_failures(no_build)))


DRIVER_PKGS = ["mesa-vulkan-drivers", "intel-media-va-driver-non-free", "libigdgmm12"]
NONFREE_SRC = "/etc/apt/sources.list.d/intel-non-free.sources"


def final_runs(final):
    return re.findall(r"^RUN (?:[^\n]*\\\n)*[^\n]*", final, re.M)


def driver_failures(df):
    """What leaves the Arc image without the drivers libvulkan and libva dlopen (bench rows 2-4 and 9 failed).

    runtime-packages.txt comes from ldd of the patched ffmpeg, which never sees a dlopen'd driver, so the
    final stage must install the Intel Vulkan ICD and the iHD VA driver itself, and a build-time RUN must
    prove the ICD JSON exists and that both driver libraries resolve every dependency.
    """
    bad = []
    final = df.rpartition("\nFROM ")[2]
    runs = final_runs(final)
    install = next((r for r in runs if "apt-get install" in r and "mesa-vulkan-drivers" in r), None)
    if install is None:
        bad.append("the final stage has no apt-get install of the Intel drivers")
    else:
        for pkg in DRIVER_PKGS:
            if not re.search(rf"(?<![\w.-]){re.escape(pkg)}(?![\w.-])", install):
                bad.append(f"the driver install does not name {pkg}")
        order = [install.find(s) for s in (f"> {NONFREE_SRC}", "apt-get update", "apt-get install", f"rm -f {NONFREE_SRC}")]
        if "non-free" not in install or min(order) < 0 or order != sorted(order):
            bad.append("the non-free component must be written, updated, installed from and removed within the one RUN")
        if "rm -rf /var/lib/apt/lists/*" not in install:
            bad.append("the driver install does not clean the apt lists")
    gate = next((r for r in runs if "/usr/share/vulkan/icd.d" in r), None)
    if gate is None:
        bad.append("no RUN checks the Intel ICD JSON in /usr/share/vulkan/icd.d")
    else:
        if "*intel*.json" not in gate:
            bad.append("the ICD check does not look for an Intel ICD JSON")
        if "library_path" not in gate:
            bad.append("the ICD check does not read the library the JSON names")
        if "iHD_drv_video.so" not in gate:
            bad.append("the acceptance does not check iHD_drv_video.so")
        if len(re.findall(r"ldd ", gate)) < 2 or len(re.findall(r"grep 'not found'", gate)) < 2:
            bad.append("the acceptance must run ldd on both the ICD library and iHD and fail on 'not found'")
        if install is not None:
            if final.find(gate) < final.find(install):
                bad.append("the driver acceptance runs before the drivers are installed")
            if final.find(gate) < final.find("RUN ldconfig"):
                bad.append("the driver acceptance runs before ldconfig")
    comments = "\n".join(re.findall(r"^#[^\n]*", final, re.M))
    if not (re.search(r"\bvainfo\b", comments) and re.search(r"\bvulkaninfo\b", comments) and "GPU" in comments):
        bad.append("no comment says vainfo/vulkaninfo need a GPU so the Arc bench is the real proof")
    return bad


def driver_checks(df, spec):
    check("the final image installs the Intel drivers and proves they load", not driver_failures(df), str(driver_failures(df)))
    for pkg in DRIVER_PKGS:
        check(f"mutant: no {pkg} is caught", bool(driver_failures(df.replace(pkg, "removed-pkg"))))
    check("mutant: the free iHD package instead of non-free is caught",
          bool(driver_failures(df.replace("intel-media-va-driver-non-free", "intel-media-va-driver"))))
    check("mutant: a non-free source left behind is caught", bool(driver_failures(df.replace(f"rm -f {NONFREE_SRC};", "true;"))))
    check("mutant: no ICD check is caught", bool(driver_failures(df.replace("icd.d", "icd.x"))))
    check("mutant: no ldd of iHD is caught", bool(driver_failures(df.replace("iHD_drv_video.so", "x.so"))))
    check("mutant: no ldd of the ICD library is caught", bool(driver_failures(df.replace("library_path", "x"))))
    check("mutant: no 'not found' gate is caught", bool(driver_failures(df.replace("grep 'not found'", "true"))))
    check("mutant: no GPU caveat is caught", bool(driver_failures(df.replace("vulkaninfo", "x"))))
    final = df.rpartition("\nFROM ")[2]
    gate = next((r for r in final_runs(final) if "/usr/share/vulkan/icd.d" in r), "")
    early = df.replace(gate, "", 1).replace("COPY --from=build /out/runtime-packages.txt", gate + "\nCOPY --from=build /out/runtime-packages.txt", 1)
    check("mutant: the driver check before the install is caught", bool(gate) and bool(driver_failures(early)))
    check("the spec page says why the drivers are installed by name",
          all(p in spec.partition("### 5.8 ")[2] for p in ["dlopen", *DRIVER_PKGS]))


SHIM_PATH = "/usr/local/bin/jellyfin-ffmpeg-upscale"


def shim_interpreter_failures(df, shim_src):
    """What lets an image ship whose shim Jellyfin cannot start (build 8: no python3, 'Failed to find valid ffmpeg').

    The shim is a script, so its interpreter must be in the FINAL stage (the build stage's python3 does not
    ship), and the acceptance RUN must make the exact call Jellyfin makes, not only test the real binary.
    python3-minimal is not enough: json, subprocess and re live in the stdlib package only python3 pulls in.
    """
    bad = []
    final = df.rpartition("\nFROM ")[2]
    shebang = shim_src.split("\n", 1)[0]
    if not re.search(r"^#!/usr/bin/env python3$", shebang):
        bad.append(f"the shim's interpreter is no longer python3 ({shebang!r}); update this check")
    apt = re.search(r"apt-get install[^\n]*(?:\\\n[^\n]*)*", final)
    if not apt or not re.search(r"(?<![\w.-])python3(?![\w.-])", apt.group(0)):
        bad.append("the final stage does not apt-get install python3 (python3-minimal does not count)")
    copy = final.find("jellyfin-ffmpeg-upscale /usr/local/bin/jellyfin-ffmpeg-upscale")
    call = final.find(f"$({SHIM_PATH} -version)")
    if copy < 0:
        bad.append("the final stage does not copy the shim")
    if call < 0:
        bad.append("the acceptance RUN does not run the shim with -version (the call Jellyfin makes)")
    elif copy > call:
        bad.append("the shim -version check runs before the shim is copied in")
    if call >= 0 and "grep -q '^ffmpeg version'" not in final[call:]:
        bad.append("the shim -version output is not required to start with 'ffmpeg version'")
    return bad


def shim_interpreter_checks(df):
    shim_src = open(os.path.join(REPO, "shim", "jellyfin-ffmpeg-upscale")).read()
    check("the final image carries the shim's interpreter and checks the shim", not shim_interpreter_failures(df, shim_src),
          str(shim_interpreter_failures(df, shim_src)))
    no_py = df.replace(" python3 jq", " jq")
    check("mutant: no python3 in the final stage is caught", bool(shim_interpreter_failures(no_py, shim_src)))
    minimal = df.replace(" python3 jq", " python3-minimal jq")
    check("mutant: python3-minimal is caught", bool(shim_interpreter_failures(minimal, shim_src)))
    no_call = df.replace(f"$({SHIM_PATH} -version)", "$(true)")
    check("mutant: no shim -version check is caught", bool(shim_interpreter_failures(no_call, shim_src)))
    no_grep = df.replace("grep -q '^ffmpeg version'", "true")
    check("mutant: a shim check that does not read the output is caught", bool(shim_interpreter_failures(no_grep, shim_src)))
    early = df.replace(f"$({SHIM_PATH} -version)", "$(true)").replace("ENV DEBIAN_FRONTEND=noninteractive\n",
                       f"ENV DEBIAN_FRONTEND=noninteractive\nRUN v=$({SHIM_PATH} -version); echo \"$v\" | grep -q '^ffmpeg version'\n", 1)
    check("mutant: a shim check before the shim is copied is caught", bool(shim_interpreter_failures(early, shim_src)))
    other = shim_src.replace("#!/usr/bin/env python3", "#!/usr/bin/env python3.12", 1)
    check("mutant: a changed shebang is caught", bool(shim_interpreter_failures(df, other)))


def dockerfile_checks(tmp):
    df = open(DOCKERFILE).read()
    spec = open(SPEC).read()
    digest = re.search(r"^ARG JELLYFIN_IMAGE=(\S+@sha256:[0-9a-f]{64})$", df, re.M)
    check("base image is pinned by digest", bool(digest))
    check("the spec page names the same base image", bool(digest) and digest.group(1).split("docker.io/")[1] in spec)
    check("the image builds no fsr2 (zero motion vectors without CUDA): no SDK, no WITH_FSR2",
          not re.search(r"WITH_FSR2|FSR2_|gen_perm|fsr2compat|FidelityFX", df))
    check("the build acceptance requires the fsr2 marker absent and the filter absent",
          "test ! -e /usr/lib/jellyfin-ffmpeg-oidn/fsr2-nocuda" in df and "carries fsr2: not shipped on Intel" in df)
    check("the .NET SDK sha512 is pinned and checked",
          bool(re.search(r"^ARG DOTNET_SDK_SHA512=[0-9a-f]{128}$", df, re.M)) and "sha512sum -c" in df)
    check("no :latest", ":latest" not in df)
    froms = re.findall(r"^FROM (\S+)", df, re.M)
    check("every stage starts from the pinned base", froms and all(f == "${JELLYFIN_IMAGE}" for f in froms), str(froms))
    for needle in ["GPU_VENDOR=intel", "WITH_ORT=1", "ORT_EP=openvino",
                   "PREFIX=/usr/lib/jellyfin-ffmpeg-oidn", "scripts/build-ffmpeg.sh",
                   "ffmpeg_required_filters intel", "-hwaccels", "h264_qsv", "-encoders | grep -qE '^ V.{5} png '",
                   "-decoders | grep -qE '^ V.{5} png '", "cmp /tmp/base-ffmpeg",
                   "not found", "S=/usr/lib/jellyfin-ffmpeg/lib", "libvulkan.so.1 libshaderc_shared.so.1",
                   'loads $so from $got, not $S',"FSRCNNX_x2_8-0-4-1.glsl", "JELLYFIN_FFMPEG=",
                   "/var/cache/jellyfin/jellyfin-upscale.json", "jellyfin-ffmpeg-upscale"]:
        check(f"Dockerfile carries {needle}", needle in df)
    check("Dockerfile publishes nothing and sets no USER", not re.search(r"docker push|^USER ", df, re.M))
    check("build-ffmpeg.sh is not edited by the image", "sed -i" not in df)
    check("NVIDIA build variables absent", not re.search(r"GPU_VENDOR=nvidia|WITH_DLSS|WITH_OPTIX", df))

    build_apt_checks(df)
    local_libs_checks(df)
    driver_checks(df, spec)
    shim_interpreter_checks(df)
    entrypoint_checks(df)
    web_step_checks(df)

    ignore = open(os.path.join(REPO, "docker", "intel", "Dockerfile.dockerignore")).read().split()
    need = ["scripts", "src", "web", "web/src", "shim", "assets", "ffmpeg", "docker", ".", "web/theme", "patches"]
    check("dockerignore keeps what the build needs", not [n for n in need if n in ignore], str(ignore))
    for rel in ["shim/jellyfin-ffmpeg-upscale", "scripts/bench-intel.sh", "scripts/ffmpeg-vendor.sh", "scripts/build-ffmpeg.sh",
                "scripts/install-shaders.sh", "scripts/jellyfin-plus-webinject", "scripts/build-web-panel.sh",
                "web/theme/jellyfinplus.css", "web/package-lock.json",
                "assets/jellyfinplus.png", "assets/plugin-meta.json", "docker/intel/stage-plugin.sh",
                "docker/intel/entrypoint.py", "docker/intel/accept-plugin.sh", "scripts/test_entrypoint.py"]:
        check(f"{rel} exists", os.path.isfile(os.path.join(REPO, rel)))
    for rel in ["scripts/bench-intel.sh", "docker/intel/stage-plugin.sh", "docker/intel/accept-plugin.sh"]:
        p = subprocess.run([BASH, "-n", os.path.join(REPO, rel)], capture_output=True, text=True)
        check(f"bash -n {rel}", p.returncode == 0, p.stderr)
    check("the spec page has section 5.8", "### 5.8 " in spec)

    out = subprocess.run([BASH, "-c", f". {VENDOR}; ffmpeg_required_filters intel"], capture_output=True, text=True).stdout.split()
    check("the Intel filter list the image checks is oidn ort", out == ["oidn", "ort"], str(out))


def entrypoint_failures(df):
    """What lets an image ship that starts Jellyfin without the plugin (the first Arc image did).

    The plugin is only staged in /usr/lib/jellyfin-plus-dist; Jellyfin loads from $JELLYFIN_DATA_DIR/plugins.
    So the final stage must set the seeding wrapper as ENTRYPOINT, copy it executable, and end with a RUN
    that starts the server headless and requires the plugin to load, after everything it depends on.
    """
    bad = []
    final = df.rpartition("\nFROM ")[2]
    wrapper = "/usr/local/bin/jellyfin-plus-entrypoint"
    if not re.search(r"^ENTRYPOINT \[\"" + re.escape(wrapper) + r"\"\]$", final, re.M):
        bad.append("the final stage does not set the seeding wrapper as ENTRYPOINT (exec form, no args)")
    if re.search(r"^CMD ", final, re.M):
        bad.append("a CMD would be passed to Jellyfin as arguments")
    copy = re.search(r"^COPY --chmod=0755 docker/intel/entrypoint\.py " + re.escape(wrapper) + r"$", final, re.M)
    if not copy:
        bad.append("the wrapper is not copied executable to " + wrapper)
    accept = re.search(r"^RUN --mount=type=bind,source=docker/intel/accept-plugin\.sh,target=(\S+) \\\n\s+bash \1 " + re.escape(wrapper) + r"$",
                       final, re.M)
    if not accept:
        bad.append("the final stage has no acceptance RUN of accept-plugin.sh against the wrapper")
    plugin_copy = final.find("COPY --from=build /out/plugin-dist /usr/lib/jellyfin-plus-dist")
    patcher_copy = final.find("COPY --from=build /out/patcher /usr/lib/jellyfin-plus\n")
    ffmpeg_accept = final.find("RUN --mount=type=bind,from=base-pkgs")
    if accept and copy:
        if not 0 <= patcher_copy < accept.start():
            bad.append("the acceptance RUN runs before the patcher is copied in")
        if not 0 <= plugin_copy < copy.start() < accept.start():
            bad.append("order must be the plugin COPY, the wrapper COPY, then the acceptance RUN")
        if not 0 <= ffmpeg_accept < accept.start():
            bad.append("the acceptance RUN runs before the ffmpeg acceptance RUN")
    return bad


def entrypoint_checks(df):
    check("the final image seeds the plugin through an ENTRYPOINT wrapper and proves it loads", not entrypoint_failures(df),
          str(entrypoint_failures(df)))
    no_ep = df.replace('ENTRYPOINT ["/usr/local/bin/jellyfin-plus-entrypoint"]\n', "")
    check("mutant: no ENTRYPOINT is caught", bool(entrypoint_failures(no_ep)))
    base_ep = df.replace('ENTRYPOINT ["/usr/local/bin/jellyfin-plus-entrypoint"]', 'ENTRYPOINT ["/jellyfin/jellyfin"]')
    check("mutant: the base ENTRYPOINT is caught", bool(entrypoint_failures(base_ep)))
    shell_form = df.replace('ENTRYPOINT ["/usr/local/bin/jellyfin-plus-entrypoint"]', "ENTRYPOINT /usr/local/bin/jellyfin-plus-entrypoint")
    check("mutant: a shell-form ENTRYPOINT is caught", bool(entrypoint_failures(shell_form)))
    not_exec = df.replace("COPY --chmod=0755 docker/intel/entrypoint.py", "COPY docker/intel/entrypoint.py")
    check("mutant: a wrapper that is not executable is caught", bool(entrypoint_failures(not_exec)))
    no_accept = re.sub(r"\nRUN --mount=type=bind,source=docker/intel/accept-plugin\.sh.*?jellyfin-plus-entrypoint\n", "\n", df, flags=re.S)
    check("mutant: no acceptance RUN is caught", bool(entrypoint_failures(no_accept)) and no_accept != df)
    with_cmd = df.replace("ENTRYPOINT [", 'CMD ["--x"]\nENTRYPOINT [', 1)
    check("mutant: a CMD is caught", bool(entrypoint_failures(with_cmd)))
    early = (df.replace(re.search(r"\n# The plugin acceptance:.*?jellyfin-plus-entrypoint\n", df, re.S).group(0), "\n")
               .replace("COPY --from=build /out/patcher /usr/lib/jellyfin-plus\n",
                        "RUN --mount=type=bind,source=docker/intel/accept-plugin.sh,target=/tmp/accept-plugin.sh \\\n"
                        "    bash /tmp/accept-plugin.sh /usr/local/bin/jellyfin-plus-entrypoint\n"
                        "COPY --from=build /out/patcher /usr/lib/jellyfin-plus\n", 1))
    check("mutant: an acceptance RUN before the plugin is in place is caught", bool(entrypoint_failures(early)))

    script = open(os.path.join(REPO, "docker", "intel", "accept-plugin.sh")).read()
    check("the acceptance script greps for the plugin's load line", "Loaded plugin: Jellyfin Plus" in script)
    check("the acceptance script asserts the plugin dir was seeded", "plugins/JellyfinPlus_" in script)
    check("the acceptance script stops the server it started", "kill " in script and "rm -rf" in script)


def web_step(df):
    """The build stage's RUN that builds the client and injects it (the one that failed silently in CI)."""
    for m in re.finditer(r"^RUN (?:[^\n]*\\\n)*[^\n]*", df, re.M):
        if "jellyfin-plus-webinject" in m.group(0):
            return m.group(0)
    return ""


def web_step_checks(df):
    step = web_step(df)
    check("the Dockerfile has the web build and inject step", "jellyfin-plus-webinject" in step)
    check("the web step starts with set -eux so a failure names its command",
          bool(re.match(r"RUN set -[a-z]*e[a-z]*x[a-z]*;", step) or re.match(r"RUN set -[a-z]*x[a-z]*e[a-z]*;", step)), step[:60])
    check("the web step echoes which phase it is in", len(re.findall(r"\becho\b", step)) >= 3, step)

    # JP_SRC / JP_THEME_SRC must name the files the step installs just before it injects.
    installed = re.findall(r"install -m 0644 \S+ ([^\s;]+)", step)
    src = re.search(r"JP_SRC=(\S+)", step)
    theme = re.search(r"JP_THEME_SRC=(\S+)", step)
    check("JP_SRC is the installed client bundle", bool(src) and src.group(1) in installed, f"{installed}")
    check("JP_THEME_SRC is the installed theme css", bool(theme) and theme.group(1) in installed, f"{installed}")
    check("the step injects into $JELLYFIN_WEB", 'JP_WEB="$JELLYFIN_WEB"' in step)
    check("the step runs after stage-plugin.sh creates /out/patcher",
          0 <= df.find("stage-plugin.sh /src") < df.find(step[:40]) if step else False)


def test_webinject_without_dpkg_package(tmp):
    """The injector must not abort silently when jellyfin-web is a plain tree, not a dpkg package.

    In the image build stage dpkg-query exits 1 for jellyfin-web; under set -e + pipefail that killed the
    script before any output (Arc image build, step 12/13).
    """
    web = os.path.join(tmp, "web")
    os.makedirs(os.path.join(web, "themes", "dark"))
    open(os.path.join(web, "index.html"), "w").write(
        '<html><head><title>x</title><script defer="defer" src="main.js"></script></head><body></body></html>')
    open(os.path.join(web, "config.json"), "w").write('{"themes": []}')
    open(os.path.join(web, "themes", "dark", "theme.css"), "w").write("")
    src = os.path.join(tmp, "src.js")
    open(src, "w").write("// bundle\n")
    css = os.path.join(tmp, "theme.css")
    open(css, "w").write("/* theme */\n")

    stubs = os.path.join(tmp, "stubs")
    os.makedirs(stubs)
    p = os.path.join(stubs, "dpkg-query")
    open(p, "w").write("#!/bin/sh\nexit 1\n")
    os.chmod(p, 0o755)

    env = clean_env({"JP_WEB": web, "JP_SRC": src, "JP_THEME_SRC": css}, path=stubs)
    env.pop("JP_CONF_MD5", None)
    rc, out, err = run(os.path.join(HERE, "jellyfin-plus-webinject"), [], env)
    check("webinject exits 0 when dpkg-query fails", rc == 0, f"rc={rc} out={out!r} err={err!r}")
    check("webinject reports the index.html hook", "index.html hooked" in out, out)
    check("webinject installed jellyfin-plus.js", os.path.isfile(os.path.join(web, "jellyfin-plus.js")))
    html = open(os.path.join(web, "index.html")).read()
    check("index.html carries the script tag", "jellyfin-plus.js?v=" in html)
    check("the theme was installed", os.path.isfile(os.path.join(web, "themes", "jellyfinplus", "theme.css")), out)

    # the apt hook's pre-dpkg call reads the same md5, so it must not die either
    rc, out, err = run(os.path.join(HERE, "jellyfin-plus-webinject"), ["--restore-conffile"], env)
    check("--restore-conffile exits 0 when dpkg-query fails", rc == 0, f"rc={rc} err={err!r}")


GATE_CASES = [("0830", True), ("0900", True), ("0700", True), ("1200", True), ("1830", True),
              ("0158", False), ("0100", False), ("0130", False), ("1630", False), ("1700", False)]
PSI_OK = "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
PSI_HOT = PSI_OK.replace("avg60=0.00", "avg60=5.00", 1)


# (BENCH_BLOCK_WINDOWS, clock, open?): half-open [start,end), bounds read as decimal (0830 and 0900 are
# invalid octal), and the owner's real night window ends at 0415.
WINDOW_CASES = [("0100-0415,1630-1830", "0414", False), ("0100-0415,1630-1830", "0415", True),
                ("0100-0415,1630-1830", "0100", False), ("0100-0415,1630-1830", "0700", True),
                ("0100-0415,1630-1830", "0059", True), ("0100-0415,1630-1830", "1629", True),
                ("0100-0415,1630-1830", "1630", False), ("0100-0415,1630-1830", "1829", False),
                ("0100-0415,1630-1830", "1830", True),
                ("0800-0930", "0759", True), ("0800-0930", "0830", False), ("0800-0930", "0900", False),
                ("0800-0930", "0930", True),
                ("0830-0900", "0829", True), ("0830-0900", "0830", False), ("0830-0900", "0859", False),
                ("0830-0900", "0900", True),
                ("0900-1000", "0830", True), ("0900-1000", "0900", False),
                ("0000-2400", "2359", False), ("0000-2400", "0000", False), ("2300-2400", "2230", True)]


def gate_run(script, w, hm, psi, windows=None, **extra):
    """One real run of row 13 with the clock faked to hm: 'open', 'closed' (a gate: refusal) or 'rcN'."""
    env = dict(w.env, BENCH_ASSERT_QUIET="1", FAKE_HM=hm, BENCH_PSI_FILE=psi, **extra)
    if windows is not None:
        env["BENCH_BLOCK_WINDOWS"] = windows
    rc, out, err = run(script, ["--rows", "13"], env)
    if rc == 0 and "MANUAL" in out:
        return "open", err
    if rc == 2 and "gate:" in err:
        return "closed", err
    return f"rc{rc}", err


def gate_failures(script, tmp, tag):
    """What the time-window and PSI gates get wrong (empty = right). `date +%H%M` is faked per case; the
    leading-zero values 0830, 0900 and 0158 are the ones bash reads as invalid octal."""
    bad = []
    w = World(tmp, tag)
    psi, hot = os.path.join(w.root, "psi"), os.path.join(w.root, "psi-hot")
    open(psi, "w").write(PSI_OK)
    open(hot, "w").write(PSI_HOT)
    for hm, ok in GATE_CASES:
        got = gate_run(script, w, hm, psi)[0]
        if got != ("open" if ok else "closed"):
            bad.append(f"{hm}:{'open' if ok else 'closed'}-but-{got}")
    for windows, hm, ok in WINDOW_CASES:
        got = gate_run(script, w, hm, psi, windows)[0]
        if got != ("open" if ok else "closed"):
            bad.append(f"{windows}@{hm}:{'open' if ok else 'closed'}-but-{got}")
    rc, out, err = run(script, ["--rows", "13"], dict(w.env, BENCH_ASSERT_QUIET="1", FAKE_HM="1200", BENCH_PSI_FILE=hot))
    if not (rc == 2 and "PSI" in err):
        bad.append(f"psi:rc{rc}")
    return bad


BAD_WINDOWS = ["abc", "0100-0700,", ",0100-0700", "0100-0700,,1630-1830", "100-700", "0100", "0100-", "-0700",
               "2500-2600", "0160-0700", "0100-0760", "0100-2401", "2400-2400", "0700-0100", "0100-0100",
               "01:00-07:00", "0100-0700 1630-1830", "0100-0700, 1630-1830", "0100-0700;1630-1830", "0100–0700"]
BAD_TZ = ["Australia Melbourne", "Australia/Melbourne;date", "$(date)", "Aus*", "Australia\\Melbourne"]


def window_failures(script, tmp, tag):
    """What BENCH_BLOCK_WINDOWS and BENCH_TZ get wrong beyond the clock cases (empty = right): the plan
    line, the zone handed to date, the refusal of malformed values in --dry-run and in a real run."""
    bad = []
    w = World(tmp, tag)
    psi = os.path.join(w.root, "psi")
    open(psi, "w").write(PSI_OK)
    tail = "memory PSI some avg60 < 1%; BENCH_ASSERT_QUIET=1 (no vzdump"

    for extra, want in [
        ({}, "plan: gates: window outside 01:00-07:00 and 16:30-18:30 Australia/Melbourne; " + tail),
        ({"BENCH_BLOCK_WINDOWS": ""}, "plan: gates: window outside 01:00-07:00 and 16:30-18:30 Australia/Melbourne; " + tail),
        ({"BENCH_BLOCK_WINDOWS": "0100-0415,1630-1830", "BENCH_TZ": "Australia/Adelaide"},
         "plan: gates: window outside 01:00-04:15 and 16:30-18:30 Australia/Adelaide; " + tail),
        ({"BENCH_BLOCK_WINDOWS": "0830-0900"}, "plan: gates: window outside 08:30-09:00 Australia/Melbourne; " + tail),
    ]:
        rc, out, err = run(script, ["--dry-run"], dict(w.env, **extra))
        if rc != 0 or not any(l.startswith(want) for l in out.splitlines()):
            bad.append(f"plan:{extra}:rc{rc}")

    log = os.path.join(w.root, "tz.log")
    for extra, zone in [({}, "Australia/Melbourne"), ({"BENCH_TZ": "Australia/Adelaide"}, "Australia/Adelaide"),
                        ({"BENCH_TZ": ""}, "Australia/Melbourne")]:
        open(log, "w").close()
        got, err = gate_run(script, w, "0200", psi, FAKE_TZ_LOG=log, **extra)
        zones = open(log).read().split()
        if got != "closed" or zones != [zone] or zone not in err:
            bad.append(f"tz:{extra}:{got}:{zones}")

    for windows in BAD_WINDOWS:
        rc, out, err = run(script, ["--dry-run"], dict(w.env, BENCH_BLOCK_WINDOWS=windows))
        if rc != 2 or "BENCH_BLOCK_WINDOWS" not in err or "plan:" in out:
            bad.append(f"dry:{windows}:rc{rc}")
        got, err = gate_run(script, w, "1200", psi, windows)
        if got != "rc2" or "BENCH_BLOCK_WINDOWS" not in err or "gate:" in err:
            bad.append(f"run:{windows}:{got}")
    for zone in BAD_TZ:
        rc, out, err = run(script, ["--dry-run"], dict(w.env, BENCH_TZ=zone))
        if rc != 2 or "BENCH_TZ" not in err:
            bad.append(f"dry-tz:{zone}:rc{rc}")
        got, err = gate_run(script, w, "1200", psi, BENCH_TZ=zone)
        if got != "rc2" or "BENCH_TZ" not in err:
            bad.append(f"run-tz:{zone}:{got}")
    return bad


def runfps_failures(script, tmp, tag):
    """A failed ffmpeg run must reach the FAIL branch of rows 2 and 11; a good one must still pass row 11."""
    bad = []
    w = World(tmp, tag, extra={"BENCH_JELLYFIN_TOKEN": "", "BENCH_PLEX_TRANSCODING": "1"})
    rc, out, err = w.run(["--rows", "2,11"], script=script, FAKE_FFMPEG_FAIL="1")
    res = results(out)
    for row in (2, 11):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "ffmpeg failed" not in detail:
            bad.append(f"row{row}:{st}:{detail[:40]}")
    rc, out, err = w.run(["--rows", "11"], script=script)
    st, detail = results(out).get(11, ("", ""))
    if st != "PASS" or "fps=120" not in detail:
        bad.append(f"row11-good:{st}:{detail[:40]}")
    return bad


def token_failures(script, tmp, tag):
    """The admin token goes to curl on stdin (--config -), never on its command line, on every call."""
    bad = []
    w = World(tmp, tag)
    rc, out, err = w.run(["--rows", "3"], script=script)
    log = w.log()
    if not log:
        return ["no curl call logged"]
    if any(TOKEN in " ".join(e["argv"]) for e in log):
        bad.append("token on argv")
    if not all(TOKEN in e["stdin"] for e in log):
        bad.append("token missing from stdin on some call")
    if not all("--config" in e["argv"] for e in log):
        bad.append("not --config -")
    # Jellyfin 12.4 takes only the MediaBrowser Authorization header; the fake rejects anything else with 401
    if not all('Authorization: MediaBrowser Token=\\"%s\\"' % TOKEN in e["stdin"] for e in log):
        bad.append("not the MediaBrowser Authorization header")
    if any("X-Emby-Token" in e["stdin"] for e in log):
        bad.append("legacy X-Emby-Token header")
    if results(out).get(3, ("",))[0] != "PASS":
        bad.append("row 3 does not authenticate: %s" % (results(out).get(3),))
    return bad


def ffmpeg_runs(w):
    """The -vf command lines the stub ffmpeg was given in this world."""
    p = os.path.join(w.root, "ffmpeg.log")
    return [l for l in open(p).read().splitlines() if " -vf " in l] if os.path.exists(p) else []


def pan_clips(w):
    for n in ("pan_720p.mkv", "pan_1080p.mkv"):
        open(os.path.join(w.clips, n), "w").close()


def size_failures(script, tmp, tag):
    """Output sizes are the reference master's own width x height (ffprobe of the master at the target height), never the nominal 16:9 size and never the source clip's shape scaled (3840x1716 for a 3840x1714 master)."""
    bad = []
    for row, title, clip, ow, oh in [
        (2, "tears", "tears_360p", 1920, 858),
        (3, "tears", "tears_720p", 1920, 858),
        (4, "tears", "tears_1080p", 3840, 1714),
        (8, "tears", "pan_720p", 1920, 858),
        (11, "tears", "tears_1080p", 3840, 1714),
        (2, "wide", "wide_360p", 1920, 1080),  # a 16:9 clip keeps 16:9: the 858 is derived, not fixed
        (4, "unreadable", "unreadable_1080p", 3840, 2160),  # ffprobe fails: the nominal size, not a crash
    ]:
        w = World(tmp, f"{tag}-{row}-{title}", extra={"BENCH_TITLE": title, "BENCH_PLEX_TRANSCODING": "1"})
        pan_clips(w)
        w.run(["--rows", str(row)], script=script)
        lines = [l for l in ffmpeg_runs(w) if f"/{clip}.mkv" in l]
        want = re.compile(rf"(w={ow}:h={oh}|scale={ow}:{oh}:)(?!\d)")
        if not lines:
            bad.append(f"row{row}-{title}:no ffmpeg run")
        for l in lines:
            sizes = re.findall(r"(?:w=(\d+):h=(\d+)|scale=(\d+):(\d+):)", l)
            if "r2-alt-" in l or (row == 2 and "custom_shader_path" in l and "scale=" in l):
                # a shader-then-bicubic chain (row 2's routed chain, or an alternative) runs its shader at an intermediate size; the file it writes is the master's
                if not sizes or (sizes[-1][0] or sizes[-1][2], sizes[-1][1] or sizes[-1][3]) != (str(ow), str(oh)):
                    bad.append(f"row{row}-{title}-alt:{l[-90:]}")
            elif not want.search(l) or any((a or c, b or d) != (str(ow), str(oh)) for a, b, c, d in sizes):
                bad.append(f"row{row}-{title}:{l[-90:]}")
    return bad


def xpsnr_failures(script, tmp, tag):
    """A failed xpsnr run is a FAIL that says so, never a 0 dB score and a +0.00 delta."""
    bad = []
    w = World(tmp, tag)
    pan_clips(w)
    rc, out, err = w.run(["--rows", "2,3,4,8,9"], script=script, FAKE_XPSNR_FAIL="1")
    res = results(out)
    for row in (2, 3, 4, 8, 9):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "xpsnr failed" not in detail or "xpsnr_delta=+0.00" in detail or "+0.00dB" in detail:
            bad.append(f"row{row}:{st}:{detail[:60]}")
    w = World(tmp, tag + "-good")
    rc, out, err = w.run(["--rows", "2,3"], script=script)
    for row in (2, 3):
        st, detail = results(out).get(row, ("", ""))
        if st != "PASS" or "xpsnr_delta=+10.00dB" not in detail:
            bad.append(f"good-row{row}:{st}:{detail[:60]}")
    return bad


def timeout_stub(w):
    """A `timeout` that logs its argv (FAKE_TIMEOUT_LOG) and then really times the command out."""
    d = os.path.join(w.root, "tbin")
    os.makedirs(d)
    p = os.path.join(d, "timeout")
    open(p, "w").write('#!/bin/sh\necho "$*" >> "$FAKE_TIMEOUT_LOG"\nexec "$REAL_TIMEOUT" "$@"\n')
    os.chmod(p, 0o755)
    w.env["PATH"] = d + os.pathsep + w.env["PATH"]
    w.env["REAL_TIMEOUT"] = REAL_TIMEOUT or "timeout"
    w.env["FAKE_TIMEOUT_LOG"] = os.path.join(w.root, "timeout.log")
    return w.env["FAKE_TIMEOUT_LOG"]


def timeout_failures(script, tmp, tag):
    """Every measured ffmpeg runs under `timeout --kill-after=10 <BENCH_RUN_TIMEOUT, default 240>`, and a run that
    times out is a FAIL that says `timeout after <N>s` (for the xpsnr runs too), never a 0 or a hang."""
    bad = []
    open(os.path.join(tmp, "still_720p.png"), "w").close()
    for label, extra, want in (("default", {}, "240"), ("set", {"BENCH_RUN_TIMEOUT": "77"}, "77")):
        w = World(tmp, f"{tag}-{label}", extra=dict(extra, BENCH_TITLE="tears", BENCH_PLEX_TRANSCODING="1", BENCH_ORT_MODEL="/m/model.onnx"))
        pan_clips(w)
        open(os.path.join(w.clips, "still_720p.png"), "w").close()
        log = timeout_stub(w)
        w.run(["--rows", "2,5,7,8,9,11"], script=script)
        ff = [l for l in open(os.path.join(w.root, "ffmpeg.log")).read().splitlines() if "-filters" not in l]
        tl = open(log).read().splitlines() if os.path.exists(log) else []
        if not ff or len(tl) != len(ff):
            bad.append(f"{label}:{len(tl)} timeouts for {len(ff)} ffmpeg runs")
        for l in tl:
            if not l.startswith(f"--kill-after=10 {want} " + os.path.join(w.stubs, "ffmpeg") + " "):
                bad.append(f"{label}:{l[:60]}")
                break
    for rows, extra, hang in (("2,5,7,8,9,11", {}, {"FAKE_FFMPEG_HANG": "1"}), ("2,3,4,8,9", {}, {"FAKE_XPSNR_HANG": "1"})):
        w = World(tmp, f"{tag}-hang-{hang and list(hang)[0]}", extra=dict(extra, BENCH_RUN_TIMEOUT="1", BENCH_PLEX_TRANSCODING="1", BENCH_ORT_MODEL="/m/model.onnx"))
        pan_clips(w)
        open(os.path.join(w.clips, "still_720p.png"), "w").close()
        rc, out, err = w.run(["--rows", rows], script=script, **hang)
        res = results(out)
        for row in map(int, rows.split(",")):
            st, detail = res.get(row, ("", ""))
            if st != "FAIL" or "timeout after 1s" not in detail:
                bad.append(f"{list(hang)[0]}-row{row}:{st}:{detail[:60]}")
    rc, out, err = run(script, ["--no-gates", "--dry-run"], clean_env({"BENCH_RUN_TIMEOUT": "soon", "BENCH_CONFIRM_NO_GATES": "1"}))
    if rc != 2 or "BENCH_RUN_TIMEOUT" not in err:
        bad.append(f"bad-timeout-accepted:{rc}")
    return bad


def row5_budget_failures(script, tmp, tag):
    """Row 5 (Real-ESRGAN x2 on an A380, ~1-2 fps) runs once per clip and stops after 48 frames, whatever
    BENCH_RUNS says; no other row is cut short."""
    bad = []
    w = World(tmp, tag, extra={"BENCH_RUNS": "3", "BENCH_ORT_MODEL": "/m/model.onnx", "BENCH_JELLYFIN_TOKEN": ""})
    w.run(["--rows", "2,5"], script=script)
    runs = ffmpeg_runs(w)
    ort = [l for l in runs if "ort=" in l]
    if len(ort) != 2 or any(not re.search(r" -frames:v 48 ", l) for l in ort) or {("tears_360p" in l, "tears_720p" in l) for l in ort} != {(True, False), (False, True)}:
        bad.append(f"ort:{len(ort)}:{[l[-60:] for l in ort]}")
    if any("-frames:v" in l for l in runs if "ort=" not in l):
        bad.append("frames-limit-on-another-row")
    if len([l for l in runs if "tears_360p" in l and "ort=" not in l]) < 3:
        bad.append("row2-runs-cut")
    rc, out, err = run(script, ["--dry-run", "--rows", "5"], clean_env({"BENCH_ORT_MODEL": "/m/model.onnx"}))
    cmds = [l for l in out.splitlines() if l.startswith("plan: row 5 cmd: ") and "ort=" in l]
    if len(cmds) != 2 or any(" -frames:v 48 " not in l for l in cmds):
        bad.append("plan-row5")
    return bad


def item_failures(script, tmp, tag):
    """An HTTP error from the item lookup is its own failure, distinct from a clip missing from the library."""
    bad = []
    w = World(tmp, tag, {"items_status": 500}, {"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "3,4,5,6,10,12"], script=script)
    res = results(out)
    for row in (3, 4, 5, 6, 10, 12):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "item lookup HTTP 500" not in detail or "not in the Jellyfin library" in detail:
            bad.append(f"http500-row{row}:{st}:{detail[:60]}")
    if w.posts()[-1:] != [w.config0]:
        bad.append("row 6 left the plugin configuration changed")
    w = World(tmp, tag + "-empty", {"items_empty": True})
    rc, out, err = w.run(["--rows", "3,6,10,12"], script=script)
    res = results(out)
    for row in (3, 6, 10, 12):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "not in the Jellyfin library" not in detail or "item lookup" in detail:
            bad.append(f"empty-row{row}:{st}:{detail[:60]}")
    return bad


def signed_failures(script, tmp, tag):
    """A delta is printed with its own sign: +10.00 or -10.00, never `+-10.00`."""
    bad = []
    w = World(tmp, tag + "-neg")
    pan_clips(w)
    rc, out, err = w.run(["--rows", "2,3,4,8"], script=script, FAKE_XPSNR_SWAP="1")
    res = results(out)
    for row in (2, 3, 4, 8):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "xpsnr_delta=-10.00dB" not in detail or "+-" in detail:
            bad.append(f"neg-row{row}:{st}:{detail[:60]}")
    if "+-" in out:
        bad.append("plus-minus-in-log")
    w = World(tmp, tag + "-collapse")
    pan_clips(w)
    rc, out, err = w.run(["--rows", "8"], script=script, FAKE_XPSNR_SWAP="1", FAKE_XPSNR_COLLAPSE="1")
    st, detail = results(out).get(8, ("", ""))
    if st != "FAIL" or "frame1=30.00dB frames2+=10.00dB" not in detail:
        bad.append(f"collapse-row8:{st}:{detail[:140]}")
    w = World(tmp, tag + "-pos")
    pan_clips(w)
    rc, out, err = w.run(["--rows", "2,3,4,8"], script=script)
    res = results(out)
    for row in (2, 3, 4):
        st, detail = res.get(row, ("", ""))
        if "xpsnr_delta=+10.00dB" not in detail:
            bad.append(f"pos-row{row}:{st}:{detail[:60]}")
    if "xpsnr_delta=+10.00dB" not in res.get(8, ("", ""))[1]:
        bad.append(f"pos-row8:{res.get(8)}")
    return bad


def chroma_failures(script, tmp, tag):
    """A shader output that kept its luma but lost its chroma (green frames) FAILs rows 2-4 on the chroma score."""
    bad = []
    w = World(tmp, tag + "-green")
    rc, out, err = w.run(["--rows", "2,3,4"], script=script, FAKE_XPSNR_GREEN="1")
    res = results(out)
    for row in (2, 3, 4):
        st, detail = res.get(row, ("", ""))
        if st != "FAIL" or "chroma_xpsnr=3dB" not in detail or "xpsnr_delta=+10.00dB" not in detail:
            bad.append(f"green-row{row}:{st}:{detail[:90]}")
    w = World(tmp, tag + "-good")
    rc, out, err = w.run(["--rows", "2,3"], script=script)
    for row in (2, 3):
        st, detail = results(out).get(row, ("", ""))
        if st != "PASS" or "chroma_xpsnr=40dB" not in detail:
            bad.append(f"good-row{row}:{st}:{detail[:90]}")
    return bad


def ffmpeg_log(w):
    p = os.path.join(w.root, "ffmpeg.log")
    return [l for l in open(p).read().splitlines() if "-filters" not in l] if os.path.exists(p) else []


def binary_failures(script, tmp, tag):
    """The shim serves plain libplacebo/FSRCNNX with the stock ffmpeg (the patched binary's libplacebo output is garbage on
    the Arc), so rows 2, 3 and 4 run the stock one and row 8 (fsr2) the patched one, in the plan and in commands.log."""
    bad = []
    w = World(tmp, tag)
    pan_clips(w)
    patched = os.path.join(w.root, "patched", "ffmpeg")
    stock = os.path.join(w.root, "stock", "ffmpeg")
    for p in (patched, stock):
        os.makedirs(os.path.dirname(p))
        os.symlink(os.path.join(w.stubs, "ffprobe"), os.path.join(os.path.dirname(p), "ffprobe"))
        open(p, "w").write(f'#!/bin/sh\nexec {w.stubs}/ffmpeg "$@"\n')
        os.chmod(p, 0o755)
    w.env.update(BENCH_FFMPEG=patched, BENCH_STOCK_FFMPEG=stock)
    rc, out, err = w.run(["--rows", "2,3,4,8"], script=script, BENCH_RUN_ID="bin")
    logged = open(os.path.join(w.out, "run-bin", "commands.log")).read().splitlines() if os.path.isfile(os.path.join(w.out, "run-bin", "commands.log")) else []
    rc, plan, err = run(script, ["--dry-run", "--rows", "2,3,4,8"], dict(w.env, BENCH_RUN_ID="bin-plan"))
    for row, want in ((2, stock), (3, stock), (4, stock), (8, patched)):
        ran = [l[len(f"row {row}: "):] for l in logged if l.startswith(f"row {row}: ")]
        planned = [l[len(f"plan: row {row} cmd: "):] for l in plan.splitlines() if l.startswith(f"plan: row {row} cmd: ") and " -hide_banner " in l]
        for kind, cmds in (("run", ran), ("plan", planned)):
            argv0 = {c.split(" ", 1)[0] for c in cmds}
            if not cmds or argv0 != {want}:
                bad.append(f"row{row}-{kind}:{sorted(argv0)}")
    return bad


def row9_failures(script, tmp, tag):
    """Row 9 reads an H.264 1080p clip (VA-API cannot decode the FFV1 masters): cut once from the lossless one with
    the stock ffmpeg when missing, measure decode, download, upload, libplacebo to yuv420p; the zero-copy hwmap path
    is tried once and its failure is recorded, never the row's failure."""
    bad = []
    w = World(tmp, tag)
    # a distinct stock binary: it logs what it is asked to run, then runs the shared stub
    stock = os.path.join(w.root, "stock")
    os.makedirs(stock)
    os.symlink(os.path.join(w.stubs, "ffprobe"), os.path.join(stock, "ffprobe"))
    open(os.path.join(stock, "ffmpeg"), "w").write(f'#!/bin/sh\necho "$*" >> {w.root}/stock.log\nexec {w.stubs}/ffmpeg "$@"\n')
    os.chmod(os.path.join(stock, "ffmpeg"), 0o755)
    w.env["BENCH_STOCK_FFMPEG"] = os.path.join(stock, "ffmpeg")
    rc, out, err = w.run(["--rows", "9"], script=script)
    st, detail = results(out).get(9, ("", ""))
    runs = ffmpeg_log(w)
    h264 = os.path.join(w.out, "tears_1080p_h264.mkv")
    prep = [l for l in runs if "-c:v libx264" in l]
    if st != "PASS" or "zerocopy=ok" not in detail:
        bad.append(f"good:{st}:{detail[:90]}")
    if len(prep) != 1 or "-crf 12 -preset fast -an" not in prep[0] or f"-i {w.clips}/tears_1080p.mkv " not in prep[0]:
        bad.append(f"prep:{prep}")
    stock_runs = [l for l in open(os.path.join(w.root, "stock.log")).read().splitlines() if "-filters" not in l] if os.path.exists(os.path.join(w.root, "stock.log")) else []
    if len(stock_runs) != 1 or "-c:v libx264" not in stock_runs[0]:
        bad.append(f"prep-not-the-stock-binary:{stock_runs}")
    if not os.path.exists(h264) or os.path.exists(os.path.join(w.out, "tears_1080p_h264.part.mkv")):
        bad.append("h264-file-not-moved-into-place")
    vf = [l for l in runs if " -vf " in l]
    # five measured runs: the VA-API and the software run that write ffv1 (for XPSNR), the same two into a null sink (for the
    # CPU figure) and the zero-copy attempt: all read the H.264 clip
    if len(vf) != 5 or any(f"-i {h264} " not in l for l in vf):
        bad.append(f"sources:{[l[:200] for l in vf]}")
    on = [l for l in vf if "-hwaccel vaapi -hwaccel_output_format vaapi" in l and "hwmap=" not in l]
    if len(on) != 2 or any("hwdownload,format=nv12,hwupload,libplacebo=" not in l or not re.search(
            r"libplacebo=[^ ]*:format=yuv420p,hwdownload,format=yuv420p ", l) for l in on):
        bad.append(f"on:{on}")
    zc = [l for l in vf if "hwmap=derive_device=vulkan" in l]
    if len(zc) != 1 or " -f null -" not in zc[0] or not zc[0].endswith(" -f null -"):
        bad.append(f"zerocopy-cmd:{zc}")
    off = [l for l in vf if "-hwaccel" not in l]
    if len(off) != 2 or any("libplacebo=" not in l for l in off):
        bad.append(f"off:{off}")
    # the CPU figure is measured without an encoder: one null-sink run per path, the off one without -hwaccel
    cpu_on = [l for l in on if l.endswith(" -f null -")]
    cpu_off = [l for l in off if l.endswith(" -f null -")]
    if len(cpu_on) != 1 or len(cpu_off) != 1 or "-c:v ffv1" in cpu_on[0] + cpu_off[0]:
        bad.append(f"cpu-sink:{cpu_on}:{cpu_off}")
    # null sink 0.6 s on against 1.1 s off (user+sys) is 45.5%; the ffv1 pair 1.6 against 2.1 is 23.8%; the old user-only
    # reading of the ffv1 pair (1.5 against 2.0) was 25.0%
    if "cpu_gain=45.5%" not in detail or "cpu_gain_ffv1=23.8%" not in detail or "on=0.60s off=1.10s" not in detail:
        bad.append(f"cpu-figures:{detail[:240]}")
    # the second run reuses the cut: it is made once
    w.run(["--rows", "9"], script=script)
    if len([l for l in ffmpeg_log(w) if "-c:v libx264" in l]) != 1:
        bad.append("cut-twice")

    w = World(tmp, tag + "-devlost")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_ZEROCOPY_FAIL="1")
    st, detail = results(out).get(9, ("", ""))
    if st != "PASS" or "zerocopy=FAIL(VK_ERROR_DEVICE_LOST" not in detail or "cpu_gain=45.5%" not in detail:
        bad.append(f"devlost:{st}:{detail[:120]}")

    # system time counts: 0.5 s of it on the VA-API side leaves 1.0 s against 1.1 s (9.1%), a FAIL that a user-time-only
    # figure (50%) would have passed
    w = World(tmp, tag + "-stime")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_ON_STIME="0.5")
    st, detail = results(out).get(9, ("", ""))
    if st != "FAIL" or "cpu_gain=9.1%" not in detail:
        bad.append(f"stime:{st}:{detail[:120]}")

    # the figure is the median of BENCH_RUNS: a spike in one VA-API run (9.0 s of user time) does not move it, the mean would
    w = World(tmp, tag + "-median")
    rc, out, err = w.run(["--rows", "9"], script=script, BENCH_RUNS="3", FAKE_ON_SEQ="0.5 9.0 0.5")
    st, detail = results(out).get(9, ("", ""))
    runs3 = [l for l in ffmpeg_log(w) if " -vf " in l and l.endswith(" -f null -") and "hwmap=" not in l]
    if st != "PASS" or "cpu_gain=45.5%" not in detail or len(runs3) != 6:
        bad.append(f"median:{st}:{len(runs3)}:{detail[:120]}")

    # an ffmpeg that prints no bench: line is a FAIL that says so, never a 0.00s reading
    w = World(tmp, tag + "-nobench")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_NO_BENCH="1")
    st, detail = results(out).get(9, ("", ""))
    if st != "FAIL" or "printed no bench: line" not in detail or "0.00s" in detail:
        bad.append(f"nobench:{st}:{detail[:120]}")

    # a gain under the threshold stays a FAIL and prints its real figure (nothing is relaxed)
    w = World(tmp, tag + "-lowgain")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_ON_STIME="0.8")
    st, detail = results(out).get(9, ("", ""))
    if st != "FAIL" or "cpu_gain=" not in detail or "cpu_gain=45.5%" in detail:
        bad.append(f"lowgain:{st}:{detail[:120]}")
    w = World(tmp, tag + "-plain")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_ZEROCOPY_FAIL="plain")
    st, detail = results(out).get(9, ("", ""))
    if st != "PASS" or "zerocopy=FAIL(Error initializing filter 'libplacebo'" not in detail:
        bad.append(f"plain:{st}:{detail[:120]}")

    w = World(tmp, tag + "-given")
    given = os.path.join(w.clips, "tears_1080p_h264.mkv")
    open(given, "w").close()
    rc, out, err = w.run(["--rows", "9"], script=script)
    runs = ffmpeg_log(w)
    if any("-c:v libx264" in l for l in runs) or not all(f"-i {given} " in l for l in runs if " -vf " in l) or not any(" -vf " in l for l in runs):
        bad.append("given-clip-not-used")

    w = World(tmp, tag + "-prepfail")
    rc, out, err = w.run(["--rows", "9"], script=script, FAKE_PREP_FAIL="1")
    st, detail = results(out).get(9, ("", ""))
    if st != "FAIL" or "H.264" not in detail or any(" -vf " in l for l in ffmpeg_log(w)) or [f for f in os.listdir(w.out) if "h264" in f]:
        bad.append(f"prepfail:{st}:{detail[:100]}:{os.listdir(w.out)}")
    return bad


RUN_FILES = ["r2-shader.mkv", "r2-bicubic.mkv", "r2-direct.mkv", "r2-alt-ewa.mkv", "r2-alt-x2-bicubic.mkv", "r2-alt-lowtarget.mkv", "r3-shader.mkv", "r3-bicubic.mkv", "r4-shader.mkv", "r4-bicubic.mkv",
             "pan-fsr2.mkv", "pan-bicubic.mkv", "hwdec-on.mkv", "hwdec-off.mkv"]
SHARED = ("results.jsonl", "tears_1080p_h264.mkv")


def stale_output_failures(script, tmp, tag, full=True):
    """rev-hxlit: run15 scored files an earlier run (run10) had left, and reported run10's deltas. A row scores only
    what this run wrote: a fresh run-<id> directory per run (an existing one is refused), any non-zero exit of a
    file-writing ffmpeg is a FAIL, and a scored file that is missing, empty or older than the run start is a FAIL.
    full=False leaves rows 6 and 10 (the plugin-config and r10 files, ~8 s) to the one full run; the mutants skip them."""
    bad = []
    rows = (2, 3, 4, 8, 9)

    # two runs in one BENCH_OUT write into their own run directories; only the history and the H.264 cut are shared
    w = World(tmp, tag + "-dirs")
    pan_clips(w)
    for i, run_rows in ((1, "2,3,4,6,8,9,10" if full else "2,3,4,8,9"), (2, "2,3,4,8,9")):
        rc, out, err = w.run(["--rows", run_rows], script=script, BENCH_RUN_ID=f"id{i}")
        res = results(out)
        for row in rows:
            st, detail = res.get(row, ("", ""))
            if st != "PASS" or ("xpsnr_on_vs_off" if row == 9 else "xpsnr_delta=+10.00dB") not in detail:
                bad.append(f"dirs-run{i}-row{row}:{st}:{detail[:60]}")
        d = os.path.join(w.out, f"run-id{i}")
        missing = [f for f in RUN_FILES if not os.path.isfile(os.path.join(d, f))]
        if missing:
            bad.append(f"dirs-run{i}-missing:{missing}")
    first = os.path.join(w.out, "run-id1")
    for name in ("hwdec-on.log", "stream-2160") + (("plugin-config.row6.json", "plugin-config.orig.json", "r10-load") if full else ()):
        if not os.path.exists(os.path.join(first, name)):
            bad.append(f"dirs-not-in-run-dir:{name}")
    stray = sorted(n for n in os.listdir(w.out) if n not in SHARED and not n.startswith("run-"))
    if stray or not os.path.isfile(os.path.join(w.out, "tears_1080p_h264.mkv")):
        bad.append(f"dirs-shared-dir:{os.listdir(w.out)}")
    if sorted(n for n in os.listdir(w.out) if n.startswith("run-")) != ["run-id1", "run-id2"]:
        bad.append(f"dirs-run-dirs:{os.listdir(w.out)}")
    if len(w.jsonl()) != (12 if full else 10):
        bad.append(f"dirs-history:{len(w.jsonl())}")

    # without BENCH_RUN_ID each run still gets its own directory
    w = World(tmp, tag + "-default")
    for _ in range(2):
        run(script, ["--no-gates", "--rows", "13"], w.env)
    dirs = [n for n in os.listdir(w.out) if n.startswith("run-")]
    if len(dirs) != 2:
        bad.append(f"default-id:{dirs}")

    # the files of earlier runs are all still on disk, and a run that writes nothing, or something old, scores none of them
    for label, env in (("skip", {"FAKE_FFV1_SKIP": "mkv"}), ("stale", {"FAKE_FFV1_STALE": "mkv"})):
        w = World(tmp, f"{tag}-{label}")
        pan_clips(w)
        w.run(["--rows", "2,3,4,8,9"], script=script, BENCH_RUN_ID="good")
        rc, out, err = w.run(["--rows", "2,3,4,8,9"], script=script, BENCH_RUN_ID="later", **env)
        res = results(out)
        for row in rows:
            st, detail = res.get(row, ("", ""))
            if st != "FAIL" or "output not written by this run" not in detail or "xpsnr_delta" in detail:
                bad.append(f"{label}-row{row}:{st}:{detail[:70]}")

    # any non-zero exit of a file-writing run is the row's FAIL, with ffmpeg's last line, and nothing is scored after it
    for label, sub in (("write-all", "mkv"), ("write-second", "bicubic.mkv")):
        w = World(tmp, f"{tag}-{label}")
        pan_clips(w)
        rc, out, err = w.run(["--rows", "2,3,4,8"], script=script, FAKE_FFV1_FAIL=sub)
        res = results(out)
        for row in (2, 3, 4, 8):
            st, detail = res.get(row, ("", ""))
            if st != "FAIL" or "ffmpeg failed writing" not in detail or "No space left" not in detail or "xpsnr_delta" in detail:
                bad.append(f"{label}-row{row}:{st}:{detail[:90]}")
        if any("xpsnr=" in l for l in ffmpeg_log(w)):
            bad.append(f"{label}-scored-after-a-failed-write")

    # a run id that was used is refused, and the refusal writes nothing
    w = World(tmp, tag + "-reuse")
    w.run(["--rows", "2"], script=script, BENCH_RUN_ID="same")
    d = os.path.join(w.out, "run-same")
    before = (w.jsonl(), ffmpeg_log(w), sorted(os.listdir(d)) if os.path.isdir(d) else None)
    rc, out, err = w.run(["--rows", "2"], script=script, BENCH_RUN_ID="same")
    after = (w.jsonl(), ffmpeg_log(w), sorted(os.listdir(d)) if os.path.isdir(d) else None)
    if rc != 2 or "already exists" not in err or "result:" in out:
        bad.append(f"reuse-accepted:rc{rc}:{err[:80]}")
    if before[2] is None or before != after:
        bad.append("reuse-wrote")
    for name in ("../x", "a b", ".hidden", "x/y", "-n"):
        rc, out, err = run(script, ["--dry-run"], clean_env({"BENCH_RUN_ID": name}))
        if rc != 2 or "BENCH_RUN_ID" not in err:
            bad.append(f"bad-id:{name}:rc{rc}")
    return bad


def argv_failures(script, tmp, tag):
    """rev-hxlit: run16 scored rows 2/3/4/8 at -27..-38 dB where a hand run of the same chains scored -0.47 dB, and nothing
    showed what the bench had run. A run logs every argv to $RUN_OUT/commands.log; the plan prints that same sequence at the
    master's own size; row 2's scorer compares $RUN_OUT/r2-shader.mkv with the 1080p master, in that order, by frame
    index, and the files it scores match the master's size and frame count (r2-inputs.txt)."""
    bad = []
    w = World(tmp, tag)
    pan_clips(w)
    ff = os.path.join(w.stubs, "ffmpeg")
    rc, out, err = w.run(["--rows", "2,3,4,8"], script=script, BENCH_RUN_ID="argv")
    for row in (2, 3, 4, 8):
        if results(out).get(row, ("",))[0] != "PASS":
            bad.append(f"run-row{row}:{results(out).get(row)}")
    run_dir = os.path.join(w.out, "run-argv")
    log_path = os.path.join(run_dir, "commands.log")
    if not os.path.isfile(log_path):
        return bad + ["no commands.log"]
    logged = open(log_path).read().splitlines()
    # what was logged is what the stub ffmpeg was handed
    handed = [f"{ff} {l}" for l in ffmpeg_log(w)]
    if [l.split(": ", 1)[1] for l in logged] != handed:
        bad.append("commands.log-differs-from-what-ffmpeg-got")

    master, src = f"{w.clips}/tears_1080p.mkv", f"{w.clips}/tears_360p.mkv"
    r2 = [l[len("row 2: "):] for l in logged if l.startswith("row 2: ")]
    sh, bi = f"{run_dir}/r2-shader.mkv", f"{run_dir}/r2-bicubic.mkv"
    xp = [l for l in r2 if "xpsnr=" in l]
    pairs = [re.search(r" -i (\S+) -i (\S+) -lavfi ", l).groups() for l in xp]
    alt = {n: f"{run_dir}/r2-alt-{n}.mkv" for n in ALTS}
    dr = f"{run_dir}/r2-direct.mkv"
    want_pairs = [(sh, master), (bi, master), (sh, master), (dr, master), (bi, master)]
    for n in ALTS:  # each alternative is scored against the master, then the same bicubic control again
        want_pairs += [(alt[n], master), (bi, master)]
    if pairs != want_pairs:
        bad.append(f"row2-xpsnr-inputs:{pairs}")
    if any(PAIR not in l for l in xp):
        bad.append("row2-xpsnr-pairing-not-by-index")
    writes = [l for l in r2 if "-c:v ffv1" in l]
    # the source is 640x286, so 2x is 1280x572 and 1.5x is 960x430 (even); every alternative ends at the master's 1920x858
    want_writes = [run_cmd(src, shader_bicubic(960, 430, 1920, 858), sh), run_cmd(src, bicubic(1920, 858), bi),
                   run_cmd(src, shader(1920, 858), dr),
                   run_cmd(src, ewa(1920, 858), alt["ewa"]),
                   run_cmd(src, shader_bicubic(1280, 572, 1920, 858), alt["x2-bicubic"]),
                   run_cmd(src, shader_bicubic(960, 430, 1920, 858), alt["lowtarget"])]
    if writes != [l.replace(FF, ff, 1) for l in want_writes]:
        bad.append(f"row2-writes:{writes}")
    fps = [l for l in r2 if "-f null -" in l and "xpsnr=" not in l]
    if fps != [run_cmd(src, shader_bicubic(960, 430, 1920, 858)).replace(FF, ff, 1), run_cmd(src, shader(1920, 858)).replace(FF, ff, 1)]:
        bad.append(f"row2-run_fps:{fps}")

    # the scored files, the bicubic control and the master have the same size and frame count
    ip = os.path.join(run_dir, "r2-inputs.txt")
    inputs = open(ip).read().splitlines() if os.path.isfile(ip) else []
    shape = [re.search(r": (\d+x\d+) .* frames=(\d+) first_pts=(\S+)", l) for l in inputs]
    if len(inputs) != 3 or not all(shape) or len({(m.group(1), m.group(2)) for m in shape}) != 1 \
            or (shape[0].group(1), shape[0].group(2)) != ("1920x858", "480") \
            or not all(m.group(3) == "0.000000" for m in shape) or not all("pix_fmt=yuv420p color_range=tv" in l for l in inputs):
        bad.append(f"r2-inputs:{inputs}")
    for row in (3, 4, 8):
        if not os.path.isfile(os.path.join(run_dir, f"r{row}-inputs.txt")):
            bad.append(f"no r{row}-inputs.txt")

    # the plan lists, for each of these rows, the argv the run logged for it
    rc, plan, err = run(script, ["--dry-run", "--rows", "2,3,4,8"], dict(w.env, BENCH_RUN_ID="argv"))
    for row in (2, 3, 4, 8):
        planned = [l[len(f"plan: row {row} cmd: "):] for l in plan.splitlines() if l.startswith(f"plan: row {row} cmd: {ff} ")]
        ran = [l[len(f"row {row}: "):] for l in logged if l.startswith(f"row {row}: ")]
        if not planned or planned != ran:
            bad.append(f"plan-vs-run-row{row}:{len(planned)}/{len(ran)}")
    return bad


def inputs_failures(script, tmp, tag):
    """A scored file whose size or frame count differs from the master's FAILs the row with both numbers, and no xpsnr
    is run on it; an xpsnr of infinity (an identical pair) is a high score, never 0 dB."""
    bad = []
    master_size = {2: "1920x858", 3: "1920x858", 4: "3840x1714", 8: "1920x858"}
    for label, env, rows, extra in (
            ("short-shader", {"FAKE_FFV1_SHORT": "r2-shader.mkv"}, (2,), ("candidate ",)),
            ("short-bicubic", {"FAKE_FFV1_SHORT": "r2-bicubic.mkv"}, (2,), ("bicubic ",)),
            ("short-all", {"FAKE_FFV1_SHORT": ".mkv"}, (2, 3, 4, 8), ()),
            ("size", {"FAKE_FFV1_BADSIZE": ".mkv"}, (2, 3, 4, 8), ())):
        w = World(tmp, f"{tag}-{label}")
        pan_clips(w)
        rc, out, err = w.run(["--rows", ",".join(map(str, rows))], script=script, **env)
        for row in rows:
            st, detail = results(out).get(row, ("", ""))
            want = ("479 frames", "480 frames") if "FAKE_FFV1_SHORT" in env else ("1280x572", master_size[row])
            if st != "FAIL" or not all(n in detail for n in want + extra) or "xpsnr_delta" in detail or "xpsnr not run" not in detail:
                bad.append(f"{label}-row{row}:{st}:{detail[:120]}")
        if any("xpsnr=" in l for l in ffmpeg_log(w)):
            bad.append(f"{label}-scored-a-file-that-does-not-line-up")

    w = World(tmp, tag + "-inf")
    rc, out, err = w.run(["--rows", "2,9"], script=script, FAKE_XPSNR_INF="1")
    st9, d9 = results(out).get(9, ("", ""))
    st2, d2 = results(out).get(2, ("", ""))
    if st9 != "PASS" or "xpsnr_on_vs_off=999dB" not in d9:
        bad.append(f"inf-row9:{st9}:{d9[:80]}")
    if "chroma_xpsnr=999dB" not in d2 or "xpsnr_delta=+0.00dB" not in d2:
        bad.append(f"inf-row2:{st2}:{d2[:100]}")
    return bad


def trace_failures(script, tmp, tag):
    """BENCH_TRACE=1 echoes each argv as it runs; without it nothing is echoed."""
    bad = []
    w = World(tmp, tag)
    rc, out, err = w.run(["--rows", "2"], script=script, BENCH_TRACE="1")
    # routed chain: fps, 2 writes, 3 scores (candidate, bicubic, chroma); direct chain: fps, a write, 2 scores;
    # then 3 alternatives of a write and 2 scores each
    if len([l for l in err.splitlines() if l.startswith("trace: row 2: ")]) != 19:
        bad.append(f"trace:{err[:120]}")
    rc, out, err = w.run(["--rows", "2"], script=script)
    if "trace:" in err or "trace:" in out:
        bad.append("trace-without-flag")
    return bad


def alt_failures(script, tmp, tag):
    """rev-zlqhh: FSRCNNX 360p to 1080p lost 0.69 dB to bicubic (run20). Row 2 also scores ewa, FSRCNNX at 2x then bicubic and
    FSRCNNX at 1.5x then bicubic on the same fixture, against the same bicubic control, and prints each delta and the best of
    all the paths. The alternatives are evidence: they never change the row's status (the +0.5 dB threshold on FSRCNNX does),
    one that cannot be scored says so and the others still run, and rows 3 and 4 have none."""
    bad = []

    def row2(label, **env):
        w = World(tmp, f"{tag}-{label}")
        rc, out, err = w.run(["--rows", "2"], script=script, **env)
        st, detail = results(out).get(2, ("", ""))
        return w, st, detail

    # FSRCNNX +10 dB over bicubic, every alternative below it: FSRCNNX is the best path
    w, st, detail = row2("good", FAKE_XPSNR_ALT="ewa=34 x2-bicubic=31 lowtarget=36")
    for want in ("xpsnr_delta=+10.00dB", "alt_ewa=+4.00dB", "alt_x2-bicubic=+1.00dB", "alt_lowtarget=+6.00dB", "best=fsrcnnx"):
        if want not in detail:
            bad.append(f"good:missing {want}:{detail[:200]}")
    if st != "PASS":
        bad.append(f"good:{st}")

    # an alternative that beats FSRCNNX is named best, and the row still FAILs on FSRCNNX against its own threshold
    w, st, detail = row2("swap", FAKE_XPSNR_SWAP="1", FAKE_XPSNR_ALT="ewa=41 x2-bicubic=39 lowtarget=43")
    for want in ("xpsnr_delta=-10.00dB", "alt_ewa=+1.00dB", "alt_x2-bicubic=-1.00dB", "alt_lowtarget=+3.00dB", "best=lowtarget"):
        if want not in detail:
            bad.append(f"swap:missing {want}:{detail[:200]}")
    if st != "FAIL":
        bad.append(f"swap:{st}")

    # nothing beats bicubic: it is the best path (a delta of 0 is its own score)
    w, st, detail = row2("none", FAKE_XPSNR_SWAP="1", FAKE_XPSNR_ALT="ewa=39 x2-bicubic=38 lowtarget=37")
    if "best=bicubic" not in detail or st != "FAIL":
        bad.append(f"none:{st}:{detail[:200]}")

    # a tie keeps the earlier path
    w, st, detail = row2("tie", FAKE_XPSNR_ALT="ewa=40")
    if "alt_ewa=+10.00dB" not in detail or "best=fsrcnnx" not in detail:
        bad.append(f"tie:{detail[:200]}")

    # a bad alternative never fails a passing row
    w, st, detail = row2("worse", FAKE_XPSNR_ALT="ewa=20 x2-bicubic=20 lowtarget=20")
    if st != "PASS" or "alt_ewa=-10.00dB" not in detail:
        bad.append(f"worse:{st}:{detail[:200]}")

    # one alternative without a score: named, never +0.00, never the best; the others are still scored
    w, st, detail = row2("noscore", FAKE_XPSNR_FAIL_ON="r2-alt-x2-bicubic.mkv", FAKE_XPSNR_ALT="ewa=34 lowtarget=36")
    if "alt_x2-bicubic=FAIL(" not in detail or "alt_x2-bicubic=+" in detail or "alt_ewa=+4.00dB" not in detail \
            or "alt_lowtarget=+6.00dB" not in detail or st != "PASS":
        bad.append(f"noscore:{st}:{detail[:200]}")

    # an alternative whose write fails, or whose file does not line up with the master, is named and not scored
    for label, env, alt, note in (("write", {"FAKE_FFV1_FAIL": "r2-alt-ewa"}, "ewa", "No space left"),
                                  ("short", {"FAKE_FFV1_SHORT": "r2-alt-lowtarget"}, "lowtarget", "479 frames"),
                                  ("size", {"FAKE_FFV1_BADSIZE": "r2-alt-x2-bicubic"}, "x2-bicubic", "1280x572")):
        w, st, detail = row2(label, **env)
        if f"alt_{alt}=FAIL(" not in detail or note not in detail or st != "PASS" or "xpsnr_delta=+10.00dB" not in detail:
            bad.append(f"{label}:{st}:{detail[:240]}")
        if any(f"r2-alt-{alt}.mkv" in l and "xpsnr=" in l for l in ffmpeg_log(w)):
            bad.append(f"{label}:scored a file that does not line up")

    # no FSRCNNX score, no alternatives: the row FAILs as before and runs nothing more
    w, st, detail = row2("noxpsnr", FAKE_XPSNR_FAIL="1")
    if st != "FAIL" or "alt_" in detail or any("r2-alt-" in l for l in ffmpeg_log(w)):
        bad.append(f"noxpsnr:{st}:{detail[:120]}")

    # rows 3 and 4 have none
    w = World(tmp, tag + "-rows34")
    rc, out, err = w.run(["--rows", "3,4"], script=script, BENCH_RUN_ID="r34")
    if "alt_" in out or any("-alt-" in l for l in ffmpeg_log(w)):
        bad.append("rows 3 and 4 ran an alternative")
    return bad


ROUTE_TEXT = "low-res route: fsrcnnx to 1.5x then bicubic"


def lowres_failures(script, tmp, tag):
    """rev-zlqhh: on Intel a source under 480p runs FSRCNNX to round_even(source x 1.5) and then bicubic to the target, and the
    session record says so. Row 2 decides on that routed chain (the same thresholds), prints the old direct chain beside it
    without letting it decide, and FAILs when the plugin's session record for a 360p stream lacks the route part. A record the
    bench cannot read is `lowres_route=unverified`: the ffmpeg result stays and is never raised to PASS."""
    bad = []

    def row2(label, scenario=None, extra=None, **env):
        w = World(tmp, f"{tag}-{label}", scenario, extra)
        rc, out, err = w.run(["--rows", "2"], script=script, BENCH_RUN_ID="lr", **env)
        st, detail = results(out).get(2, ("", ""))
        return w, st, detail

    def argv(cmd):
        return cmd[len(FF) + 1:]

    # the routed chain is what is measured and written as r2-shader.mkv; the direct one goes to r2-direct.mkv
    w, st, detail = row2("good")
    run_dir = f"{w.out}/run-lr"
    src = f"{w.clips}/tears_360p.mkv"
    runs = ffmpeg_log(w)
    routed = argv(run_cmd(src, shader_bicubic(960, 430, 1920, 858), f"{run_dir}/r2-shader.mkv"))
    direct = argv(run_cmd(src, shader(1920, 858), f"{run_dir}/r2-direct.mkv"))
    if routed not in runs:
        bad.append(f"good:r2-shader.mkv is not the routed chain:{[l[-110:] for l in runs if 'r2-shader' in l]}")
    if direct not in runs:
        bad.append(f"good:r2-direct.mkv is not the direct chain:{[l[-110:] for l in runs if 'r2-direct' in l]}")
    nulls = [l for l in runs if " -f null -" in l and "xpsnr=" not in l]
    if len(nulls) != 2 or "scale=1920:858:" not in nulls[0] or "scale=" in nulls[1] or "libplacebo=w=960:h=430:" not in nulls[0]:
        bad.append(f"good:fps runs:{[l[-90:] for l in nulls]}")
    if st != "PASS" or not detail.startswith("new_chain=lowres-1.5x fps=120.00 xpsnr_delta=+10.00dB chroma_xpsnr=40dB"):
        bad.append(f"good:{st}:{detail[:140]}")
    if "old_chain=direct fps=120.00 xpsnr_delta=+10.00dB" not in detail or "lowres_route=ok" not in detail:
        bad.append(f"good:direct or route missing:{detail[:300]}")
    if not any("sr=fsrcnnx" in u and "MaxHeight=1080" in u and "/Videos/id360/" in u for u in w.urls()):
        bad.append("good:the route check did not stream the 360p clip with sr=fsrcnnx at 1080")
    if direct == routed:
        bad.append("good:test cannot tell the chains apart")

    # the routed chain decides: a good direct chain does not save a bad routed one, a bad direct one does not sink a good routed one
    w, st, detail = row2("routed-bad", FAKE_XPSNR_ROUTED="30", FAKE_XPSNR_DIRECT="45")
    if st != "FAIL" or "xpsnr_delta=+0.00dB" not in detail or "old_chain=direct fps=120.00 xpsnr_delta=+15.00dB" not in detail:
        bad.append(f"routed-bad:{st}:{detail[:200]}")
    w, st, detail = row2("direct-bad", FAKE_XPSNR_DIRECT="20")
    if st != "PASS" or not detail.startswith("new_chain=lowres-1.5x") or "old_chain=direct fps=120.00 xpsnr_delta=-10.00dB" not in detail:
        bad.append(f"direct-bad:{st}:{detail[:200]}")
    # the direct chain is evidence only: a write or a score that fails is named, and the row keeps the routed result
    for label, env in (("direct-write", {"FAKE_FFV1_FAIL": "r2-direct"}), ("direct-score", {"FAKE_XPSNR_FAIL_ON": "r2-direct.mkv"})):
        w, st, detail = row2(label, **env)
        if st != "PASS" or "old_chain=direct FAIL(" not in detail or "old_chain=direct fps=" in detail or "lowres_route=ok" not in detail:
            bad.append(f"{label}:{st}:{detail[:240]}")
    # a routed chain that cannot be written or scored fails the row, and the direct chain and the route check do not run
    w, st, detail = row2("routed-write", FAKE_FFV1_FAIL="r2-shader")
    if st != "FAIL" or "old_chain" in detail or "lowres_route" in detail or any("/Videos/" in u for u in w.urls()):
        bad.append(f"routed-write:{st}:{detail[:160]}")
    w, st, detail = row2("routed-noscore", FAKE_XPSNR_FAIL="1")
    if st != "FAIL" or "old_chain" in detail:
        bad.append(f"routed-noscore:{st}:{detail[:160]}")

    # a session record without the route part is the row's FAIL, with the reason, whatever ffmpeg measured
    for label, scenario in (("no-part", {"summary": "Intel Arc, fsrcnnx 360p to 1080p"}), ("no-summary", {"summary": None})):
        w, st, detail = row2(label, scenario)
        if st != "FAIL" or "plugin did not take the low-res route for a 360p source" not in detail or "lowres_route=FAIL(" not in detail \
                or "xpsnr_delta=+10.00dB" not in detail:
            bad.append(f"{label}:{st}:{detail[:240]}")
    w, st, detail = row2("other-scale", {"summary": "low-res route: fsrcnnx to 2x then bicubic"})
    if st != "FAIL" or "plugin did not take the low-res route" not in detail:
        bad.append(f"other-scale:{st}:{detail[:200]}")

    # a record the bench cannot read is unverified: the ffmpeg result stays, a pass stays a pass and a fail stays a fail
    for label, scenario, extra in (
            ("session-error", {"session_fail": True}, None),
            ("unknown-session", {"no_record": True}, None),
            ("stream-fails", {"fail_master": True}, None),
            ("no-token", None, {"BENCH_JELLYFIN_TOKEN": ""}),
            ("api-down", {"info_status": 503}, None),
            ("clip-missing", {"items_empty": True}, None)):
        w, st, detail = row2(label, scenario, extra)
        if st != "PASS" or "lowres_route=unverified(" not in detail or "lowres_route=ok" in detail or "lowres_route=FAIL" in detail:
            bad.append(f"{label}:{st}:{detail[:240]}")
        w, st, detail = row2(label + "-bad", scenario, extra, FAKE_XPSNR_ROUTED="30")
        if st != "FAIL" or "lowres_route=unverified(" not in detail:
            bad.append(f"{label}-bad:unverified raised a fail:{st}:{detail[:200]}")
    # no token, no call: nothing is asked of a server that was not named
    w, st, detail = row2("no-token-calls", None, {"BENCH_JELLYFIN_TOKEN": ""})
    if w.log():
        bad.append("no-token-calls:a request was made")
    # a token the server rejects is a FAIL, as on every other API row
    w, st, detail = row2("rejected", None, {"BENCH_JELLYFIN_TOKEN": "wrong-token"})
    if st != "FAIL" or "lowres_route=FAIL(token rejected" not in detail or "wrong-token" in detail:
        bad.append(f"rejected:{st}:{detail[:200]}")

    # a 640x360 source goes to 960x540 (the 360p clip of a 16:9 title), then bicubic to the master's 1920x1080
    w, st, detail = row2("wide", extra={"BENCH_TITLE": "wide"})
    runs = [l for l in ffmpeg_log(w) if "/wide_360p.mkv" in l and " -vf " in l]
    want = argv(run_cmd(f"{w.clips}/wide_360p.mkv", shader_bicubic(960, 540, 1920, 1080), f"{w.out}/run-lr/r2-shader.mkv"))
    if want not in runs or not all("libplacebo=w=960:h=540:" in l for l in runs if "r2-alt-lowtarget" in l or l.endswith("r2-shader.mkv")):
        bad.append(f"wide:{[l[-130:] for l in runs]}")
    return bad


def test_scored_inputs(tmp):
    for name, fn in (("argv", argv_failures), ("inputs", inputs_failures), ("trace", trace_failures), ("alts", alt_failures),
                     ("lowres", lowres_failures)):
        got = fn(BENCH, tmp, name)
        check(f"scored inputs: {name}", not got, str(got))


def fake_proc(w, name, argv0, pid="4242"):
    """A stand-in /proc (BENCH_PROC) holding one process with a DRM client: the transcode of another pod is not in the
    real /proc of the bench pod."""
    root = os.path.join(w.root, name)
    os.makedirs(os.path.join(root, pid, "fdinfo"))
    open(os.path.join(root, pid, "cmdline"), "wb").write(argv0.encode() + b"\0-i\0clip.mkv\0")
    open(os.path.join(root, pid, "fdinfo", "5"), "w").write("pos:\t0\ndrm-driver:\ti915\ndrm-client-id:\t7\ndrm-engine-render:\t1000 ns\n")
    return root


def row10_prime_failures(script, tmp, tag):
    """Row 10 primes /Gpu/Load, waits >= 5 s and reads it again (the plugin caches a figure for 5 s and the first call has
    no baseline); without a transcode visible to this pod it reports the plugin figure and says fdinfo is not visible."""
    bad = []
    w = World(tmp, tag, {"load_needs_prime": True}, {"BENCH_PROC": os.path.join(tmp, tag + "-emptyproc")})
    os.makedirs(os.path.join(tmp, tag + "-emptyproc"))
    sleeps = os.path.join(tmp, tag + "-sleeps")
    rc, out, err = w.run(["--rows", "10"], script=script, FAKE_SLEEP_LOG=sleeps)
    st, detail = results(out).get(10, ("", ""))
    loads = [e["t"] for e in w.log() if e["url"].endswith("/JellyfinPlus/Gpu/Load")]
    if len(loads) < 2 or loads[-1] - loads[0] < 0.5:  # 5 s at FAKE_FAST_WAIT's tenth-length waits
        bad.append(f"calls:{len(loads)}:{[round(t - loads[0], 1) for t in loads]}")
    # the stub shortens the wait tenfold, so the gap alone cannot tell 4 s from 5 s: the sleep it was asked for can
    asked = open(sleeps).read().split() if os.path.exists(sleeps) else []
    if "5" not in asked:
        bad.append(f"requested-sleeps:{asked}")
    if "gpuBusyPercent=12 " not in detail + " " or "gpuBusyPercent=none" in detail:
        bad.append(f"load:{detail[:120]}")
    if "fdinfo=not visible from the bench pod" not in detail or "none" in detail.replace("encoder=none", ""):
        bad.append(f"fdinfo:{detail[:120]}")
    if st != "MANUAL":
        bad.append(f"status:{st}")
    return bad


def row10_fdinfo_failures(script, tmp, tag, which=("stock", "oidn")):
    """The transcode is found whichever ffmpeg runs it: stock jellyfin-ffmpeg (a chain without a patched filter) or
    jellyfin-ffmpeg-oidn; a process that is neither is not it."""
    bad = []
    names = {"stock": "/usr/lib/jellyfin-ffmpeg/ffmpeg", "oidn": "/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg"}
    for kind in which:
        w = World(tmp, f"{tag}-{kind}", {"load": 5})
        w.env["BENCH_PROC"] = fake_proc(w, "proc", names[kind])
        rc, out, err = w.run(["--rows", "10"], script=script)
        st, detail = results(out).get(10, ("", ""))
        if st != "PASS" or "gpuBusyPercent=5" not in detail or "fdinfo_busy=0.0" not in detail:
            bad.append(f"{kind}:{st}:{detail[:120]}")
    return bad


def row10_reason_failures(script, tmp, tag):
    """rev-qd0mh: the codec is judged against the encoder the session record names (a QSV encoder must produce its own
    codec; no encoder accepts h264 or hevc and says encoder=none), from the first line ffprobe prints; a process that exits
    between the /proc scan and the cmdline read prints nothing; a transcode that exited during the window is not
    measured; every FAIL names its reason."""
    bad = []

    def one(label, scenario, extra=None, proc="empty", rows="10"):
        w = World(tmp, f"{tag}-{label}", scenario)
        if proc == "empty":
            w.env["BENCH_PROC"] = os.path.join(w.root, "emptyproc")
            os.makedirs(w.env["BENCH_PROC"])
        elif proc:
            w.env["BENCH_PROC"] = proc(w)
        rc, out, err = w.run(["--rows", rows], script=script, **(extra or {}))
        st, detail = results(out).get(10, ("", ""))
        return st, detail, err

    # the encoder matches the segment: no reason to fail, and the figure stands with fdinfo not visible (MANUAL, never FAIL)
    st, detail, err = one("match", {"load": 85.9, "encoder": "h264_qsv"})
    if st != "MANUAL" or "segment_codec=h264" not in detail or "encoder=h264_qsv" not in detail or "gpuBusyPercent=85.9" not in detail \
            or "fail=" in detail or "fdinfo=not visible from the bench pod" not in detail:
        bad.append(f"match:{st}:{detail[:160]}")
    # the encoder says hevc and the segment is h264: a FAIL naming both
    st, detail, err = one("mismatch", {"load": 85.9, "encoder": "hevc_qsv"})
    if st != "FAIL" or "fail=codec" not in detail or "hevc_qsv" not in detail or "h264" not in detail:
        bad.append(f"mismatch:{st}:{detail[:160]}")
    # no encoder in the record: h264 or hevc is accepted, and the detail says there was none
    st, detail, err = one("no-encoder", {"load": 85.9})
    if st != "MANUAL" or "encoder=none" not in detail or "fail=" in detail:
        bad.append(f"no-encoder:{st}:{detail[:160]}")
    # a segment that is neither h264 nor hevc fails with its reason, with or without an encoder
    st, detail, err = one("mpeg4", {"load": 85.9}, {"FAKE_CODEC": "mpeg4"})
    if st != "FAIL" or "fail=codec" not in detail or "mpeg4" not in detail:
        bad.append(f"mpeg4:{st}:{detail[:160]}")
    # the codec is the first line, trimmed (a second stream line or trailing blanks are not part of it)
    st, detail, err = one("first-line", {"load": 85.9, "encoder": "h264_qsv"}, {"FAKE_CODEC": "h264 ", "FAKE_CODEC_EXTRA": "1"})
    if st != "MANUAL" or "segment_codec=h264 " not in detail + " " or "fail=" in detail:
        bad.append(f"first-line:{st}:{detail[:160]}")

    # a pid directory with no cmdline (the process exited between the scan and the read) is not an error on stderr
    def with_gone(w):
        root = fake_proc(w, "proc-gone", "/usr/lib/jellyfin-ffmpeg/ffmpeg", pid="4242")
        os.makedirs(os.path.join(root, "1111"))
        return root
    st, detail, err = one("gone-cmdline", {"load": 5, "encoder": "h264_qsv"}, proc=with_gone)
    if "No such file" in err or "cmdline" in err or st != "PASS":
        bad.append(f"gone-cmdline:{st}:{err[:160]}")

    # the transcode exits during the window: no fdinfo figure, said so, and MANUAL (the plugin figure stands)
    def vanishing(w):
        return fake_proc(w, "proc-vanish", "/usr/lib/jellyfin-ffmpeg-oidn/ffmpeg")
    w = World(tmp, f"{tag}-vanish", {"load": 5, "encoder": "h264_qsv"})
    w.env["BENCH_PROC"] = vanishing(w)
    rc, out, err = w.run(["--rows", "10"], script=script, FAKE_VANISH=w.env["BENCH_PROC"] + "/4242",
                         FAKE_VANISH_LOCK=os.path.join(w.root, "vanish.lock"))
    st, detail = results(out).get(10, ("", ""))
    if st != "MANUAL" or "exited" not in detail or "gpuBusyPercent=5" not in detail or "fdinfo_busy=" in detail:
        bad.append(f"vanish:{st}:{detail[:160]}")

    # plugin and fdinfo far apart: FAIL, and it says which figures
    w = World(tmp, f"{tag}-apart", {"load": 60, "encoder": "h264_qsv"})
    w.env["BENCH_PROC"] = fake_proc(w, "proc", "/usr/lib/jellyfin-ffmpeg/ffmpeg")
    rc, out, err = w.run(["--rows", "10"], script=script)
    st, detail = results(out).get(10, ("", ""))
    if st != "FAIL" or "fail=load-vs-fdinfo" not in detail or "60" not in detail or "fdinfo_busy=0.0" not in detail:
        bad.append(f"apart:{st}:{detail[:160]}")
    return bad


def test_gates_and_confirmation(tmp):
    bad = gate_failures(BENCH, tmp, "gates")
    check("the window and PSI gates read HHMM as decimal", not bad, str(bad))
    bad = window_failures(BENCH, tmp, "windows")
    check("BENCH_BLOCK_WINDOWS and BENCH_TZ: plan line, zone, malformed values refused", not bad, str(bad))

    w = World(tmp, "nogates")
    rc, out, err = w.run(["--no-gates", "--rows", "13"], BENCH_CONFIRM_NO_GATES="")
    check("--no-gates without BENCH_CONFIRM_NO_GATES=1 exits 2", rc == 2 and "BENCH_CONFIRM_NO_GATES" in err, f"rc={rc} {err}")
    rc, out, err = w.run(["--no-gates", "--rows", "13"])
    check("--no-gates with BENCH_CONFIRM_NO_GATES=1 proceeds", rc == 0 and "MANUAL" in out, f"rc={rc} {err}")
    rc, out, err = w.run(["--help"])
    check("--help names --no-gates and its confirmation", rc == 0 and "--no-gates" in out and "BENCH_CONFIRM_NO_GATES" in out, out)
    check("--help names BENCH_BLOCK_WINDOWS and BENCH_TZ", "BENCH_BLOCK_WINDOWS" in out and "BENCH_TZ" in out, out)


def test_hermetic_api(tmp):
    """The run against a fake Jellyfin (a stub curl serving JSON by path) and the real jq."""
    # every call is judged, even when each one works
    w = World(tmp, "happy", {"stream_logs": "libplacebo=w=1920"})
    rc, out, err = w.run(["--rows", "2,3,4,12"])
    res = results(out)
    check("happy run exits 0 with a summary", rc == 0 and "summary:" in out, f"rc={rc} {err} {out[-300:]}")
    check("rows 2, 3, 4 and 12 PASS against the fake API", all(res.get(r, ("",))[0] == "PASS" for r in (2, 3, 4, 12)), str(res))
    segs = [u for u in w.urls() if "/hls1/main/" in u]
    check("each of the 7 streams (row 2's route check, rows 3 and 4, row 12's four) fetches its 5 segments (the loop keeps its token)", len(segs) == 7 * 5, str(len(segs)))
    check("a dying stream is not what passes: row 12 saw each height", all(f"{h}p:" in res.get(12, ("", ""))[1] for h in (480, 720, 1080, 2160)), str(res.get(12)))

    # item 2: an API that answers only /System/Info/Public
    w = World(tmp, "failall", {"fail_all": True}, {"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "3,4,5,6,10,12"])
    res = results(out)
    check("every row that calls a failing API records FAIL", all(res.get(r, ("",))[0] == "FAIL" for r in (3, 4, 5, 6, 10, 12)), f"{res} {err}")
    check("the run still prints summary: and exits 1", rc == 1 and "summary:" in out, f"rc={rc} {err}")
    w = World(tmp, "failmaster", {"fail_master": True})
    rc, out, err = w.run(["--rows", "3,4,10,12"])
    res = results(out)
    check("a stream that cannot start records FAIL for rows 3, 4, 10 and 12", all(res.get(r, ("",))[0] == "FAIL" for r in (3, 4, 10, 12)) and "summary:" in out, f"{res} {err}")
    w = World(tmp, "failseg", {"fail_segments": True})
    rc, out, err = w.run(["--rows", "3,12"])
    res = results(out)
    check("a stream with no segment is a FAIL, not a 0x PASS", res.get(3, ("",))[0] == "FAIL" and res.get(12, ("",))[0] == "FAIL", str(res))

    # each of these runs the script for seconds, some waiting out a stub hang: in parallel, each in its own directory
    table = [
        (token_failures, "token", "the token is on curl's stdin, not its command line"),
        (runfps_failures, "runfps", "a failed ffmpeg run reaches the FAIL branch of rows 2 and 11"),
        (size_failures, "sizes", "rows 2, 3, 4, 8 and 11 scale to the master's letterboxed size, a 16:9 clip keeps 16:9"),
        (xpsnr_failures, "xpsnr", "a failed xpsnr is FAIL 'xpsnr failed' on rows 2, 3, 4, 8 and 9, never +0.00 dB"),
        (timeout_failures, "timeouts", "every measured ffmpeg runs under timeout --kill-after=10 (BENCH_RUN_TIMEOUT, default 240) and a timeout is FAIL 'timeout after Ns'"),
        (row5_budget_failures, "row5-budget", "row 5 runs once per clip and stops after 48 frames; other rows are not cut"),
        (item_failures, "items", "an item lookup HTTP error is its own FAIL, distinct from 'not in the library'"),
        (signed_failures, "signed", "rows 2, 3, 4 and 8 print a signed delta, +10.00 or -10.00, never +-"),
        (chroma_failures, "chroma", "rows 2, 3 and 4 FAIL on chroma XPSNR when the shader output lost its chroma, and print it when it did not"),
        (binary_failures, "binary", "rows 2, 3 and 4 run the stock ffmpeg (as the shim) and row 8 the patched one, in the plan and in commands.log"),
        (row9_failures, "row9", "row 9 reads an H.264 source, cuts it once with the stock ffmpeg, measures the download/upload path and records a zero-copy failure without failing on it"),
        (stale_output_failures, "stale", "a row scores only files this run wrote: a fresh run-<id> directory (a used id is refused), any failed write is FAIL, a missing or old file is FAIL 'not written by this run'"),
        (row10_prime_failures, "row10-prime", "row 10 primes /Gpu/Load, reads it again >= 5 s later and says fdinfo is not visible from the bench pod"),
        (row10_fdinfo_failures, "row10-fd", "row 10 finds the transcode of the stock and of the oidn ffmpeg"),
        (row10_reason_failures, "row10-reason", "row 10 judges the codec by the session's encoder, tolerates a vanished pid, MANUALs a vanished transcode and names every FAIL"),
    ]

    def judged(item):
        fn, tag, _ = item
        sub = os.path.join(tmp, "c-" + tag)
        os.makedirs(sub)
        return fn(BENCH, sub, tag)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for (_, _, msg), bad in zip(table, pool.map(judged, table)):
            check(msg, not bad, str(bad))


def test_token_rejected(tmp):
    """Jellyfin 12.4 answers 401 to a token it does not accept: have_api must see that, not pass on /System/Info/Public."""
    w = World(tmp, "badtoken", extra={"BENCH_JELLYFIN_TOKEN": "wrong-token", "BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "1,3,5,6,10,12"])
    res = results(out)
    check("a rejected token is FAIL 'token rejected' on rows 1, 3, 5, 6, 10 and 12",
          all(res.get(r, ("", ""))[0] == "FAIL" and "token rejected" in res[r][1] for r in (1, 3, 5, 6, 10, 12)), f"{res} {err}")
    check("a rejected token stops at the probe: no library, plugin or stream call",
          not any(re.search(r"/Items|/Plugins|/Videos|/JellyfinPlus", u) for u in w.urls()), str(w.urls()))
    check("a rejected token is not the token in the output", "wrong-token" not in out + err)
    w = World(tmp, "info503", {"info_status": 503}, {"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "1,3,6,10,12"])
    res = results(out)
    check("an API that errors on the probe without a 401 stays SKIP, not 'token rejected'",
          all(res.get(r, ("",))[0] == "SKIP" for r in (1, 6, 10, 12)) and "token rejected" not in out and "e2e=SKIP" in res.get(3, ("", ""))[1],
          f"{res} {err}")
    w = World(tmp, "notoken", extra={"BENCH_JELLYFIN_TOKEN": ""})
    rc, out, err = w.run(["--rows", "1,6"])
    check("no token at all is SKIP and makes no call", all(results(out).get(r, ("",))[0] == "SKIP" for r in (1, 6)) and not w.log(), str(results(out)))


def test_row_1_scope(tmp):
    w = World(tmp, "row1-old")
    open(w.shim, "w").write("old session: h264_nvenc\n")
    rc, out, err = w.run(["--rows", "1"])
    st, detail = results(out).get(1, ("", ""))
    check("row 1 ignores shim-log lines written before the run", st == "PASS", f"{st} {detail} {err}")
    w = World(tmp, "row1-new", {"probe_logs": "new session: h264_nvenc"})
    open(w.shim, "w").write("old line\n")
    rc, out, err = w.run(["--rows", "1"])
    st, detail = results(out).get(1, ("", ""))
    check("row 1 fails on an nvenc line written during the run", st == "FAIL" and "nvenc/cuda-in-log" in detail, f"{st} {detail}")
    w = World(tmp, "row12-old")
    open(w.shim, "w").write("old libplacebo line\n")
    rc, out, err = w.run(["--rows", "12"])
    st, detail = results(out).get(12, ("", ""))
    check("row 12 does not take an old libplacebo line as this run's", st == "FAIL" and "no-libplacebo-in-log" in detail, f"{st} {detail}")
    w = World(tmp, "row12-new", {"stream_logs": "libplacebo=w=1920"})
    rc, out, err = w.run(["--rows", "12"])
    check("row 12 passes on a libplacebo line written during the run", results(out).get(12, ("",))[0] == "PASS", str(results(out)))


def test_no_fsr2(tmp):
    """The Intel image ships no fsr2 filter: rows 7 and 8 are SKIP with the reason, row 1 wants it withheld."""
    w = World(tmp, "nofsr2", {"game_levels": ["off"]}, extra={"FAKE_NO_FSR2": "1"})
    pan_clips(w)
    os.remove(os.path.join(w.stubs, "fsr2-nocuda"))
    rc, out, err = w.run(["--rows", "1,7,8"])
    res = results(out)
    check("without an fsr2 filter the run is not refused", rc == 0 and "not the Intel build" not in err, f"rc={rc} {err}")
    for row in (7, 8):
        st, detail = res.get(row, ("", ""))
        check(f"row {row} is SKIP, not FAIL, on a binary without fsr2",
              st == "SKIP" and "no fsr2 filter" in detail and "zero motion vectors without CUDA" in detail, f"{st} {detail}")
    check("row 8 runs no ffmpeg when it skips", not [l for l in ffmpeg_log(w) if "pan_" in l], str(ffmpeg_log(w)))
    st, detail = res.get(1, ("", ""))
    check("row 1 passes with fsr2 withheld and no marker", st == "PASS" and "fsr2 not offered" in detail, f"{st} {detail}")

    w = World(tmp, "nofsr2-offered", {"game_levels": ["off", "fsr2"]}, extra={"FAKE_NO_FSR2": "1"})
    os.remove(os.path.join(w.stubs, "fsr2-nocuda"))
    st, detail = results(w.run(["--rows", "1"])[1]).get(1, ("", ""))
    check("row 1 fails when fsr2 is still offered", st == "FAIL" and "offered:fsr2" in detail, f"{st} {detail}")

    w = World(tmp, "nofsr2-marker", {"game_levels": ["off"]}, extra={"FAKE_NO_FSR2": "1"})
    st, detail = results(w.run(["--rows", "1"])[1]).get(1, ("", ""))
    check("row 1 fails when the marker is left in the image", st == "FAIL" and "marker-present" in detail, f"{st} {detail}")


def drm_fixture(root, cards, in_dri):
    """A fake /sys/class/drm (renderDnnn/device/vendor for every card) and /dev/dri (a file per name in in_dri)."""
    sysdrm = os.path.join(root, "sys-class-drm")
    dri = os.path.join(root, "dev-dri")
    os.makedirs(dri)
    for name, vendor in cards.items():
        os.makedirs(os.path.join(sysdrm, name, "device"))
        open(os.path.join(sysdrm, name, "device", "vendor"), "w").write(vendor + "\n")
    os.makedirs(sysdrm, exist_ok=True)
    for name in in_dri:
        open(os.path.join(dri, name), "w").close()
    return {"BENCH_SYSFS_DRM": sysdrm, "BENCH_DRI_DIR": dri}, dri


ARC, RTX = "0x8086", "0x10de"
# (case, sysfs cards, names in /dev/dri, extra env, node wanted ("dri/<name>" = under the fixture dir), vulkan device wanted)
PICK_CASES = [
    ("arc-at-128", {"renderD128": ARC}, ["renderD128"], {}, "dri/renderD128", "0"),
    ("arc-at-129-alone-in-the-pod", {"renderD128": RTX, "renderD129": ARC}, ["renderD129"], {}, "dri/renderD129", "0"),
    ("arc-at-129-beside-an-rtx", {"renderD128": RTX, "renderD129": ARC}, ["renderD128", "renderD129"], {}, "dri/renderD129", "Intel"),
    ("arc-at-128-beside-an-rtx", {"renderD128": ARC, "renderD129": RTX}, ["renderD128", "renderD129"], {}, "dri/renderD128", "Intel"),
    ("the-only-node-is-used", {"renderD129": RTX}, ["renderD129"], {}, "dri/renderD129", "0"),
    ("no-node-falls-back-to-128", {}, [], {}, "/dev/dri/renderD128", "0"),
    ("a-card-with-no-dev-node-is-not-a-candidate", {"renderD129": ARC}, [], {}, "/dev/dri/renderD128", "0"),
    ("node-override", {"renderD128": RTX, "renderD129": ARC}, ["renderD129"], {"BENCH_RENDER_NODE": "/dev/dri/renderD130"},
     "/dev/dri/renderD130", "0"),
    ("vulkan-override", {"renderD128": RTX, "renderD129": ARC}, ["renderD128", "renderD129"], {"BENCH_VULKAN_DEVICE": "1"},
     "dri/renderD129", "1"),
]


def pick_failures(script, tmp, tag):
    """What the runtime pick of the render node and the Vulkan device gets wrong (empty = right), by --dry-run plan."""
    bad = []
    for case, cards, in_dri, extra, node, vk in PICK_CASES:
        env_fix, dri = drm_fixture(os.path.join(tmp, f"{tag}-{case}"), cards, in_dri)
        want = os.path.join(dri, node[4:]) if node.startswith("dri/") else node
        rc, out, err = run(script, ["--dry-run", "--rows", "1,2,9"], clean_env(dict(env_fix, **extra)))
        if rc != 0:
            bad.append(f"{case}:rc{rc}:{err[:80]}")
            continue
        if f"render node: {want}\n" not in out:
            bad.append(f"{case}:node:{re.findall(r'render node: (.*)', out)}")
        if f"vaapi=va:{want} " not in out:
            bad.append(f"{case}:row9-node")
        if f"-init_hw_device vulkan=vk:{vk} -filter_hw_device vk" not in out or f"vulkan=vk:{vk} -hwaccel vaapi" not in out:
            bad.append(f"{case}:vulkan-device:{vk}")
        if f"Vulkan vk:{vk};" not in out:
            bad.append(f"{case}:row1-must")
        if re.search(r"vk@|drm=dr:", out):
            bad.append(f"{case}:vulkan-from-drm")
        if "renderD128" not in want and "renderD128" in out:
            bad.append(f"{case}:renderD128-left-in-plan")
    return bad


def test_render_node_pick(tmp):
    bad = pick_failures(BENCH, tmp, "real")
    check("the Arc is picked by vendor or as the only node, and Vulkan is opened by index or name (never from DRM)", not bad, str(bad))
    check("a clean run (no fixture, no node) still plans renderD128 with vk:0",
          "render node: /dev/dri/renderD128\n" in run(BENCH, ["--dry-run"])[1] and not plan_failures(run(BENCH, ["--dry-run"])[1]))

    # row 1 with the Arc at renderD129 alone in the pod: the live probe opens vk:0 and says nothing of DRM
    env_fix, _ = drm_fixture(os.path.join(tmp, "row1"), {"renderD128": RTX, "renderD129": ARC}, ["renderD129"])
    w = World(tmp, "row1-arc129", extra=env_fix)
    w.run(["--rows", "1"])
    probe = [l for l in ffmpeg_log(w) if "nullsrc" in l]
    check("row 1's probe initialises Vulkan as vk:0 with no DRM device",
          len(probe) == 1 and "-init_hw_device vulkan=vk:0 " in probe[0] and not re.search(r"vk@|drm=dr:", probe[0]), str(probe))

    for name, old, new in [
        ("pick-vendor-check-dropped", '[[ -n "$intel" || "$vendor" != 0x8086 ]] || intel="$name"', '[[ -n "$intel" ]] || intel="$name"'),
        ("pick-dev-node-check-dropped", '[[ -r "$d/device/vendor" && -e "$dri/$name" ]]', '[[ -r "$d/device/vendor" ]]'),
        ("pick-fixed-128", 'else NODE=/dev/dri/renderD128; fi', 'else NODE=/dev/dri/renderD128; fi; NODE=/dev/dri/renderD128'),
        ("pick-vulkan-index-always", 'if (( count > 1 )); then VK_DEV=Intel; else VK_DEV=0; fi', 'VK_DEV=0'),
        ("pick-vulkan-override-ignored", 'VK_DEV="${BENCH_VULKAN_DEVICE:-$VK_DEV}"', ':'),
        ("pick-node-override-ignored", 'NODE="${BENCH_RENDER_NODE:-}"', 'NODE=""'),
    ]:
        path = mutant_dir(tmp, name, old, new)
        check(f"mutant {name} is caught", bool(pick_failures(path, tmp, name)))


def row6_case(tmp, tag, scenario=None, **extra):
    w = World(tmp, tag, scenario, extra={"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "6"], **extra)
    return w, rc, out, err, results(out).get(6, ("", ""))


def test_row_6(tmp):
    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-pass")
    check("row 6 PASSes when the 720p source is routed to the shader path", st == "PASS", f"{st} {detail} {err}")
    masters = [u for u in w.urls() if "master.m3u8" in u]
    check("row 6 asks for a real neural level, never `fast`",
          len(masters) == 1 and "neural=realesr-anime-x2" in masters[0] and "neural=fast" not in masters[0], str(masters))
    check("row 6 streams the 720p item", bool(masters) and "/Videos/id720/" in masters[0], str(masters))
    posts = w.posts()
    check("row 6 sets NeuralMaxSourceHeight=480 (and NeuralAllowed) then restores the configuration",
          len(posts) == 2 and posts[0].get("NeuralMaxSourceHeight") == 480 and posts[0].get("NeuralAllowed") is True
          and posts[0].get("Keep") == "me" and posts[1] == w.config0, str(posts))

    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-already", {"config": {"NeuralAllowed": True, "NeuralMaxSourceHeight": 480}})
    check("row 6 leaves a configuration that is already right alone", st == "PASS" and not w.posts(), f"{st} {detail} {w.posts()}")

    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-noapply", {"config_applies": False})
    check("row 6 FAILs, saying how to fix it, when the probe does not show 480",
          st == "FAIL" and "NeuralMaxSourceHeight" in detail and w.posts()[-1] == w.config0, f"{st} {detail}")

    for tag, scn in [("chosen", {"routing": "neural realesr-anime-x2 was chosen"}), ("none", {"routing": None}),
                     ("applied", {"applied": True})]:
        w, rc, out, err, (st, detail) = row6_case(tmp, "r6-" + tag, scn)
        check(f"row 6 FAILs on a record that is not the NOT-run wording ({tag})", st == "FAIL", f"{st} {detail}")

    # a FAIL names what the session asked for and what withheld it, with the values the record held
    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-why", {"applied": True, "requested": "realesr-anime-x2",
                                                              "withheld": "not measured on Intel"})
    check("row 6 FAIL prints neural_requested= and session_withheld= with the record's values",
          st == "FAIL" and "neural_requested=realesr-anime-x2" in detail and "session_withheld=not measured on Intel" in detail,
          f"{st} {detail}")
    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-why-empty", {"applied": True})
    check("row 6 FAIL prints neural_requested=off and session_withheld=none when the record has neither",
          st == "FAIL" and "neural_requested=off" in detail and "session_withheld=none" in detail, f"{st} {detail}")

    rc, out, err = World(tmp, "r6-fast").run(["--rows", "6"], BENCH_NEURAL_LEVEL="fast")
    check("BENCH_NEURAL_LEVEL=fast is refused (it is not a level)", rc == 2 and "BENCH_NEURAL_LEVEL" in err, f"rc={rc} {err}")

    # a server-supplied string with quotes, a backslash and a newline must not break results.jsonl
    nasty = 'say "hi" \\ back\nline two'
    w, rc, out, err, (st, detail) = row6_case(tmp, "r6-json", {"routing": nasty})
    lines = w.jsonl()
    parsed = []
    for l in lines:
        try:
            parsed.append(json.loads(l))
        except ValueError:
            parsed.append(None)
    check("every results.jsonl line is valid JSON", bool(lines) and all(parsed), str(lines))
    check("a detail with quotes, a backslash and a newline round-trips",
          bool(parsed) and parsed[-1] is not None and nasty in parsed[-1]["detail"] and parsed[-1]["status"] == "FAIL", str(lines))
    check("a multi-line detail stays on one result: line", "\nline two" not in out and "back line two" in out, out)


def test_row_5(tmp):
    extra = {"BENCH_ORT_MODEL": "/m/model.onnx"}
    w = World(tmp, "r5-ok", extra=extra)
    rc, out, err = w.run(["--rows", "5"])
    st, detail = results(out).get(5, ("", ""))
    check("row 5 on openvino is MANUAL with the device recorded", st == "MANUAL" and "device=openvino" in detail, f"{st} {detail} {err}")
    urls = w.urls()
    stream = next((i for i, u in enumerate(urls) if "master.m3u8" in u and "neural=realesr-anime-x2" in u), -1)
    sid = re.search(r"PlaySessionId=([^&]+)", urls[stream]).group(1) if stream >= 0 else ""
    sess = next((i for i, u in enumerate(urls) if u.endswith("/JellyfinPlus/Session/" + sid)), -1) if sid else -1
    check("row 5 starts its neural stream before it reads that stream's session", 0 <= stream < sess, f"{stream} {sess} {urls}")
    check("row 5 streams the 360p item", stream >= 0 and "/Videos/id360/" in urls[stream], str(urls[:2]))
    w = World(tmp, "r5-cpu", {"device": "cpu"}, extra)
    st, detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))
    check("row 5 FAILs on a CPU fallback", st == "FAIL" and "cpu" in detail, f"{st} {detail}")
    w = World(tmp, "r5-nostream", {"fail_master": True}, extra)
    st, detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))
    check("row 5 FAILs when its stream cannot start", st == "FAIL", f"{st} {detail}")

    # the exact fallback reason: the ort filter's own warning, the environment's answers, and what the session record says
    warn = "OpenVINO execution provider unavailable (Device GPU is not available), using CPU"
    w = World(tmp, "r5-reason", {"device": None, "applied": False,
                                 "routing": "neural realesr-anime-x2 NOT run: not measured on Intel"},
              dict(extra, FAKE_ORT_WARN=warn))
    st, detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))
    check("row 5 prints the ort filter's fallback reason for both clips",
          detail.count("CPU_FALLBACK[") == 2 and "Device GPU is not available" in detail, detail)
    both = "libonnxruntime_providers_openvino.so,libopenvino_intel_gpu_plugin.so,"
    check("row 5 names both ORT libraries as missing when $PREFIX/ort/lib has neither",
          f"ep_libs_missing={both} " in detail and "gpu_plugin_unresolved=none " in detail
          and "libOpenCL=UNKNOWN(" in detail and "open=FAIL(" in detail and "process_gids=" in detail, detail)
    ldconfig = any(os.path.exists(p) for p in ("/sbin/ldconfig", "/usr/sbin/ldconfig"))
    z = re.search(r"level_zero_loader=(\S+)", detail)
    check("row 5 reports the Level Zero loader as UNKNOWN(no ldconfig) only when the host has no ldconfig",
          bool(z) and (z.group(1) in ("present", "MISSING") if ldconfig else z.group(1) == "UNKNOWN(no"), detail)

    # seed the ort lib directory the report reads, and a fake ldd for the GPU plugin
    lib = os.path.join(w.stubs, "ort", "lib")
    os.makedirs(lib)
    open(os.path.join(lib, "libonnxruntime_providers_openvino.so"), "w").close()
    detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))[1]
    check("row 5 names only the GPU plugin as missing when the provider is there",
          "ep_libs_missing=libopenvino_intel_gpu_plugin.so, " in detail, detail)
    open(os.path.join(lib, "libopenvino_intel_gpu_plugin.so"), "w").close()
    ldd = os.path.join(w.stubs, "ldd")
    open(ldd, "w").write('#!/bin/sh\ncat "$FAKE_LDD_OUT"\n')
    os.chmod(ldd, 0o755)
    ldd_out = os.path.join(w.root, "ldd.txt")
    open(ldd_out, "w").write("\tlibOpenCL.so.1 => not found\n\tlibc.so.6 => /lib/libc.so.6 (0x1)\n")
    detail = results(w.run(["--rows", "5"], FAKE_LDD_OUT=ldd_out)[1]).get(5, ("", ""))[1]
    check("row 5 reports no missing ORT library, the unresolved plugin library, and libOpenCL missing from that ldd",
          "ep_libs_missing=none " in detail and "gpu_plugin_unresolved=libOpenCL.so.1, " in detail
          and "libOpenCL=MISSING " in detail, detail)
    open(ldd_out, "w").write("\tlibOpenCL.so.1 => /usr/lib/libOpenCL.so.1 (0x1)\n")
    detail = results(w.run(["--rows", "5"], FAKE_LDD_OUT=ldd_out)[1]).get(5, ("", ""))[1]
    check("row 5 reports libOpenCL present when the plugin's ldd resolves it",
          "gpu_plugin_unresolved=none " in detail and "libOpenCL=present " in detail, detail)
    detail = results(w.run(["--rows", "5"], FAKE_LDD_OUT=ldd_out, BENCH_RENDER_NODE="/dev/null")[1]).get(5, ("", ""))[1]
    check("row 5 reports open=ok for a render node this process can open (a character device)",
          "render_node=/dev/null open=ok " in detail, detail)
    check("row 5 says a null device is a session that never ran ORT, with the routing it recorded",
          st == "FAIL" and "device=none" in detail and "session_neural_requested=" in detail and "NOT run: not measured on Intel" in detail, f"{st} {detail}")
    check("row 5 keeps each chain's ffmpeg log in the run directory",
          all(os.path.isfile(os.path.join(w.out, "run-w1", f"row5-{h}p.ffmpeg.log")) for h in (360, 720)), os.listdir(w.out))
    w = World(tmp, "r5-quiet", extra=extra)
    detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))[1]
    check("row 5 without a fallback line says so instead of claiming openvino", "ffmpeg_ort_360p=no_ort_line_in_log" in detail, detail)
    # ort_why: `using CPU` is read first, and the device line alone is enough to call a CPU run a fallback
    w = World(tmp, "r5-noise", extra=dict(extra, FAKE_ORT_NOISE="1", FAKE_ORT_WARN=warn))
    detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))[1]
    check("row 5 keeps the `using CPU` reason when three earlier OpenVINO lines precede it",
          detail.count("CPU_FALLBACK[") == 2 and "using CPU" in detail and "OpenVINO runtime note 2" in detail
          and "OpenVINO runtime note 3" not in detail, detail)
    w = World(tmp, "r5-devcpu", extra=dict(extra, FAKE_ORT_DEVICE="cpu"))
    detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))[1]
    check("row 5 flags CPU_FALLBACK from a device=cpu line with no `using CPU` warning",
          detail.count("CPU_FALLBACK[") == 2 and "device=cpu" in detail and "using CPU" not in detail, detail)
    w = World(tmp, "r5-devgpu", extra=dict(extra, FAKE_ORT_DEVICE="openvino"))
    detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))[1]
    check("row 5 does not flag a device=openvino line as a fallback",
          "CPU_FALLBACK" not in detail and "ffmpeg_ort_360p=log[" in detail and "device=openvino" in detail, detail)
    w = World(tmp, "r5-chainfail", extra=dict(extra, FAKE_FFMPEG_FAIL="1"))
    st, detail = results(w.run(["--rows", "5"])[1]).get(5, ("", ""))
    check("row 5 FAILs with the environment report when the ort chain dies", st == "FAIL" and "ort_env:" in detail, f"{st} {detail}")


# What Intel neural withheld looks like to the bench: a record with `neural <level>` in Record.GpuWithheldUnmeasured, no device, no routing.
WITHHELD = {"withheld": "neural realesr-anime-x2", "device": None, "applied": False, "routing": None}
CONFIG_WITHHELD = {"config": {"NeuralAllowed": True, "NeuralMaxSourceHeight": 0, "Keep": "me", "IntelNeuralAllowed": False}}
# the stub clips are 24 fps and 240 frames: 20 s is 12 fps, 40 s is 6 fps, the default 2 s is 120 fps
SLOW = {"FAKE_RTIME_360": "20", "FAKE_RTIME_720": "40"}
SLOW_REASON = re.compile(r"^withheld on Intel: measured 12\.00/6\.00 fps below real time \(level_zero_loader=(present|MISSING|UNKNOWN)\)")
MIXED_360_REASON = "neural reaches real time on Intel at 360p (120.00 fps; 720p 6.00 fps below); review IntelNeuralAllowed"
MIXED_720_REASON = "neural reaches real time on Intel at 720p (120.00 fps; 360p 12.00 fps below); review IntelNeuralAllowed"
CPU_WARN = "OpenVINO execution provider unavailable (Device GPU is not available), using CPU"


def neural_world(script, tmp, tag, row, scenario=None, **env):
    """One bench run of row 5 or 6 with the ort model set; env goes to the run."""
    w = World(tmp, tag, scenario, extra={"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", str(row)], script=script, **env)
    return w, results(out).get(row, ("", ""))


def neural_class_failures(script, tmp, tag):
    """Rows 5 and 6 with neural withheld: all below real time is SKIP with both fps, any at real time is MANUAL, an error is FAIL (never SKIP)."""
    bad = []
    for row in (5, 6):
        for label, scn, env, want in [
            ("slow", WITHHELD, SLOW, "SKIP"),
            ("mixed720-slow", WITHHELD, {"FAKE_RTIME_720": "40"}, "MANUAL"),
            ("mixed360-slow", WITHHELD, {"FAKE_RTIME_360": "20"}, "MANUAL"),
            ("fast", WITHHELD, {}, "MANUAL"),
            ("config-slow", CONFIG_WITHHELD, SLOW, "SKIP"),
            ("nobench", WITHHELD, {"FAKE_NO_BENCH": "1"}, "FAIL"),
            ("cpu", WITHHELD, {"FAKE_ORT_WARN": CPU_WARN}, "FAIL"),
            ("ffmpeg-fail", WITHHELD, {"FAKE_FFMPEG_FAIL": "1"}, "FAIL"),
            ("nosrc-fps", WITHHELD, {"FAKE_SRC_FPS": "0/0"}, "FAIL"),
        ]:
            w, (st, detail) = neural_world(script, tmp, f"{tag}-{row}-{label}", row, scn, **env)
            if st != want:
                bad.append(f"row{row}-{label}:{st}")
            elif label == "slow" and not SLOW_REASON.match(detail):
                bad.append(f"row{row}-reason:{detail[:90]}")
            elif label == "mixed720-slow" and not detail.startswith(MIXED_360_REASON):
                bad.append(f"row{row}-mixed720-reason:{detail[:90]}")
            elif label == "mixed360-slow" and not detail.startswith(MIXED_720_REASON):
                bad.append(f"row{row}-mixed360-reason:{detail[:90]}")
            elif label == "fast" and not detail.startswith("neural reaches real time on Intel (120.00/120.00 fps); review IntelNeuralAllowed"):
                bad.append(f"row{row}-fast-reason:{detail[:90]}")
            elif label == "nosrc-fps" and "source frame rate is unreadable" not in detail:
                bad.append(f"row{row}-nosrc-reason:{detail[:90]}")
    return bad


def row5_cpu_failures(script, tmp, tag):
    """Row 5, neural withheld, but the session's own device is cpu: FAIL with that reason at any fps, never SKIP or MANUAL."""
    bad = []
    for label, env in [("slow", SLOW), ("fast", {})]:
        w, (st, detail) = neural_world(script, tmp, f"{tag}-5-cpu-{label}", 5, dict(WITHHELD, device="cpu"), **env)
        if st != "FAIL" or "device=cpu" not in detail or "CPU fallback" not in detail:
            bad.append(f"row5-cpu-{label}:{st}:{detail[:90]}")
    return bad


def row6_shader_failures(script, tmp, tag):
    """Row 6, neural withheld, but the record says neural ran or was chosen: FAIL with today's reason, not SKIP or MANUAL."""
    bad = []
    for label, scn, env in [
        ("applied-slow", dict(WITHHELD, applied=True), SLOW),
        ("applied-fast", dict(WITHHELD, applied=True), {}),
        ("chosen-slow", dict(WITHHELD, routing="neural realesr-anime-x2 was chosen"), SLOW),
        ("chosen-fast", dict(WITHHELD, routing="neural realesr-anime-x2 was chosen"), {}),
    ]:
        w, (st, detail) = neural_world(script, tmp, f"{tag}-6-{label}", 6, scn, **env)
        if st != "FAIL" or "must go to the shader path with a record saying so" not in detail or "neural_withheld=yes" not in detail:
            bad.append(f"row6-{label}:{st}:{detail[:90]}")
    return bad


def test_row_5_withheld(tmp):
    for label, scn, env, want, frag in [
        ("slow", WITHHELD, SLOW, "SKIP", "measured 12.00/6.00 fps below real time"),
        ("fast-360-slow-720", WITHHELD, {"FAKE_RTIME_720": "40"}, "MANUAL", MIXED_360_REASON),
        ("slow-360-fast-720", WITHHELD, {"FAKE_RTIME_360": "20"}, "MANUAL", MIXED_720_REASON),
        ("fast", WITHHELD, {}, "MANUAL", "neural reaches real time on Intel (120.00/120.00 fps); review IntelNeuralAllowed"),
        ("config-slow", CONFIG_WITHHELD, SLOW, "SKIP", "measured 12.00/6.00 fps below real time"),
        ("config-fast", CONFIG_WITHHELD, {}, "MANUAL", "review IntelNeuralAllowed"),
    ]:
        w, (st, detail) = neural_world(BENCH, tmp, "r5w-" + label, 5, scn, **env)
        check(f"row 5 withheld ({label}) is {want}", st == want and frag in detail, f"{st} {detail}")
        check(f"row 5 withheld ({label}) still prints fps, source fps, device, loader and the withheld state",
              all(k in detail for k in ("fps_360p=", "fps_720p=", "src_fps=24.000/24.000", "device=", "level_zero_loader=", "neural_withheld=yes")),
              detail)
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-slow-reason", 5, WITHHELD, **SLOW)
    check("row 5 withheld+slow carries the exact SKIP reason", bool(SLOW_REASON.match(detail)), detail)

    # a session-level cpu verdict stays FAIL with its reason even when neural is withheld
    for label, env in [("slow", SLOW), ("fast", {})]:
        w, (st, detail) = neural_world(BENCH, tmp, "r5w-cpu-" + label, 5, dict(WITHHELD, device="cpu"), **env)
        check(f"row 5 withheld with a cpu session ({label}) stays FAIL with its reason",
              st == "FAIL" and "CPU fallback" in detail and "device=cpu" in detail and "neural_withheld=yes" in detail, f"{st} {detail}")
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-nosrc", 5, WITHHELD, FAKE_SRC_FPS="0/0")
    check("row 5 withheld with an unreadable source fps is FAIL", st == "FAIL" and "source frame rate is unreadable" in detail, f"{st} {detail}")

    # not withheld: today's results, whatever the fps
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-open-slow", 5, None, **SLOW)
    check("row 5 not withheld and slow is still MANUAL on openvino", st == "MANUAL" and "neural_withheld=no" in detail
          and "fps_360p=12.00 fps_720p=6.00" in detail, f"{st} {detail}")
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-open-cpu", 5, {"device": "cpu"}, **SLOW)
    check("row 5 not withheld on a cpu device is still FAIL", st == "FAIL" and "device=cpu" in detail, f"{st} {detail}")
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-open-allowed", 5, {"config": {"IntelNeuralAllowed": True}, "device": "cpu"}, **SLOW)
    check("row 5 with IntelNeuralAllowed=true is not withheld", st == "FAIL" and "neural_withheld=no" in detail, f"{st} {detail}")
    w, (st, detail) = neural_world(BENCH, tmp, "r5w-open-nocfg", 5, {"device": "cpu"})
    check("row 5 never reads the configuration as withheld when the key is absent", st == "FAIL" and "neural_withheld=no" in detail, f"{st} {detail}")

    # a measurement error is FAIL with its reason, never SKIP
    for label, env, frag in [("ffmpeg-fail", {"FAKE_FFMPEG_FAIL": "1"}, "the ort chain failed at 360p"),
                             ("no-fps", {"FAKE_NO_BENCH": "1"}, "no fps measured"),
                             ("cpu-fallback", {"FAKE_ORT_WARN": CPU_WARN}, "fell back to CPU")]:
        w, (st, detail) = neural_world(BENCH, tmp, "r5w-err-" + label, 5, WITHHELD, **env)
        check(f"row 5 withheld with a measurement error ({label}) is FAIL with its reason", st == "FAIL" and frag in detail, f"{st} {detail}")


def test_row_6_withheld(tmp):
    for label, scn, env, want, frag in [
        ("slow", WITHHELD, SLOW, "SKIP", "measured 12.00/6.00 fps below real time"),
        ("fast-360-slow-720", WITHHELD, {"FAKE_RTIME_720": "40"}, "MANUAL", MIXED_360_REASON),
        ("slow-360-fast-720", WITHHELD, {"FAKE_RTIME_360": "20"}, "MANUAL", MIXED_720_REASON),
        ("fast", WITHHELD, {}, "MANUAL", "neural reaches real time on Intel (120.00/120.00 fps); review IntelNeuralAllowed"),
        ("config-slow", CONFIG_WITHHELD, SLOW, "SKIP", "measured 12.00/6.00 fps below real time"),
        ("config-fast", CONFIG_WITHHELD, {}, "MANUAL", "review IntelNeuralAllowed"),
    ]:
        w, (st, detail) = neural_world(BENCH, tmp, "r6w-" + label, 6, scn, **env)
        check(f"row 6 withheld ({label}) is {want}", st == want and frag in detail, f"{st} {detail}")
        check(f"row 6 withheld ({label}) prints fps, device, loader and the withheld state",
              all(k in detail for k in ("fps_360p=", "fps_720p=", "device=", "level_zero_loader=", "neural_withheld=yes")), detail)
        posts = w.posts()
        check(f"row 6 withheld ({label}) puts the plugin configuration back", posts[-1] == w.config0 if posts else True, str(posts))
    w, (st, detail) = neural_world(BENCH, tmp, "r6w-slow-reason", 6, WITHHELD, **SLOW)
    check("row 6 withheld+slow carries the exact SKIP reason", bool(SLOW_REASON.match(detail)), detail)

    # the shader-path check comes first: a withheld record that still says neural ran or was chosen is FAIL with today's reason
    for label, scn, env in [("applied", dict(WITHHELD, applied=True), SLOW), ("applied-fast", dict(WITHHELD, applied=True), {}),
                            ("chosen", dict(WITHHELD, routing="neural realesr-anime-x2 was chosen"), SLOW),
                            ("chosen-fast", dict(WITHHELD, routing="neural realesr-anime-x2 was chosen"), {})]:
        w, (st, detail) = neural_world(BENCH, tmp, "r6w-shader-" + label, 6, scn, **env)
        check(f"row 6 withheld but neural {label} is FAIL with the shader-path reason",
              st == "FAIL" and "must go to the shader path with a record saying so" in detail and "neural_withheld=yes" in detail, f"{st} {detail}")
        check(f"row 6 withheld but neural {label} puts the plugin configuration back", w.posts()[-1] == w.config0, str(w.posts()))
    w, (st, detail) = neural_world(BENCH, tmp, "r6w-nosrc", 6, WITHHELD, FAKE_SRC_FPS="0/0")
    check("row 6 withheld with an unreadable source fps is FAIL", st == "FAIL" and "source frame rate is unreadable" in detail, f"{st} {detail}")

    # not withheld: today's PASS/FAIL, now with the fps printed
    w, (st, detail) = neural_world(BENCH, tmp, "r6w-open-pass", 6, None, **SLOW)
    check("row 6 not withheld still PASSes on the NOT-run routing, and prints the fps",
          st == "PASS" and "fps_360p=12.00 fps_720p=6.00" in detail and "neural_withheld=no" in detail, f"{st} {detail}")
    w, (st, detail) = neural_world(BENCH, tmp, "r6w-open-fail", 6, {"routing": None}, **SLOW)
    check("row 6 not withheld with no routing is still FAIL", st == "FAIL" and "neural_withheld=no" in detail, f"{st} {detail}")
    w = World(tmp, "r6w-nomodel", WITHHELD)
    st, detail = results(w.run(["--rows", "6"])[1]).get(6, ("", ""))
    check("row 6 without BENCH_ORT_MODEL keeps today's logic", st == "FAIL" and "fps_360p" not in detail, f"{st} {detail}")

    for label, env, frag in [("ffmpeg-fail", {"FAKE_FFMPEG_FAIL": "1"}, "the ort chain failed at 360p"),
                             ("no-fps", {"FAKE_NO_BENCH": "1"}, "no fps measured"),
                             ("cpu-fallback", {"FAKE_ORT_WARN": CPU_WARN}, "fell back to CPU")]:
        w, (st, detail) = neural_world(BENCH, tmp, "r6w-err-" + label, 6, WITHHELD, **env)
        check(f"row 6 withheld with a measurement error ({label}) is FAIL with its reason", st == "FAIL" and frag in detail, f"{st} {detail}")
        check(f"row 6 withheld ({label}) puts the plugin configuration back", w.posts()[-1] == w.config0, str(w.posts()))

    # rows 5 and 6 in one run measure once
    w = World(tmp, "r56-once", WITHHELD, extra={"BENCH_ORT_MODEL": "/m/model.onnx"})
    rc, out, err = w.run(["--rows", "5,6"], **SLOW)
    res = results(out)
    check("rows 5 and 6 in one run both SKIP withheld", res.get(5, ("",))[0] == "SKIP" and res.get(6, ("",))[0] == "SKIP", str(res))
    check("rows 5 and 6 in one run measure the ort chain once per clip", len([l for l in ffmpeg_runs(w) if "ort=" in l]) == 2,
          str([l[-50:] for l in ffmpeg_runs(w) if "ort=" in l]))


def stage_meta_check(tmp, label, guid, abi, strip_source):
    """Run the real stage-plugin.sh against a stub dotnet and read the meta.json it writes."""
    root = tempfile.mkdtemp(dir=tmp)
    repo = os.path.join(root, "repo")
    for d in ("scripts", "assets", "src/patcher", "docker/intel"):
        os.makedirs(os.path.join(repo, d))
    shutil.copy(os.path.join(HERE, "plugin-meta.sh"), os.path.join(repo, "scripts"))
    shutil.copy(os.path.join(REPO, "assets", "plugin-meta.json"), os.path.join(repo, "assets"))
    open(os.path.join(repo, "assets", "jellyfinplus.png"), "wb").close()
    script = open(os.path.join(REPO, "docker", "intel", "stage-plugin.sh")).read()
    if strip_source:
        script = "".join(l for l in script.splitlines(True) if not re.match(r"\s*\.\s+\"\$REPO/scripts/plugin-meta\.sh\"", l))
    staged = os.path.join(repo, "docker", "intel", "stage-plugin.sh")
    open(staged, "w").write(script)
    bindir = os.path.join(root, "bin")
    os.makedirs(bindir)
    fake = os.path.join(bindir, "dotnet")
    # dotnet publish <proj> ... -o <dir>: leave the files stage-plugin.sh copies out of it.
    open(fake, "w").write('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                          'mkdir -p "$out"; cd "$out"; touch Jellyfin.Plugin.Plus.dll Jellyfin.Plugin.Plus.deps.json '
                          'Jellyfin.Plugin.Plus.Patcher.dll 0Harmony.dll\n')
    os.chmod(fake, 0o755)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], PLUGIN_VERSION="3.2.1.0")
    dist, patcher = os.path.join(root, "dist"), os.path.join(root, "patcher")
    p = subprocess.run([BASH, staged, repo, root, dist, patcher], capture_output=True, text=True, env=env)
    try:
        doc = json.load(open(os.path.join(dist, "JellyfinPlus_3.2.1.0", "meta.json")))
    except (OSError, ValueError):
        doc = {}
    ok = bool(doc) and p.returncode == 0 and doc.get("guid") == (guid.group(1) if guid else None) \
        and doc.get("targetAbi") == (abi.group(1) if abi else None)
    if strip_source:
        check(f"{label} fails instead of writing meta.json", not ok and p.returncode != 0, p.stdout + p.stderr)
    else:
        check(f"{label} writes meta.json with plugin-meta.sh's guid and targetAbi", ok, p.stdout + p.stderr)


def test_shared_files(tmp):
    meta = os.path.join(HERE, "plugin-meta.sh")
    check("scripts/plugin-meta.sh exists", os.path.isfile(meta))
    src = open(meta).read() if os.path.isfile(meta) else ""
    guid = re.search(r"^PLUGIN_GUID=\"?([0-9a-f-]{36})", src, re.M)
    abi = re.search(r"^PLUGIN_TARGET_ABI=\"?([0-9.]+)", src, re.M)
    plugin_cs = open(os.path.join(REPO, "src", "Plugin.cs")).read()
    manifest = json.load(open(os.path.join(REPO, "manifest.json")))[0]
    check("plugin-meta.sh GUID is Plugin.cs's Id", bool(guid) and f'Guid.Parse("{guid.group(1)}")' in plugin_cs)
    check("plugin-meta.sh GUID is manifest.json's", bool(guid) and manifest["guid"] == guid.group(1))
    check("plugin-meta.sh target ABI is manifest.json's", bool(abi) and manifest["versions"][0]["targetAbi"] == abi.group(1))
    for rel in ("scripts/proxmox-build.sh", "docker/intel/stage-plugin.sh"):
        text = open(os.path.join(REPO, rel)).read()
        if rel.startswith("scripts/"):
            check(f"{rel} sources plugin-meta.sh", re.search(r"^\.\s+\S*scripts/plugin-meta\.sh", text, re.M) is not None)
        check(f"{rel} carries neither the GUID nor the target ABI", not (guid and guid.group(1) in text) and not (abi and abi.group(1) in text.replace("12.1.0.0", "")))
    p = subprocess.run([BASH, "-c", f". {meta}; plugin_first_meta 9.8.7.6"], capture_output=True, text=True)
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        doc = {}
    check("plugin_first_meta prints the first-install meta.json",
          doc.get("guid") == (guid.group(1) if guid else None) and doc.get("version") == "9.8.7.6" and doc.get("name") == "Jellyfin Plus"
          and doc.get("targetAbi") == (abi.group(1) if abi else None) and {"owner", "overview", "category"} <= set(doc), p.stdout + p.stderr)
    stage_meta_check(tmp, "stage-plugin.sh", guid, abi, strip_source=False)
    stage_meta_check(tmp, "stage-plugin.sh without its source line", guid, abi, strip_source=True)

    ignore = open(os.path.join(REPO, "docker", "intel", "Dockerfile.dockerignore")).read().split()
    for pat in ("**/.env*", "**/*.pem", "**/*.key"):
        check(f"Dockerfile.dockerignore excludes {pat}", pat in ignore, str(ignore))


def main():
    if not BASH:
        print("no bash")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        parts = (test_plan_and_refusals, test_stub_run, test_mutants, test_webinject_without_dpkg_package,
                 test_gates_and_confirmation, test_hermetic_api, test_token_rejected, test_row_1_scope, test_no_fsr2,
                 test_render_node_pick, test_row_6, test_row_5, test_row_5_withheld, test_row_6_withheld,
                 test_shared_files, test_scored_inputs)

        def go(part):
            sub = os.path.join(tmp, part.__name__)
            os.makedirs(sub)
            part(sub)

        # the parts share nothing but the check() counters: the two long ones (mutants, hermetic API) overlap the rest
        with ThreadPoolExecutor(max_workers=4) as pool:
            for f in [pool.submit(go, p) for p in parts]:
                f.result()
        dockerfile_checks(tmp)
    print(f"{PASSES} checks passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
