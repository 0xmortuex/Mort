"""Tests for selfhost/support.mx, driven through selfhost/support_test_main.mx.

Run from the repository root:

    python -m pytest selfhost/test_support.py -q

Expected results come from Python models of each structure run over the same
operations. Run the driver once with a sequence of operations and compare.
"""
import os
import platform
import random
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(HERE / "tools"))

import check_subset  # noqa: E402
import mortc  # noqa: E402
from mort import mort_ast as A  # noqa: E402
from mort.errors import MortError  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.parser import Parser  # noqa: E402
from mort.typechecker import Checker  # noqa: E402

U64_MAX = 2**64 - 1
SEED = int(os.environ.get("MORT_SELFHOST_SUPPORT_SEED", "20261108"))
EXIT_DIAGNOSTIC = 1
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


# ----- hash map -----

def map_keys(rng, count):
    keys = [0, 1, U64_MAX, U64_MAX - 1, 2**32, 2**63]
    keys += [i for i in range(count)]
    keys += [i << 48 for i in range(count)]
    keys += [rng.randrange(2**64) for _ in range(count)]
    return keys


def map_script(rng, count):
    """Random map operations and the results a Python dict gives."""
    keys = map_keys(rng, count)
    ops = ["map_new", "map_len"]
    expected = ["0"]
    model = {}
    for _ in range(count * 8):
        key = rng.choice(keys)
        roll = rng.random()
        if roll < 0.55:
            value = rng.choice((0, U64_MAX, rng.randrange(2**64)))
            ops.append(f"map_put {key} {value}")
            model[key] = value
        elif roll < 0.8:
            ops.append(f"map_get {key}")
            expected.append(str(model[key]) if key in model else "none")
        elif roll < 0.95:
            ops.append(f"map_has {key}")
            expected.append("1" if key in model else "0")
        else:
            ops.append("map_len")
            expected.append(str(len(model)))
    for key in sorted(model):
        ops.append(f"map_get {key}")
        expected.append(str(model[key]))
    ops.append("map_len")
    expected.append(str(len(model)))
    return ops, expected


@pytest.mark.parametrize("count", [1, 12, 13, 100, 5000])
def test_map_matches_model(driver, tmp_path, count):
    rng = random.Random(SEED + count)
    ops, expected = map_script(rng, count)
    assert run_ops(driver, tmp_path, ops) == expected


def test_map_get_of_a_missing_key_leaves_nothing_behind(driver, tmp_path):
    ops = ["map_new", "map_get 5", "map_len", "map_has 5", "map_put 5 9", "map_get 5"]
    assert run_ops(driver, tmp_path, ops) == ["none", "0", "0", "9"]


# ----- interner -----

def fnv1a(data):
    value = 0xcbf29ce484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001b3) & U64_MAX
    return value


class InternerModel:
    """First-seen handles assigned by Python's dict on byte strings."""

    def __init__(self):
        self.handles = {}
        self.texts = []

    def intern(self, text):
        if text not in self.handles:
            self.handles[text] = len(self.texts)
            self.texts.append(text)
        return self.handles[text]


def intern_words(rng, count):
    words = [b"", b"a", b"ab", b"abc", b"a\x00", b"a\x00b", b"\x00", b"\xff\xfe",
             "\u20ac\U0001f600".encode()]
    words += [b"x" * length for length in (1, 2, 200, 4096)]
    words += [f"name_{i}".encode() for i in range(count)]
    words += [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
              for _ in range(count)]
    return words


def intern_script(rng, count, intern_op="intern"):
    """Random interner operations and the results the model gives."""
    words = intern_words(rng, count)
    ops, expected = [], []
    model = InternerModel()
    for _ in range(count * 6):
        word = rng.choice(words)
        if intern_op == "intern_z" and b"\x00" in word:
            continue
        roll = rng.random()
        if roll < 0.7 or not model.texts:
            ops.append(f"{intern_op} {word.hex()}")
            expected.append(str(model.intern(word)))
        elif roll < 0.9:
            handle = rng.randrange(len(model.texts))
            ops.append(f"intern_text {handle}")
            expected += [hex_text(model.texts[handle]), "0"]
        else:
            ops.append("intern_count")
            expected.append(str(len(model.texts)))
    for handle, text in enumerate(model.texts):
        ops.append(f"intern_text {handle}")
        expected += [hex_text(text), "0"]
    ops.append("intern_count")
    expected.append(str(len(model.texts)))
    return ops, expected


