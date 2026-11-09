"""Expression code generation (T7): Mort's C against mort/codegen.py, and C that runs.

    python -m pytest selfhost/test_codegen_expr_differential.py -q

Every comparison is against the real CodeGen, whole file, byte for byte. Nothing
is told to the Mort side. Helper use comes from real programs, so a helper the
Mort generator fails to request shows up as a difference, and each of those tests
also asserts that the Python side really used the helper.
"""
import os
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT))

import corpus  # noqa: E402,F401
import gen_subset_programs as gen  # noqa: E402
import py_check_dump  # noqa: E402
import py_codegen_dump  # noqa: E402
from test_checker_decl_differential import MODES, first_difference  # noqa: E402
from test_codegen_differential import (  # noqa: E402,F401
    PRELUDE, agree, assert_same_c, bounded, build_driver, cg_exe, compare, run_mort,
)

FIXED = ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64")
C_INTS = ("c_char", "c_uchar", "c_short", "c_ushort", "c_int", "c_uint", "c_long",
          "c_ulong", "c_size")
ARITH = ("+", "-", "*", "/", "%", "&", "|", "^")
COMPARE = ("==", "!=", "<", "<=", ">", ">=")
SHIFTS = ("<<", ">>")


def body(text):
    return f"{PRELUDE}\nfn main() -> int {{\n{text}\nreturn 0;\n}}\n"


