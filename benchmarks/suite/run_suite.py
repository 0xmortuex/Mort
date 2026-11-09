"""The Mort benchmark suite and its CI guard. See BENCHMARKS.md.

    uv run --no-project --with ziglang --with cryptography==49.0.0 python benchmarks/suite/run_suite.py
        measure, and write benchmarks/results/latest.json
    uv run --no-project --with ziglang --with cryptography==49.0.0 python benchmarks/suite/run_suite.py --guard
        the CI regression guard

Each benchmark is a Mort program and a C program doing the same work, both compiled with zig cc at -O2.
A wrong result is always a failure: a broken optimisation must not be able to look fast.
"""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
RESULTS = HERE.parent / "results" / "latest.json"
ZIG_CC = [sys.executable, "-m", "ziglang", "cc"]
COMPILE_TIMEOUT_S = 300
RUN_TIMEOUT_S = 120


@dataclasses.dataclass(frozen=True)
class Benchmark:
    name: str
    full: int
    quick: int
    full_expected: str
    quick_expected: str
    max_ratio: float


# max_ratio is a Mort/C time ratio at least 3x above the worst seen on the development machine.
BENCHMARKS = [
    Benchmark("fib", 42, 38, "267914296", "39088169", 5.0),
    Benchmark("nbody", 20000000, 1000000, "-169075163 -169031664", "-169075163 -169086184", 5.0),
    Benchmark("spectral", 3000, 1000, "1274224152", "1274224148", 5.0),
    Benchmark("strings", 4000000, 1000000, "1242721314 368044 108190475", "1189388557 85917 24619047", 5.0),
    Benchmark("hashmap", 20000000, 2000000, "14872353 11341604 8658396", "1996230584 1135450 864550", 5.0),
    Benchmark("stdmap", 40000, 20000, "400049523 22733 17267", "99343386 11323 8677", 5.0),
]


class BenchmarkFailure(Exception):
    """A benchmark produced the wrong result or broke its limit."""


def normalise(output: str) -> str:
    return " ".join(output.split())


def check_output(name: str, who: str, size: int, got: str, expected: str) -> None:
    if normalise(got) != expected:
        raise BenchmarkFailure(f"{name}({size}) on {who}: expected {expected!r}, got {normalise(got)!r}")


def _run(argv: list[str], timeout: int, env: dict[str, str] | None = None) -> tuple[float, str]:
    start = time.perf_counter()
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    elapsed = time.perf_counter() - start
    if done.returncode != 0:
        raise BenchmarkFailure(f"{' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}")
    return elapsed, done.stdout


def _executable(path: pathlib.Path) -> str:
    return str(path) + (".exe" if sys.platform == "win32" else "")


def build_mort(name: str, build: pathlib.Path) -> tuple[float, str]:
    """Compile with the repository's mortc, forced onto zig cc. Returns (seconds, executable)."""
    executable = _executable(build / f"{name}_mort")
    env = dict(os.environ)
    env["MORT_CC"] = f'"{sys.executable}" -m ziglang cc'
    elapsed, _ = _run(
        [sys.executable, str(REPO / "mortc.py"), str(HERE / f"{name}.mx"), "-O", "2", "-l", "m", "-o", executable],
        COMPILE_TIMEOUT_S,
        env,
    )
    return elapsed, executable


def build_c(name: str, build: pathlib.Path) -> tuple[float, str]:
    """Compile the C reference with the same flags mortc gives the backend."""
    output = build / f"{name}_c"
    executable = _executable(output)
    elapsed, _ = _run(
        [*ZIG_CC, "-O2", "-std=c11", str(HERE / f"{name}.c"), "-lm", "-o", executable], COMPILE_TIMEOUT_S
    )
    return elapsed, executable


def run_binary(executable: str, size: int) -> tuple[float, str]:
    return _run([executable, str(size)], RUN_TIMEOUT_S)


def spread(samples: list[float]) -> dict[str, Any]:
    return {
        "median_s": statistics.median(samples),
        "min_s": min(samples),
        "max_s": max(samples),
        "samples_s": samples,
    }


def hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "os": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "cpu": platform.processor(),
    }
    if sys.platform == "win32":
        name = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
            capture_output=True,
            text=True,
            check=True,
        )
        info["cpu"] = name.stdout.strip()

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("rest", ctypes.c_ulonglong * 6),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        info["ram_gb"] = round(status.total / 1024**3, 1)
    else:
        info["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    return info


def tool_versions() -> dict[str, str]:
    commit = subprocess.run(
        ["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    )
    zig = subprocess.run([sys.executable, "-m", "ziglang", "version"], capture_output=True, text=True, check=True)
    mort = subprocess.run(
        [sys.executable, str(REPO / "mortc.py"), "--version"], capture_output=True, text=True, check=True
    )
    return {
        "python": platform.python_version(),
        "zig": zig.stdout.strip(),
        "mortc": mort.stdout.strip(),
        "mort_commit": commit.stdout.strip(),
    }


def measure(runs: int, note: str) -> dict[str, Any]:
    build = pathlib.Path(tempfile.mkdtemp(prefix="mort-bench-"))
    rows = []
    try:
        for bench in BENCHMARKS:
            mort_compile, mort_binary = build_mort(bench.name, build)
            c_compile, c_binary = build_c(bench.name, build)
            mort_samples: list[float] = []
            c_samples: list[float] = []
            for _ in range(runs):
                # Alternate, so a burst of load on the machine lands on both sides.
                elapsed, out = run_binary(mort_binary, bench.full)
                check_output(bench.name, "mort", bench.full, out, bench.full_expected)
                mort_samples.append(elapsed)
                elapsed, out = run_binary(c_binary, bench.full)
                check_output(bench.name, "c", bench.full, out, bench.full_expected)
                c_samples.append(elapsed)
            mort = spread(mort_samples)
            c = spread(c_samples)
            row = {
                "name": bench.name,
                "size": bench.full,
                "result": bench.full_expected,
                "mort": mort,
                "c": c,
                "mort_compile_s": mort_compile,
                "c_compile_s": c_compile,
                "ratio_median": mort["median_s"] / c["median_s"],
                "ratio_low": mort["min_s"] / c["max_s"],
                "ratio_high": mort["max_s"] / c["min_s"],
            }
            print(
                f"{bench.name:<10} mort {mort['median_s']:7.3f}s [{mort['min_s']:.3f}-{mort['max_s']:.3f}]  "
                f"c {c['median_s']:7.3f}s [{c['min_s']:.3f}-{c['max_s']:.3f}]  "
                f"{row['ratio_median']:5.2f}x  {bench.full_expected}",
                flush=True,
            )
            rows.append(row)
    finally:
        shutil.rmtree(build, ignore_errors=False)
    return {
        "runs": runs,
        "note": note,
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "hardware": hardware(),
        "tools": tool_versions(),
        "benchmarks": rows,
    }


def verdict(bench: Benchmark, mort_seconds: float, c_seconds: float, mort_out: str, c_out: str) -> str | None:
    """The guard's decision for one benchmark: None if it passes, else the reason it fails."""
    for who, out in (("mort", mort_out), ("c", c_out)):
        try:
            check_output(bench.name, who, bench.quick, out, bench.quick_expected)
        except BenchmarkFailure as failure:
            return str(failure)
    ratio = mort_seconds / c_seconds
    if ratio > bench.max_ratio:
        return f"{bench.name}: Mort is {ratio:.2f}x C, limit {bench.max_ratio:.1f}x"
    return None


def guard(attempts: int) -> int:
    build = pathlib.Path(tempfile.mkdtemp(prefix="mort-bench-guard-"))
    failures = []
    try:
        for bench in BENCHMARKS:
            _, mort_binary = build_mort(bench.name, build)
            _, c_binary = build_c(bench.name, build)
            # Fastest of several attempts: other load on the machine can only make a run slower.
            mort = min((run_binary(mort_binary, bench.quick) for _ in range(attempts)), key=lambda item: item[0])
            c = min((run_binary(c_binary, bench.quick) for _ in range(attempts)), key=lambda item: item[0])
            failure = verdict(bench, mort[0], c[0], mort[1], c[1])
            print(
                f"{bench.name:<10} mort {mort[0]:.4f}s  c {c[0]:.4f}s  ratio {mort[0] / c[0]:5.2f}x "
                f"(limit {bench.max_ratio:.1f}x)  result {'ok' if failure is None else 'FAIL'}",
                flush=True,
            )
            if failure is not None:
                failures.append(failure)
    finally:
        shutil.rmtree(build, ignore_errors=False)
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guard", action="store_true", help="run the CI regression guard")
    parser.add_argument("--runs", type=int, default=5, help="runs per program (default: 5)")
    parser.add_argument("--attempts", type=int, default=3, help="guard: attempts per program (default: 3)")
    parser.add_argument("--note", default="", help="free text recorded with the results, such as machine load")
    args = parser.parse_args()
    if args.guard:
        return guard(args.attempts)
    results = measure(args.runs, args.note)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