@pytest.mark.parametrize("intern_op", ["intern", "intern_z"])
@pytest.mark.parametrize("count", [1, 40, 5000])
def test_interner_matches_model(driver, tmp_path, count, intern_op):
    rng = random.Random(SEED + count)
    ops, expected = intern_script(rng, count, intern_op)
    assert run_ops(driver, tmp_path, ["intern_new", *ops]) == expected


def test_equal_text_gives_equal_handles_whatever_came_between(driver, tmp_path):
    rng = random.Random(SEED)
    words = intern_words(rng, 400)
    ops, expected = ["intern_new"], []
    model = InternerModel()
    for word in words + words[::-1] + words:
        ops.append(f"intern {word.hex()}")
        expected.append(str(model.intern(word)))
    assert run_ops(driver, tmp_path, ops) == expected


def test_handles_depend_only_on_first_appearance_order(driver, tmp_path):
    words = [f"word_{i}".encode() for i in range(300)]
    first = ["intern_new"] + [f"intern {w.hex()}" for w in words]
    expected_first = [str(i) for i in range(len(words))]
    assert run_ops(driver, tmp_path, first) == expected_first
    reversed_order = words[::-1]
    second = ["intern_new"] + [f"intern {w.hex()}" for w in reversed_order]
    assert run_ops(driver, tmp_path, second) == expected_first


def test_full_hash_collisions_still_compare_the_text(driver, tmp_path):
    words = [b"", b"a", b"b", b"a\x00", b"ab"] + [f"n{i}".encode() for i in range(600)]
    ops, expected = ["intern_new"], []
    model = InternerModel()
    for word in words + words:
        ops.append(f"intern_hashed 12345 {word.hex()}")
        expected.append(str(model.intern(word)))
    assert run_ops(driver, tmp_path, ops) == expected


def test_a_returned_span_survives_later_growth(driver, tmp_path):
    words = [f"later_{i}".encode() for i in range(5000)]
    ops = ["intern_new", f"intern {b'early'.hex()}", "intern_keep 0",
           *[f"intern {w.hex()}" for w in words], "intern_show_kept"]
    lines = run_ops(driver, tmp_path, ops)
    assert lines[-1] == hex_text(b"early")
    assert lines[:-1] == [str(i) for i in range(1 + len(words))]


@pytest.mark.parametrize("data", [b"", b"a", b"abc\x00def", bytes(range(256))],
                         ids=["empty", "single", "nul", "all_bytes"])
def test_hash_is_the_fixed_fnv1a_function(driver, tmp_path, data):
    assert run_ops(driver, tmp_path, [f"intern_hash {data.hex()}"]) == [str(fnv1a(data))]


# ----- diagnostics -----

DIAGNOSTIC_LOCATIONS = [
    (1, 1), (2, 7), (10, None), (None, None), (None, 4), (1048576, 4096),
]
DIAGNOSTIC_MESSAGES = [
    "unexpected character '@'", "", "caf\u00e9 \u20ac \U0001f600", "100% {done}",
    "a, b: c",
]
DIAGNOSTIC_PATHS = ["input.mx", "dir/sub/file name.mx", "C:\\repo\\caf\u00e9.mx"]


@pytest.mark.parametrize("path", DIAGNOSTIC_PATHS)
def test_diagnostic_matches_the_python_format(driver, tmp_path, path):
    ops, expected = [], []
    for line, col in DIAGNOSTIC_LOCATIONS:
        for message in DIAGNOSTIC_MESSAGES:
            ops.append(f"diag {line or 0} {col or 0} {path.encode().hex()} "
                       f"{message.encode().hex()}")
            text = MortError(message, line=line, col=col, filename=path).format()
            expected.append(hex_text(text.encode()))
    assert run_ops(driver, tmp_path, ops) == expected


def test_diagnostic_keeps_text_after_an_embedded_nul(driver, tmp_path):
    message = b"bad \x00 byte"
    lines = run_ops(driver, tmp_path, [f"diag 1 2 {b'a.mx'.hex()} {message.hex()}"])
    assert bytes.fromhex(lines[0]) == b"a.mx:1:2: error: bad \\x00 byte"


def test_diag_fatal_exits_with_a_located_error(driver):
    result = run_fatal(driver, "diag_fatal")
    expected = MortError(
        "something is wrong", line=2, col=7, filename="dir/input.mx").format()
    assert result.returncode == EXIT_DIAGNOSTIC, result
    assert result.stdout == ""
    assert result.stderr == expected + "\n"


def test_diag_report_does_not_end_the_program(driver):
    result = run_fatal(driver, "diag_report")
    first = MortError("first problem", line=3, col=1, filename="dir/input.mx").format()
    second = MortError("second problem", line=4, col=1, filename="dir/input.mx").format()
    assert result.returncode == 0, result
    assert result.stdout == "continued\n"
    assert result.stderr == f"{first}\n{second}\n"