EXPRESSIONS = {
    # literals
    "int_edges": "let a = 1; let b: u64 = 18446744073709551615; let c: i64 = 9223372036854775807; "
                 "let d: u8 = 255; let e: u64 = 9223372036854775808; let f: i32 = 2147483647;",
    "int_bases": "let a = 0xFF; let b = 0b101; let c = 0o17; let d = 1_000;",
    "int_folds": "let a: i64 = 1 + 2 * 3; let b: i64 = 1 << 40; let c: u64 = (1 << 63) - 1 + (1 << 63); "
                 "let d: i64 = -9223372036854775807 - 1; let e: i64 = -2147483649; let f: i32 = -2147483648; "
                 "let g: i64 = ~0; let h: u8 = ~0 & 255;",
    "int_fold_edges": "let a: i64 = 2147483646 + 1; let b: i64 = 2147483647 + 1; let c: i64 = -2147483646 - 1; "
                      "let d: i64 = -2147483647 - 1; let e: i64 = -2147483647 - 2; let f: i32 = 2147483646 + 1; "
                      "let g2: u64 = 9223372036854775806 + 1; let h: u64 = 9223372036854775807 + 1; "
                      "let i: i64 = 9223372036854775806 + 1; let j: i64 = -9223372036854775806 - 1; "
                      "let k: u64 = 18446744073709551614 + 1; let l: i64 = -9223372036854775807 - 1;",
    "char_lits": "let a = 'a'; let b: u8 = '\\n'; let c = '\\x7f'; let d: i64 = 'z';",
    "bool_null": "let p: *u8 = null; let q = true; let r = false; let s = !q;",
    "strings": 'let s = "hi"; let t = "a\\nb\\"c"; println(s); println(t); println("");',
    "string_non_ascii": 'println("größe ✓");',
    "string_order_call_args": 'println("first"); println("second"); eprintln("third");',
    # variables and functions as values
    "fn_pointer": "let f: fn() -> i64 = g; let v = f(); const h: fn() -> i64 = g; let w = h();",
    # casts
    "casts_between_fixed": "let a: i64 = 300; " + " ".join(f"let c{t} = a as {t};" for t in FIXED),
    "casts_to_c_ints": "let a: i64 = 300; " + " ".join(f"let c{t} = a as {t};" for t in C_INTS),
    "casts_from_float": "let f = 1.5; " + " ".join(f"let c{t} = f as {t};" for t in FIXED),
    "casts_from_f32": "let f: f32 = 1.5; let a = f as i32; let b = f as f64; let c = 1 as f32;",
    "casts_to_float": "let a: i64 = 1; let b = a as f64; let c = a as f32;",
    "casts_pointer": "let a: i64 = 1; let p = &a; let u = p as u64; let i = p as i32; let v = u as *i64; "
                     "let w = p as *void; let x = w as *u8; let y = p as c_size;",
    "casts_enum_bool": "let a = E.B as i64; let c = E.A as u32;",
    "cast_chain": "let a: i64 = 300; let b = a as u8 as i32 as u64 as i16 as i64;",
    "cast_literal": "let a = 255 as u8; let b = 70000 as u16; let c = 1 as *void; let d = -1 as u64;",
    # unary
    "unary_float": "let a = 1.5; let b = -a; let c: f32 = 2.5; let d = -c; let e = -1.5;",
    "unary_ptr": "let a: i64 = 1; let p = &a; let b = *p; *p = 2; let q = &*p; let r = &p; let s = **r;",
    "unary_not": "let a = true; let b = !a; let c = !!a; let d = !(a && b);",
    "unary_c_int": "let a: c_int = 1; let b = -a; let c = ~a; let d: c_long = 1; let e = -d;",
    "unary_literal_folds": "let a: i8 = -128; let b: u8 = ~1 & 3; let c: i64 = -(1 << 40); let d: i64 = -(-5);",
    # binary
    "float_arith": "let a = 1.5; let b = 2.5; let c = a + b; let d = a - b; let e = a * b; let f = a / b; "
                   "let g = a < b; let h = a == b; let i: f32 = 1.5; let j = i + 2.5; let k = 2.5 * i;",
    "logical": "let a = true; let b = false; let c = a && b; let d = a || b; let e = (a && b) || !a;",
    "pointer_compare": "let a: i64 = 1; let p = &a; let b = p == null; let c = null != p; let q = p; let d = p == q;",
    "enum_compare": "let a = E.A; let b = a == E.B; let c = E.A != a;",
    "bool_compare": "let a = true; let b = a == false; let c = a != true;",
    "mixed_literal_operand": "let a: u8 = 3; let b = a + 1; let c = 1 + a; let d = a << 1; let e = 1 << a; let f = a * 200;",
    "shift_literal_count": "let a: i32 = 1; let b = a << 2; let c = a >> 31; let d = a << 40; let e: u64 = 1; let f = e << 63;",
    "nested_arith": "let a: i64 = 1; let b: i32 = 2; let c = (a + 1) * (a - 1) / (a | 3); let d = b + b * b - (b / b) % b;",
    "comparison_of_arith": "let a: u8 = 1; if a + 1 == 2 { } while a * 2 < 4 { a += 1; }",
    "c_int_arith": "let a: c_int = 1; let b: c_int = 2; let c = a + b; let d = a << 2; let e = a / b; let f = a % b; let g = a < b;",
    "size_arith": "let a: c_size = 1; let b = a + 1; let c: c_ulong = 2; let d = c * c;",
    # conditions keep or lose their outer parentheses
    "conditions": "let a: i64 = 1; let b = true; if a == 1 { } if b { } if !b { } if g() == 1 { } "
                  "if (a == 1) && (a == 2) { } while b && a < 2 { break; } if a < 1 || a > 2 { }",
    "condition_call_group": "if g() == 1 { } if g() < g() { }",
    "condition_cast": "let a: u8 = 1; if (a as i64) == 1 { }",
    # builtins
    "print_forms": "print(1); print(1.5); let a: f32 = 2.5; print(a); let b: u8 = 3; print(b); print(b as i64 + 1);",
    "println_forms": 'println("x"); eprintln("y"); let p: *u8 = "z"; println(p);',
    "assert_call": "assert(true); assert(1 == 1);",
    "alloc_free": "let p = alloc(8); free(p); let q = alloc(sizeof<i64>()) as *i64; free(q as *void);",
    "sizeof_forms": "let a = sizeof<i64>(); let b = sizeof<*u8>(); let c = sizeof<A4>(); "
                    "let e = sizeof<Pt>(); let g2 = sizeof<E>(); let h = sizeof<[]u8>(); let i = sizeof<(i64,u8)>();",
    "len_forms": 'let a: [u8; 3] = [1, 2, 3]; let n = len(a); let m = len("abc"); let u: *u8 = null; let s = slice<u8>(u, 1); let k = len(s);',
    "slice_builtin": "let a: [u8; 3] = [1, 2, 3]; let u: *u8 = &a[0]; let s = slice<u8>(u, 3); let n = s.len; let d = s.data;",
    "slice_strings_generated_twice": 'let s = slice<u8>("ab", len("cd")); let n = len(slice<u8>("ef", 1));',
    "time_builtins": "let t = unix_time(); let c = cpu_millis();",
    "file_builtins": 'let f = file_open("a.txt", "r"); let b: *u8 = alloc(4) as *u8; let n = file_read(f, b, 4); let m = file_write(f, b, n); let o = file_flush(f); let p = file_close(f);',
    "arg_builtins": "let n = arg_count(); if n > 1 { println(arg_get(1)); }",
    "volatile_forms": "let a: i32 = 1; let p = &a; let v = volatile_read<i32>(p); volatile_write<i32>(p, v + 1);",
    "port_builtins": "let v = inb(96); outb(96, v); let w = inw(96); outw(96, w); let d = inl(96); outl(96, d);",
    "concurrency_builtins": "let m = mutex_create(); let l = mutex_lock(m); let u = mutex_unlock(m); mutex_destroy(m);"
                            " let a = atomic_i64_create(1); let v = atomic_i64_fetch_add(a, 1); atomic_i64_destroy(a);"
                            " thread_sleep_millis(1);",
    # aggregates
    "struct_literal": "let a = Pt { x: 1, y: 2 }; let b = Pt { y: 2, x: 1 }; let c = a.x + b.x; let d = a.y;",
    "struct_nested_access": "let h = Holder { r: make_r(), n: 1 }; let a = h.n; destroy(&h.r);",
    "struct_via_pointer": "let a = Pt { x: 1, y: 2 }; let p = &a; let b = (*p).x; (*p).x = 2; let c = &(*p).y;",
    "struct_empty_literal": "let a = Em { }; let b = sizeof<Em>();",
    "tuple_values": "let t = (1, true); let a = t.0; let b = t.1; let n = (1, (2, 3)); let c = n.1.0;",
    "enum_units": "let a = E.A; let b = Owns.Empty; match move b { Owns.Has(r) => { destroy(&r); } Owns.Empty => { } }",
    "enum_payloads": "let o = Owns.Has(make_r()); match move o { Owns.Has(r) => { destroy(&r); } Owns.Empty => { } }",
    "enum_tuple_payload": "let m = Two.P(1, 2); match m { Two.P(a, b) => { let c = a + b; } Two.Q => { } }",
    "enum_wildcard_binding": "let m = Two.P(1, 2); match m { Two.P(_, b) => { let c = b; } Two.Q => { } } "
                             "let n = Two.P(3, 4); match n { Two.P(a, _) => { let c = a; } Two.Q => { } }",
    "generic_values": "let b = Box<i64> { v: 1 }; let a = b.v; let o = Opt<i64>.Some(1); let n = Opt<i64>.None; "
                      "let c = identity(1); let d = identity<u8>(2); let e = identity(true);",
    "generic_payload_match": "let o = Opt<i64>.Some(1); match o { Opt<i64>.Some(v) => { let a = v; } Opt<i64>.None => { } }",
    # indexing
    "index_array": "let a: [u8; 3] = [1, 2, 3]; let b = a[1]; let i: u64 = 0; let c = a[i]; a[i] = 2; a[0] = 1;",
    "index_array_const_folded": "let a: [u8; 2] = [1, 2]; let b = a[1]; let i: u64 = 1; let c = a[i]; let d = a[i + 1 - 1];",
    "index_pointer": 'let p: *u8 = "abc"; let a = p[1]; let i: u64 = 2; let b = p[i]; p[0] = 65;',
    "index_slice": "let a: [u8; 3] = [1, 2, 3]; let s = slice<u8>(&a[0], 3); let b = s[0]; let i: u64 = 1; let c = s[i]; s[i] = 9;",
    "index_slice_while_unprepared": "let a: [u8; 3] = [1, 2, 3]; let s = slice<u8>(&a[0], 3); let i: u64 = 0; while s[i] != 0 && i < 2 { i += 1; }",
    "index_slice_in_defer": "let a: [u8; 3] = [1, 2, 3]; let s = slice<u8>(&a[0], 3); defer print(s[0]); defer print(s[1] as i64 + 1);",
    "index_slice_field_chain": "let a: [u8; 3] = [1, 2, 3]; let s = slice<u8>(&a[0], 3); let t = (s, 1); let b = t.0[1]; let c = t.0.len;",
    "index_evaluates_index_first": 'let n = "abc"[len("de")];',
    "index_string_literal_nested": 'let n = "abc"["de"[0] as u64 - 100];',
    "index_lvalue_expression": "let a: [i64; 4] = [1; 4]; let i: u64 = 0; a[i + 1] = a[i] + 1; a[i] += 1;",
    "index_in_freestanding_shape": "let a: [u8; 3] = [1, 2, 3]; let i: u64 = 0; let b = a[i] + a[1];",
    # arrays
    "array_literals": "let a: [u8; 3] = [1, 2, 3]; let c: [bool; 1] = [true]; let d: [*u8; 2] = [null, \"x\"];",
    "array_repeats": "let a: [u8; 3] = [7; 3]; let c: [i64; 256] = [0; 256]; let d: [i64; 300] = [0; 300];",
    "array_zero_forms": "let a: [f64; 300] = [0.0; 300]; let b: [f32; 300] = [0.0; 300]; let c: [bool; 300] = [false; 300]; let d: [*u8; 300] = [null; 300];",
    "array_nonzero_large": "let a: [i64; 300] = [5; 300]; let b: [f64; 257] = [1.5; 257];",
    "array_of_struct": "let a: [Pt; 2] = [Pt { x: 1, y: 2 }, Pt { x: 3, y: 4 }]; let b = a[0].x; let c: [Pt; 2] = [Pt { x: 1, y: 1 }; 2];",
    "array_of_fn": "let a: [fn() -> i64; 2] = [g, g];",
    "array_string_repeat_registers_once": 'let a: [*u8; 3] = ["x"; 3]; println("y");',
    # moves and destruction
    "move_resource": "let r = make_r(); let s = move r; destroy(&s);",
    "move_plain": "let a = 1; let b = move a;",
    "move_into_struct": "let r = make_r(); let h = Holder { r: move r, n: 1 }; destroy(&h.r);",
    "destroy_call": "let r = make_r(); destroy(&r);",
    "destroy_deferred": "let r = make_r(); defer destroy(&r);",
    "destroy_in_branch": "let r = make_r(); if g() == 1 { destroy(&r); } else { destroy(&r); }",
}

