# Plan: rev-7n4fe.4 - explicit Enhance choice beats the slow-link default

## What I found (code, not memory)

- `state.stage` (model/state.js) already is the "explicit choice" record. `'unset'` is Automatic (no opinion). `'off'`, `'custom'` or a stage recipe object is something the viewer picked. All writers are in view/panel-dom.js (Quality chips, slider, axis pick). `resetUpscaleForNewSource` (controller/playback-hooks.js) puts it back to `'unset'` on each new item, so a stored stage never leaks across items. No new flag is needed: a pure `explicitChoice(stage)` in model/link-profile.js answers it.
- `wireParams()` (controller/network.js:46) returns all-off whenever `linkProfile().slow`. Fix: do that only when `!explicitChoice(state.stage)`. With a choice, fall through to the normal path, which already sends the stage and the other axes and the `swapfrom` marker. Slow link with no choice stays exactly as today.
- A conflict outside wireParams that the bead says to name: on a slow link `forceTranscodeUrl` and `forceTranscodeBody` only call `slowUrl`/`slowBody`. Those set `EnableDirectPlay=false` and the bitrate cap but leave `EnableDirectStream` true, so Jellyfin would still remux (stream copy) and the explicit choice would do nothing. For slow plus an explicit enhancement (`anyEnhancement()` true), also send `EnableDirectStream=false`. The cap and the no-direct-play rule stay unchanged, and `slowBody`/`slowUrl` themselves stay unchanged (the existing test asserts `EnableDirectStream` stays true). The stale comment at network.js:333 gets fixed. Say this in the PR.
- The notice: `model/link-profile.js` gets `SLOW_NOTICE = 'Link slow: enhancement off (tap Enhance to override)'` and a pure `slowNotice(slow, stage)`. It returns the text only when slow and no explicit choice, else null. `controller/link-probe.js` gets `slowOffNotice()` (= `slowNotice(linkProfile().slow, state.stage)`).
  - Badge: `badgeModel(caps, s, act, notice)` (model/player-badge.js) shows the notice as text and title when it is set and `act.key !== 'active'`. The key stays `act.key`, so CSS is untouched. `controller/player-badge.js` render passes `slowOffNotice()`.
  - Playback Info: `liveLines()` (controller/live-block.js) feeds both the panel and the "Enhance - " rows in playback-hooks.js (via `liveRows`). It gets `['Enhancement', notice]` first, replacing the existing 'Enhancement' line in the `!s` branch. No change to playback-hooks.js is needed.
- Built asset: `web/jellyfin-plus.js` is gitignored and not committed (CLAUDE.md, `.gitignore`). `scripts/build-web-panel.sh` and the proxmox build make it, so there is nothing to rebuild or commit.
- Scope guards respected: no `src/patcher`, no thresholds (SLOW_RTT_MS, SLOW_MBPS), no bench, no `.github`.

## Tests (written first, must fail before the fix)

The web tests are plain node scripts (`node web/src/<dir>/<name>.test.mjs`). No npm test runner and no CI wiring exists for them.

- `web/src/model/link-profile.test.mjs`: `explicitChoice` ('unset'/null/undefined are no, 'off'/'custom'/recipe object are yes). `slowNotice` gives the text for slow with unset, null for slow with a choice, and null for fast. Existing tests stay.
- `web/src/model/player-badge.test.mjs`: with a notice and act 'off' the text is the notice. With act 'active' the summary wins. With no notice nothing changes.
- New `web/src/controller/network-slow.test.mjs`: stubs `window`, `document` and `localStorage`, sets `state.link = {rtt: 200}` and `state.serverCaps = {full: true}`, imports network.js and checks:
  - slow + stage `{rank, sr, denoise}` or `'custom'` with `prefs.upscale='1080'` gives `wireParams().upscale` equal to the stage's target and not 'off';
  - slow + `'unset'` gives all seven axes 'off';
  - fast link is unchanged (null for unset, the normal object for a stage);
  - slow + explicit gives forceTranscodeUrl output containing `enableDirectStream=false` and `enableDirectPlay=false` (export the two force functions if not already exported).
- Mutant check: each case asserts both sides of the flag, so a wireParams that ignores `explicitChoice` (always off, or never off) fails one case. The test also runs the same assertions against a deliberately mutated copy of the decision function and expects them to throw.

Honest limit: node is refused in this workbench shell (checked: `node --version` is blocked by policy), so the web tests cannot be run in the plan, implement or test stages here. I will keep the code simple, check imports by reading, and report plainly that the node count is not shown. If the network.js import graph needs more DOM stubs than I can verify, I keep the pure-function tests as the guard and say so.

`python3 scripts/test_bench_intel.py` is unrelated to this change and runs as the required regression. `bash -n` is moot (no shell scripts touched) and is not on the allow list either.

