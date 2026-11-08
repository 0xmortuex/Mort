"""Tests for selfhost/support.mx, driven through selfhost/support_test_main.mx.

Run from the repository root:

    python -m pytest selfhost/test_support.py -q

Expected results come from Python models of each structure run over the same
operations. Run the driver once with a sequence of operations and compare.
"""
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

U64_MAX = 2**64 - 1
SEED = int(os.environ.get("MORT_SELFHOST_SUPPORT_SEED", "20261108"))
EXIT_INTERNAL = 2


@pytest.fixture(scope="session")
def driver(tmp_path_factory):
    build = tmp_path_factory.mktemp("support-build")
    exe = build / ("support_test_main.exe" if os.name == "nt" else "support_test_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "support_test_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, (
        f"compiling support_test_main.mx failed:\n{result.stdout}\n{result.stderr}")
    return exe


def run_ops(driver, tmp_path, ops):
    """Run the operations; returns the result lines."""
    ops_path = tmp_path / "ops.txt"
    out_path = tmp_path / "out.txt"
    ops_path.write_bytes(("\n".join(ops) + "\n").encode("ascii"))
    result = subprocess.run(
        [str(driver), "run", str(ops_path), str(out_path)],
        capture_output=True, timeout=120)
    assert result.returncode == 0, (
        f"driver exit {result.returncode}: {result.stdout!r} {result.stderr!r}")
    return out_path.read_bytes().decode("ascii").splitlines()


def run_fatal(driver, name):
    return subprocess.run(
        [str(driver), "fatal", name], capture_output=True, text=True, timeout=60)


def hex_text(data):
    return data.hex() if data else "-"


# ----- byte buffers -----

BUFFER_CASES = {
    "empty": [],
    "single": [("push", b"a")],
    "embedded_nul": [("push", b"a\x00b")],
    "high_bytes": [("push", bytes(range(256)))],
    "z_stops_at_nul": [("z", b"abc")],
    "span": [("span", b"xyz\x00q")],
    "numbers": [("u64", 0), ("u64", 7), ("u64", 10), ("u64", U64_MAX)],
    "growth_across_many_pushes": [("push", b"q" * 5000)],
}


def buffer_model(actions):
    data = bytearray()
    ops = []
    for kind, value in actions:
        if kind == "push":
            ops.append(f"buf_push {value.hex()}")
            data += value
        elif kind == "z":
            ops.append(f"buf_push_z {value.hex()}")
            data += value
        elif kind == "span":
            ops.append(f"buf_push_span {value.hex()}")
            data += value
        else:
            ops.append(f"buf_push_u64 {value}")
            data += str(value).encode("ascii")
    return ops, bytes(data)


@pytest.mark.parametrize("name", sorted(BUFFER_CASES))
@pytest.mark.parametrize("capacity", [0, 1, 16])
def test_buffer_matches_model(driver, tmp_path, name, capacity):
    ops, data = buffer_model(BUFFER_CASES[name])
    lines = run_ops(driver, tmp_path, [f"buf_new {capacity}", *ops, "buf_show"])
    assert lines == [hex_text(data)]


# ----- vectors -----

def vector_script(rng, prefix, length_bias):
    """Random vector operations and the results a Python list gives."""
    ops = [f"{prefix}_new"]
    expected = []
    model = []
    for _ in range(length_bias):
        roll = rng.random()
        if roll < 0.6 or not model:
            value = rng.choice((0, 1, U64_MAX, rng.randrange(2**64)))
            ops.append(f"{prefix}_push {value}")
            model.append(value)
        elif roll < 0.75:
            index = rng.randrange(len(model))
            ops.append(f"{prefix}_get {index}")
            expected.append(str(model[index]))
        elif roll < 0.85:
            index = rng.randrange(len(model))
            value = rng.randrange(2**64)
            ops.append(f"{prefix}_set {index} {value}")
            model[index] = value
        elif roll < 0.93:
            ops.append(f"{prefix}_pop")
            expected.append(str(model.pop()))
        else:
            ops.append(f"{prefix}_len")
            expected.append(str(len(model)))
    ops.append(f"{prefix}_len")
    expected.append(str(len(model)))
    return ops, expected


@pytest.mark.parametrize("prefix", ["vec", "ptr"])
def test_vector_matches_model(driver, tmp_path, prefix):
    rng = random.Random(SEED)
    all_ops, all_expected = [], []
    for length in (0, 1, 7, 8, 9, 40, 3000):
        ops, expected = vector_script(rng, prefix, length)
        all_ops += ops
        all_expected += expected
    assert run_ops(driver, tmp_path, all_ops) == all_expected


def range_message(index, length, what):
    return f"mort: internal error: index {index} out of range for {what} of length {length}"


FATAL_CASES = {
    "vec_get_past_end": range_message(1, 1, "u64vec"),
    "vec_get_huge": range_message(U64_MAX, 1, "u64vec"),
    "vec_set_past_end": range_message(1, 1, "u64vec"),
    "vec_pop_empty": "mort: internal error: pop from an empty u64vec",
    "ptr_get_past_end": range_message(1, 1, "ptrvec"),
    "ptr_set_past_end": range_message(1, 1, "ptrvec"),
    "ptr_pop_empty": "mort: internal error: pop from an empty ptrvec",
    "alloc_impossible": "mort: out of memory",
    "alloc_array_overflow": "mort: internal error: allocation size overflow",
}


@pytest.mark.parametrize("name", sorted(FATAL_CASES))
def test_misuse_is_a_clean_failure(driver, name):
    result = run_fatal(driver, name)
    assert result.returncode == EXIT_INTERNAL, result
    assert result.stdout == ""
    assert result.stderr == FATAL_CASES[name] + "\n"


def test_usage_error_is_a_clean_failure(driver):
    result = subprocess.run([str(driver)], capture_output=True, text=True, timeout=60)
    assert result.returncode == EXIT_INTERNAL
    assert "usage:" in result.stderr