PRELUDE_EXTRA = """
struct Em { }
enum Two { P(i64, i64), Q }
type A4 = [u8; 4];
struct Box<T> { v: T }
enum Opt<T> { Some(T), None }
fn identity<T>(x: T) -> T { return x; }
"""

for typ in FIXED:
    assign = f"let a: {typ} = 1; let b: {typ} = 2; let n: i32 = 1; let m: u32 = 1;"
    EXPRESSIONS[f"binary_arith_{typ}"] = assign + " " + " ".join(
        f"let r{i} = a {op} b;" for i, op in enumerate(ARITH))
    EXPRESSIONS[f"binary_compare_{typ}"] = assign + " " + " ".join(
        f"let c{i} = a {op} b;" for i, op in enumerate(COMPARE))
    EXPRESSIONS[f"binary_shift_{typ}"] = assign + " " + " ".join(
        f"let s{i} = a {op} n; let t{i} = a {op} m; let v{i} = a {op} 1;"
        for i, op in enumerate(SHIFTS))
    EXPRESSIONS[f"unary_{typ}"] = f"let a: {typ} = 1; let b = -a; let c = ~a;"

FLOAT_LITERALS = (
    "0.0", "1.0", "1.5", "0.1", "0.2", "0.3", "100.0", "1e22", "1e16", "1e17",
    "1e300", "1.7976931348623157e308", "5e-324", "2.2250738585072014e-308",
    "0.0001", "0.00001", "2.5e-5", "123456789012345678.0", "9007199254740993.0",
    "1.0000000000000002", "3.141592653589793", "12345678901234567890.0",
    "9.999999999999999e22", "4.35", "0.000123456789",
)