# ----- structural gates: nothing may expose table order or addresses -----

PUBLIC_TABLE_API = {
    "map_new", "map_put", "map_get", "map_has", "map_len",
    "intern_new", "intern", "intern_z", "intern_hash", "intern_hashed",
    "intern_text", "intern_count", "intern_within_capacity",
}
INTERNAL_TABLE_FUNCTIONS = {
    "map_with_capacity", "map_mix", "map_find_slot", "map_grow",
    "intern_slot_for_new_entry", "intern_grow_slots", "intern_grow_entries",
    "intern_copy",
}
ALLOWED_SUPPORT_EXTERNS = {"exit"}
ALLOWED_SUPPORT_BUILTINS = {"alloc", "free", "sizeof", "eprintln"}


def parse_source(text):
    return Parser(Lexer(text).tokenize()).parse()


def parse_file(path):
    return parse_source(path.read_text(encoding="utf-8"))


def walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for value in vars(current).values():
            stack.extend(check_subset.nodes_of(value))


def table_fields(support):
    return {
        field.name
        for struct in support.structs if struct.name in ("Map", "Interner")
        for field in struct.fields
    }


def table_violations(programs, fields):
    """programs: mapping of file name to parsed program."""
    problems = []
    for name, program in programs.items():
        for function in program.funcs:
            is_table_function = function.name.startswith(("map_", "intern"))
            known = function.name in PUBLIC_TABLE_API | INTERNAL_TABLE_FUNCTIONS
            if name == "support.mx" and is_table_function and not known:
                problems.append(f"{function.name} is not in the reviewed table API")
            if name == "support.mx" and is_table_function:
                continue
            for node in walk(function):
                if isinstance(node, A.FieldAccess) and node.field in fields:
                    problems.append(f"{function.name} reads table field {node.field}")
                if isinstance(node, A.Call) and node.name in INTERNAL_TABLE_FUNCTIONS:
                    problems.append(f"{function.name} calls {node.name}")
    return problems


def support_program():
    return parse_file(HERE / "support.mx")


def repo_programs():
    return {path.name: parse_file(path) for path in sorted(HERE.glob("*.mx"))}


def test_no_source_can_reach_table_internals():
    programs = repo_programs()
    assert table_violations(programs, table_fields(programs["support.mx"])) == []


TABLE_LEAKS = {
    "unreviewed_iterator": ("support.mx", "fn map_each(m: *Map) -> u64 { return 0; }"),
    "unreviewed_interner_entry": (
        "support.mx", "fn intern_slot_at(it: *Interner, i: u64) -> u64 { return i; }"),
    "field_read_elsewhere": (
        "parser.mx", "fn leak(m: *Map) -> u64 { return (*m).map_capacity; }"),
    "internal_call_elsewhere": (
        "checker.mx", "fn leak(m: *Map) -> u64 { return map_find_slot(m, 1); }"),
}


@pytest.mark.parametrize("name", sorted(TABLE_LEAKS))
def test_table_gate_detects_a_leak(name):
    file_name, source = TABLE_LEAKS[name]
    fields = table_fields(support_program())
    assert table_violations({file_name: parse_source(source)}, fields), name


def test_support_reaches_no_clock_environment_or_random_source():
    support = support_program()
    assert {extern.name for extern in support.externs} == ALLOWED_SUPPORT_EXTERNS
    declared = {function.name for function in support.funcs}
    declared |= {extern.name for extern in support.externs}
    called = {node.name for node in walk(support) if isinstance(node, A.Call)}
    assert called - declared <= ALLOWED_SUPPORT_BUILTINS


def pointer_to_integer_casts(sources):
    """Type-check the sources together and list casts from pointer to integer."""
    programs = [parse_source(source) for source in sources]
    program = A.Program(
        [f for p in programs for f in p.funcs],
        [s for p in programs for s in p.structs],
        [g for p in programs for g in p.globals],
        [e for p in programs for e in p.externs],
        enums=[e for p in programs for e in p.enums],
        tests=[], aliases=[])
    Checker(program).check()
    return [
        node.line for node in walk(program)
        if isinstance(node, A.Cast) and node.expr.type.startswith("*")
        and not node.target_type.startswith("*")
    ]


NO_MAIN = "fn main() -> int { return 0; }"


def test_support_never_turns_an_address_into_a_number():
    source = (HERE / "support.mx").read_text(encoding="utf-8")
    assert pointer_to_integer_casts([source, NO_MAIN]) == []


