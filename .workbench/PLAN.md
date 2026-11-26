# Plan: row 9 cpu_secs reads the wrong `bench:` line

`cpu_secs` in scripts/bench-intel.sh (lines 1099-1110) awk-matches every `/^bench:/` line and keeps the last
one's utime+stime. Real `ffmpeg -benchmark` ends with `bench: maxrss=...KiB`, which has neither field, so the
sum resets to 0 and `seen` is still 1: row 9 prints on=0.00s off=0.00s and cpu_gain=0.0%. The fix narrows the
awk pattern to `/^bench: utime=/`. When no such line exists `seen` stays 0, `cpu_secs` prints nothing, and
`run_cpu` already returns 98 ("printed no bench: line"). The ffv1 pair uses the same function, so it shows
`none` there instead of 0. The comment above `cpu_secs` is updated to match. No thresholds or other script
logic change.

Tests (scripts/test_bench_intel.py): the two ffmpeg stub printf lines (zero-copy at ~479, `-benchmark` at ~537)
print the real two-line output, `bench: utime=.. stime=.. rtime=..` followed by `bench: maxrss=26176KiB`. A new
mutant `row9-cpu-last-bench-line` turns `/^bench: utime=/ {` back into `/^bench:/ {` and is registered in the
behaviour-mutant list with `row9_failures`, which then sees on=0.00s and must fail. `row9_failures` also gains a
case where the stub prints no `bench:` line (`FAKE_NO_BENCH=1`). The row must FAIL with "printed no bench: line"
and never report 0.00s. The `ThreadPoolExecutor` scheme in `main()` and `test_mutants` stays as it is. Then
run `python3 scripts/test_bench_intel.py` and quote the final "N checks passed, M failed" line and the wall
time in the PR body.

Scope: scripts/bench-intel.sh and scripts/test_bench_intel.py only. The bead's `## Do not` (no plugin C#, no
shim) is respected. Nothing outside the bead's Where is needed.

```yaml
{
  "goal": "Make bench-intel.sh cpu_secs read utime/stime only from the `bench: utime=` line so row 9 stops reporting 0.00s, return status 98 when no such line exists, and cover it with realistic two-line ffmpeg stubs plus a caught mutant.",
  "files": ["scripts/bench-intel.sh", "scripts/test_bench_intel.py"],
  "test_commands": ["python3 scripts/test_bench_intel.py"],
  "tdd": "required",
  "fast_path": false,
  "acceptance": [
    "cpu_secs sums utime and stime only from the `bench: utime=` line; a trailing `bench: maxrss=` line does not zero it",
    "A run with no `bench: utime=` line makes run_cpu return 98 and row 9 FAIL with 'cpu run printed no bench: line', not 0.00s",
    "ffmpeg stubs in test_bench_intel.py print utime line then maxrss line",
    "Mutant reading the last bench line (/^bench:/) is caught by the row 9 behaviour check",
    "HWDEC_CPU_GAIN=30, HWDEC_XPSNR_MIN=50, LOAD_TOLERANCE=10 unchanged; test threading scheme unchanged",
    "python3 scripts/test_bench_intel.py passes; final summary line and wall time quoted in the PR body"
  ],
  "reuse_check": {"need": "parse ffmpeg -benchmark CPU seconds", "checked": "existing cpu_secs awk helper in scripts/bench-intel.sh (only consumer: run_cpu and row_9)", "verdict": "extend", "reason": "a one-pattern change to the existing helper"},
  "premises": [
    {"id": "P1", "claim": "cpu_secs currently matches /^bench:/ and keeps the last such line, so a trailing maxrss line yields 0.00", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 1099-1110: awk '/^bench:/ {u=s=0 ... t=u+s; seen=1}' resets u,s on every bench: line", "load_bearing": true},
    {"id": "P2", "claim": "run_cpu already returns 98 and cpu_fail maps it to 'printed no bench: line' when cpu_secs prints nothing", "label": "VERIFIED", "check": "Read scripts/bench-intel.sh lines 1118-1143", "load_bearing": true},
    {"id": "P3", "claim": "The ffmpeg stubs print only a single bench: line today, so the existing tests could not see the bug", "label": "VERIFIED", "check": "Read scripts/test_bench_intel.py lines 479 and 537 (one printf each, no maxrss)", "load_bearing": true},
    {"id": "P4", "claim": "Real ffmpeg -benchmark prints `bench: utime=.. stime=.. rtime=..` followed by `bench: maxrss=..KiB`", "label": "INFERRED", "check": "Taken from the bead's report of running ffmpeg in the test pod; the planner cannot exec in a pod. Settle with ffmpeg -benchmark -f lavfi -i nullsrc -t 1 -f null - in the bench pod", "load_bearing": false},
    {"id": "P5", "claim": "A mutant replacing '/^bench: utime=/ {' with '/^bench:/ {' fails row9_failures once the stub prints maxrss last, via the cpu-figures check", "label": "INFERRED", "check": "Run python3 scripts/test_bench_intel.py and confirm 'mutant row9-cpu-last-bench-line is caught'", "load_bearing": false},
    {"id": "P6", "claim": "Other consumers of `bench:` lines (row 5 rtime awk at ~545 and zerocopy_note) are unaffected", "label": "VERIFIED", "check": "Grep of bench: in scripts/bench-intel.sh: row 5 reads only the rtime field of lines matching /^bench:.*rtime/, which the utime line still satisfies", "load_bearing": false}
  ]
}
```
