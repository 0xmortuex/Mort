"""The benchmark guard's decision logic: a wrong result is always a failure, however fast.

The guard itself compiles and runs every benchmark, so it runs as its own CI job
(`python benchmarks/suite/run_suite.py --guard`); nothing here needs a C compiler or a clock.
"""

import importlib.util
import pathlib
import sys

PATH = pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "suite" / "run_suite.py"
SPEC = importlib.util.spec_from_file_location("mort_benchmark_suite", PATH)
suite = importlib.util.module_from_spec(SPEC)
sys.modules["mort_benchmark_suite"] = suite
SPEC.loader.exec_module(suite)

BENCH = suite.BENCHMARKS[0]


def test_every_benchmark_pins_a_real_result():
    for bench in suite.BENCHMARKS:
        assert bench.full_expected and bench.quick_expected and bench.max_ratio > 1.0


def test_published_constants_pin_the_float_benchmarks():
    # Benchmarks Game reference output, truncated: nbody starts at -0.169075164.
    nbody = next(bench for bench in suite.BENCHMARKS if bench.name == "nbody")
    assert nbody.quick_expected.split()[0] == "-169075163"


def test_a_wrong_result_fails_the_guard_even_when_fast():
    reason = suite.verdict(BENCH, 0.001, 1.0, "12345", BENCH.quick_expected)
    assert reason is not None and "expected" in reason


def test_a_wrong_reference_result_fails_the_guard():
    assert suite.verdict(BENCH, 0.1, 1.0, BENCH.quick_expected, "1") is not None


def test_a_ratio_over_the_limit_fails_the_guard():
    reason = suite.verdict(BENCH, BENCH.max_ratio + 1.0, 1.0, BENCH.quick_expected, BENCH.quick_expected)
    assert reason is not None and "limit" in reason


def test_a_right_result_within_the_limit_passes():
    assert suite.verdict(BENCH, 1.0, 1.0, BENCH.quick_expected, BENCH.quick_expected) is None
