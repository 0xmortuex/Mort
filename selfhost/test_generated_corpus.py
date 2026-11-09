"""The accepted-program corpus: its floors, and the generator that supplies it.

    python -m pytest selfhost/test_generated_corpus.py -q

See selfhost/corpus.py for the classes and selfhost/PLAN.md section 5.1 for why
the floors have these values.

Larger corpus: MORT_SELFHOST_CORPUS_SCALE=4 multiplies the generated programs and
their size; expect about scale times as long.
"""
import collections
import functools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT))

import check_subset  # noqa: E402
import corpus  # noqa: E402
import gen_subset_programs as gen  # noqa: E402
import mortc  # noqa: E402
import py_ast_dump  # noqa: E402
from mort import mort_ast as A  # noqa: E402
from test_parser_differential import assert_agrees, parse_exe  # noqa: E402,F401

try:
    # T4's differential, once its branch is merged.
    from test_checker_decl_differential import check_exe, compare  # noqa: F401
    HAS_DECLARATION_CHECKER = True
except ModuleNotFoundError:
    HAS_DECLARATION_CHECKER = False

SCALE = int(os.environ.get("MORT_SELFHOST_CORPUS_SCALE", "1"))
SUBSET_SPECS = gen.default_corpus(SCALE)
FULL_SPECS = gen.full_corpus(SCALE)
ALL_SPECS = SUBSET_SPECS + FULL_SPECS


@functools.cache
def program_of(spec):
    return gen.generate(spec)


def written(spec, directory):
    return gen.write_program(program_of(spec), directory)


def checked_ast(root):
    """The checker's annotated AST of a program, from the real compiler."""
    captured = []

    class CaptureProgram:
        def __init__(self, program, **options):
            captured.append(program)

        def generate(self):
            return ""

    original = mortc.CodeGen
    mortc.CodeGen = CaptureProgram
    try:
        mortc.compile_files_to_c([str(root)])
    finally:
        mortc.CodeGen = original
    return captured[0]


# ----- the floors -----


def test_accepted_corpus_meets_the_plan_floor(tmp_path):
    """Section 5.1: at least 8000 accepted lines, from real and generated programs."""
    existing = corpus.existing_subset_files()
    assert len(existing) >= corpus.EXISTING_FILES, [p.name for p in existing]
    accepted_existing = [p for p in existing if corpus.compiles(p)]
    kernel = corpus.write_stubbed_kernel(tmp_path)
    programs = [program_of(spec) for spec in SUBSET_SPECS]
    generated_lines = sum(program.line_count for program in programs)

    assert len(programs) >= corpus.MIN_GENERATED_PROGRAMS
    assert generated_lines >= corpus.MIN_GENERATED_LINES
    large = [p for p in programs if p.line_count >= corpus.LARGE_PROGRAM_LINES]
    assert len(large) >= corpus.MIN_LARGE_PROGRAMS

    kernel_lines = corpus.line_count(kernel)
    assert kernel_lines >= corpus.MIN_KERNEL_LINES
    assert kernel_lines >= corpus.MIN_REAL_PROGRAM_LINES

    accepted = (sum(corpus.line_count(p) for p in accepted_existing)
                + kernel_lines + generated_lines)
    assert accepted >= corpus.MIN_ACCEPTED_LINES, accepted


def test_stubbed_kernel_stays_inside_the_subset(tmp_path):
    path = corpus.write_stubbed_kernel(tmp_path)
    assert check_subset.check_file(path, collections.Counter()) == []
    mortc.compile_files_to_c([str(path)], freestanding=True)


def test_existing_programs_still_pass_the_gate():
    """A real program dropping out of the subset must be noticed."""
    assert len(corpus.existing_subset_files()) >= corpus.EXISTING_FILES


def test_parser_agrees_on_the_stubbed_kernel(parse_exe, tmp_path):  # noqa: F811
    assert_agrees(parse_exe, corpus.write_stubbed_kernel(tmp_path), tmp_path / "out.txt")


# ----- determinism -----

HASH_SCRIPT = """
import hashlib, sys
sys.path.insert(0, sys.argv[1])
import gen_subset_programs as gen
digest = hashlib.sha256()
for spec in gen.default_corpus() + gen.full_corpus():
    program = gen.generate(spec)
    for name in sorted(program.files):
        digest.update(name.encode() + b"\\0" + program.files[name] + b"\\0")
    digest.update(program.expected_stdout + b"\\0" + program.expected_stderr)
    digest.update(str(program.expected_exit).encode())
print(digest.hexdigest())
"""