```yaml
{
  "goal": "On a slow link, an explicit Enhance choice (state.stage != 'unset') is sent by wireParams and forces a real transcode; with no choice the axes stay off and the badge and Playback Info rows say 'Link slow: enhancement off (tap Enhance to override)'.",
  "files": [
    "web/src/model/link-profile.js",
    "web/src/model/link-profile.test.mjs",
    "web/src/model/player-badge.js",
    "web/src/model/player-badge.test.mjs",
    "web/src/controller/link-probe.js",
    "web/src/controller/network.js",
    "web/src/controller/network-slow.test.mjs",
    "web/src/controller/player-badge.js",
    "web/src/controller/live-block.js",
    "ENHANCEMENTS.md"
  ],
  "test_commands": [
    "node web/src/model/link-profile.test.mjs",
    "node web/src/model/player-badge.test.mjs",
    "node web/src/controller/network-slow.test.mjs",
    "python3 scripts/test_bench_intel.py"
  ],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "slow link (state.link rtt 200) with an explicit stage: wireParams().upscale is that stage's target, not 'off'",
    "slow link with stage 'unset': all axes 'off' as today, and the badge model and liveLines report 'Link slow: enhancement off (tap Enhance to override)'",
    "fast link: wireParams unchanged",
    "a mutant that ignores the explicit flag fails the tests",
    "slow link plus explicit enhancement also sends EnableDirectStream=false (URL and body) while the bitrate cap and no-direct-play stay",
    "slowBody and slowUrl, the thresholds, src/patcher, bench and .github are untouched",
    "web tests pass (count shown, or stated as not runnable in this shell); python3 scripts/test_bench_intel.py passes"
  ],
  "premises": [
    {"id": "P1", "claim": "state.stage is the record of an explicit choice: 'unset' = none, written only by panel-dom.js handlers, reset to 'unset' per new source", "label": "VERIFIED", "check": "Read state.js:117, panel-dom.js:504-587 and 955, playback-hooks.js:58-78, prefs-store.js", "load_bearing": true},
    {"id": "P2", "claim": "wireParams all-off path is the only place the panel's choice is dropped on a slow link", "label": "VERIFIED", "check": "Read network.js:41-55; effective()/anyEnhancement() read wireParams, so the fix flows to wantsRewrite and the live stale-note", "load_bearing": true},
    {"id": "P3", "claim": "slowUrl/slowBody leave EnableDirectStream untouched, so slow plus explicit would still remux unless network.js also forces EnableDirectStream=false", "label": "VERIFIED", "check": "Read link-profile.js:51-84 and link-profile.test.mjs:43-51 (asserts EnableDirectStream stays true); network.js:330-396", "load_bearing": true},
    {"id": "P4", "claim": "The badge and the Playback Info 'Enhance - ' rows share their text sources: badgeModel via controller/player-badge.js render, and liveLines via liveRows", "label": "VERIFIED", "check": "Read controller/player-badge.js:107-136, live-block.js:96-185, panel-dom.js:607-611, playback-hooks.js:323-330", "load_bearing": true},
    {"id": "P5", "claim": "web/jellyfin-plus.js is not committed, so no built asset needs regenerating", "label": "VERIFIED", "check": "Glob web/jellyfin-plus.js returned nothing; CLAUDE.md says it is gitignored and built by scripts/build-web-panel.sh", "load_bearing": false},
    {"id": "P6", "claim": "Web tests are plain node scripts with no runner or CI wiring, and node cannot be run in this workbench", "label": "VERIFIED", "check": "Grep for .test.mjs in scripts/.github/package.json found none; 'node --version' refused by the shell policy", "load_bearing": false},
    {"id": "P7", "claim": "controller/network.js can be imported under node with window/document/localStorage stubs", "label": "INFERRED", "check": "Cannot run node here; controller/prefs-sync.test.mjs imports controller code with window and localStorage stubs only. Settle by running node web/src/controller/network-slow.test.mjs in CI or locally. Fallback: the pure-function tests carry the guard", "load_bearing": false},
    {"id": "P8", "claim": "Showing the notice only when stage is 'unset' is right: with a choice the axes are sent, so the server (not the link) decides what ran", "label": "INFERRED", "check": "Follows from the wireParams change; settle live on a throttled phone session, which is not possible from this workbench", "load_bearing": false}
  ],
  "reuse_check": [
    {"need": "explicit-choice flag", "checked": "existing state.stage ('unset' vs anything else) in web/src/model/state.js", "verdict": "use", "reason": ""},
    {"need": "badge and Playback Info wording", "checked": "existing badgeModel (model/player-badge.js) and liveLines/liveRows (controller/live-block.js, view/panel-dom.js)", "verdict": "extend", "reason": ""},
    {"need": "web test runner", "checked": "existing plain-node *.test.mjs convention (assert/strict, t() helper) in web/src", "verdict": "use", "reason": ""}
  ]
}
```
