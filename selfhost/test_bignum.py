"""Differential test: selfhost/bignum.mx against Python's integers.

Run from the repository root:

    python -m pytest selfhost/test_bignum.py -q -s

Every expected value comes from Python's `int` or from the real
`Checker._const_value`; nothing is hardcoded. The driver program is rebuilt on
each run. Set BIGNUM_DIFF_SEED to run the differential on another seed.
"""
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from mort import mort_ast as A
from mort.typechecker import Checker, _c_div, _c_mod

sys.set_int_max_str_digits(0)

SEED = int(os.environ.get("BIGNUM_DIFF_SEED", "20261108"))
BATCHES = 60
BATCH_SIZE = 5000
MIN_DIFFERENTIAL_CASES = 300_000
SOURCES = ("bignum.mx", "support.mx", "bignum_test_main.mx")

CHECKER = Checker(None)

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def build_driver(source_dir, build_dir):
    exe = build_dir / ("bignum_test_main.exe" if os.name == "nt" else "bignum_test_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(source_dir / "bignum_test_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build_dir, timeout=170, check=False)
    assert result.returncode == 0, (
        f"compiling bignum_test_main.mx failed:\n{result.stdout}\n{result.stderr}")
    assert exe.exists()
    return exe


@pytest.fixture(scope="session")
def bn_exe(tmp_path_factory):
    return build_driver(HERE, tmp_path_factory.mktemp("bignum-build"))


def run_driver(exe, lines, work, timeout=120):
    """Returns (exit code, combined stdout+stderr, output lines or None)."""
    in_path = work / "in.txt"
    out_path = work / "out.txt"
    in_path.write_bytes("".join(line + "\n" for line in lines).encode("ascii"))
    if out_path.exists():
        out_path.unlink()
    env = dict(os.environ, MORT_BN_IN=str(in_path), MORT_BN_OUT=str(out_path))
    result = subprocess.run(
        [str(exe)], env=env, capture_output=True, timeout=timeout, check=False)
    output = None
    if out_path.exists():
        output = out_path.read_bytes().decode("ascii").split("\n")
        assert output[-1] == ""
        output.pop()
    return result.returncode, result.stdout + result.stderr, output


def to_base(value, base):
    """Canonical digits of a non-negative integer, lower case."""
    if base == 10:
        return str(value)
    if base == 2:
        return format(value, "b")
    if base == 8:
        return format(value, "o")
    if base == 16:
        return format(value, "x")
    if value == 0:
        return "0"
    out = []
    while value:
        value, digit = divmod(value, base)
        out.append(DIGITS[digit])
    return "".join(reversed(out))


class Writer:
    """Spells a value in a base, with the variations the parser must accept."""

    def __init__(self, rng):
        self.rng = rng

    def base_for(self, value):
        roll = self.rng.random()
        if roll < 0.55:
            return 10
        if roll < 0.7:
            return 16
        if roll < 0.82:
            return 2
        if roll < 0.9 or value.bit_length() > 2000:
            return 8
        return self.rng.randint(2, 36)

    def spell(self, value, base):
        text = to_base(abs(value), base)
        roll = self.rng.random()
        if roll < 0.1:
            text = "0" * self.rng.randint(1, 8) + text
        if roll > 0.9 and len(text) > 1:
            cut = self.rng.randint(1, len(text) - 1)
            text = text[:cut] + "_" + text[cut:]
        if self.rng.random() < 0.3:
            text = text.upper()
        return ("-" if value < 0 else "") + text


BOUNDARY_POWERS = (0, 1, 7, 8, 15, 16, 31, 32, 33, 63, 64, 65, 96, 127, 128, 129, 256)


def boundary_values():
    values = {0, 1, -1, 2, -2, 3, -3, 10, -10, 1 << 65, -(1 << 65)}
    for power in BOUNDARY_POWERS:
        for delta in (-2, -1, 0, 1, 2):
            base = (1 << power) + delta
            values.add(base)
            values.add(-base)
    return sorted(values)


BOUNDARY = boundary_values()


def small_value(rng):
    roll = rng.random()
    if roll < 0.25:
        return rng.randint(-256, 256)
    if roll < 0.5:
        return rng.choice(BOUNDARY)
    bits = rng.randint(1, 130)
    value = rng.getrandbits(bits)
    return -value if rng.random() < 0.5 else value


def medium_value(rng):
    bits = rng.choice((32, 64, 96, 128, 200, 256, 500, 1024, 2048))
    value = rng.getrandbits(bits + rng.randint(-3, 3))
    return -value if rng.random() < 0.5 else value