def test_address_gate_detects_a_cast():
    leak = "fn leak(p: *u8) -> u64 { return p as u64; } " + NO_MAIN
    assert pointer_to_integer_casts([leak]) == [1]


def compile_with_hash_seed(seed):
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import mortc; "
        "sys.stdout.buffer.write("
        "mortc.compile_files_to_c([sys.argv[2]]).encode('utf-8'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT), str(HERE / "support_test_main.mx")],
        capture_output=True, env=dict(os.environ, PYTHONHASHSEED=str(seed)), timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_generated_c_is_independent_of_python_hash_seed():
    reference = compile_with_hash_seed(0)
    for seed in (1, 2, 12345):
        assert compile_with_hash_seed(seed) == reference, seed
    assert str(ROOT).encode() not in reference
    assert ROOT.as_posix().encode() not in reference


def test_repeated_runs_write_the_same_bytes(driver, tmp_path):
    rng = random.Random(SEED)
    interned, _ = intern_script(rng, 400)
    mapped, _ = map_script(rng, 400)
    ops = ["intern_new", *interned, *mapped]
    outputs = []
    for attempt in range(3):
        directory = tmp_path / str(attempt)
        directory.mkdir()
        outputs.append(run_ops(driver, directory, ops))
    assert outputs[0] == outputs[1] == outputs[2]


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
    "intern_text_bad": range_message(1, 1, "interner"),
    "grow_capacity_overflow": "mort: internal error: capacity overflow",
    "buf_capacity_overflow": "mort: internal error: capacity overflow",
    "alloc_impossible": "mort: out of memory",
    "alloc_array_overflow": "mort: internal error: allocation size overflow",
}


@pytest.mark.parametrize("name", sorted(FATAL_CASES))
def test_misuse_is_a_clean_failure(driver, name):
    result = run_fatal(driver, name)
    assert result.returncode == EXIT_INTERNAL, result
    assert result.stdout == ""
    assert result.stderr == FATAL_CASES[name] + "\n"


# ----- 32-bit targets: size_t is narrower than u64 -----

def x86_target():
    if os.name == "nt":
        return "x86-windows-gnu"
    if sys.platform.startswith("linux") and platform.machine() == "x86_64":
        return "x86-linux-musl"
    return None


@pytest.fixture(scope="session")
def x86_driver(tmp_path_factory):
    zig = mortc.find_zig()
    target = x86_target()
    if zig is None or target is None:
        pytest.skip("needs Zig and a host that runs 32-bit x86 programs")
    build = tmp_path_factory.mktemp("support-build-x86")
    source = build / "support_test_main.c"
    source.write_bytes(
        mortc.compile_files_to_c([str(HERE / "support_test_main.mx")]).encode("utf-8"))
    exe = build / ("support_test_main.exe" if os.name == "nt" else "support_test_main")
    result = subprocess.run(
        [*zig, "-target", target, str(source), "-o", str(exe), "-O2", "-std=c11"],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return exe


X86_FATAL_CASES = {
    "alloc_past_size_t": "mort: out of memory",
    "alloc_array_past_size_t": "mort: internal error: allocation size overflow",
    "vec_grow_past_size_t": "mort: internal error: allocation size overflow",
    "ptr_grow_past_size_t": "mort: internal error: allocation size overflow",
    "grow_past_size_t": "mort: internal error: capacity overflow",
}


@pytest.mark.parametrize("name", sorted(X86_FATAL_CASES))
def test_size_beyond_a_32_bit_size_t_is_a_clean_failure(x86_driver, name):
    result = run_fatal(x86_driver, name)
    assert result.returncode == EXIT_INTERNAL, result
    assert result.stdout == ""
    assert result.stderr == X86_FATAL_CASES[name] + "\n"


def test_x86_and_x64_builds_give_the_same_results(driver, x86_driver, tmp_path):
    rng = random.Random(SEED)
    interned, _ = intern_script(rng, 400)
    mapped, _ = map_script(rng, 400)
    vector, _ = vector_script(rng, "vec", 3000)
    ops = ["intern_new", *interned, *mapped, *vector]
    native = tmp_path / "native"
    x86 = tmp_path / "x86"
    native.mkdir()
    x86.mkdir()
    assert run_ops(driver, native, ops) == run_ops(x86_driver, x86, ops)


def test_usage_error_is_a_clean_failure(driver):
    result = subprocess.run([str(driver)], capture_output=True, text=True, timeout=60)
    assert result.returncode == EXIT_INTERNAL
    assert "usage:" in result.stderr