def test_output_is_identical_across_hash_seeds():
    digests = set()
    for seed in ("0", "1", "2", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", HASH_SCRIPT, str(HERE / "tools")],
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
        assert result.returncode == 0, result.stderr
        digests.add(result.stdout)
    assert len(digests) == 1, digests


def test_same_spec_gives_the_same_program():
    spec = gen.Spec("again", 12345, 400)
    first, second = gen.generate(spec), gen.generate(spec)
    assert first.files == second.files
    assert first.expected_stdout == second.expected_stdout
    assert first.expected_exit == second.expected_exit


def test_seed_and_size_change_the_program():
    base = gen.generate(gen.Spec("s", 1, 200))
    assert gen.generate(gen.Spec("s", 2, 200)).files != base.files
    smaller, larger = gen.generate(gen.Spec("s", 1, 150)), gen.generate(gen.Spec("s", 1, 900))
    assert smaller.line_count < larger.line_count
    assert larger.line_count >= 900


def test_command_line_writes_the_program(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HERE / "tools" / "gen_subset_programs.py"),
         "--seed", "9", "--lines", "120", "--out", str(tmp_path / "p")],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "p" / "main.mx").exists()
    assert (tmp_path / "p" / "expected_stdout.txt").exists()


# ----- gate, compilation, and the independent expected result -----


@pytest.mark.parametrize("spec", SUBSET_SPECS, ids=lambda s: s.name)
def test_subset_program_passes_the_gate(spec, tmp_path):
    root = written(spec, tmp_path)
    for relative in program_of(spec).files:
        assert check_subset.check_file(root.parent / relative,
                                       collections.Counter()) == []


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_program_compiles_and_matches_the_model(spec, tmp_path):
    """The model's stdout, stderr and exit status against the real program."""
    program = program_of(spec)
    root = written(spec, tmp_path)
    exe = tmp_path / ("program.exe" if os.name == "nt" else "program")
    built = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(root), "-o", str(exe)],
        capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert built.returncode == 0, f"{spec}: {built.stdout}\n{built.stderr}"
    run = subprocess.run([str(exe)] + program.args, capture_output=True, cwd=tmp_path,
                         timeout=60)
    stdout = run.stdout.replace(b"\r\n", b"\n")
    assert stdout == program.expected_stdout, spec
    if program.expect_trap:
        assert run.returncode != 0, spec
        assert run.stderr != b"", spec
    else:
        assert run.returncode == program.expected_exit, spec
        assert run.stderr.replace(b"\r\n", b"\n") == program.expected_stderr, spec


# ----- coverage of the language, read from the compiler's own AST -----


@pytest.fixture(scope="module")
def subset_coverage(tmp_path_factory):
    """Node kinds, (op, S, T) pairs and literal values across the subset corpus."""
    kinds = collections.Counter()
    pairs = set()
    literal_uses = set()
    int_literals = set()
    base = tmp_path_factory.mktemp("coverage")
    for index, spec in enumerate(SUBSET_SPECS):
        directory = base / str(index)
        root = gen.write_program(program_of(spec), directory)
        # Per-file parses for the node kinds: flattening removes ImportDecl.
        for relative in sorted(program_of(spec).files):
            parsed = py_ast_dump.parse_source(
                (directory / relative).read_text(encoding="ascii"))
            kinds.update(type(node).__name__ for node in check_subset.walk(parsed))
        program = checked_ast(root)
        for node in check_subset.walk(program):
            if isinstance(node, A.IntLit):
                int_literals.add(node.value)
            if isinstance(node, A.Binary) and node.op in gen.INT_BINARY_OPS:
                record_binary(node, pairs, literal_uses)
    return kinds, pairs, literal_uses, int_literals


def record_binary(node, pairs, literal_uses):
    left, right = node.left, node.right
    if left.is_lit and not right.is_lit:
        literal_uses.add(("lit", node.op, right.type))
    if right.is_lit and not left.is_lit:
        literal_uses.add((node.op, left.type, "lit"))
    if node.op in gen.SHIFTS:
        pairs.add((node.op, left.type, right.type))
    elif isinstance(left, A.Cast):
        pairs.add((node.op, left.expr.type, left.target_type))


def canonical(ty):
    """`int` is an alias: the checked AST records it as i64."""
    return "i64" if ty == "int" else ty


def test_every_subset_node_kind_is_generated(subset_coverage):
    kinds, _, _, _ = subset_coverage
    assert check_subset.ALLOWED_NODES - set(kinds) == set()


def test_every_operator_on_every_integer_type_pair(subset_coverage):
    """Section 5.1: each operator on each ordered pair of subset integer types."""
    _, pairs, _, _ = subset_coverage
    expected = {(op, canonical(source), canonical(target))
                for op in gen.INT_BINARY_OPS
                for source in gen.SUBSET_INTS for target in gen.SUBSET_INTS}
    assert expected - pairs == set()


