"""Differential test: selfhost's C generator must agree with mort/codegen.py.

Run from the repository root:

    python -m pytest selfhost/test_codegen_differential.py -q

Every expected byte comes from the real, unmodified CodeGen. The compiler driver
cg_main.mx is given nothing but the program. Helper combinations no real program
can reach (threads in a freestanding build) are exercised through the test-only
driver cg_forced_main.mx; test_codegen_expr_differential.py drives every helper
from real programs through cg_main.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT))

import corpus  # noqa: E402
import gen_subset_programs as gen  # noqa: E402
import py_check_dump  # noqa: E402
import py_codegen_dump  # noqa: E402
from test_checker_decl_differential import (  # noqa: E402
    BODY_CASES, DECL_CASES, MODES, REPO_FILES, first_difference, run_mort_bounded,
)
from test_checker_expr_differential import (  # noqa: E402
    EXPR_CASES, FN_ONLY_CASES, full_body,
)


def build_driver(tmp_path_factory, name):
    build = tmp_path_factory.mktemp(f"selfhost-{name}-build")
    exe = build / (f"{name}.exe" if os.name == "nt" else name)
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / f"{name}.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, (
        f"compiling {name}.mx failed:\n{result.stdout}\n{result.stderr}")
    return exe


@pytest.fixture(scope="session")
def cg_exe(tmp_path_factory):
    return build_driver(tmp_path_factory, "cg_main")


@pytest.fixture(scope="session")
def cg_forced_exe(tmp_path_factory):
    return build_driver(tmp_path_factory, "cg_forced_main")


def run_mort(exe, manifest_text, tmp_path, forced_text=None, timeout=120):
    manifest = tmp_path / "manifest.txt"
    manifest.write_bytes(manifest_text.encode("utf-8"))
    check_out = tmp_path / "check.txt"
    c_out = tmp_path / "out.c"
    if c_out.exists():
        c_out.unlink()
    command = [str(exe), str(manifest), str(check_out), str(c_out)]
    if forced_text is not None:
        forced = tmp_path / "forced.txt"
        forced.write_bytes(forced_text.encode("ascii"))
        command.append(str(forced))
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    text = c_out.read_bytes() if c_out.exists() else None
    return result.returncode, result.stderr.replace(b"\r\n", b"\n").rstrip(b"\n"), text


def assert_same_c(expected_text, produced, label):
    expected = expected_text.encode("utf-8")
    if expected != produced:
        raise AssertionError(
            f"{label}: C differs: "
            f"{first_difference(expected_text, produced.decode('utf-8', 'replace'))}")


def compare(exe, path, mode, tmp_path):
    """Run both generators on one root file and assert they agree."""
    result, text, generator_error = py_codegen_dump.reference(path, mode)
    label = f"{path} ({mode})"
    if result.outcome == "load":
        return "load"
    assert result.outcome != "crash", f"{label}: Python crash"
    if result.outcome == "parse":
        code, stderr, _ = run_mort(exe, f"MODE {mode}\nFILE {result.failing_file}\n", tmp_path)
        assert code == 1, f"{label}: {stderr!r}"
        assert stderr.decode("utf-8") == result.error, label
        return "parse"
    code, stderr, produced = run_mort(exe, result.manifest, tmp_path)
    assert code in (0, 1), f"{label}: exit {code}: {stderr!r}"
    if result.outcome == "error":
        assert code == 1, f"{label}: Mort accepted; python: {result.error}"
        assert stderr.decode("utf-8") == result.error, label
        return "error"
    if generator_error is not None:
        assert code == 1, f"{label}: Mort accepted; python: {generator_error}"
        assert stderr.decode("utf-8") == generator_error, label
        return "generator-error"
    assert code == 0, f"{label}: Mort rejected a valid program: {stderr!r}"
    assert produced is not None, f"{label}: no C written"
    assert_same_c(text, produced, label)
    return "ok"


def agree(exe, tmp_path, source, mode="hosted"):
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8"))
    outcome = compare(exe, path, mode, tmp_path)
    assert outcome != "load", source
    return outcome


# ----- declarations and statements, one construct at a time -----

PRELUDE = """
resource struct R { v: i64 }
fn destroy(value: *R) -> void { }
fn make_r() -> R { return R { v: 1 }; }
struct Holder { r: R, n: i64 }
enum Owns { Has(R), Empty }
enum Result<V, E> { Ok(V), Err(E) }
fn may() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }
struct Pt { x: i64, y: u8 }
enum E { A, B }
fn g() -> i64 { return 1; }
"""

STATEMENTS = {
    "let_plain": "let a = 1; let b: u8 = 2; const c: i64 = 3;",
    "let_pointer_const": "let a = 1; const p: *i64 = &a; const q: *const i64 = &a;",
    "let_fn_pointer": "const f: fn() -> i64 = g; let h: fn() -> i64 = g;",
    "let_array": "let a: [u8; 3] = [1, 2, 3]; const b: [i64; 2] = [1; 2];",
    "let_array_of_fn": "let a: [fn() -> i64; 2] = [g, g];",
    "let_tuple_slice": "let t = (1, true); let u: *u8 = null; let s = slice<u8>(u, 1);",
    "let_resource": "let r = make_r();",
    "let_nested_resource": "let h = Holder { r: make_r(), n: 1 };",
    "let_array_resource": "let a: [R; 2] = [make_r(), make_r()];",
    "let_tuple_resource": "let t = (make_r(), 1);",
    "assign": "let a = 1; a = 2; let p = &a; *p = 3;",
    "compound_i64": "let a: i64 = 1; a += 2; a -= 2; a *= 2; a /= 2; a %= 2;",
    "compound_bits": "let a: i64 = 1; a &= 2; a |= 2; a ^= 2; a <<= 2; a >>= 2;",
    "compound_u8": "let a: u8 = 1; a += 2; a /= 2; a %= 3; a <<= 1; a >>= 1;",
    "compound_i32_signed_count": "let a: i32 = 1; let n: i32 = 2; a <<= n; a >>= n;",
    "compound_u64_unsigned_count": "let a: u64 = 1; let n: u32 = 2; a <<= n; a >>= n;",
    "compound_c_int": "let a: c_int = 1; a += 2; a <<= 1;",
    "compound_float": "let a = 1.5; a += 2.5; a *= 2.5;",
    "compound_ptr_target": "let a: u16 = 1; let p = &a; *p += 1;",
    "return_value": "if true { return 1; }",
    "return_with_defer": "defer g(); return 1;",
    "if_else": "if true { let a = 1; } else { let b = 2; }",
    "if_else_if": "if true { } else if false { } else { let c = 1; }",
    "if_binary_cond": "if 1 == 2 { }",
    "if_call_cond": "if g() == 1 { }",
    "while_loop": "let a = 0; while a < 3 { a += 1; if a == 2 { continue; } else { break; } }",
    "while_defer": "while true { defer g(); break; }",
    "for_exclusive": "for i in 0..3 { let a = i; }",
    "for_inclusive": "for i in 0..=3 { let a = i; }",
    "for_typed": "for i: u8 in 0..3 { let a = i; }",
    "for_inclusive_defer": "for i in 0..=3 { defer g(); continue; }",
    "block": "{ let a = 1; { let b = 2; } }",
    "block_defer": "{ defer g(); let a = 1; }",
    "expr_stmt": "g(); 1 + 2;",
    "defer_order": "defer g(); defer g(); { defer g(); }",
    "defer_on_break": "while true { defer g(); { defer g(); break; } }",
    "defer_destroy": "let r = make_r(); defer destroy(&r);",
    "destroy_call": "let r = make_r(); destroy(&r);",
    "move_resource": "let r = make_r(); let s = move r;",
    "match_enum": "let e = E.A; match e { E.A => { let a = 1; } E.B => { let b = 2; } }",
    "match_wildcard": "let e = E.A; match e { E.A => { } _ => { } }",
    "match_payload": "let o = Owns.Has(make_r()); match move o { Owns.Has(r) => { destroy(&r); } Owns.Empty => { } }",
    "match_int": "let a = 1; match a { 1 => { } 2 => { } _ => { } }",
    "try_let": "let a = may(); match a { Result<i64, i64>.Ok(v) => { let b = v; } Result<i64, i64>.Err(e) => { } }",
    "asm": 'asm("nop");',
}

# Generic forms need their own functions; each of these replaces main.
DECLARATIONS = {
    "enum_plain": "enum A { X, Y, Z }\n",
    "enum_single": "enum A { Only }\n",
    "enum_payload_tuple": "enum A { X(i64, u8), Y(*u8), Z }\n",
    "enum_all_payload": "enum A { X(i64), Y(*A) }\n",
    "struct_fields": "struct S { a: i64, b: *u8, c: [u8; 4], d: fn(i64) -> i64, e: (i64, bool) }\n",
    "struct_empty": "struct S { }\n",
    "struct_nested_order": "struct B { a: A }\nstruct A { x: i64 }\n",
    "struct_mutual_pointer": "struct A { b: *B }\nstruct B { a: *A }\n",
    "struct_tuple_dep": "struct S { t: (T, i64) }\nstruct T { x: i64 }\n",
    "tuple_of_struct": "struct T { x: i64 }\nfn h(a: (T, i64)) -> void { }\n",
    "tag_of_function_type": "struct B<T> { v: T }\nstruct S { a: B<fn(i64) -> i64> }\nfn h(a: []fn(i64) -> i64, b: []const fn() -> i64) -> void { }\n",
    "tuple_of_fn": "fn h(a: (fn(i64) -> i64, u8), b: (*fn() -> i64, []const u8)) -> void { }\n",
    "array_of_struct": "struct T { x: i64 }\nstruct S { a: [T; 2] }\n",
    "generic_struct": "struct B<T> { v: T }\nstruct S { a: B<i64>, b: B<*u8> }\n",
    "generic_enum": "enum O<T> { Some(T), None }\nstruct S { a: O<i64> }\n",
    "generic_fn": "fn id<T>(a: T) -> T { return a; }\nfn h() -> i64 { let a = id(1); let b = id(true); return a; }\n",
    "generic_explicit": "fn size<T>() -> u64 { return sizeof<T>(); }\nfn h() -> u64 { return size<(i64, u8)>(); }\n",
    "generic_only_template": "struct B<T> { v: T }\nfn id<T>(a: T) -> T { return a; }\n",
    "alias": "type P = *u8;\nstruct S { a: P }\nfn h(p: P) -> P { return p; }\n",
    "extern_fn": "extern fn puts(text: *const c_char) -> c_int;\n",
    "extern_void": "extern fn abort() -> void;\n",
    "fn_pointer_param": "fn h(f: fn(i64, *u8) -> i64) -> i64 { return 0; }\n",
    "fn_returns_fn": "fn h() -> fn() -> i64 { return g2; }\nfn g2() -> i64 { return 1; }\n",
    "fn_param_slice": "fn h(s: []u8, t: []const i64) -> u64 { return s.len; }\n",
    "fn_many_params": "fn h(a: i64, b: u8, c: *void, d: bool, e: f64) -> void { }\n",
    "global_const": "const G: i64 = 1 + 2;\nlet H: u8 = 3;\n",
    "global_pointer": "const G: *u8 = \"text\";\nlet H: *const u8 = \"t\";\n",
    "global_fn": "fn f() -> i64 { return 1; }\nconst G: fn() -> i64 = f;\n",
    "global_array": "const G: [u8; 2] = [1, 2];\nlet H: [i64; 3] = [0; 3];\n",
    "global_enum": "enum A { X }\nconst G: A = A.X;\n",
    "resource_param": "resource struct R { v: i64 }\nfn destroy(value: *R) -> void { }\nfn t(r: R) -> void { destroy(&r); }\n",
    "drop_struct_order": "resource struct R { v: i64 }\nfn destroy(value: *R) -> void { }\nstruct H { a: R, b: i64, c: R }\nfn t(h: H) -> void { }\n",
    "drop_enum": "resource struct R { v: i64 }\nfn destroy(value: *R) -> void { }\nenum H { A(R), B(i64), C }\nfn t(h: H) -> void { }\n",
    "drop_array": "resource struct R { v: i64 }\nfn destroy(value: *R) -> void { }\nstruct H { a: [R; 3] }\nfn t(h: H) -> void { }\n",
    "drop_tuple": "resource struct R { v: i64 }\nfn destroy(value: *R) -> void { }\nfn t(h: (R, i64, R)) -> void { }\n",
    "generic_destructor": "resource struct R<T> { v: T }\nfn destroy<T>(value: *R<T>) -> void { }\nfn t(h: R<i64>) -> void { }\nfn u(h: R<u8>) -> void { }\n",
    "try_propagates": "enum Result<V, E> { Ok(V), Err(E) }\nfn a() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }\nfn b() -> Result<i64, i64> { let v = try a(); return Result<i64, i64>.Ok(v); }\n",
    "try_short_circuit": "enum Result<V, E> { Ok(V), Err(E) }\nfn a() -> Result<bool, i64> { return Result<bool, i64>.Ok(true); }\nfn b(x: bool) -> Result<bool, i64> { let v = x && try a(); let w = x || try a(); return Result<bool, i64>.Ok(v); }\n",
    "try_in_condition": "enum Result<V, E> { Ok(V), Err(E) }\nfn a() -> Result<bool, i64> { return Result<bool, i64>.Ok(true); }\nfn b() -> Result<bool, i64> { while try a() { break; } if try a() { } return Result<bool, i64>.Ok(true); }\n",
    "try_defer_exit": "enum Result<V, E> { Ok(V), Err(E) }\nfn f() -> void { }\nfn a() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }\nfn b() -> Result<i64, i64> { defer f(); let v = try a(); return Result<i64, i64>.Ok(v); }\n",
    "try_in_index_and_call": "enum Result<V, E> { Ok(V), Err(E) }\nfn a() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }\nfn c(x: i64, y: i64) -> i64 { return x; }\nfn b(s: []i64) -> Result<i64, i64> { let v = s[try a()]; let w = c(try a(), try a()); let t = (try a(), 1); return Result<i64, i64>.Ok(v); }\n",
    "slice_index_temp": "fn b(s: []u8) -> u8 { return s[0]; }\n",
    "non_ascii_names": "struct Größe { länge: i64 }\nfn größe(ä: i64) -> i64 { return ä; }\n",
    "test_blocks": 'test "one" { let a = 1; }\ntest "two" { assert(true); defer g2(); }\nfn g2() -> void { }\n',
}


@pytest.mark.parametrize("name", sorted(STATEMENTS))
@pytest.mark.parametrize("mode", MODES)
def test_statement(cg_exe, tmp_path, name, mode):
    source = f"{PRELUDE}\nfn main() -> int {{\n{STATEMENTS[name]}\nreturn 0;\n}}\n"
    assert agree(cg_exe, tmp_path, source, mode=mode) in ("ok", "error")


@pytest.mark.parametrize("name", sorted(DECLARATIONS))
@pytest.mark.parametrize("mode", MODES)
def test_declaration(cg_exe, tmp_path, name, mode):
    source = f"{DECLARATIONS[name]}\nfn main() -> int {{ return 0; }}\n"
    assert agree(cg_exe, tmp_path, source, mode=mode) in ("ok", "error")


def test_statement_cases_are_valid_programs(cg_exe, tmp_path):
    """A case that the checker rejects tests nothing here."""
    rejected = []
    for name, text in sorted(STATEMENTS.items()):
        source = f"{PRELUDE}\nfn main() -> int {{\n{text}\nreturn 0;\n}}\n"
        if agree(cg_exe, tmp_path, source) != "ok":
            rejected.append(name)
    for name, text in sorted(DECLARATIONS.items()):
        if agree(cg_exe, tmp_path, f"{text}\nfn main() -> int {{ return 0; }}\n") != "ok":
            rejected.append(name)
    assert not rejected, rejected


def test_comparison_reaches_every_statement(cg_exe, tmp_path):
    """The comparison must see the statement text, not just agree on nothing."""
    source = f"{PRELUDE}\nfn main() -> int {{\nlet a = 1;\nreturn a;\n}}\n"
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8"))
    _, text, _ = py_codegen_dump.reference(path, "hosted")
    assert "int64_t m_a = 1;" in text and "= m_a;" in text and "return mort_return_" in text
    assert "$" not in text


# ----- helper selection -----

HELPER_PROGRAM = "fn main() -> int { return 0; }\n"
INT_OPERATIONS = ("wrap", "shl", "shr", "div", "rem", "float_cast")
FIXED_TYPES = ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64")


def compare_with_effects(exe, tmp_path, effects, mode):
    path = tmp_path / "input.mx"
    path.write_bytes(HELPER_PROGRAM.encode("ascii"))
    result = py_check_dump.run_reference(path, mode, exact=True)
    assert result.outcome == "ok"
    program = py_check_dump.LAST[0].program
    text = py_codegen_dump.ForcedCodeGen(
        program, effects, freestanding=mode == "freestanding",
        test_mode=mode == "test").generate()
    code, stderr, produced = run_mort(
        exe, result.manifest, tmp_path, forced_text=py_codegen_dump.forced_text(effects))
    assert code == 0, stderr
    assert_same_c(text, produced, f"effects {effects.flags} {effects.ints} ({mode})")


@pytest.mark.parametrize("flag", py_codegen_dump.FLAG_NAMES)
@pytest.mark.parametrize("mode", MODES)
def test_every_flag_alone(cg_forced_exe, tmp_path, flag, mode):
    compare_with_effects(cg_forced_exe, tmp_path, py_codegen_dump.Effects(flags=[flag]), mode)


@pytest.mark.parametrize("operation", INT_OPERATIONS)
@pytest.mark.parametrize("typ", FIXED_TYPES)
@pytest.mark.parametrize("mode", ("hosted", "freestanding"))
def test_every_integer_helper_alone(cg_forced_exe, tmp_path, operation, typ, mode):
    compare_with_effects(
        cg_forced_exe, tmp_path, py_codegen_dump.Effects(ints=[(operation, typ)]), mode)


@pytest.mark.parametrize("mode", MODES)
def test_all_helpers_together(cg_forced_exe, tmp_path, mode):
    ints = [(operation, typ) for operation in INT_OPERATIONS for typ in FIXED_TYPES]
    ints.append(("shift_count", "signed"))
    compare_with_effects(cg_forced_exe, tmp_path, py_codegen_dump.Effects(
        flags=py_codegen_dump.FLAG_NAMES, ints=ints,
        strings=["first", 'with \\"escape\\"', "\\n", ""]), mode)


def test_signed_shift_requests_its_wrap_helper(cg_forced_exe, tmp_path):
    compare_with_effects(
        cg_forced_exe, tmp_path, py_codegen_dump.Effects(ints=[("shl", "i32")]), "hosted")


# ----- the corpus -----

@pytest.mark.parametrize(
    "path", REPO_FILES, ids=[p.relative_to(ROOT).as_posix() for p in REPO_FILES])
def test_repo_file(cg_exe, tmp_path, path):
    for mode in MODES:
        compare(cg_exe, path, mode, tmp_path)


SPECS = gen.default_corpus() + gen.full_corpus()


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_generated_program(cg_exe, tmp_path, spec):
    program = gen.generate(spec)
    root = gen.write_program(program, tmp_path / "program")
    assert compare(cg_exe, root, "hosted", tmp_path) in ("ok", "error", "generator-error")
    for mode in ("test", "freestanding"):
        compare(cg_exe, root, mode, tmp_path)


def test_generated_corpus_keeps_its_floors():
    assert len(gen.default_corpus()) >= corpus.MIN_GENERATED_PROGRAMS


def test_stubbed_kernel(cg_exe, tmp_path):
    path = corpus.write_stubbed_kernel(tmp_path)
    assert corpus.line_count(path) >= corpus.MIN_KERNEL_LINES
    assert compare(cg_exe, path, "freestanding", tmp_path) == "ok"


def test_selfhost_sources_generate_as_themselves(cg_exe, tmp_path):
    roots = sorted(path for path in HERE.glob("*.mx") if path.name.endswith("_main.mx"))
    assert len(roots) >= 5
    outcomes = {compare(cg_exe, path, "hosted", tmp_path) for path in roots}
    assert outcomes == {"ok"}


@pytest.mark.parametrize("name", sorted(EXPR_CASES)[::3])
def test_expression_programs(cg_exe, tmp_path, name):
    agree(cg_exe, tmp_path, full_body(EXPR_CASES[name]))


@pytest.mark.parametrize("name", sorted(FN_ONLY_CASES))
def test_function_programs(cg_exe, tmp_path, name):
    agree(cg_exe, tmp_path, FN_ONLY_CASES[name] + "\nfn main() -> int { return 0; }\n")


@pytest.mark.parametrize("name", sorted(DECL_CASES)[::2])
def test_t4_declaration_programs(cg_exe, tmp_path, name):
    agree(cg_exe, tmp_path, DECL_CASES[name])


@pytest.mark.parametrize("name", sorted(BODY_CASES)[::2])
def test_t4_body_programs(cg_exe, tmp_path, name):
    agree(cg_exe, tmp_path, BODY_CASES[name])


# ----- determinism -----

ORDERING_PROGRAM = """
struct S { a: (i64, (u8, bool)), b: (bool, i64), c: []u8, d: []const i64 }
fn f(a: []bool, b: (u8, u8), c: []*u8) -> void { }
fn main() -> int { return 0; }
"""


def test_output_does_not_depend_on_hash_seed_or_run(cg_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_bytes(ORDERING_PROGRAM.encode("ascii"))
    outputs = set()
    for seed in ("0", "1", "12345"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, str(HERE / "py_codegen_dump.py"), str(path), "hosted"],
            capture_output=True, env=environment, check=True)
        outputs.add(result.stdout)
    assert len(outputs) == 1
    result, text, _ = py_codegen_dump.reference(path, "hosted")
    first = run_mort(cg_exe, result.manifest, tmp_path)[2]
    second = run_mort(cg_exe, result.manifest, tmp_path)[2]
    assert first == second == text.encode("ascii")


def test_slices_and_tuples_are_sorted(cg_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_bytes(ORDERING_PROGRAM.encode("ascii"))
    _, text, _ = py_codegen_dump.reference(path, "hosted")
    assert text.index("struct mort_slice_bool {") < text.index("struct mort_slice_const_i64 {")
    assert text.index("struct mort_tuple__u8_u8;") < text.index("struct mort_tuple__i64__u8_bool;")


def test_no_paths_in_the_output(cg_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_bytes(f"{PRELUDE}\nfn main() -> int {{ return 0; }}\n".encode("ascii"))
    result, text, _ = py_codegen_dump.reference(path, "hosted")
    produced = run_mort(cg_exe, result.manifest, tmp_path)[2]
    assert str(tmp_path).encode("utf-8") not in produced
    assert produced == text.encode("ascii")


# ----- hostile programs -----

def bounded(exe, tmp_path, source):
    """Run Mort alone under the 500 MB guard."""
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8"))
    c_out = tmp_path / "out.c"
    code, stderr = run_mort_bounded(
        exe, f"MODE hosted\nFILE {path}\n", tmp_path, extra=(c_out,))
    return code, stderr, c_out


def test_defer_replay_budget_matches_python(cg_exe, tmp_path):
    defers = "".join("defer g();\n" for _ in range(3000))
    exits = "".join("if true { return 1; }\n" for _ in range(3000))
    source = f"fn g() -> i64 {{ return 1; }}\nfn main() -> int {{\n{defers}{exits}return 0;\n}}\n"
    assert agree(cg_exe, tmp_path, source) == "generator-error"


def test_defer_replay_stays_within_memory(cg_exe, tmp_path):
    defers = "".join("defer g();\n" for _ in range(20000))
    exits = "".join("if true { return 1; }\n" for _ in range(20000))
    source = f"fn g() -> i64 {{ return 1; }}\nfn main() -> int {{\n{defers}{exits}return 0;\n}}\n"
    code, stderr, _ = bounded(cg_exe, tmp_path, source)
    assert code == 1, stderr
    assert b"replaying deferred statements at each exit" in stderr


def test_reversed_struct_chain_agrees(cg_exe, tmp_path):
    count = 1500
    decls = "".join(f"struct S{i} {{ a: S{i + 1} }}\n" for i in range(count))
    source = f"{decls}struct S{count} {{ x: i64 }}\nfn main() -> int {{ return 0; }}\n"
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_long_reversed_chain_stays_within_memory(cg_exe, tmp_path):
    count = 30000
    decls = "".join(f"struct S{i} {{ a: S{i + 1} }}\n" for i in range(count))
    source = f"{decls}struct S{count} {{ x: i64 }}\nfn main() -> int {{ return 0; }}\n"
    code, stderr, _ = bounded(cg_exe, tmp_path, source)
    assert code == 1, stderr
    assert b"exceeds the limit of 1000000 work units" in stderr


def test_long_operator_chain_does_not_recurse(cg_exe, tmp_path):
    """`_prepare_try_expr` must walk a left spine of any depth."""
    chain = " + 1" * 300000
    source = f"fn main() -> int {{\nlet x: i64 = 1{chain};\nreturn 0;\n}}\n"
    code, stderr, c_out = bounded(cg_exe, tmp_path, source)
    assert code == 0, stderr
    assert c_out.read_bytes().count(b"int64_t m_x = ") == 1


def test_deep_pointer_alias_does_not_overflow_the_stack(cg_exe, tmp_path):
    stars = "*" * 900
    aliases = f"type A0 = {stars}u8;\n" + "".join(
        f"type A{i} = {stars}A{i - 1};\n" for i in range(1, 40))
    source = f"{aliases}fn f(a: A39) -> void {{ }}\nfn main() -> int {{ return 0; }}\n"
    code, stderr, _ = bounded(cg_exe, tmp_path, source)
    assert code in (0, 1), stderr


def test_many_distinct_tuple_types_stay_within_memory(cg_exe, tmp_path):
    params = "".join(
        f"fn f{i}(a: (i64, S{i}), b: []S{i}) -> void {{ }}\nstruct S{i} {{ }}\n"
        for i in range(5000))
    source = f"{params}fn main() -> int {{ return 0; }}\n"
    code, stderr, c_out = bounded(cg_exe, tmp_path, source)
    assert code == 0, stderr
    assert c_out.stat().st_size > 0


# ----- the generated source and the gate -----

def test_helper_text_is_current():
    """cg_helper_text.mx must be what the tool generates from mort/codegen.py."""
    target = HERE / "cg_helper_text.mx"
    before = target.read_bytes().replace(b"\r\n", b"\n")
    subprocess.run(
        [sys.executable, str(HERE / "tools" / "gen_helper_text.py")],
        capture_output=True, check=True)
    assert target.read_bytes().replace(b"\r\n", b"\n") == before


def test_subset_gate_covers_the_generator():
    result = subprocess.run(
        [sys.executable, str(HERE / "tools" / "check_subset.py")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