def huge_value(rng):
    """Up to the widest literal the lexer can produce: 4300 hex digits."""
    roll = rng.random()
    if roll < 0.4:
        magnitude = rng.randrange(10 ** rng.randint(4290, 4300))
    elif roll < 0.8:
        magnitude = rng.getrandbits(rng.randint(14000, 4300 * 4))
    elif roll < 0.9:
        # Long runs of all-ones and all-zeros limbs catch carry propagation.
        magnitude = (1 << rng.randint(8000, 17200)) - rng.randint(0, 3)
    else:
        magnitude = 1 << rng.randint(8000, 17200)
    return -magnitude if rng.random() < 0.5 else magnitude


def pick_value(rng):
    roll = rng.random()
    if roll < 0.62:
        return small_value(rng)
    if roll < 0.90:
        return medium_value(rng)
    if roll < 0.97:
        return huge_value(rng)
    return rng.choice(BOUNDARY) * rng.getrandbits(40) + rng.randint(-2, 2)


def checker_fold(symbol, a, b):
    node = A.Binary(symbol, A.IntLit(a, 1), A.IntLit(b, 1), 1)
    value = CHECKER._const_value(node)
    return "none" if value is None else str(value)


BINARY_ORACLES = {
    "add": lambda a, b: str(a + b),
    "sub": lambda a, b: str(a - b),
    "mul": lambda a, b: str(a * b),
    "fdiv": lambda a, b: str(a // b),
    "fmod": lambda a, b: str(a % b),
    "cdiv": lambda a, b: str(_c_div(a, b)),
    "cmod": lambda a, b: str(_c_mod(a, b)),
    "and": lambda a, b: str(a & b),
    "or": lambda a, b: str(a | b),
    "xor": lambda a, b: str(a ^ b),
    "cmp": lambda a, b: str((a > b) - (a < b)),
}
UNARY_ORACLES = {
    "val": lambda a: str(a),
    "neg": lambda a: str(-a),
    "not": lambda a: str(~a),
    "bitlen": lambda a: str(a.bit_length()),
    "iszero": lambda a: str(int(a == 0)),
    "isneg": lambda a: str(int(a < 0)),
}
FOLD_SYMBOLS = ("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>")
NEEDS_NONZERO = ("fdiv", "fmod", "cdiv", "cmod")


def shift_count(rng):
    roll = rng.random()
    if roll < 0.6:
        return rng.randint(0, 70)
    if roll < 0.9:
        return rng.randint(0, 2000)
    return rng.randint(2000, 18000)


def fold_shift_operand(rng):
    roll = rng.random()
    if roll < 0.5:
        return rng.randint(0, 70)
    if roll < 0.7:
        return rng.randint(-6, 0)
    if roll < 0.9:
        return rng.choice((63, 64, 65, 66, 1 << 32, 1 << 63, 1 << 64, 1 << 70, 1 << 200))
    return rng.choice(BOUNDARY)


def make_case(rng, writer):
    """Returns (driver line, expected output line)."""
    a = pick_value(rng)
    base = writer.base_for(a)
    roll = rng.random()
    if roll < 0.08:
        op = rng.choice(tuple(UNARY_ORACLES))
        return f"{op} {base} {writer.spell(a, base)}", UNARY_ORACLES[op](a)
    if roll < 0.14:
        op = rng.choice(("shl", "shr"))
        if op == "shl":
            if a.bit_length() > 20000:
                a >>= a.bit_length() - 20000
            count = shift_count(rng)
            return f"shl {base} {writer.spell(a, base)} {count}", str(a << count)
        if rng.random() < 0.05:
            count = rng.choice((2 ** 63, 2 ** 64 - 1, 2 ** 64 - 33))
        else:
            count = shift_count(rng)
        return f"shr {base} {writer.spell(a, base)} {count}", str(a >> count)
    if roll < 0.38:
        symbol = rng.choice(FOLD_SYMBOLS)
        if symbol in ("<<", ">>"):
            b = fold_shift_operand(rng)
        else:
            b = pick_value(rng) if rng.random() > 0.12 else 0
        return (f"F{symbol} {base} {writer.spell(a, base)} {writer.spell(b, base)}",
                checker_fold(symbol, a, b))
    op = rng.choice(tuple(BINARY_ORACLES))
    alias = rng.random() < 0.04
    if alias:
        b = a
    else:
        b = pick_value(rng)
    if op in NEEDS_NONZERO:
        if b == 0:
            alias = False
            b = 7
        # Divisors close to the dividend exercise the correction steps.
        if not alias and rng.random() < 0.25 and a != 0:
            b = (a ** 2 + rng.randint(-3, 3)) >> rng.randint(1, a.bit_length() + 1)
            if b == 0:
                b = 3
    b_text = "=" if alias and rng.random() < 0.7 else writer.spell(b, base)
    return f"{op} {base} {writer.spell(a, base)} {b_text}", BINARY_ORACLES[op](a, b)


def assert_agrees(lines, expected, status, stream, output):
    assert status == 0, f"driver exit {status}: {stream[:600]!r}"
    assert output is not None
    assert len(output) == len(expected), (len(output), len(expected))
    for index, (line, want, got) in enumerate(zip(lines, expected, output)):
        assert want == got, (
            f"case {index}\n  input {line[:400]}\n  want  {want[:300]}\n  got   {got[:300]}")


def generate(seed, count):
    rng = random.Random(seed)
    writer = Writer(rng)
    lines, expected = [], []
    for _ in range(count):
        line, want = make_case(rng, writer)
        lines.append(line)
        expected.append(want)
    return lines, expected


def run_cases(exe, seed, count, work, timeout=120):
    lines, expected = generate(seed, count)
    status, stream, output = run_driver(exe, lines, work, timeout)
    return lines, expected, status, stream, output


def test_random_differential(bn_exe, tmp_path, capsys):
    assert BATCHES * BATCH_SIZE >= MIN_DIFFERENTIAL_CASES
    start = time.monotonic()
    for batch in range(BATCHES):
        lines, expected, status, stream, output = run_cases(
            bn_exe, SEED + batch, BATCH_SIZE, tmp_path)
        assert_agrees(lines, expected, status, stream, output)
    with capsys.disabled():
        print(f"\nbignum differential: {BATCHES * BATCH_SIZE} cases, seed {SEED}, "
              f"{time.monotonic() - start:.1f}s")


def test_boundary_cross_product(bn_exe, tmp_path):
    lines, expected = [], []
    subset = BOUNDARY[::3] + [BOUNDARY[-1]]
    for a in subset:
        for b in subset:
            for op, oracle in BINARY_ORACLES.items():
                if op in NEEDS_NONZERO and b == 0:
                    continue
                lines.append(f"{op} 10 {a} {b}")
                expected.append(oracle(a, b))
            for symbol in FOLD_SYMBOLS:
                if symbol in ("<<", ">>") and b > 80:
                    continue
                lines.append(f"F{symbol} 10 {a} {b}")
                expected.append(checker_fold(symbol, a, b))
    assert len(lines) > 30000
    status, stream, output = run_driver(bn_exe, lines, tmp_path)
    assert_agrees(lines, expected, status, stream, output)


@pytest.mark.parametrize("seed", (1, 2))
def test_parse_every_base(bn_exe, tmp_path, seed):
    rng = random.Random(seed)
    lines, expected = [], []
    for base in range(2, 37):
        values = [0, 1, base - 1, base, base + 1,
                  2 ** 64 - 1, 2 ** 64, base ** 40, base ** 100 - 1]
        values += [rng.getrandbits(rng.randint(1, 400)) for _ in range(60)]
        for value in values:
            text = to_base(value, base)
            if rng.random() < 0.3 and len(text) > 2:
                cut = rng.randint(1, len(text) - 1)
                text = text[:cut] + "_" + text[cut:]
            lines.append(f"val {base} {text}")
            expected.append(str(value))
    # The lexer's limit, in each base: 4300 digits.
    for base in (2, 8, 10, 16):
        for _ in range(8):
            top = base ** 4300 - 1
            value = rng.randint(top // 2, top)
            lines.append(f"val {base} {to_base(value, base)}")
            expected.append(str(value))
        lines.append(f"val {base} {to_base(top, base)}")
        expected.append(str(top))
    # A maximal hex literal reaches the checker as this many decimal digits.
    widest = 16 ** 4300 - 1
    assert len(str(widest)) == 5178
    lines.append(f"val 10 {widest}")
    expected.append(str(widest))
    status, stream, output = run_driver(bn_exe, lines, tmp_path)
    assert_agrees(lines, expected, status, stream, output)


def test_from_machine_integers(bn_exe, tmp_path):
    values = [0, 1, 2 ** 31, 2 ** 32 - 1, 2 ** 32, 2 ** 63 - 1, 2 ** 63, 2 ** 64 - 1]
    lines = [f"fu {v}" for v in values]
    expected = [str(v) for v in values]
    signed = [0, 1, -1, 2 ** 31, -(2 ** 31), 2 ** 63 - 1, -(2 ** 63), -(2 ** 63) + 1]
    lines += [f"fi {v}" for v in signed]
    expected += [str(v) for v in signed]
    status, stream, output = run_driver(bn_exe, lines, tmp_path)
    assert_agrees(lines, expected, status, stream, output)


def test_values_at_the_constant_width_cap(bn_exe, tmp_path):
    """Multiplies and divides around 2^18 bits, the checker's cap."""
    rng = random.Random(4)
    lines, expected = [], []
    cap = 2 ** 18
    a = rng.getrandbits(cap) | (1 << (cap - 1))
    b = -(rng.getrandbits(cap) | (1 << (cap - 1)))
    product = a * b
    assert product.bit_length() > cap  # the fold is computed before the cap is checked
    for op, x, y in (("mul", a, b), ("fdiv", product, a), ("fmod", product, a + 1),
                     ("cdiv", product - 1, b), ("cmod", product + 5, b), ("and", a, b),
                     ("or", a, b), ("xor", a, b), ("cmp", product, a)):
        lines.append(f"{op} 16 {x:x} {y:x}")
        expected.append(BINARY_ORACLES[op](x, y))
    lines.append(f"shl 16 {a:x} 19")
    expected.append(str(a << 19))
    lines.append(f"bitlen 16 {a:x}")
    expected.append(str(a.bit_length()))
    start = time.monotonic()
    status, stream, output = run_driver(bn_exe, lines, tmp_path, timeout=60)
    assert_agrees(lines, expected, status, stream, output)
    assert time.monotonic() - start < 30


def die_case(bn_exe, tmp_path, line, fragment):
    status, stream, _ = run_driver(bn_exe, [line], tmp_path, timeout=120)
    text = stream.decode("utf-8", "replace")
    assert status == 2, (status, text)
    assert fragment in text, text
    assert "Traceback" not in text


@pytest.mark.parametrize("line,fragment", [
    ("fdiv 10 7 0", "division by zero"),
    ("fmod 10 7 0", "division by zero"),
    ("cdiv 10 -7 0", "division by zero"),
    ("cmod 10 -7 0", "division by zero"),
    ("val 10 12a", "bad digit"),
    ("val 8 8", "bad digit"),
    ("val 10 _", "empty text"),
    ("val 37 1", "base out of range"),
    ("val 1 1", "base out of range"),
    ("shl 10 3 2000000", "wider than the bignum limit"),
    ("shl 10 3 18446744073709551615", "wider than the bignum limit"),
    (f"alloc {2 ** 59}", "out of memory"),
    (f"alloc {2 ** 64 - 1}", "allocation size overflow"),
])
def test_clean_diagnostics(bn_exe, tmp_path, line, fragment):
    die_case(bn_exe, tmp_path, line, fragment)


def test_over_limit_multiply_is_rejected_cleanly(bn_exe, tmp_path):
    wide = f"{(1 << 1048500) - 1:x}"
    die_case(bn_exe, tmp_path, f"mul 16 {wide} {wide}", "wider than the bignum limit")


def test_over_limit_parse_stops_early(bn_exe, tmp_path):
    """Text far past the bound fails fast rather than running quadratically."""
    line = "val 10 " + "9" * 2_000_000
    start = time.monotonic()
    die_case(bn_exe, tmp_path, line, "wider than the bignum limit")
    assert time.monotonic() - start < 60


def test_output_is_deterministic(bn_exe, tmp_path):
    lines, _ = generate(99, 4000)
    first = run_driver(bn_exe, lines, tmp_path)
    second = run_driver(bn_exe, lines, tmp_path)
    assert first[0] == 0 and second[0] == 0
    assert first[2] == second[2]


def test_links_beside_the_lexer(tmp_path):
    """bignum.mx shares a flat namespace with lexer_float.mx's `Big`."""
    probe = tmp_path / "bignum_lexer_probe.mx"
    probe.write_text(
        'import lexer;\nimport bignum;\n'
        'fn main() -> int {\n'
        '    let b = big_new(4);\n'
        '    big_mul_add(&b, 1, 7);\n'
        '    let v = bn_from_u64(b.data[0] as u64);\n'
        '    println(bn_format(v));\n'
        '    return 0;\n'
        '}\n', encoding="utf-8")
    for name in os.listdir(HERE):
        if name.endswith(".mx"):
            shutil.copy(HERE / name, tmp_path / name)
    exe = tmp_path / ("bignum_lexer_probe.exe" if os.name == "nt" else "probe")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(probe), "-o", str(exe)],
        capture_output=True, text=True, cwd=tmp_path, timeout=170, check=False)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, check=False)
    assert run.returncode == 0 and run.stdout.strip() == "7"


