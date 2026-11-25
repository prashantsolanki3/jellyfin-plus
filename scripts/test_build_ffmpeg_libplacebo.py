#!/usr/bin/env python3
"""scripts/build-ffmpeg.sh builds libplacebo with jellyfin-ffmpeg's patch and meson flags.

    python3 -I scripts/test_build_ffmpeg_libplacebo.py

Needs no root, GPU, network or compiler. It cuts the libplacebo block out of the real build script and runs
it under bash with stub git, patch, meson and ninja that only record their arguments, then proves:

  - the checkout is the pinned commit and the vendored patch is applied with patch -p1 after it and before
    meson setup;
  - meson setup receives every flag jellyfin-ffmpeg builds with, plus the ones this build already had;
  - a stamp that matches skips everything, and a changed patch changes the stamp (so a host that built the
    old libplacebo rebuilds);
  - the vendored patch and its README (which names the source) exist;
  - mutation: dropping the patch step, any flag, or the patch from the stamp, or applying the patch after
    meson, is caught.

It does not run a real patch or build libplacebo: whether the patch applies to the pinned commit is settled
by the image build. Exit status: 0 every check passed, 1 a check failed.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "build-ffmpeg.sh")
PATCH_DIR = os.path.join(REPO, "builder", "patches", "libplacebo")
BASH = shutil.which("bash")

FLAGS = [
    "--buildtype=release", "-Dvk-proc-addr=enabled", "-Dglslang=disabled",
    "-Dtests=false", "-Dbench=false", "-Dfuzz=false",
    "--prefix=/usr/local", "--libdir=lib/x86_64-linux-gnu", "--default-library=static",
    "-Dvulkan=enabled", "-Dshaderc=enabled", "-Ddemos=false",
]
INSTALLED_A = "/usr/local/lib/x86_64-linux-gnu/libplacebo.a"

STUBS = {
    "git": 'if [ "$1" = init ]; then mkdir -p "${@: -1}"; fi',
    "patch": "",
    "meson": "",
    "ninja": "",
}

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + detail))
    if not ok:
        FAILED.append(name)


def block_of(text):
    m = re.search(r"^# --- libplacebo -.*?(?=^# --- nv-codec-headers)", text, re.S | re.M)
    assert m, "libplacebo block not found in build-ffmpeg.sh"
    return m.group(0)


def ref_line(text):
    m = re.search(r"^LIBPLACEBO_REF=.*$", text, re.M)
    assert m, "LIBPLACEBO_REF assignment not found in build-ffmpeg.sh"
    return m.group(0)


def run_block(block, ref, patches, installed, stamp=None):
    """Run the block once. Returns (exit code, stderr, recorded calls, stamp file text, stamp path)."""
    with tempfile.TemporaryDirectory() as tmp:
        stubs = os.path.join(tmp, "stubs")
        os.makedirs(stubs)
        log = os.path.join(tmp, "calls.log")
        for name, body in STUBS.items():
            path = os.path.join(stubs, name)
            with open(path, "w") as f:
                f.write('#!/usr/bin/env bash\nprintf \'%s|%s|%s\\n\' "$(basename "$0")" "$(basename "$PWD")" "$*" >> "$LOG"\n' + body + "\n")
            os.chmod(path, 0o755)
        repo = os.path.join(tmp, "repo")
        pdir = os.path.join(repo, "builder", "patches", "libplacebo")
        os.makedirs(pdir)
        for name, content in patches.items():
            with open(os.path.join(pdir, name), "w") as f:
                f.write(content)
        stamp_dir = os.path.join(tmp, "stamps")
        os.makedirs(stamp_dir)
        if stamp is not None:
            with open(os.path.join(stamp_dir, "libplacebo.tag"), "w") as f:
                f.write(stamp)
        installed_a = os.path.join(tmp, "libplacebo.a")
        if installed:
            open(installed_a, "w").close()
        assert INSTALLED_A in block, "installed-library path not found in the libplacebo block"
        prog = "\n".join([
            "set -euo pipefail",
            "say () { :; }",
            'die () { printf "!! %s\\n" "$*" >&2; exit 1; }',
            'HERE="%s"' % repo,
            'STAMP_DIR="%s"' % stamp_dir,
            ref,
            block.replace(INSTALLED_A, installed_a),
        ])
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        env = dict(os.environ, PATH=stubs + os.pathsep + os.environ["PATH"], LOG=log)
        r = subprocess.run([BASH, "-c", prog], cwd=work, env=env, capture_output=True, text=True)
        calls = []
        if os.path.exists(log):
            with open(log) as f:
                calls = [line.rstrip("\n").split("|", 2) for line in f]
        stamp_path = os.path.join(stamp_dir, "libplacebo.tag")
        new_stamp = open(stamp_path).read().strip() if os.path.exists(stamp_path) else None
        return r.returncode, r.stderr, calls, new_stamp, repo


def scenario(block, ref):
    """Every behaviour the block must have; returns a list of failure descriptions."""
    bad = []
    patches = {"0001-a.patch": "alpha\n", "0002-b.patch": "beta\n"}

    rc, err, calls, stamp, repo = run_block(block, ref, patches, installed=False)
    cmds = ["%s %s" % (c[0], c[2]) for c in calls]
    if rc != 0:
        bad.append("fresh run exited %d: %s" % (rc, err.strip()))
        return bad
    ref_sha = re.search(r"\$\{LIBPLACEBO_REF:-([^}]+)\}", ref).group(1)
    idx = {}
    for key, pred in (
        ("fetch", lambda c: c.startswith("git ") and " fetch " in c and c.endswith(ref_sha)),
        ("checkout", lambda c: c.startswith("git ") and " checkout " in c and "FETCH_HEAD" in c),
        ("patch1", lambda c: c.startswith("patch ") and "-p1" in c and c.endswith("0001-a.patch")),
        ("patch2", lambda c: c.startswith("patch ") and "-p1" in c and c.endswith("0002-b.patch")),
        ("meson", lambda c: c.startswith("meson ") and " setup " in c),
    ):
        hits = [i for i, c in enumerate(cmds) if pred(c)]
        if not hits:
            bad.append("no %s call: %s" % (key, cmds))
        else:
            idx[key] = hits[0]
    if len(idx) == 5:
        if not idx["fetch"] < idx["checkout"] < idx["patch1"] < idx["patch2"] < idx["meson"]:
            bad.append("order is not fetch, checkout, patches, meson: %s" % idx)
        if calls[idx["patch1"]][1] != "libplacebo":
            bad.append("patch ran in %r, not in the libplacebo checkout" % calls[idx["patch1"]][1])
        meson_argv = cmds[idx["meson"]].split()
        for flag in FLAGS:
            if flag not in meson_argv:
                bad.append("meson setup lacks %s" % flag)
    if stamp is None:
        bad.append("no stamp written")

    # a matching stamp and an installed library skip the whole block
    rc, err, calls, _, _ = run_block(block, ref, patches, installed=True, stamp=stamp)
    if rc != 0 or calls:
        bad.append("matching stamp did not skip the rebuild: rc=%d calls=%s" % (rc, calls))

    # a changed patch invalidates the stamp
    rc, err, calls, new_stamp, _ = run_block(block, ref, {"0001-a.patch": "alpha\n", "0002-b.patch": "BETA\n"},
                                             installed=True, stamp=stamp)
    if not any(c[0] == "meson" for c in calls) or new_stamp == stamp:
        bad.append("a changed patch did not trigger a rebuild with a new stamp")

    # the old tag-only stamp rebuilds
    rc, err, calls, _, _ = run_block(block, ref, patches, installed=True, stamp="v7.351.0")
    if not any(c[0] == "meson" for c in calls):
        bad.append("the previous tag-only stamp did not trigger a rebuild")

    # no patches at all is a build error, not a silent unpatched build
    rc, err, calls, _, _ = run_block(block, ref, {}, installed=False)
    if rc == 0 or any(c[0] == "meson" for c in calls):
        bad.append("a build with no patches under builder/patches/libplacebo went ahead")
    return bad


def mutants(block):
    out = []
    out.append(("patch step dropped", block.replace('patch -p1 -i "$patch"', "true")))
    loop = re.search(r"^    for patch in .*?^    done\n", block, re.S | re.M)
    meson_line = re.search(r'^    meson setup .*\n', block, re.M)
    if loop and meson_line:
        moved = block.replace(loop.group(0), "")
        moved = moved.replace(meson_line.group(0), meson_line.group(0) + loop.group(0))
        out.append(("patch applied after meson", moved))
    out.append(("patches left out of the stamp", block.replace('cat "${LIBPLACEBO_PATCHES[@]}"', "true")))
    out.append(("tag-only stamp", re.sub(r'^LIBPLACEBO_STAMP=.*$', 'LIBPLACEBO_STAMP="$LIBPLACEBO_REF"', block, flags=re.M)))
    for flag in FLAGS:
        out.append(("flag %s dropped" % flag, block.replace(" " + flag, "", 1)))
    return out


def main():
    if not BASH:
        print("SKIP bash not found")
        return 0
    with open(SCRIPT) as f:
        text = f.read()
    block, ref = block_of(text), ref_line(text)

    patch_files = sorted(p for p in os.listdir(PATCH_DIR) if p.endswith(".patch")) if os.path.isdir(PATCH_DIR) else []
    check("a libplacebo patch is vendored", bool(patch_files), PATCH_DIR)
    for p in patch_files:
        check("%s is non-empty and a diff" % p, "\n--- a/" in open(os.path.join(PATCH_DIR, p)).read())
    readme = os.path.join(PATCH_DIR, "README.md")
    body = open(readme).read() if os.path.exists(readme) else ""
    check("README names jellyfin-ffmpeg builder/patches/libplacebo and issue 349",
          "jellyfin-ffmpeg" in body and "builder/patches/libplacebo" in body and "issues/349" in body)
    check("the pinned ref is a full commit sha",
          bool(re.search(r"\$\{LIBPLACEBO_REF:-[0-9a-f]{40}\}", ref)), ref)

    bad = scenario(block, ref)
    check("libplacebo block behaves as required", not bad, "; ".join(bad))

    for name, mutated in mutants(block):
        if mutated == block:
            check("mutant applies: " + name, False, "the text to mutate was not found")
            continue
        found = scenario(mutated, ref)
        check("mutant caught: " + name, bool(found), "scenario still passed")

    print("\n%s" % ("ALL PASS" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