def test_literals_coerce_on_either_side_of_every_operator(subset_coverage):
    """Literal adoption: `x op 1` and `1 op x`, for every operator and type."""
    _, _, literal_uses, _ = subset_coverage
    expected = set()
    for ty in map(canonical, gen.SUBSET_INTS):
        for op in gen.INT_BINARY_OPS:
            expected.add((op, ty, "lit"))
            if op not in gen.SHIFTS:  # a literal shifted by a variable has no context
                expected.add(("lit", op, ty))
    assert expected - literal_uses == set()


def test_boundary_literals_are_generated(subset_coverage):
    _, _, _, literals = subset_coverage
    edges = {127, 255, (1 << 31) - 1, 1 << 31, (1 << 32) - 1,
             (1 << 63) - 1, 1 << 63, (1 << 64) - 1}
    assert edges - literals == set()


def test_full_profile_covers_the_rest_of_the_language(tmp_path):
    kinds = collections.Counter()
    sections = set()
    modules = imports_with_alias = public = 0
    for index, spec in enumerate(FULL_SPECS):
        root = gen.write_program(program_of(spec), tmp_path / str(index))
        sections.update(program_of(spec).sections)
        for relative in sorted(program_of(spec).files):
            parsed = py_ast_dump.parse_source(
                (root.parent / relative).read_text(encoding="ascii"))
            modules += 1 if parsed.module_name else 0
            imports_with_alias += sum(1 for item in parsed.imports if item.alias)
            public += sum(1 for function in parsed.funcs if function.public)
            kinds.update(type(node).__name__ for node in check_subset.walk(parsed))
    wanted = {"Try", "Move", "Defer", "TupleLit", "ArrayRepeat", "NullLit", "FloatLit",
              "Asm", "TypeAliasDecl", "TestDecl"}
    assert wanted - set(kinds) == set()
    assert set(gen.FULL_ONLY_SECTIONS) - sections == set()
    assert modules and imports_with_alias and public


# ----- the differential corpus -----


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_parser_agrees_on_generated_program(parse_exe, spec, tmp_path):  # noqa: F811
    root = written(spec, tmp_path)
    for relative in program_of(spec).files:
        assert_agrees(parse_exe, root.parent / relative, tmp_path / "out.txt")


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_declaration_checker_agrees_on_generated_program(request, spec, tmp_path):
    if not HAS_DECLARATION_CHECKER:
        pytest.skip("selfhost/check_main.mx (T4) is not on this branch")
    exe = request.getfixturevalue("check_exe")
    root = written(spec, tmp_path)
    assert compare(exe, root, "hosted", tmp_path) == "ok"


# ----- the generator finds real defects -----

# Defects planted in a copy of parser.mx. Each must be invisible on a trivial
# program, so that catching it says something about the generated corpus.
PARSER_MUTANTS = {
    "bitor_tighter_than_bitxor": (
        "if kind == Tok.PIPE { return 3; }\n    if kind == Tok.CARET { return 4; }",
        "if kind == Tok.CARET { return 3; }\n    if kind == Tok.PIPE { return 4; }"),
    "binary_right_associative": (
        "(*node).b = ps_binary(p, level + 1);", "(*node).b = ps_binary(p, level);"),
    "inclusive_range_lost": ("(*node).flag = true;\n    } else {\n        ps_expect(p, Tok.DOTDOT",
                             "(*node).flag = false;\n    } else {\n        ps_expect(p, Tok.DOTDOT"),
    "cast_chain_stops_after_one": (
        "while ps_at(p, Tok.AS) {", "if ps_at(p, Tok.AS) {"),
    "operator_line_is_the_left_operand": (
        "let node = ast_new(NodeKind.BINARY, (*op).line);",
        "let node = ast_new(NodeKind.BINARY, (*left).line);"),
    "remainder_at_additive_level": (
        "|| kind == Tok.PERCENT { return 10; }",
        "{ return 10; }\n    if kind == Tok.PERCENT { return 9; }"),
}
TRIVIAL_PROGRAM = "fn main() -> int { return 0; }\n"


def build_parser(directory, mutation):
    source_dir = directory / "src"
    source_dir.mkdir()
    for path in HERE.glob("*.mx"):
        shutil.copy(path, source_dir / path.name)
    if mutation is not None:
        old, new = mutation
        parser_path = source_dir / "parser.mx"
        text = parser_path.read_text(encoding="ascii")
        assert text.count(old) == 1, f"mutation site not unique: {old!r}"
        parser_path.write_text(text.replace(old, new), encoding="ascii", newline="\n")
    exe = directory / ("parse_main.exe" if os.name == "nt" else "parse_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(source_dir / "parse_main.mx"),
         "-o", str(exe)], capture_output=True, text=True, cwd=directory, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


