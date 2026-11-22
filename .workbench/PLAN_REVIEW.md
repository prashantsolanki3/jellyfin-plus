# Plan review, round 1

I re-ran the checks myself with Read, Grep, `git diff` and `git log`. The tool-call budget ran out before I could finish the scope check. What was left unchecked is labelled INFERRED below.

## Premises

- **P1 VERIFIED (load-bearing).** `git diff 1117833 449a361 -- ffmpeg/gu_inputs.h` shows the removed lines.
  - They returned `ret` for a failed `cuda_load_functions`, and used `GU_CHECK_CU` for `cuInit`, `cuDeviceGet`, retain and push.
  - The added `gu_cuda_open` result only sets `cuda_on` and logs "no usable CUDA ... ZERO".
  - That is the reviewer's point 1.
- **P2 VERIFIED (load-bearing).** The same diff shows that before the PR only `gu_nvof_init` failing was soft (`nvof_ready = 0`).
  - Everything before it was a hard error.
  - The plan's fix (hard error under `#ifndef GU_NO_NVOF`, soft NVOFA only) restores the original behaviour.
- **P3 VERIFIED (load-bearing).**
  - `ffmpeg/vf_fsr2.c` has a `device` option (line 162, default 0), and picks `devs[device_index < n ? device_index : 0]` after `vkEnumeratePhysicalDevices` (lines 313-334).
  - In `ShaderLibrary.cs`, the only `device=` emission is the ort filter (line 1460).
  - `GameFilter` (1164 onwards) emits no `device=`. So a probe running the same fsr2 node without `-init_hw_device` tests the device index a session uses.
  - The mixed-box gap (index 0 not Intel) is correctly called pre-existing.
- **P4 VERIFIED, not load-bearing.**
  - `git diff HEAD worktree-agent-b7ff7a746bf4099c -- src/patcher/CachedProbe.cs` shows a new, self-contained 166-line file in namespace `Jellyfin.Plugin.Plus.Patcher`.
  - `GpuVendorHarness.csproj` line 28 includes `../../src/patcher/**/*.cs`.
  - The semantics fit: `Ok == true` is final, anything else is retried after the TTL, and `Peek` never probes inline.
  - A throw after an earlier `Ok=false` keeps the old value, which is not a pass, so that is harmless.
- **P5 VERIFIED (load-bearing).**
  - `NvidiaOnly` (2231-2245) calls `NoCudaGameNow` for game levels (2241).
  - `OfferedOnThisGpu` (2281), `AddWithheld` (2310) and `Decide` (1588, `Withhold(... NvidiaOnly("Game", ...))`) all go through `NvidiaOnly`.
  - Requiring the probe in `NoCudaGameNow` therefore changes offer, withheld and run together.
- **P6 VERIFIED, not load-bearing.** `no_cuda.c` line 163: `f_retain` sets `*c` before `step()`. The scenarios (257-266) are load, default, cuInit and push. There is no retain scenario.
- **P7 VERIFIED, not load-bearing.** `gu_inputs_uninit` (833-856) guards both the push and the pop/release on `g->cu && g->cu_ctx`. `cuda_free_functions` runs afterwards. The expected call list `load,cuInit,cuDeviceGet,retain,free` is right.
- **P8 VERIFIED, not load-bearing.** `build-ffmpeg.sh` 920-931 removes the marker only in the `elif ORT_EP=openvino` branch. A later non-openvino build leaves it. The header sentence "removed by an OpenVINO build without it" is also stale, as the plan says.
- **P9: I did the hand arithmetic myself and it matches the plan, so the check is VERIFIED.**
  - The pre-patch line numbers of 0001 and 0015 put the 0016 old-side context at configure 2073, 4155 and 7361, Makefile 420 and allfilters 393. Those starts and hunk counts are right.
  - The `+` starts that are wrong are hunk 2 (4155 should be 4156) and hunk 3 (7361 should be 7363).
  - `git apply` ignores `+` starts, so the reviewer's "does not apply" may come from real FFmpeg 8.1.2 context. That stays INFERRED until `git apply --check` is run on a real tree, and the plan says so.
- **P10 INFERRED (accepted).** The shell does not allow bash, dotnet, network or `git apply`. So the C test and the harness counts cannot be produced here, and a later stage must not approve them without output.
- **P11 VERIFIED.** Glob finds neither `docs/agent-lessons.md` nor `docs/automations.md`.

## Scope defects (files is not the whole change)

1. **`src/Configuration/configPage.html` is missing from `files`.**
   - `withheldText()` (lines 819-828) hard-codes "Not offered on this GPU (needs NVIDIA): ..." for every withheld level and ignores the served reason.
   - The plan gives fsr2 a new withheld reason ("probe failed or pending") on an Intel box that has the marker. The dashboard would then still say "needs NVIDIA" for it.
   - That is the overstatement AGENTS.md invariant 7 forbids. The plan's claim that "a small `AddWithheld` change means invariant 7 holds" is therefore not true.
   - The page is an embedded resource, so the plan must also say that it needs a rebuild and restart.
   - It must be added to `files`, with a test or a check (CLAUDE.md's "5 places" rule for the page).
2. **The existing harness case `Program.cs` 911-912 asserts every withheld level equals "needs NVIDIA".**
   - The plan lists new cases but does not name this assertion.
   - It must say whether fsr2 can appear in that rig (marker present, probe not passed). If it can, the assertion has to change deliberately.
3. **INFERRED, not checked (budget ran out).**
   - How the session summary and the `GpuWithheld` record (written in the `Withhold` region, around line 2325 onwards) carry the reason string.
   - Whether any web client file also reads the reason text.
   - The plan should list them as a check.

## TDD honesty

The tdd label "required" is honest, and the test list answers real bugs:
- a probe-less pass;
- a permanent failure cache;
- the probe running on the request path;
- a NVIDIA box spawning the probe;
- an `-init_hw_device` that tests the wrong device;
- an uninit on a half-open context.

The `test_patch_stack.py` check is a stdlib reconstruction and cannot replace `git apply`. The plan says so. The plan has no test for the config page text (scope defect 1).

VERDICT: REJECT