# ----- mutation: each edit below must be caught by the differential -----

MUTANTS = [
    ("add_carry_dropped", "    r[n] = carry as u32;\n    return bn_make(r, n + 1, neg);",
     "    r[n] = 0;\n    return bn_make(r, n + 1, neg);"),
    ("sub_borrow_ignored", "        if value < BN_BASE {\n            borrow = 1;",
     "        if value < 0 {\n            borrow = 1;"),
    ("mul_sign_wrong", "    return bn_make(r, n, a.neg != b.neg);",
     "    return bn_make(r, n, a.neg);"),
    ("knuth_addback_skipped", "        if borrow == 1 {\n            qhat -= 1;",
     "        if borrow == 7 {\n            qhat -= 1;"),
    ("knuth_correction_loop_weakened", "while qhat >= BN_BASE || qhat * next",
     "while qhat >= BN_BASE && qhat * next"),
    ("knuth_quotient_digit_off", "        q[j] = qhat as u32;",
     "        q[j] = (qhat + 1) as u32;"),
    ("c_div_floors_instead", "    pair.quot.neg = (a.neg != b.neg) && pair.quot.len > 0;",
     "    pair.quot.neg = a.neg && pair.quot.len > 0;"),
    ("floor_adjust_skipped", "    if pair.rem.len > 0 && a.neg != b.neg {",
     "    if pair.rem.len > 7 && a.neg != b.neg {"),
    ("floor_rem_sign_wrong", "        let rem = bn_add(pair.rem, b);",
     "        let rem = bn_sub(pair.rem, b);"),
    ("shr_rounds_toward_zero", "    if a.neg && lost {", "    if a.neg && !lost {"),
    ("shr_carry_stops_early", "        let i: u64 = 0;\n        loop {",
     "        let i: u64 = 1;\n        loop {"),
    ("bitwise_no_sign_limb", "    n += 1;\n    let x = bn_to_twos(a, n);",
     "    if n == 0 {\n        n = 1;\n    }\n    let x = bn_to_twos(a, n);"),
    ("twos_carry_lost", "    let carry: u64 = 1;\n    for i: u64 in 0..n {\n        let value = ((t[i]",
     "    let carry: u64 = 0;\n    for i: u64 in 0..n {\n        let value = ((t[i]"),
    ("cmp_negatives_inverted", "        return bn_mag_cmp(b, a);\n", "        return bn_mag_cmp(a, b);\n"),
    ("parse_chunk_truncates", "factor * (base as u64) <= BN_MASK",
     "factor * (base as u64) <= 0xFFFFFFFFFF"),
    ("format_zero_chunks", "    while count > 1 && reversed[count - 1] == '0' {",
     "    while count > 8 && reversed[count - 1] == '0' {"),
    ("fold_shl_cap_at_63", "bn_mag_u64(b) > 64", "bn_mag_u64(b) > 63"),
    ("fold_div_is_floor", "            *out = bn_c_div(a, b);", "            *out = bn_floor_div(a, b);"),
]


