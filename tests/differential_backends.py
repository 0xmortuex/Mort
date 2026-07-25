#!/usr/bin/env python3
"""Black-box differential and metamorphic checks for Mort's C backends.

Each source variant is required to produce the language-defined output.  The
same variants are compiled at every supported optimization level by GCC,
Clang, and Zig cc when those toolchains are available.  CI and releases
require all three, while developers can run a smaller locally available set.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MORTC = ROOT / "mortc.py"
OPTIMIZATION_LEVELS = ("0", "1", "2", "3", "s")
EXPECTED_OUTPUT = "70\n21\n-2147483648\n0\n"

# These programs deliberately use different control-flow and declaration
# shapes for the same computation.  Keep the expected output independent of
# other variants so a shared compiler bug cannot become the oracle.
VARIANTS = {
    "range-recursion": r"""
fn record(calls: *i64) -> bool {
    *calls += 1;
    return true;
}

fn fib(n: i64) -> i64 {
    if n < 2 {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

fn weighted_sum(limit: i64) -> i64 {
    let total: i64 = 0;
    for value: i64 in 1..=limit {
        total += value * (value + 1);
    }
    return total;
}

fn main() -> int {
    print(weighted_sum(5));
    print(fib(8));

    let maximum: i32 = 2147483647;
    let one: i32 = 1;
    print((maximum + one) as i64);

    let calls: i64 = 0;
    let neither = false && record(&calls);
    let already = true || record(&calls);
    if neither || !already {
        return 1;
    }
    print(calls);
    return 0;
}
""",
    "while-iteration": r"""
fn weighted_sum(limit: i64) -> i64 {
    let total: i64 = 0;
    let value: i64 = 1;
    while value <= limit {
        total = total + value * value + value;
        value += 1;
    }
    return total;
}

fn fib(n: i64) -> i64 {
    let previous: i64 = 0;
    let current: i64 = 1;
    let index: i64 = 0;
    while index < n {
        let next = previous + current;
        previous = current;
        current = next;
        index += 1;
    }
    return previous;
}

fn main() -> int {
    print(weighted_sum(5));
    print(fib(8));

    let wrapped: i32 = 2147483647;
    wrapped += 1;
    print(wrapped as i64);

    let calls: i64 = 0;
    if false {
        record(&calls);
    }
    print(calls);
    return 0;
}

fn record(calls: *i64) -> bool {
    *calls = *calls + 1;
    return true;
}
""",
    "closed-form-reordered": r"""
fn main() -> int {
    print(weighted_sum(5));
    print(fib(8));

    let high: i32 = 2147483646;
    let two: i32 = 2;
    print((high + two) as i64);

    let calls: i64 = 0;
    if true || record(&calls) {
        // The right operand must remain unevaluated.
    }
    print(calls);
    return 0;
}

fn weighted_sum(limit: i64) -> i64 {
    return (limit * (limit + 1) * (limit + 2)) / 3;
}

fn fib(n: i64) -> i64 {
    let values: [i64; 9] = [0, 1, 0, 0, 0, 0, 0, 0, 0];
    for index: i64 in 2..=n {
        values[index] = values[index - 1] + values[index - 2];
    }
    return values[n];
}

fn record(calls: *i64) -> bool {
    *calls += 1;
    return true;
}
""",
}


def _command_string(arguments):
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def discover_backends():
    """Return distinct named C toolchains available on this machine."""
    backends = []
    for name in ("gcc", "clang"):
        executable = shutil.which(name)
        if executable:
            backends.append((name, [executable]))

    zig = shutil.which("zig")
    if zig:
        backends.append(("zig", [zig, "cc"]))
    else:
        try:
            import ziglang  # noqa: F401
        except ImportError:
            pass
        else:
            backends.append(("zig", [sys.executable, "-m", "ziglang", "cc"]))
    return backends


def _run(command, *, environment, timeout=300):
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        rendered = _command_string([str(item) for item in command])
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {rendered}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def verify(backends, levels):
    observations = {}
    with tempfile.TemporaryDirectory(prefix="mort-differential-") as directory:
        root = Path(directory)
        for variant, source in VARIANTS.items():
            source_path = root / f"{variant}.mx"
            source_path.write_text(source.strip() + "\n", encoding="utf-8")
            for backend, compiler in backends:
                for level in levels:
                    suffix = ".exe" if os.name == "nt" else ""
                    output = root / f"{variant}-{backend}-O{level}{suffix}"
                    environment = os.environ.copy()
                    environment["MORT_CC"] = _command_string(compiler)
                    _run(
                        [
                            sys.executable,
                            str(MORTC),
                            str(source_path),
                            "-o",
                            str(output),
                            "--opt-level",
                            level,
                        ],
                        environment=environment,
                    )
                    result = _run([str(output)], environment=environment)
                    key = (variant, backend, level)
                    observations[key] = result.stdout
                    if result.stdout != EXPECTED_OUTPUT:
                        raise RuntimeError(
                            f"wrong output for {variant} with {backend} -O{level}:\n"
                            f"expected {EXPECTED_OUTPUT!r}\n"
                            f"actual   {result.stdout!r}\n"
                            f"stderr:\n{result.stderr}"
                        )
                    print(
                        f"pass: {variant} / {backend} / -O{level}",
                        flush=True,
                    )

    distinct = set(observations.values())
    if distinct != {EXPECTED_OUTPUT}:
        raise RuntimeError("backend observations diverged")
    return len(observations)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-backends",
        type=int,
        default=1,
        help="minimum discovered backend count (CI and releases use 3)",
    )
    parser.add_argument(
        "--levels",
        nargs="+",
        choices=OPTIMIZATION_LEVELS,
        default=OPTIMIZATION_LEVELS,
        help="optimization levels to exercise",
    )
    args = parser.parse_args(argv)
    backends = discover_backends()
    names = ", ".join(name for name, _ in backends) or "none"
    print(f"discovered C backends: {names}", flush=True)
    if len(backends) < args.require_backends:
        print(
            f"differential test requires {args.require_backends} backends, "
            f"found {len(backends)} ({names})",
            file=sys.stderr,
        )
        return 2
    try:
        count = verify(backends, args.levels)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"differential test failed: {error}", file=sys.stderr)
        return 1
    print(
        f"differential test passed: {count} executions across "
        f"{len(backends)} backends, {len(args.levels)} optimization levels, "
        f"and {len(VARIANTS)} metamorphic variants"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