def expression_program(name):
    return f"{PRELUDE}{PRELUDE_EXTRA}\nfn main() -> int {{\n{EXPRESSIONS[name]}\nreturn 0;\n}}\n"


@pytest.mark.parametrize("name", sorted(EXPRESSIONS))
@pytest.mark.parametrize("mode", MODES)
def test_expression(cg_exe, tmp_path, name, mode):
    assert agree(cg_exe, tmp_path, expression_program(name), mode=mode) in ("ok", "error")


def test_expression_cases_are_valid_programs(cg_exe, tmp_path):
    """A case the checker rejects tests nothing here."""
    rejected = [name for name in sorted(EXPRESSIONS)
                if agree(cg_exe, tmp_path, expression_program(name)) != "ok"]
    assert not rejected, rejected


@pytest.mark.parametrize("literal", FLOAT_LITERALS)
def test_float_literal(cg_exe, tmp_path, literal):
    source = body(f"let a: f64 = {literal};\nlet b = {literal} + 1.0;")
    assert agree(cg_exe, tmp_path, source) == "ok"


# ----- the compiler computes every helper from real programs -----

FLAG_BODIES = {
    "used_inb": "let v = inb(96);",
    "used_outb": "outb(96, 1);",
    "used_inw": "let v = inw(96);",
    "used_outw": "outw(96, 1);",
    "used_inl": "let v = inl(96);",
    "used_outl": "outl(96, 1);",
    "used_println": 'println("x");',
    "used_eprintln": 'eprintln("x");',
    "used_print": "print(1);",
    "used_print_float": "print(1.5);",
    "used_assert": "assert(true);",
    "used_alloc": "let p = alloc(8);",
    "used_free": "let p = alloc(8); free(p);",
    "used_len": 'let n = len("ab");',
    "used_bounds": "let a: [u8; 2] = [1, 2]; let i: u64 = 0; let b = a[i];",
    "used_time": "let t = unix_time();",
    "used_file": 'let f = file_open("a", "r");',
    "used_args": "let n = arg_count();",
    "used_threads": "thread_sleep_millis(1);",
    "used_mutexes": "let m = mutex_create();",
    "used_atomics": "let a = atomic_i64_create(1);",
    "used_network": "let b = net_last_error_would_block();",
    "used_secure_random": "let b: *u8 = alloc(4) as *u8; let k = secure_random_fill(b, 4);",
    "used_tls": 'let h = tls_client_connect("h", 1);',
}


def test_flag_bodies_cover_every_flag():
    assert set(FLAG_BODIES) == set(py_codegen_dump.FLAG_NAMES)


@pytest.mark.parametrize("flag", sorted(FLAG_BODIES))
@pytest.mark.parametrize("mode", ("hosted", "test"))
def test_real_program_sets_flag(cg_exe, tmp_path, flag, mode):
    source = body(FLAG_BODIES[flag])
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8"))
    assert compare(cg_exe, path, mode, tmp_path) == "ok"
    generator = py_codegen_dump.effects_of(path, mode)
    assert getattr(generator, flag), flag


def int_helper_program(operation, typ):
    declarations = f"let a: {typ} = 1; let b: {typ} = 2; let f = 1.5; let n: u32 = 1; let s: i32 = 1;"
    use = {
        "wrap": f"let r = a + b; let u: {typ} = a as {typ};",
        "shl": "let r = a << n;",
        "shr": "let r = a >> n;",
        "div": "let r = a / b;",
        "rem": "let r = a % b;",
        "float_cast": f"let r = f as {typ};",
    }[operation]
    return body(f"{declarations}\n{use}")


@pytest.mark.parametrize("operation", ("wrap", "shl", "shr", "div", "rem", "float_cast"))
@pytest.mark.parametrize("typ", FIXED)
def test_real_program_requests_integer_helper(cg_exe, tmp_path, operation, typ):
    path = tmp_path / "input.mx"
    path.write_bytes(int_helper_program(operation, typ).encode("utf-8"))
    assert compare(cg_exe, path, "hosted", tmp_path) == "ok"
    generator = py_codegen_dump.effects_of(path, "hosted")
    if operation != "wrap" or typ.startswith("i"):
        assert (operation, typ) in generator.used_int_helpers