def sources_with_mutation(destination, name, old, new):
    text = (HERE / "bignum.mx").read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutant {name}: pattern must occur exactly once"
    mutated = text.replace(old, new)
    assert mutated != text
    for file in SOURCES:
        (destination / file).write_text(
            mutated if file == "bignum.mx" else (HERE / file).read_text(encoding="utf-8"),
            encoding="utf-8")


def mutant_is_caught(exe, work, seeds):
    """True when the corpus exposes the mutant by output, exit status or hang."""
    for seed in seeds:
        lines, expected = generate(seed, 6000)
        try:
            status, _, output = run_driver(exe, lines, work, timeout=30)
        except subprocess.TimeoutExpired:
            return True
        if status != 0 or output is None or output != expected:
            return True
    return False


@pytest.mark.parametrize("name,old,new", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_mutant_is_detected(tmp_path, name, old, new):
    source = tmp_path / "src"
    source.mkdir()
    sources_with_mutation(source, name, old, new)
    exe = build_driver(source, tmp_path)
    assert mutant_is_caught(exe, tmp_path, (31, 32, 33)), f"mutant {name} survived"


def test_mutant_corpus_passes_on_the_real_library(bn_exe, tmp_path):
    assert not mutant_is_caught(bn_exe, tmp_path, (31, 32, 33))