def caught_by_corpus(exe, tmp_path):
    """The first generated program whose dump the mutant gets wrong, or None."""
    for spec in ALL_SPECS:
        program = program_of(spec)
        program_dir = tmp_path / spec.name
        root = gen.write_program(program, program_dir)
        for relative in program.files:
            path = root.parent / relative
            expected = py_ast_dump.dump_file(path)
            out = tmp_path / "dump.txt"
            if out.exists():
                out.unlink()
            run = subprocess.run([str(exe), str(path), str(out)], capture_output=True,
                                 timeout=60)
            got = out.read_bytes().decode("ascii") if out.exists() else None
            if run.returncode != 0 or got != expected.dump:
                return spec.name
    return None


@pytest.mark.parametrize("name", sorted(PARSER_MUTANTS))
def test_generated_programs_catch_a_planted_parser_defect(name, tmp_path):
    exe = build_parser(tmp_path, PARSER_MUTANTS[name])
    trivial = tmp_path / "t.mx"
    trivial.write_bytes(TRIVIAL_PROGRAM.encode())
    out = tmp_path / "t.txt"
    run = subprocess.run([str(exe), str(trivial), str(out)], capture_output=True,
                         timeout=60)
    assert run.returncode == 0, f"mutant {name} does not even parse a trivial program"
    assert out.read_bytes().decode("ascii") == py_ast_dump.dump_file(trivial).dump
    assert caught_by_corpus(exe, tmp_path) is not None, f"mutant {name} survived"


def test_unmutated_parser_passes_the_mutation_check(tmp_path):
    """The check itself must not fire on the real parser."""
    assert caught_by_corpus(build_parser(tmp_path, None), tmp_path) is None


# ----- the model checks itself -----


@pytest.mark.parametrize("ty,op,left,right,want", [
    ("u8", "+", 255, 1, 0), ("i8", "+", 127, 1, -128),
    ("i8", "-", -128, 1, 127), ("u8", "-", 0, 1, 255),
    ("i32", "*", 1 << 30, 4, 0), ("u32", "*", 1 << 31, 2, 0),
    ("i64", "/", -(1 << 63), -1, -(1 << 63)), ("i64", "%", -(1 << 63), -1, 0),
    ("i32", "/", -7, 2, -3), ("i32", "%", -7, 2, -1), ("i32", "%", 7, -2, 1),
    ("u8", "&", 0xF0, 0x3C, 0x30), ("i8", "|", -128, 1, -127), ("u16", "^", 0xFF00, 0x0FF0, 0xF0F0),
])
def test_model_arithmetic_follows_the_specification(ty, op, left, right, want):
    assert gen.int_binary(op, ty, left, right) == want


@pytest.mark.parametrize("op,ty,left,count,want", [
    ("<<", "u8", 1, 8, 0), (">>", "u8", 128, 9, 0), (">>", "i8", -128, 8, -1),
    (">>", "i8", 127, 8, 0), ("<<", "i8", 1, 7, -128), (">>", "i8", -128, 7, -1),
    (">>", "u64", 1 << 63, 63, 1), ("<<", "u32", 1, 31, 1 << 31),
    (">>", "i64", -1, 64, -1), (">>", "i64", 5, 200, 0),
])
def test_model_shifts_follow_the_specification(op, ty, left, count, want):
    assert gen.int_shift(op, ty, left, count) == want


def test_model_traps_where_the_specification_says():
    with pytest.raises(gen.Trap):
        gen.int_binary("/", "u8", 1, 0)
    with pytest.raises(gen.Trap):
        gen.int_shift("<<", "i32", 1, -1)


def test_model_refuses_target_specific_c_arithmetic():
    with pytest.raises(gen.Invalid):
        gen.int_binary("+", "c_int", (1 << 31) - 1, 1)
    with pytest.raises(gen.Invalid):
        gen.int_binary("-", "c_char", 0, 1)


@pytest.mark.parametrize("value,target,want", [
    (300, "u8", 44), (-1, "u8", 255), (255, "i8", -1), (1 << 63, "i64", -(1 << 63)),
    (-1, "u64", (1 << 64) - 1), (5, "int", 5), (-129, "i8", 127),
])
def test_model_casts_take_the_value_modulo_the_width(value, target, want):
    assert gen.cast_int(value, target) == want


def test_expected_output_is_never_derived_from_the_compiler():
    """The generator stays independent of the compiler under test."""
    source = (HERE / "tools" / "gen_subset_programs.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(mort|mortc|py_ast_dump|check_subset)\b",
                         source, re.MULTILINE)
    assert "subprocess" not in source
