# Mort benchmarks

**Mort compiles to C and runs at about C speed**: within 1.26x of C on every workload, and between 0.98x and 1.13x
on all but the string workload. These benchmarks use plain arithmetic, arrays and pointers; the result does not
show how Mort's bounds-checked slices behave outside them.

## Results

Five runs of each program, alternating Mort and C. Both compiled with zig cc at `-O2`. Times are whole-process wall
time in seconds: median, then `[min-max]`. The ratio is median over median, and its range is the extreme pairing
(slowest Mort over fastest C and the reverse).

| benchmark | size | Mort | C `-O2` | Mort / C | checked result |
|---|---:|---:|---:|---:|---|
| `fib` | 42 | 0.889 [0.853-0.899] | 0.789 [0.735-0.831] | **1.13x** (1.03-1.22) | `267914296` |
| `nbody` | 20,000,000 steps | 0.869 [0.860-0.939] | 0.883 [0.859-0.899] | **0.98x** (0.96-1.09) | `-169075163 -169031664` |
| `spectral` | 3000 | 0.430 [0.388-0.514] | 0.417 [0.387-0.488] | **1.03x** (0.80-1.33) | `1274224152` |
| `strings` | 4,000,000 | 1.662 [1.555-1.797] | 1.321 [1.312-1.374] | **1.26x** (1.13-1.37) | `1242721314 368044 108190475` |
| `hashmap` | 20,000,000 | 1.040 [1.029-1.205] | 1.066 [1.015-1.137] | **0.98x** (0.91-1.19) | `14872353 11341604 8658396` |
| `stdmap` | 40,000 | 0.362 [0.354-0.404] | 0.350 [0.325-0.381] | **1.04x** (0.93-1.24) | `400049523 22733 17267` |

Raw samples are in [`benchmarks/results/latest.json`](benchmarks/results/latest.json). Each Mort program compiled
in about 1.6 s and each C program in about 0.6 s.

The slowest is `strings`, where every Mort string operation goes through `std.owned_string` and `std.strings`.

**`std.map` is not a hash table.** It scans its keys linearly, so it is quadratic in the number of distinct keys.
`stdmap` runs at about C speed only because the C reference is the same linear scan. `hashmap` uses an open-addressing
table written by hand in both languages. A Mort program using `std.map` with many keys will be far slower than one
using a real hash table in any language, and this table shows that as a size of 40,000 next to one of 20,000,000.

## Environment and caveats

- **Machine:** 12th Gen Intel Core i5-12400F, 12 logical CPUs, 15.8 GB RAM, Windows 11 (build 26300).
- **Software:** Zig 0.17.1, `mortc` 0.137.0 at commit `c7e1a20` plus this suite, CPython 3.13.12 (driving the
  compiler only).
- **The machine was shared.** Other agents were running pytest during the measurement (about 11 Python or Zig
  processes and 9.3 GB RAM free when it started). Spread is real: read the `[min-max]` columns, and treat ratio
  differences below about 20% as noise.
- The C references give every function external linkage, because that is what `mortc` emits. With `static` C
  functions, clang inlined `advance` into `main` and n-body was slower (1.112 s versus 0.919 s, same machine, median
  of 7), which made Mort look 21% faster than C for no reason in Mort. External linkage is the fair comparison.
- `mortc` emits explicitly wrapping signed arithmetic (`mort_wrap_i64`) while the C uses plain `int64_t`. The
  backend keeps both as fast in `fib`, up to the 1.13x shown.

## Workloads

Each one is a pair in `benchmarks/suite`: `name.mx` and `name.c`, the same algorithm with the size from the command
line.

- **fib**: recursive Fibonacci; calls and integer arithmetic.
- **nbody**: five-body simulation from the Benchmarks Game, floats and pointer indexing. Prints energy before and
  after as integers scaled by 1e9.
- **spectral**: spectral norm from the Benchmarks Game, float arithmetic through calls. Same scaling.
- **strings**: `std.owned_string`/`std.strings` append, split, search and upper-casing, then a modular byte hash;
  the C reference mirrors the allocation pattern.
- **hashmap**: insert-or-update then lookups on a hand-written open-addressing table.
- **stdmap**: the same workload on `std.map`, against a C linear-scan map.

**Results are checked, not only printed.** The runner fails if any output differs from the values pinned in
`run_suite.py`, in either language. C and Mort agree on all of them. N-body's first energy, and its energy after 5
million steps during calibration (`-169083133`), match the published Benchmarks Game reference output (truncated to
9 digits).

## Reproducing

```text
uv run --no-project --with ziglang --with cryptography==49.0.0 python benchmarks/suite/run_suite.py --runs 5 --note "what else is running"
```

This writes `benchmarks/results/latest.json`, including CPU, memory, tool versions and compile times.

`mortc bench` remains the per-project timing tool.

## The regression guard

```text
python benchmarks/suite/run_suite.py --guard    # needs ziglang; the CI job "Benchmark guard"
python -m pytest tests/test_benchmarks.py       # the guard's decision logic, no compiler needed
```

The guard compiles all six pairs with zig cc at `-O2`, runs each smaller workload and takes the fastest of 3
attempts on each side, since other load can only slow a run down. It fails if the Mort/C ratio passes **5x** (at
least 3 times the 1.25x worst seen on the development machine) or if any result is wrong. It runs as one Ubuntu
job, so the test matrix does not pay for it. The ratio is to C, not seconds, so a slow CI machine does not fail it.

A wrong result always fails: a broken program copy and a wrong expected value each made the real guard exit 1.