def test_real_program_requests_signed_shift_count(cg_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_bytes(body("let a: i64 = 1; let s: i32 = 1; let r = a << s;").encode("utf-8"))
    assert compare(cg_exe, path, "hosted", tmp_path) == "ok"
    assert ("shift_count", "signed") in py_codegen_dump.effects_of(path, "hosted").used_int_helpers


def test_string_table_comes_from_the_program(cg_exe, tmp_path):
    """Numbering follows Python's evaluation order, including index before object."""
    path = tmp_path / "input.mx"
    path.write_bytes(body('let n = "abc"[len("de")];\nprintln("z");').encode("utf-8"))
    assert compare(cg_exe, path, "hosted", tmp_path) == "ok"
    generator = py_codegen_dump.effects_of(path, "hosted")
    assert generator.strings == ["de", "abc", "z"]


# ----- _gen_cond's text rule -----

CONDITION_TEXTS = (
    "x", "(a)", "((a))", "(a)(b)", "(a) b", "(a) && (b)", "(a)(b)(c)", "a(b)", "(a(b))",
    "()", "(", ")", "(()", "())", "(a))(", "(a)b)", "((a)(b))", "(())", "(a)()",
)


def assert_condition_texts_match(exe, tmp_path):
    from mort.codegen import CodeGen

    generator = CodeGen.__new__(CodeGen)
    expected = []
    for text in CONDITION_TEXTS:
        generator._gen_expr = lambda _node, value=text: value
        expected.append(generator._gen_cond(None))
    source = tmp_path / "conds.txt"
    source.write_bytes(("\n".join(CONDITION_TEXTS) + "\n").encode("ascii"))
    out = tmp_path / "stripped.txt"
    subprocess.run([str(exe), str(source), str(out)], check=True, timeout=60)
    actual = out.read_bytes().decode("ascii").split("\n")[:-1]
    assert actual == expected


def test_condition_text_rule_matches_python(tmp_path_factory, tmp_path):
    assert_condition_texts_match(build_driver(tmp_path_factory, "cond_strip_main"), tmp_path)


# ----- %.17g -----

@pytest.fixture(scope="session")
def float_exe(tmp_path_factory):
    return build_driver(tmp_path_factory, "float_g17_main")


def assert_float_texts_match(exe, tmp_path, patterns):
    source = tmp_path / "bits.bin"
    source.write_bytes(b"".join(struct.pack("<Q", bits) for bits in patterns))
    out = tmp_path / "g17.txt"
    subprocess.run([str(exe), str(source), str(out)], check=True, timeout=170)
    actual = out.read_text(encoding="ascii").splitlines()
    expected = [format(struct.unpack("<d", struct.pack("<Q", bits))[0], ".17g")
                for bits in patterns]
    assert len(actual) == len(expected)
    wrong = [(hex(bits), want, got) for bits, want, got
             in zip(patterns, expected, actual) if want != got]
    assert not wrong, f"{len(wrong)} of {len(patterns)} differ, first: {wrong[:5]}"


def test_format_g17_on_constructed_values(float_exe, tmp_path):
    patterns = {0, 1, 0x000FFFFFFFFFFFFF, 0x0010000000000000, 0x7FEFFFFFFFFFFFFF}
    for exponent in range(0, 2047):
        patterns.add(exponent << 52)
        patterns.add((exponent << 52) | 1)
        patterns.add((exponent << 52) | 0xFFFFFFFFFFFFF)
    for power in range(-320, 309):
        for digits in (1, 5, 9, 123456789):
            value = float(f"{digits}e{power}")
            if value != float("inf"):
                patterns.add(struct.unpack("<Q", struct.pack("<d", value))[0])
    assert_float_texts_match(float_exe, tmp_path, sorted(patterns))


def test_format_g17_on_ties_and_short_integers(float_exe, tmp_path):
    # Integers with an exact tie at the 18th significant digit, both directions.
    values = [float(n * 10 + 5) * 10.0 ** k for n in range(10 ** 15, 10 ** 15 + 400)
              for k in (0, 1, 5)]
    # A true tie at digit 18: 15 integer digits, then .125, .375, .625 or .875.
    values += [a + r / 8 for a in range(450359962737000, 450359962737300) for r in (1, 3, 5, 7)]
    values += [float(2 ** k) for k in range(60, 300)]
    values += [float(n) for n in range(1, 2000)]
    assert_float_texts_match(
        float_exe, tmp_path,
        [struct.unpack("<Q", struct.pack("<d", value))[0] for value in values])


def test_format_g17_on_random_values(float_exe, tmp_path):
    rng = random.Random(0x17)
    patterns = [rng.getrandbits(63) for _ in range(150000)]
    patterns = [bits for bits in patterns if (bits >> 52) != 0x7FF]
    assert_float_texts_match(float_exe, tmp_path, patterns)


# ----- the C compiles and runs, and matches the independent model -----

SPECS = gen.default_corpus() + gen.full_corpus()


def c_compiler_command(c_source, c_file, exe):
    command = [sys.executable, "-m", "ziglang", "cc", "-O0", "-std=c11", str(c_file), "-o", str(exe)]
    if os.name == "nt":
        if "MORT_REQUIRES_WINSOCK" in c_source:
            command.append("-lws2_32")
        if "MORT_REQUIRES_BCRYPT" in c_source:
            command.append("-lbcrypt")
    elif "MORT_REQUIRES_PTHREAD" in c_source:
        command.append("-pthread")
    return command


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_generated_c_runs_and_matches_the_model(cg_exe, tmp_path, spec):
    program = gen.generate(spec)
    root = gen.write_program(program, tmp_path / "program")
    result = py_check_dump.run_reference(root, "hosted", exact=True)
    assert result.outcome == "ok", result.error
    python_c, generator_error = py_codegen_dump.generate(
        py_check_dump.LAST[0].program, False, False)[:2]
    assert generator_error is None, generator_error
    code, stderr, produced = run_mort(cg_exe, result.manifest, tmp_path)
    assert code == 0, stderr
    assert_same_c(python_c, produced, spec.name)
    c_file = tmp_path / "generated.c"
    c_file.write_bytes(produced)
    exe = tmp_path / ("generated.exe" if os.name == "nt" else "generated")
    command = c_compiler_command(python_c, c_file, exe)
    if "MORT_REQUIRES_TLS" in python_c:
        pytest.skip("the TLS runtime is linked by mortc.py")
    built = subprocess.run(command, capture_output=True, text=True, timeout=170)
    assert built.returncode == 0, built.stderr[:4000]
    run = subprocess.run([str(exe)] + program.args, capture_output=True, cwd=tmp_path, timeout=60)
    assert run.stdout.replace(b"\r\n", b"\n") == program.expected_stdout, spec.name
    if program.expect_trap:
        assert run.returncode != 0, spec.name
        assert run.stderr != b"", spec.name
    else:
        assert run.returncode == program.expected_exit, spec.name
        assert run.stderr.replace(b"\r\n", b"\n") == program.expected_stderr, spec.name


# ----- left-deep expressions, against Python, then Mort alone at scale -----

DEEP = 480  # inside the 1000-frame budget of mort/depth.py


def deep_spines():
    fields = "".join(f"struct F{i} {{ a: F{i + 1} }}\n" for i in range(DEEP))
    return {
        "add": f"let x: i64 = 1;\nlet y = x{' + x' * DEEP};",
        "mul_u8": f"let x: u8 = 1;\nlet y = x{' * x' * DEEP};",
        "compare_chain": f"let x: bool = true;\nlet y = x{' && x' * DEEP};",
        "c_int": f"let x: c_int = 1;\nlet y = x{' + x' * DEEP};",
        "cast": f"let x: i64 = 1;\nlet y = x{' as u8 as i64' * (DEEP // 2)};",
    }, fields


def test_deep_spines_agree_with_python(cg_exe, tmp_path):
    cases, _ = deep_spines()
    for name, text in cases.items():
        assert agree(cg_exe, tmp_path, body(text)) == "ok", name


def test_deep_field_chain_agrees_with_python(cg_exe, tmp_path):
    count = 400
    decls = "".join(f"struct F{i} {{ a: F{i + 1} }}\n" for i in range(count))
    source = (
        f"{decls}struct F{count} {{ x: i64 }}\n"
        f"fn f(v: F0) -> i64 {{ return v{'.a' * count}.x; }}\n"
        "fn main() -> int { return 0; }\n")
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_deep_index_chain_agrees_with_python(cg_exe, tmp_path):
    count = 200
    array_type = "u8"
    for _ in range(count):
        array_type = f"*{array_type}"
    source = f"fn f(p: {array_type}, i: u64) -> u8 {{ return p{'[i]' * count}; }}\nfn main() -> int {{ return 0; }}\n"
    assert agree(cg_exe, tmp_path, source) == "ok"


NEAR_BUDGET = 490  # just inside the 1000-frame budget of mort/depth.py
PAST_BUDGET = 300000
NESTING = b"source nesting exceeds the compiler safety limit"


def test_long_non_constant_chain_within_memory(cg_exe, tmp_path):
    source = body(f"let x: i64 = 1;\nlet y = x{' + x' * NEAR_BUDGET};")
    code, stderr, c_out = bounded(cg_exe, tmp_path, source)
    assert code == 0, stderr
    text = c_out.read_bytes()
    assert text.count(b"mort_wrap_i64(") == NEAR_BUDGET + 1  # the helper's definition is one
    source = body(f"let x: i64 = 1;\nlet y = x{' + x' * PAST_BUDGET};")
    code, stderr, _ = bounded(cg_exe, tmp_path, source)
    assert code == 1 and NESTING in stderr, stderr


def test_long_cast_chain_within_memory(cg_exe, tmp_path):
    source = body(f"let x: i64 = 1;\nlet y = x{' as u64 as i64' * (NEAR_BUDGET // 2)};")
    code, stderr, c_out = bounded(cg_exe, tmp_path, source)
    assert code == 0, stderr
    assert c_out.stat().st_size > NEAR_BUDGET
    source = body(f"let x: i64 = 1;\nlet y = x{' as u64 as i64' * (PAST_BUDGET // 2)};")
    code, stderr, _ = bounded(cg_exe, tmp_path, source)
    assert code == 1 and NESTING in stderr, stderr


def test_long_index_chain_within_memory(cg_exe, tmp_path):
    """A pointer type near the budget, indexed all the way; past it, a located error."""
    def chain(depth):
        stars = "*" * depth
        return (
            f"type A0 = {stars}u8;\nfn f(p: A0, i: u64) -> u8 {{ return p{'[i]' * depth}; }}\n"
            "fn main() -> int { return 0; }\n")

    code, stderr, c_out = bounded(cg_exe, tmp_path, chain(NEAR_BUDGET))
    assert code == 0, stderr
    assert c_out.read_bytes().count(b"[m_i]") == NEAR_BUDGET
    code, stderr, _ = bounded(cg_exe, tmp_path, chain(900))
    assert code == 1 and NESTING in stderr, stderr


def test_nesting_at_the_parser_limit_does_not_overflow(cg_exe, tmp_path):
    """The recursive shapes: every one at the parser's 1000 units."""
    unary = f"let a: i64 = 1;\nlet b = {'-' * 900}a;"
    nested_calls = "let a: i64 = 1;\nlet b = " + "id2(" * 450 + "a" + ")" * 450 + ";"
    parens = "let a: i64 = 1;\nlet b = " + "(" * 900 + "a" + ")" * 900 + ";"
    for text in (unary, nested_calls, parens):
        source = f"fn id2(v: i64) -> i64 {{ return v; }}\n{body(text)}"
        code, stderr, _ = bounded(cg_exe, tmp_path, source)
        assert code in (0, 1) and b"internal" not in stderr, stderr[:300]


# ----- what Python has no limit for, and what it does -----

def test_array_repeat_limit_matches_python(cg_exe, tmp_path):
    source = body("let a: [u64; 100000000] = [12345678901; 100000000];")
    assert agree(cg_exe, tmp_path, source) == "generator-error"


def test_cumulative_array_repeat_limit_matches_python(cg_exe, tmp_path):
    lines = "\n".join(
        f"let a{i}: [u64; 20000000] = [12345678901; 20000000];" for i in range(4))
    assert agree(cg_exe, tmp_path, body(lines)) == "generator-error"


def test_array_repeat_above_the_inline_threshold_agrees(cg_exe, tmp_path):
    source = body("let a: [u8; 500000] = [7; 500000];\nlet b: [u64; 300000] = [5; 300000];")
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_array_repeat_near_the_limit_within_memory(cg_exe, tmp_path):
    """About 240 MB of C, under the 500 MB guard: it is never held in memory."""
    count = 80000000
    source = body(f"let a: [u8; {count}] = [7; {count}];")
    code, stderr, c_out = bounded(cg_exe, tmp_path, source)
    assert code == 0, stderr
    size = c_out.stat().st_size
    assert size > 3 * count - 2
    with c_out.open("rb") as handle:
        handle.seek(-4096, os.SEEK_END)
        tail = handle.read()
    assert b"7, 7, 7};" in tail


def test_array_repeat_text_across_a_chain_is_exact(cg_exe, tmp_path):
    """Nested repeats above the threshold expand to Python's bytes."""
    source = "struct W { a: [u8; 400000] }\n" + body(
        "let b: [W; 3] = [W { a: [1; 400000] }; 3];")
    assert agree(cg_exe, tmp_path, source) == "ok"


def slice_chain(depth):
    slice_type = "[]" * depth + "u8"
    return (
        f"fn g2(v: u8) -> void {{ }}\n"
        f"fn main() -> int {{\nlet p: *{slice_type[2:]} = null;\n"
        f"let s = slice<{slice_type[2:]}>(p, 1);\ndefer g2(s{'[0]' * depth});\nreturn 0;\n}}\n")


def test_doubled_slice_text_agrees_while_small(cg_exe, tmp_path):
    assert agree(cg_exe, tmp_path, slice_chain(8)) == "ok"


def test_doubled_slice_text_is_bounded(cg_exe, tmp_path):
    """Python's text doubles per level and would exhaust memory; Mort reports it."""
    code, stderr, _ = bounded(cg_exe, tmp_path, slice_chain(64))
    assert code == 1, stderr
    assert b"duplicated slice operands" in stderr


def slice_while_chain(depth):
    """An unprepared `while` condition: no temporaries, and no defer replay budget."""
    slice_type = "[]" * depth + "u8"
    return (
        f"fn main() -> int {{\nlet p: *{slice_type[2:]} = null;\n"
        f"let s = slice<{slice_type[2:]}>(p, 1);\n"
        f"while s{'[0]' * depth} != 0 {{ break; }}\nreturn 0;\n}}\n")


def test_doubled_slice_text_agrees_at_python_sizes(cg_exe, tmp_path):
    """Python writes about 13 MB here; Mort must not reject it."""
    assert agree(cg_exe, tmp_path, slice_chain(18)) == "ok"
    assert agree(cg_exe, tmp_path, slice_while_chain(18)) == "ok"


def test_doubled_slice_text_is_exact_beyond_the_inline_threshold(cg_exe, tmp_path):
    """21 levels is about 109 MB of C: Python compiles it, so Mort must, in bounded memory.

    Depth 18 is compared byte for byte in test_..._at_python_sizes. The `while` line
    obeys L(d) = 2 L(d - 1) + c, so 18 and 19 predict 21 exactly; the head does not."""
    lines = []
    for depth in (18, 19, 21):
        code, stderr, c_out = bounded(cg_exe, tmp_path, slice_while_chain(depth))
        assert code == 0, stderr
        data = c_out.read_bytes()
        assert b"\xff" not in data
        start = data.index(b"while (")
        lines.append(data.index(b"\n", start) - start)
    constant = lines[1] - 2 * lines[0]
    assert lines[2] == 8 * (lines[0] + constant) - constant


def nested_len_slice(depth):
    expression = "1"
    for _ in range(depth):
        expression = f"len(slice<u8>(u, {expression}))"
    return body(f"let u: *u8 = null;\nlet n = {expression};")


def test_nested_len_of_slice_agrees(cg_exe, tmp_path):
    """Python's work is 4^11 nodes for 1,090 bytes of text: nothing should reject it."""
    assert agree(cg_exe, tmp_path, nested_len_slice(11)) == "ok"


def test_nested_len_of_slice_is_linear(cg_exe, tmp_path):
    """Without string literals the second generation repeats nothing, so reuse the first."""
    code, stderr, c_out = bounded(cg_exe, tmp_path, nested_len_slice(30))
    assert code == 0, stderr
    assert c_out.read_bytes().count(b"mort_bounds") == 0


def regenerated_chain(depth):
    expression = '"s"'
    for _ in range(depth):
        expression = f"slice<u8>({expression}, 1).data"
    return body(f"let p = {expression};")


def test_regenerated_slice_builtin_agrees_while_small(cg_exe, tmp_path):
    assert agree(cg_exe, tmp_path, regenerated_chain(6)) == "ok"


def test_regeneration_is_bounded(cg_exe, tmp_path):
    """Each `slice` or `len` of a slice generates its arguments twice."""
    code, stderr, _ = bounded(cg_exe, tmp_path, regenerated_chain(60))
    assert code == 1, stderr
    assert b"string table" in stderr


# ----- type text is charged and limited in code points, as Python measures it -----

CJK = "漢"


def test_long_cjk_struct_name_within_python_limits_agrees(cg_exe, tmp_path):
    """30,000 characters is under MAX_TYPE_LENGTH; its 90,000 UTF-8 bytes are not."""
    name = CJK * 30000
    source = (
        f"struct {name} {{ x: i64 }}\nfn f(a: *{name}) -> *{name} {{ return a; }}\n"
        f"fn main() -> int {{ let a: *{name} = null; let b = f(a); return 0; }}\n")
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_cjk_generic_argument_within_python_limit_agrees(cg_exe, tmp_path):
    """Instantiation allows 4096 characters of type argument, not 4096 bytes."""
    name = CJK * 2000
    source = (
        f"struct {name} {{ x: i64 }}\nfn id<T>(x: T) -> T {{ return x; }}\n"
        f"fn main() -> int {{ let a = id({name} {{ x: 1 }}); return 0; }}\n")
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_cjk_type_charges_are_free_below_the_threshold(cg_exe, tmp_path):
    """A 63-character name is free to Python; in bytes it would spend the budget."""
    name = CJK * 63
    lets = "".join(f"let a{i}: {name} = {name} {{ x: 1 }};\n" for i in range(50000))
    source = f"struct {name} {{ x: i64 }}\nfn main() -> int {{\n{lets}return 0;\n}}\n"
    assert agree(cg_exe, tmp_path, source) == "ok"


def test_deterministic_over_runs(cg_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_bytes(expression_program("generic_values").encode("utf-8"))
    result, text, _ = py_codegen_dump.reference(path, "hosted")
    runs = {run_mort(cg_exe, result.manifest, tmp_path)[2] for _ in range(3)}
    assert runs == {text.encode("utf-8")}
    first_difference(text, text)


def replay_cjk_program():
    """Fits Python's replay budget in code points, not in UTF-8 bytes (3 per character)."""
    count = 780
    name = "函" * 20
    defers = f"defer {name}();\n" * count
    exits = "if true { return 1; }\n" * count
    return f"fn {name}() -> i64 {{ return 1; }}\nfn main() -> int {{\n{defers}{exits}return 0;\n}}\n"


def test_replay_budget_counts_code_points(cg_exe, tmp_path):
    assert agree(cg_exe, tmp_path, replay_cjk_program()) == "ok"


def replay_cjk_at_the_limit():
    """A long deferred name replayed to about the replay budget: charged in code
    points, so its UTF-8 is three times the budget and must never be held twice."""
    name = CJK * 9000
    defers = f"defer {name}();\n" * 29
    exits = "if true { return 1; }\n" * 180
    return f"fn {name}() -> i64 {{ return 1; }}\nfn main() -> int {{\n{defers}{exits}return 0;\n}}\n"


def test_replay_text_stays_within_memory(cg_exe, tmp_path):
    code, stderr, c_out = bounded(cg_exe, tmp_path, replay_cjk_at_the_limit())
    assert code == 0, stderr
    assert c_out.stat().st_size > 100_000_000


def test_driver_takes_no_effects_input(tmp_path):
    source = (HERE / "cg_main.mx").read_text(encoding="utf-8")
    assert "forced" not in source.lower()
    assert "cg_apply_forced" not in (HERE / "codegen.mx").read_text(encoding="utf-8")
    assert "placeholder" not in (HERE / "cg_stmt.mx").read_text(encoding="utf-8").lower()
