"""Differential test: selfhost's type checker must agree with mort/typechecker.py.

Run from the repository root:

    python -m pytest selfhost/test_checker_expr_differential.py -q

Every expected value comes from the real Python checker (py_check_dump.checked_dump):
the dump holds every attribute the checker sets on every node, the final tables,
the drop table and the warnings. When the Python checker reports an error, the
message and location must be equal and the walk up to that point the same.
"""
import ast
import collections
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
import py_ast_dump  # noqa: E402
import py_check_dump  # noqa: E402
from test_checker_decl_differential import (  # noqa: E402,F401
    BODY_CASES, BODY_PRELUDE, DECL_CASES, MODES, OK_MAIN, REPO_FILES, TEST_BLOCK_CASES,
    alias_chain, check_exe, first_difference, run_mort_bounded, split_dump,
)

EVENTS = py_check_dump.EVENTS_MARK
FINAL = "--final--\n"


def run_mort(exe, manifest_text, tmp_path, timeout=120):
    manifest = tmp_path / "manifest.txt"
    manifest.write_bytes(manifest_text.encode("utf-8"))
    out = tmp_path / "out.txt"
    checked = tmp_path / "checked.txt"
    for path in (out, checked):
        if path.exists():
            path.unlink()
    result = subprocess.run(
        [str(exe), str(manifest), str(out), str(checked)],
        capture_output=True, timeout=timeout)
    text = out.read_bytes().decode("ascii") if out.exists() else None
    checked_text = checked.read_bytes().decode("ascii") if checked.exists() else None
    return result.returncode, result.stderr.replace(b"\r\n", b"\n").rstrip(b"\n"), text, checked_text


def compare(exe, path, mode, tmp_path):
    """Run both implementations on one root file and assert they agree."""
    expected = py_check_dump.run_reference(path, mode, exact=True)
    if expected.outcome == "load":
        return "load"
    assert expected.outcome != "crash", f"{path}: Python crash"
    if expected.outcome == "parse":
        code, stderr, _text, _checked = run_mort(
            exe, f"MODE {mode}\nFILE {expected.failing_file}\n", tmp_path)
        assert code == 1, f"{path}: {stderr!r}"
        assert stderr.decode("utf-8") == expected.error, path
        return "parse"
    code, stderr, text, checked = run_mort(exe, expected.manifest, tmp_path)
    assert code in (0, 1), f"{path} ({mode}): exit {code}: {stderr!r}"
    assert text is not None, f"{path} ({mode}): no dump written"
    tables, events = split_dump(text)
    if expected.outcome == "ok":
        assert code == 0, f"{path} ({mode}): Mort rejected a valid program: {stderr!r}"
    else:
        assert code == 1, f"{path} ({mode}): Mort accepted; python: {expected.error}"
        assert stderr.decode("utf-8") == expected.error, (
            f"{path} ({mode})\n  python: {expected.error}\n"
            f"  mort:   {stderr.decode('utf-8')}")
    assert (expected.tables or "") == tables, (
        f"{path} ({mode}): tables differ: "
        f"{first_difference(expected.tables or '', tables)}")
    assert expected.events == events, (
        f"{path} ({mode}): events differ: "
        f"{first_difference(expected.events, events)}")
    if expected.outcome == "ok":
        assert checked is not None, f"{path} ({mode}): no checked dump"
        assert expected.checked == checked, (
            f"{path} ({mode}): checked dump differs: "
            f"{first_difference(expected.checked, checked)}")
    return "ok" if expected.outcome == "ok" else "error"


@pytest.fixture
def expr_exe(check_exe):
    return check_exe


def assert_source_agrees(exe, tmp_path, source, mode="hosted"):
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8") if isinstance(source, str) else source)
    outcome = compare(exe, path, mode, tmp_path)
    assert outcome != "load", source
    return outcome


def body(text):
    return f"{BODY_PRELUDE}\n{T5_PRELUDE}\nfn main() -> int {{\n{text}\nreturn 0;\n}}\n"


T5_PRELUDE = """
resource struct R { v: i64 }
fn destroy(value: *R) -> void { }
fn make_r() -> R { return R { v: 1 }; }
fn use_r(r: *R) -> i64 { return (*r).v; }
fn take_r(r: R) -> void { }
struct Holder { r: R, n: i64 }
enum Owns { Has(R), Empty }
enum Result<V, E> { Ok(V), Err(E) }
fn may_i() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }
fn may_u() -> Result<u8, i64> { return Result<u8, i64>.Ok(1); }
fn propagates() -> Result<i64, i64> { let v = try may_i(); return Result<i64, i64>.Ok(v); }
fn swap<A, B>(a: A, b: B) -> (B, A) { return (b, a); }
fn first<T>(a: *T) -> T { return *a; }
fn only<T>() -> u64 { return sizeof<T>(); }
fn pair<T>(a: T, b: T) -> T { return a; }
fn id2<T>(a: T) -> T { return a; }
fn applies<T>(f: fn(T) -> T, x: T) -> T { return f(x); }
fn add_one(x: i64) -> i64 { return x + 1; }
fn id_slice<T>(s: []T) -> []T { return s; }
fn id_ptr<T>(p: *const T) -> *const T { return p; }
struct Pt { x: i64, y: u8 }
struct Gbox<T> { v: T }
let g_u8: u8 = 7;
let g_arr: [u8; 3] = [1, 2, 3];
const G_CONST: i64 = 1 + 2 * 3;
let g_fn: fn(i64) -> i64 = add_one;
let g_str: *u8 = "text";
let g_enum: E = E.A;
let g_tuple: (i64, u8) = (1, 2);
let g_null: *u8 = null;
"""

EXPR_CASES = {
    # literals and adoption
    "lit_i64": "let a = 1; let b: u8 = a as u8;",
    "lit_adopt_let": "let a: u8 = 200;",
    "lit_overflow_u8": "let a: u8 = 256;",
    "lit_neg_u8": "let a: u8 = -1;",
    "lit_i8_min": "let a: i8 = -128; let b: i8 = -129;",
    "lit_u64_max": "let a: u64 = 18446744073709551615; let b: u64 = 18446744073709551616;",
    "lit_big_message": "let a: i64 = 1 << 200;",
    "lit_huge_shift_message": "let a: i64 = 1 << 100000;",
    "lit_huge_neg": "let a: i64 = -(1 << 200);",
    "lit_expr_adopt": "let a: u16 = 1 + 2 * 3 - 4; let b: i8 = ~0;",
    "lit_not_adopted_var": "let a = 1; let b: u8 = a;",
    "lit_float": "let a: f32 = 1.5; let b: f64 = 1.5; let c = 2.5;",
    "lit_float_too_big": "let a: f32 = 1e39;",
    "lit_float_int_mix": "let a = 1.5 + 1;",
    "lit_float_percent": "let a = 1.5 % 2.0;",
    "lit_float_mixed_widths": "let a: f32 = 1.5; let b: f64 = 2.5; let c = a + b;",
    "lit_float_adopts_other": "let a: f32 = 1.5; let c = a + 2.5; let d = 2.5 * a;",
    "lit_float_compare": "let a: f32 = 1.5; let c = a < 2.5; let d = a == 2.5;",
    "lit_char": "let a = 'x'; let b: u32 = 'y'; let c: i64 = 'z' + 1;",
    "lit_char_negative": "let a = -'x';",
    "lit_null_ptr": "let a: *u8 = null; let b: fn() -> i64 = null;",
    "lit_null_compare": "let a: *u8 = null; let b = a == null; let c = null != a;",
    "lit_null_compare_bad": "let a = 1; let b = a == null;",
    "lit_null_both": "let b = null == null;",
    "lit_null_field": "let s = Pt { x: null, y: 1 };",
    "lit_bool_string": 'let a = true; let b = false; let c = "s";',
    "lit_string_arg": 'println("x" + 1);',
    # arithmetic and unification
    "bin_unify_lit_right": "let a: u8 = 1; let b = a + 1; let c = a * 200;",
    "bin_unify_lit_right_overflow": "let a: u8 = 1; let b = a + 300;",
    "bin_unify_lit_left": "let a: u16 = 1; let b = 70000 + a;",
    "bin_unify_mismatch": "let a: u8 = 1; let b: u16 = 2; let c = a + b;",
    "bin_both_lit_diff": "let a = 'x' + 1; let b: u8 = 'x' + 1;",
    "bin_shift": "let a: u8 = 1; let b = a << 3; let c = 1 << a; let d: u64 = 1 << 63;",
    "bin_shift_neg": "let a = 1 << -1;",
    "bin_shift_neg_right": "let a = 1 >> -2;",
    "bin_shift_bool": "let a = true << 1;",
    "bin_shift_big_count": "let a: u64 = 1; let b = a << 70;",
    "bin_compare": "let a: u8 = 1; let b = a < 2; let c = 1 <= 2; let d = a >= a;",
    "bin_compare_mismatch": "let a: u8 = 1; let b: u16 = 1; let c = a < b;",
    "bin_eq_enum": "let a = E.A; let b = a == E.B; let c = a != a;",
    "bin_eq_enum_mixed": "let a = E.A; let b = P.B; let c = a == b;",
    "bin_eq_payload_enum": "let a = P.B; let b = a == a;",
    "bin_eq_struct": "let a = Pt { x: 1, y: 1 }; let b = a == a;",
    "bin_eq_tuple": "let a = (1, 2); let b = a == a;",
    "bin_eq_ptr": "let a: *u8 = null; let b: *u8 = null; let c = a == b;",
    "bin_eq_ptr_mismatch": "let a: *u8 = null; let b: *i64 = null; let c = a == b;",
    "bin_eq_bool": "let a = true == false;",
    "bin_logic": "let a = true && false || !true;",
    "bin_logic_bad": "let a = 1 && true;",
    "bin_bitand_lit": "let a: u8 = 0xF0 & 0x3C; let b: u8 = 1 | 2 ^ 3;",
    "bin_bitand_mismatch": "let a: u8 = 1; let b: u32 = 1; let c = a & b;",
    "bin_div_zero": "let a: i64 = 1; let b = a / 0;",
    "bin_mod_zero": "let a: i64 = 1; let b = a % (2 - 2);",
    "bin_div_lit": "let a = -7 / 2; let b = -7 % 2; let c = 7 / -2; let d: i64 = -7 % -2;",
    "bin_add_ptr": "let a: *u8 = null; let b = a + 1;",
    "bin_add_bool": "let a = true + 1;",
    "bin_float_var_mix": "let a = 1.5; let b = 1; let c = a * b;",
    # unary
    "un_minus_var": "let a: u8 = 1; let b = -a;",
    "un_minus_bool": "let a = -true;",
    "un_not_int": "let a = !1;",
    "un_tilde_float": "let a = ~1.5;",
    "un_tilde_lit": "let a: u8 = ~255; let b: u8 = ~0;",
    "un_addr_lit": "let a = &1;",
    "un_addr_call": "let a = &g();",
    "un_addr_of_const": "const a: i64 = 1; let b = &a; let c = &global_const;",
    "un_addr_field_of_const": "const s: Pt = Pt { x: 1, y: 2 }; let b = &s.x;",
    "un_deref": "let a: *u8 = null; let b = *a; let c = **a;",
    "un_deref_int": "let a = 1; let b = *a;",
    "un_deref_void": "let a: *void = null; let b = *a;",
    "un_addr_deref": "let a: *u8 = null; let b = &*a;",
    # casts
    "cast_ints": "let a = 1 as u8; let b = a as u16; let c = b as i64;",
    "cast_float": "let a = 1.5 as i64; let b = 1 as f32;",
    "cast_ptr": "let a = 1 as *u8; let b = a as u64; let c = a as *i64;",
    "cast_enum_unit": "let a = E.A; let b = a as i64; let c = 1 as E;",
    "cast_enum_payload": "let a = P.B; let b = a as i64;",
    "cast_to_payload": "let a = 1 as P;",
    "cast_bool": "let a = true as u8;",
    "cast_to_bool": "let a = 1 as bool;",
    "cast_null_ptr": "let a = null as *u8;",
    "cast_null_int": "let a = null as u8;",
    "cast_to_struct": "let a = 1 as Pt;",
    "cast_chain_lit_not_lit": "let a: u8 = (1 + 2) as u8; let b: u8 = (1 as u8) + 1;",
    "cast_chain": "let a = 1 as u8 as u16 as u32 as u64;",
    "cast_function_ptr": "let a = g as *void;",
    # const and pointer coercions
    "coerce_ptr_to_const": "let a: *u8 = null; let b: *const u8 = a;",
    "coerce_const_to_ptr": "let a: *const u8 = null; let b: *u8 = a;",
    "coerce_ptr_mismatch": "let a: *u8 = null; let b: *i64 = a;",
    "coerce_void_ptr": "let a: *void = null; let b: *u8 = a as *u8; let c: *void = a;",
    "coerce_string_const": 'let a: *const u8 = "abc";',
    "coerce_tuple_lit": "let a: (u8, i64) = (1, 2);",
    "coerce_tuple_lit_overflow": "let a: (u8, i64) = (300, 2);",
    "coerce_tuple_lit_arity": "let a: (u8, i64) = (1, 2, 3);",
    "coerce_tuple_var": "let a = (1, 2); let b: (u8, i64) = a;",
    "coerce_struct_lit_field": "let a = Pt { x: 1, y: 300 };",
    # field access, index
    "field_ptr_error": "let a = Pt { x: 1, y: 2 }; let p = &a; let b = p.x;",
    "field_deref": "let a = Pt { x: 1, y: 2 }; let p = &a; let b = (*p).x;",
    "field_on_int": "let a = 1; let b = a.x;",
    "field_tuple": "let a = (1, true); let b = a.0; let c = a.1;",
    "field_tuple_oob": "let a = (1, true); let b = a.2;",
    "field_tuple_named": "let a = (1, true); let b = a.x;",
    "field_slice": "let a: *u8 = null; let s = slice<u8>(a, 1); let b = s.len; let c = s.data;",
    "field_slice_bad": "let a: *u8 = null; let s = slice<u8>(a, 1); let b = s.other;",
    "field_generic_struct": "let a = G<u8> { x: 1 }; let b = a.x;",
    "index_array": "let a: [u8; 3] = [1, 2, 3]; let b = a[0]; let c = a[2];",
    "index_array_oob": "let a: [u8; 3] = [1, 2, 3]; let b = a[3];",
    "index_array_oob_expr": "let a: [u8; 3] = [1, 2, 3]; let b = a[1 + 5];",
    "index_array_oob_neg": "let a: [u8; 3] = [1, 2, 3]; let b = a[-1];",
    "index_ptr": "let a: *u8 = null; let b = a[100];",
    "index_void_ptr": "let a: *void = null; let b = a[0];",
    "index_bad_base": "let a = 1; let b = a[0];",
    "index_non_int": "let a: [u8; 3] = [1, 2, 3]; let b = a[true];",
    "index_slice": "let a: *u8 = null; let s = slice<u8>(a, 1); let b = s[0];",
    "index_const_ptr": "let a: *const u8 = null; let b = a[0];",
    "index_global_array": "let a = g_arr[1];",
    "index_big": "let a: [u8; 3] = [1, 2, 3]; let b = a[99999999999999999999999];",
    # arrays
    "array_let": "let a: [u8; 2] = [1, 2]; let b = [1, 2]; let c: [i64; 3] = [0; 3]; let d = [1; 2];",
    "array_elem_mismatch": "let a: [u8; 2] = [1, true];",
    "array_elem_overflow": "let a: [u8; 2] = [1, 300];",
    "array_repeat_elem_mismatch": "let a: [u8; 2] = [true; 2];",
    "array_no_decl_mixed": "let a = [1 as u8, 2 as u8]; let b = a[0];",
    "array_no_decl_mismatch": "let a = [1 as u8, 2];",
    "array_struct_elems": "let a: [Pt; 2] = [Pt { x: 1, y: 2 }, Pt { x: 3, y: 4 }];",
    "array_of_array_type": "let a: [[u8; 2]; 2] = [[1, 2], [3, 4]];",
    "array_assign_whole": "let a: [u8; 2] = [1, 2]; let b: [u8; 2] = [3, 4]; a = b;",
    "array_elem_assign": "let a: [u8; 2] = [1, 2]; a[0] = 7; a[1] = 300;",
    "array_of_struct_field": "let h = H { a: [1, 2] }; let v = h.a[1];",
    "array_len": "let a: [u8; 3] = [1, 2, 3]; let n = len(a);",
    "array_as_var": "let a: [u8; 2] = [1, 2]; let b = a;",
    # assignment
    "assign_type_mismatch": "let a: u8 = 1; a = true;",
    "assign_lit": "let a: u8 = 1; a = 300;",
    "assign_through_const_ptr": "let a: *const u8 = null; *a = 1;",
    "assign_through_const_index": "let a: *const u8 = null; a[0] = 1;",
    "assign_through_const_slice": "let a: *const u8 = null; let s = slice<u8>(a, 1); s[0] = 1;",
    "assign_field": "let a = Pt { x: 1, y: 2 }; a.x = 5; a.y = 300;",
    "assign_deref_field": "let a = Pt { x: 1, y: 2 }; let p = &a; (*p).y = 300;",
    "assign_tuple_elem": "let a = (1, 2); a.0 = 5;",
    "compound_assign": "let a: u8 = 1; a += 2; a <<= 1; a |= 3;",
    "compound_lit_overflow": "let a: u8 = 1; a += 300;",
    "compound_type_mismatch": "let a: u8 = 1; let b: u16 = 1; a += b;",
    "compound_ptr": "let a: *u8 = null; a += 1;",
    "compound_bool": "let a = true; a &= false;",
    "compound_float": "let a = 1.5; a += 2.5; a -= 1.0;",
    "compound_shift_negative": "let a: i64 = 1; a <<= -1;",
    "compound_on_rvalue": "g() += 1;",
    "assign_to_call": "g() = 1;",
    "assign_to_lit": "1 = 1;",
    "assign_in_module_global": "global_x = 2; let a = global_x;",
    # return
    "ret_ok": "return 1 + 2;",
    "ret_mismatch": "return true;",
    "ret_overflow": "return 1 << 70;",
    # conditions and loops
    "cond_if": "if 1 { }",
    "cond_while": "while 1 { }",
    "cond_if_ok": "if true { } else { }",
    "for_bounds": "for i in 0..3 { let a = i; }",
    "for_bounds_u8_start": "let u: u8 = 1; for i in u..3 { let a = i; }",
    "for_bounds_u8_end": "let u: u8 = 1; for i in 0..u { let a = i; }",
    "for_bounds_mismatch": "let a: u8 = 0; let b: u16 = 1; for i in a..b { }",
    "for_bounds_bool": "for i in true..3 { }",
    "for_lits": "for i in 0..'a' { let c = i; }",
    "for_typed": "for i: u8 in 0..300 { let a = i; }",
    "for_typed_lit_overflow": "let e: u16 = 1; for i: u8 in 0..e { }",
    "for_typed_bad": "for i: bool in 0..3 { }",
    "for_inclusive": "for i: u8 in 0..=5 { }",
    "for_shadow_binding_outer": "let i = 1; for i in 0..2 { let j = i; } let k = i;",
    "for_body_scope": "for i in 0..3 { let i2 = i; let i2 = 1; }",
    "while_shadow": "let a = true; while a { let a = 1; } ",
    # return analysis (the fn is built around it, so these are full programs)
    # struct literals and tuples
    "struct_lit_wrong_type": "let a = Pt { x: true, y: 1 };",
    "struct_lit_field_lit": "let a = Pt { x: 1, y: 'c' };",
    "tuple_types": "let a = (1, true, 'c', 1.5); let b = a.2; let c = a.3;",
    "tuple_nested": "let a = ((1, 2), (true, 3)); let b = a.0.1; let c = a.1.0;",
    "tuple_coerce_nested": "let a: ((u8, i64), bool) = ((1, 2), true);",
    "tuple_coerce_nested_bad": "let a: ((u8, i64), bool) = ((300, 2), true);",
    "tuple_void_elem": "let a = (g4(), 1);",
    # calls
    "call_arg_mismatch": "g3(true, 1, 2);",
    "call_arg_literal_overflow": "let a = narrow(300);",
    "call_arg_adopts": "let a = narrow(30); let b = narrow(1 + 2);",
    "call_args_extra": "let a = narrow(1, 2);",
    "call_result_void_used": "let a = g4();",
    "call_void_as_arg": "narrow(g4());",
    "call_via_fn_value": "let f = add_one; let a = f(1);",
    "call_via_fn_value_wrong_arity": "let f = add_one; let a = f();",
    "call_via_fn_value_wrong_type": "let f = add_one; let a = f(true);",
    "call_via_fn_value_type_args": "let f = add_one; let a = f<i64>(1);",
    "call_via_global_fn": "let a = g_fn(1);",
    "call_non_callable_named": "let a = 1; a();",
    "call_global_non_callable": "global_x();",
    "call_fn_value_shadowing": "let add_one = g4; add_one();",
    "call_moved_callable": "let r = make_r(); take_r(move r); r();",
    "fn_value_types": "let f = add_one; let g2 = g; let c: fn(i64) -> i64 = f; let d = c(1);",
    "fn_value_mismatch": "let f: fn() -> void = add_one;",
    "fn_null_call": "let f: fn(i64) -> i64 = null; let a = f(1);",
    "fn_compared_null": "let f: fn(i64) -> i64 = add_one; let a = f == null;",
    # builtins
    "b_println": 'println("a"); eprintln("b"); print(1); print(1.5);',
    "b_println_bad": "println(1);",
    "b_eprintln_bad": "eprintln(true);",
    "b_print_bad": 'print("a");',
    "b_print_bool": "print(true);",
    "b_arity": 'println("a", "b");',
    "b_alloc": "let a = alloc(10); let b = alloc(1 + 2); let c = alloc(g() as u64); free(a); free(b); free(c);",
    "b_alloc_bad": "let a = alloc(true);",
    "b_alloc_neg": "let a = alloc(-1);",
    "b_free_bad": "free(1);",
    "b_assert": "assert(true); assert(1 == 1);",
    "b_assert_bad": "assert(1);",
    "b_len_kinds": 'let a: [u8; 3] = [1, 2, 3]; let n = len(a); let m = len("x"); let p: *u8 = null; let s = slice<u8>(p, 1); let k = len(s);',
    "b_len_bad": "let n = len(1);",
    "b_sizeof_types": "let a = sizeof<u8>(); let b = sizeof<*u8>(); let c = sizeof<(u8, i64)>(); let d = sizeof<[u8; 3]>(); let e = sizeof<G<i64>>(); let f = sizeof<E>();",
    "b_slice": "let p: *u8 = null; let s = slice<u8>(p, 3); let c: *const u8 = null; let d = slice<u8>(c, 1);",
    "b_slice_bad_ptr": "let s = slice<u8>(1, 3);",
    "b_slice_void": "let p: *void = null; let s = slice<u8>(p, 3);",
    "b_slice_len": "let p: *u8 = null; let s = slice<u8>(p, true);",
    "b_slice_arity": "let p: *u8 = null; let s = slice<u8>(p);",
    "b_files": 'let h = file_open("a", "b"); file_write(h, "x", 1); let c: *u8 = null; file_read(h, c, 1); file_flush(h); file_close(h);',
    "b_file_open_bad": "let h = file_open(1, 2);",
    "b_file_open_arity": 'let h = file_open("a");',
    "b_file_close_bad": "file_close(1);",
    "b_file_close_arity": "file_close();",
    "b_file_read_bad_handle": 'let c: *u8 = null; file_read(1, c, 1);',
    "b_file_read_bad_buffer": 'let h = file_open("a", "b"); file_read(h, 1, 1);',
    "b_file_read_bad_len": 'let h = file_open("a", "b"); let c: *u8 = null; file_read(h, c, true);',
    "b_file_write_const_buf": 'let h = file_open("a", "b"); let c: *const u8 = null; file_write(h, c, 1); file_read(h, c, 1);',
    "b_file_arity": 'let h = file_open("a", "b"); file_read(h);',
    "b_time": "let a = unix_time(); let b = cpu_millis();",
    "b_time_bad": "let a = unix_time(1);",
    "b_args": "let a = arg_count(); let b = arg_get(0);",
    "b_args_bad": "let a = arg_count(1); ",
    "b_args_bad2": "let b = arg_get();",
    "b_args_bad3": "let b = arg_get(true);",
    "b_threads": "let h = thread_spawn(tcb, null); let r = thread_join(h); thread_sleep_millis(1);",
    "b_thread_spawn_bad": "let h = thread_spawn(g, null);",
    "b_thread_spawn_ctx": "let h = thread_spawn(tcb, 1);",
    "b_thread_spawn_arity": "let h = thread_spawn(tcb);",
    "b_thread_join_bad": "let r = thread_join(1);",
    "b_thread_join_arity": "let r = thread_join();",
    "b_thread_sleep_bad": "thread_sleep_millis(true);",
    "b_thread_sleep_arity": "thread_sleep_millis();",
    "b_mutex": "let m = mutex_create(); mutex_lock(m); mutex_unlock(m); mutex_destroy(m);",
    "b_mutex_create_args": "let m = mutex_create(1);",
    "b_mutex_bad": "mutex_lock(1);",
    "b_mutex_arity": "mutex_lock();",
    "b_atomic": "let a = atomic_i64_create(1); let b = atomic_i64_load(a); atomic_i64_store(a, 2); let c = atomic_i64_exchange(a, 3); let d = atomic_i64_fetch_add(a, 1); let e = atomic_i64_fetch_sub(a, 1); let f = atomic_i64_compare_exchange(a, 1, 2); atomic_i64_destroy(a);",
    "b_atomic_create_bad": "let a = atomic_i64_create(true);",
    "b_atomic_create_arity": "let a = atomic_i64_create();",
    "b_atomic_load_bad": "let b = atomic_i64_load(1);",
    "b_atomic_load_arity": "let b = atomic_i64_load();",
    "b_atomic_store_bad": "let a = atomic_i64_create(1); atomic_i64_store(1, 1);",
    "b_atomic_store_value": "let a = atomic_i64_create(1); atomic_i64_store(a, true);",
    "b_atomic_store_arity": "let a = atomic_i64_create(1); atomic_i64_store(a);",
    "b_atomic_cx_bad": "let f = atomic_i64_compare_exchange(1, 1, 2);",
    "b_atomic_cx_value": "let a = atomic_i64_create(1); let f = atomic_i64_compare_exchange(a, true, 2);",
    "b_atomic_cx_arity": "let f = atomic_i64_compare_exchange(1);",
    "b_net": 'let a = net_tcp_connect("h", 1); let l = net_tcp_listen("h", 1, 2); let c = net_tcp_accept(l); let u = net_udp_connect("h", 1); let b = net_udp_bind("h", 1); let x = net_socket_local_port(u); net_socket_shutdown(u); net_socket_close(u); let y = net_last_error_would_block(); net_socket_set_nonblocking(a, true); let z = net_socket_wait(a, 1, 1);',
    "b_net_tcp_bad_host": "let a = net_tcp_connect(1, 1);",
    "b_net_tcp_bad_port": 'let a = net_tcp_connect("h", true);',
    "b_net_tcp_port_overflow": 'let a = net_tcp_connect("h", 70000);',
    "b_net_tcp_arity": 'let a = net_tcp_connect("h");',
    "b_net_listen_arity": 'let a = net_tcp_listen("h", 1);',
    "b_net_listen_backlog": 'let a = net_tcp_listen("h", 1, true);',
    "b_net_udp_bad": 'let a = net_udp_bind(1, 1);',
    "b_net_udp_arity": 'let a = net_udp_bind("h");',
    "b_net_udp_send": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_send_to(a, "h", 1, c, 1);',
    "b_net_udp_send_arity": "net_udp_send_to(1);",
    "b_net_udp_send_bad_handle": 'let c: *u8 = null; net_udp_send_to(1, "h", 1, c, 1);',
    "b_net_udp_send_bad_host": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_send_to(a, 1, 1, c, 1);',
    "b_net_udp_send_bad_port": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_send_to(a, "h", true, c, 1);',
    "b_net_udp_send_bad_buffer": 'let a = net_udp_bind("h", 1); net_udp_send_to(a, "h", 1, 1, 1);',
    "b_net_udp_send_bad_len": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_send_to(a, "h", 1, c, true);',
    "b_net_udp_recv": 'let a = net_udp_bind("h", 1); let c: *u8 = null; let p: *u16 = null; net_udp_recv_from(a, c, 1, c, 1, p);',
    "b_net_udp_recv_arity": "net_udp_recv_from(1);",
    "b_net_udp_recv_bad": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_recv_from(a, c, true, c, 1, c);',
    "b_net_udp_recv_last": 'let a = net_udp_bind("h", 1); let c: *u8 = null; net_udp_recv_from(a, c, 1, c, 1, 1);',
    "b_net_socket_send": 'let a = net_tcp_connect("h", 1); let c: *u8 = null; let n = net_socket_send(a, c, 1); let m = net_socket_recv(a, c, 1);',
    "b_net_socket_send_bad_handle": 'let c: *u8 = null; let n = net_socket_send(1, c, 1);',
    "b_net_socket_send_arity": 'let n = net_socket_send(1);',
    "b_net_socket_send_buffer": 'let a = net_tcp_connect("h", 1); let n = net_socket_send(a, 1, 1);',
    "b_net_socket_send_const_ok": 'let a = net_tcp_connect("h", 1); let c: *const u8 = null; let n = net_socket_send(a, c, 1);',
    "b_net_socket_send_len": 'let a = net_tcp_connect("h", 1); let c: *u8 = null; let n = net_socket_send(a, c, true);',
    "b_net_socket_bad_handle": "net_socket_close(1);",
    "b_net_socket_arity": "net_socket_close();",
    "b_net_set_nonblocking_bad": "net_socket_set_nonblocking(1, true);",
    "b_net_set_nonblocking_bad2": 'let a = net_tcp_connect("h", 1); net_socket_set_nonblocking(a, 1);',
    "b_net_set_nonblocking_arity": "net_socket_set_nonblocking(1);",
    "b_net_wait_bad": 'let z = net_socket_wait(1, 1, 1);',
    "b_net_wait_events": 'let a = net_tcp_connect("h", 1); let z = net_socket_wait(a, true, 1);',
    "b_net_wait_timeout": 'let a = net_tcp_connect("h", 1); let z = net_socket_wait(a, 1, true);',
    "b_net_wait_arity": "let z = net_socket_wait(1);",
    "b_net_would_block_args": "let y = net_last_error_would_block(1);",
    "b_secure": "let c: *u8 = null; secure_random_fill(c, 1);",
    "b_secure_bad": "secure_random_fill(1, 1);",
    "b_secure_len": "let c: *u8 = null; secure_random_fill(c, true);",
    "b_secure_arity": "secure_random_fill(1);",
    "b_tls": 'let t = tls_client_connect("h", 1); let c: *u8 = null; let cc: *const u8 = null; let s = tls_send(t, cc, 1); let r = tls_recv(t, c, 1); let v = tls_protocol_version(t); tls_close(t); let t2 = tls_client_connect_with_ca("h", 1, cc, 1);',
    "b_tls_bad_host": "let t = tls_client_connect(1, 1);",
    "b_tls_bad_port": 'let t = tls_client_connect("h", true);',
    "b_tls_arity": 'let t = tls_client_connect("h");',
    "b_tls_ca_arity": "let t = tls_client_connect_with_ca(1);",
    "b_tls_ca_bad": 'let t = tls_client_connect_with_ca("h", 1, true, 1);',
    "b_tls_send_bad_handle": "let c: *u8 = null; tls_send(1, c, 1);",
    "b_tls_send_arity": "tls_send(1);",
    "b_tls_send_buffer": 'let t = tls_client_connect("h", 1); tls_send(t, 1, 1);',
    "b_tls_recv_const": 'let t = tls_client_connect("h", 1); let c: *const u8 = null; tls_recv(t, c, 1);',
    "b_tls_send_len": 'let t = tls_client_connect("h", 1); let c: *u8 = null; tls_send(t, c, true);',
    "b_tls_close_bad": "tls_close(1);",
    "b_tls_close_arity": "tls_close();",
    "b_ports": "outb(1, 2); let a = inb(1); outw(1, 2); let b = inw(1); outl(1, 2); let c = inl(1);",
    "b_outb_arity": "outb(1);",
    "b_outb_bad_port": "outb(true, 1);",
    "b_outb_bad_value": "outb(1, 300);",
    "b_outb_lit_port_overflow": "outb(70000, 1);",
    "b_inb_arity": "inb();",
    "b_inb_bad": "inb(true);",
    "b_outw_arity": "outw(1);",
    "b_outw_bad": "outw(true, 1);",
    "b_outw_bad2": "outw(1, 70000);",
    "b_inw_arity": "inw();",
    "b_inw_bad": "inw(true);",
    "b_outl_arity": "outl(1);",
    "b_outl_bad": "outl(true, 1);",
    "b_outl_bad2": "outl(1, true);",
    "b_inl_arity": "inl();",
    "b_inl_bad": "inl(true);",
    "volatile_ok": "let p: *u32 = null; let v = volatile_read<u32>(p); volatile_write<u32>(p, 1); volatile_write<u32>(p, v);",
    "volatile_bad_type_count": "let p: *u32 = null; let v = volatile_read<u32, u32>(p);",
    "volatile_bad_width": "let p: *u32 = null; let v = volatile_read<bool>(p);",
    "volatile_alias_width": "let p: *u32 = null; volatile_write<AU32>(p, 7);",
    "volatile_arity_read": "let p: *u32 = null; let v = volatile_read<u32>();",
    "volatile_arity_write": "let p: *u32 = null; volatile_write<u32>(p);",
    "volatile_not_pointer": "let v = volatile_read<u32>(1);",
    "volatile_wrong_pointee": "let p: *u8 = null; let v = volatile_read<u32>(p);",
    "volatile_const_write": "let p: *const u32 = null; volatile_write<u32>(p, 1);",
    "volatile_write_bad_value": "let p: *u32 = null; volatile_write<u32>(p, true);",
    "volatile_write_overflow": "let p: *u8 = null; volatile_write<u8>(p, 300);",
    "volatile_write_negative": "let p: *u8 = null; volatile_write<u8>(p, -1);",
    "volatile_write_huge": "let p: *u8 = null; volatile_write<u8>(p, 1 << 300);",
    # generics
    "gen_infer": "let a = id2(1); let b = id2(true); let c = id2(1 as u8);",
    "gen_infer_ptr": "let x: u8 = 1; let a = first(&x);",
    "gen_infer_const": "const x: u8 = 1; let a = first(&x);",
    "gen_explicit": "let a = id2<u8>(1); let b = id2<u8>(300);",
    "gen_explicit_coerce": "let a = pair<u8>(1, 2);",
    "gen_two_params": "let a = swap(1, true); let b = a.0;",
    "gen_conflict": "let a = pair(1 as u8, 1 as u16);",
    "gen_literal_conflict": "let u: u8 = 1; let a = pair(u, 1);",
    "gen_cant_infer": "let a = only();",
    "gen_only_explicit": "let a = only<u8>(); let b = only<(u8, i64)>();",
    "gen_fn_callback": "let a = applies(add_one, 1);",
    "gen_fn_callback_bad": "let a = applies(add_one, true);",
    "gen_slice": "let p: *u8 = null; let s = slice<u8>(p, 1); let t = id_slice(s);",
    "gen_slice_mismatch": "let s = id_slice(1);",
    "gen_const_ptr": "const x: u8 = 1; let a = id_ptr(&x);",
    "gen_const_ptr_mut": "let x: u8 = 1; let a = id_ptr(&x);",
    "gen_array_param": "let a: [u8; 2] = [1, 2]; let b = id2(a);",
    "gen_void_arg": "let a = id2(g4());",
    "gen_null_arg": "let a = id2(null);",
    "gen_recursive_generic": "let b = Gbox<G<u8>> { v: G<u8> { x: 1 } }; let c = b.v.x;",
    "gen_body_error": "let a = bad_body(1);",
    "gen_body_error_instance_only": "let a = bad_struct_only<i64>(1); let b = bad_struct_only<G<u8>>(G<u8> { x: 1 });",
    "gen_same_instance_twice": "let a = id2(1); let b = id2(2); let c = id2<i64>(3);",
    "gen_nested_instantiation": "let a = outer_gen(1);",
    "gen_enum_ctor_inside": "let a = wrap_enum(1); let b = wrap_enum(true);",
    "gen_match_inside": "let a = unwrap_gen(O<u8>.Some(1), 2);",
    "gen_alias_args": "let a = id2<AU32>(1);",
    "gen_return_mismatch": "let a = id2<bool>(true); let b: u8 = id2(true);",
    "gen_private_template": "let a = hidden_gen();",
    # try
    "try_ok": "let v = propagates();",
    "try_outside_result": "let v = try may_i();",
    "try_non_result": "let v = try g();",
    "try_wrong_error_type": "let v = wrong_err<i64>();",
    "try_value_type": "let v = try_u();",
    "try_in_generic": "let v = try_in_generic<i64>();",
    # moves, resources, defer
    "move_basic": "let r = make_r(); take_r(move r);",
    "move_use_after": "let r = make_r(); take_r(move r); use_r(&r);",
    "move_twice": "let r = make_r(); take_r(move r); take_r(move r);",
    "move_plain_copy": "let a = 1; let b = move a; let c = a;",
    "move_no_transfer": "let r = make_r(); let s = r;",
    "move_let": "let r = make_r(); let s = move r; use_r(&s);",
    "move_in_if_then": "let r = make_r(); if true { take_r(move r); }",
    "move_in_if_then_used": "let r = make_r(); if true { take_r(move r); } use_r(&r);",
    "move_in_returning_branch": "let r = make_r(); if true { take_r(move r); return 1; } use_r(&r);",
    "move_in_both_branches": "let r = make_r(); if true { take_r(move r); } else { take_r(move r); }",
    "move_in_else_branch": "let r = make_r(); if true { } else { take_r(move r); } use_r(&r);",
    "move_in_else_if": "let r = make_r(); if true { } else if false { take_r(move r); } else { } use_r(&r);",
    "move_in_returning_else": "let r = make_r(); if true { take_r(move r); } else { return 1; } use_r(&r);",
    "move_in_loop": "let r = make_r(); while true { take_r(move r); break; }",
    "move_in_for": "let r = make_r(); for i in 0..2 { take_r(move r); }",
    "move_declared_in_loop": "while true { let r = make_r(); take_r(move r); break; }",
    "move_field": "let h = Holder { r: make_r(), n: 1 }; take_r(move h.r);",
    "move_from_tuple": "let a = 1; let b = 2; let t = (move a, move b);",
    "move_resource_tuple_no_move": "let r = make_r(); let t = (r, 1);",
    "move_resource_tuple": "let r = make_r(); let t = (move r, 1);",
    "move_resource_struct_field": "let r = make_r(); let h = Holder { r: move r, n: 1 };",
    "move_resource_struct_field_no_move": "let r = make_r(); let h = Holder { r: r, n: 1 };",
    "move_shadow": "let r = make_r(); { let r = make_r(); take_r(move r); } use_r(&r);",
    "move_match_owning": "let o = Owns.Has(make_r()); match move o { Owns.Has(r) => { take_r(move r); } Owns.Empty => { } }",
    "move_match_owning_no_move": "let o = Owns.Has(make_r()); match o { Owns.Has(r) => { take_r(move r); } Owns.Empty => { } }",
    "move_match_owning_wildcard": "let o = Owns.Has(make_r()); match move o { Owns.Has(r) => { take_r(move r); } _ => { } }",
    "move_match_owning_ignored": "let o = Owns.Has(make_r()); match move o { Owns.Has(_) => { } Owns.Empty => { } }",
    "move_match_arm_merge": "let r = make_r(); let e = E.A; match e { E.A => { take_r(move r); } E.B => { } } use_r(&r);",
    "move_match_arm_returns": "let r = make_r(); let e = E.A; match e { E.A => { take_r(move r); return 1; } E.B => { } } use_r(&r);",
    "move_match_all_arms": "let r = make_r(); let e = E.A; match e { E.A => { take_r(move r); } E.B => { take_r(move r); } }",
    "move_global": "take_r(move missing_r);",
    "move_loop_var": "for i in 0..2 { let j = move i; }",
    "resource_overwrite": "let r = make_r(); r = make_r();",
    "resource_field_overwrite": "let h = Holder { r: make_r(), n: 1 }; h.r = make_r();",
    "resource_ptr_overwrite": "let r = make_r(); let p = &r; *p = make_r();",
    "resource_index_overwrite": "let p = alloc(16) as *R; p[0] = make_r();",
    "resource_const_ptr_overwrite": "let r = make_r(); let p = &r as *const R; *p = make_r();",
    "resource_binding_scope_ok": "{ let r = make_r(); use_r(&r); }",
    "resource_unused_warns": "let r = make_r();",
    "resource_array": "let a: [R; 2] = [make_r(), make_r()];",
    "resource_tuple_binding": "let t = (make_r(), 1);",
    "destroy_binding": "let r = make_r(); destroy(&r); use_r(&r);",
    "destroy_moved": "let r = make_r(); destroy(&r); take_r(move r);",
    "destroy_in_defer": "let r = make_r(); defer destroy(&r); use_r(&r);",
    "destroy_field_no_tracking": "let h = Holder { r: make_r(), n: 1 }; destroy(&h.r);",
    "destroy_of_param": "destroy_param(make_r());",
    "defer_call": "let p = alloc(1); defer free(p);",
    "defer_undefined": "defer missing();",
    "defer_expr": "let a = 1; defer a + 1;",
    "defer_generic": "defer id2(1);",
    "use_after_move_call_target": "let r = make_r(); take_r(move r); let z = r;",
    "moved_callable": "let cb = make_cb_holder(); cb();",
    "warn_unused_all": "let unused_a = 1; let _quiet = 2; for loop_v in 0..1 { }",
    "warn_used": "let a = 1; let b = a;",
    # match
    "match_exhaustive": "let e = E.A; match e { E.A => { } E.B => { } }",
    "match_missing": "let e = E.A; match e { E.A => { } }",
    "match_missing_many": "let m = M.A; match m { M.B => { } }",
    "match_wildcard_covers": "let e = E.A; match e { _ => { } }",
    "match_int_subject": "let a = 1; match a { 1 => { } 2 => { } _ => { } }",
    "match_int_no_wildcard": "let a = 1; match a { 1 => { } }",
    "match_int_pattern_type": "let a: u8 = 1; match a { 300 => { } _ => { } }",
    "match_bool_subject": "let a = true; match a { true => { } false => { } _ => { } }",
    "match_char": "let a = 'x'; match a { 'x' => { } _ => { } }",
    "match_struct_subject": "let a = Pt { x: 1, y: 2 }; match a { _ => { } }",
    "match_array_subject": "let a: [u8; 2] = [1, 2]; match a { _ => { } }",
    "match_ptr_subject": "let a: *u8 = null; match a { _ => { } }",
    "match_slice_subject": "let p: *u8 = null; let s = slice<u8>(p, 1); match s { _ => { } }",
    "match_tuple_subject": "let a = (1, 2); match a { _ => { } }",
    "match_pattern_wrong_enum": "let e = E.A; match e { P.B => { } _ => { } }",
    "match_pattern_int_for_enum": "let e = E.A; match e { 1 => { } _ => { } }",
    "match_pattern_from_alias": "let e = EA.A; match e { EA.A => { } EA.B => { } }",
    "match_unknown_variant_ident": "let e = E.A; match e { E.Z => { } _ => { } }",
    "match_payload_generic_subject": "let o = O<u8>.Some(1); match o { O<u8>.Some(v) => { let w: u8 = v; } O<u8>.None => { } }",
    "match_payload_binding_type": "let p = P.A(1); match p { P.A(v) => { let b: bool = v; } P.B => { } }",
    "match_payload_missing_arm": "let p = P.A(1); match p { P.A(v) => { } }",
    "match_payload_prefix_mismatch": "let p = P.A(1); match p { Q.A(a, b) => { } P.B => { } }",
    "match_payload_alias_subject": "let p = PA.A(1); match p { PA.A(v) => { } PA.B => { } }",
    "match_int_subject_payload_pattern": "let x = 1; match x { P.A(v) => { } _ => { } }",
    "match_unit_on_payload_enum": "let p = P.A(1); match p { P.B => { } P.A(_) => { } }",
    "match_payload_underscore_single": "let p = P.A(1); match p { P.A(_) => { } P.B => { } }",
    "match_call_pattern": "let a = 1; match a { g() => { } _ => { } }",
    "match_expr_pattern": "let a = 1; match a { 1 + 2 => { } _ => { } }",
    "match_shadow_binding": "let p = P.A(1); let v = 2; match p { P.A(v) => { let x: i64 = v; } P.B => { } } let y = v;",
    "match_return_analysis": "let e = E.A; match e { E.A => { return 1; } E.B => { return 2; } }",
    "match_binding_warn": "let p = Q.A(1, 2); match p { Q.A(a, b) => { let x = a; } }",
    "match_move_binding_twice": "let o = Owns.Has(make_r()); match move o { Owns.Has(r) => { take_r(move r); take_r(move r); } Owns.Empty => { } }",
    "match_nested_exhaustive": "let e = E.A; let f = E.B; match e { E.A => { match f { E.A => { } E.B => { } } } E.B => { } }",
    "match_multiple_wildcards": "let e = 1; match e { 1 => { } _ => { } _ => { } }",
    # aliases at expression sites
    "alias_cast": "let a = 1 as AU32;",
    "alias_let": "let a: AU32 = 1; let b: u32 = a;",
    "alias_sizeof": "let a = sizeof<AU32>();",
    "alias_struct_field": "let s = SA { a: 1 }; let v = s.a;",
    "alias_tuple": "let t: ATuple = (1, 2); let v = t.0;",
    "alias_array": "let a: AArr = [1, 2]; let v = a[0];",
    "alias_ptr": "let x = 1; let p: AIntPtr = &x; let v = *p;",
    "alias_fn": "let f: AFn = add_one; let v = f(1);",
}

GENERIC_RESOURCE = (
    "resource struct RG<T> { v: T }\nfn destroy<T>(value: *RG<T>) -> void { }\n"
    "fn use_rg<T>(r: *RG<T>) -> void { }\nfn take_rg<T>(r: RG<T>) -> void { }\n")

FN_ONLY_CASES = {
    "ret_missing_int": "fn f() -> i64 { }",
    "ret_ok_if_both": "fn f(a: bool) -> i64 { if a { return 1; } else { return 2; } }",
    "ret_only_then": "fn f(a: bool) -> i64 { if a { return 1; } }",
    "ret_if_else_if": "fn f(a: bool) -> i64 { if a { return 1; } else if a { return 2; } else { return 3; } }",
    "ret_if_else_if_open": "fn f(a: bool) -> i64 { if a { return 1; } else if a { return 2; } }",
    "ret_while_true": "fn f() -> i64 { while true { } }",
    "ret_loop_break": "fn f() -> i64 { loop { break; } }",
    "ret_loop_break_nested": "fn f() -> i64 { loop { while true { break; } } }",
    "ret_loop_break_for": "fn f() -> i64 { loop { for i in 0..1 { break; } } }",
    "ret_loop_break_if": "fn f(a: bool) -> i64 { loop { if a { break; } } }",
    "ret_loop_break_else": "fn f(a: bool) -> i64 { loop { if a { } else { break; } } }",
    "ret_loop_break_else_if": "fn f(a: bool) -> i64 { loop { if a { } else if a { break; } } }",
    "ret_loop_break_match": "enum E2 { A }\nfn f(a: E2) -> i64 { loop { match a { E2.A => { break; } } } }",
    "ret_loop_break_block": "fn f() -> i64 { loop { { break; } } }",
    "ret_while_cond": "fn f(a: bool) -> i64 { while a { return 1; } }",
    "ret_block": "fn f() -> i64 { { return 1; } }",
    "ret_after_return": "fn f() -> i64 { return 1; let a = 2; }",
    "ret_match_all": "enum E2 { A, B }\nfn f(a: E2) -> i64 { match a { E2.A => { return 1; } E2.B => { return 2; } } }",
    "ret_match_wildcard": "enum E2 { A, B }\nfn f(a: E2) -> i64 { match a { E2.A => { return 1; } _ => { return 2; } } }",
    "ret_match_open": "fn f(a: i64) -> i64 { match a { 1 => { return 1; } } }",
    "ret_match_payload": "enum E2 { A(i64) }\nfn f(a: E2) -> i64 { match a { E2.A(v) => { return v; } } }",
    "ret_void_value": "fn f() { return 1; }",
    "ret_void_empty": "fn f() { return; }",
    "ret_nonvoid_empty": "fn f() -> i64 { return; }",
    "param_shadow": "fn f(a: i64) { let a = 1; }",
    "param_dup": "fn f(a: i64, a: i64) { }",
    "param_resource": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nfn f(r: R2) { }",
    "param_struct_of_resource": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nstruct H2 { r: R2 }\nfn f(h: H2) { }",
    "param_unused_warn": "fn f(a: i64) { }",
    "instance_order_runs_after": "fn a1() { b1(1); }\nfn b1<T>(x: T) { }\nfn c1() { b1(true); }",
    "instance_then_test_order": "fn a1<T>(x: T) { }\nfn a2() { a1(1); }\ntest \"t\" { a1(true); }",
    "main_in_test": "test \"t\" { let x: u8 = 300; }",
    "generic_in_global": "fn id3<T>(a: T) -> T { return a; }\nlet g: i64 = id3(1);",
    "global_const_expr": "let g: u8 = 1 + 2;\nlet h: u8 = 300;",
    "global_null": "let g: *u8 = null;\nlet h = null;",
    "global_void": "fn v() {}\nlet g = v();",
    "global_non_const": "fn v() -> i64 { return 1; }\nlet g = v();",
    "global_tuple_const": "let g: (u8, i64) = (1, 2);\nlet h = (1, true, 'c');",
    "global_enum": "enum E2 { A }\nlet g = E2.A;",
    "global_enum_payload": "enum E2 { A(u8) }\nlet g = E2.A;",
    "global_array": "let g: [u8; 2] = [1, 300];",
    "global_array_nodecl": "let g = [1, 2];",
    "global_array_non_const": "fn v() -> i64 { return 1; }\nlet g: [i64; 1] = [v()];",
    "global_fn_value": "fn v() -> i64 { return 1; }\nlet g = v;\nlet h: fn() -> i64 = v;",
    "global_resource": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nlet g = R2 { x: 1 };",
    "global_resource_array": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nlet g: [*R2; 1] = [null];",
    "global_generic_struct": "struct G2<T> { x: T }\nlet g = G2<u8> { x: 1 };",
    "global_cast": "let g = 1 as u8;",
    "module_fn_value": "module m;\nfn f() -> i64 { return 1; }\nfn h() -> i64 { let g = f; return g(); }",
    "destructor_drop_order": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nstruct H1 { r: R2 }\nstruct H2 { h: H1, r: R2 }\nfn f(h: H2) { }\nfn g(a: (R2, i64)) { }",
    "destructor_drop_enum": "resource struct R2 { x: i64 }\nfn destroy(v: *R2) -> void { }\nenum E2 { A(R2), B(*R2) }\nfn f(e: E2) { match move e { E2.A(r) => { } E2.B(p) => { } } }",
    "destructor_generic_instance": "resource struct R2<T> { x: T }\nfn destroy<T>(v: *R2<T>) -> void { }\nfn f(a: R2<u8>, b: R2<i64>) { }",
    "generic_resource_flow": GENERIC_RESOURCE + "fn main2() { let r = RG<u8> { v: 1 }; let s = RG<u8> { v: 2 }; use_rg(&r); take_rg(move s); destroy(&r); }",
    "generic_resource_use_after_destroy": GENERIC_RESOURCE + "fn main2() { let r = RG<u8> { v: 1 }; destroy(&r); use_rg(&r); }",
    "generic_resource_two_instances": GENERIC_RESOURCE + "fn main2() { let r = RG<u8> { v: 1 }; let s = RG<i64> { v: 2 }; take_rg(move r); take_rg(move s); }",
    "generic_resource_deferred": GENERIC_RESOURCE + "fn main2() { let r = RG<u8> { v: 1 }; defer destroy(&r); use_rg(&r); }",
    "destructor_generic_nested": "resource struct R2<T> { x: T }\nfn destroy<T>(v: *R2<T>) -> void { }\nstruct H<T> { r: R2<T> }\nfn f(a: H<u8>) { }",
}


T5_MAIN_HELPERS = """
fn g4() { }
fn narrow(a: u8) -> u8 { return a; }
fn tcb(ctx: *void) -> i64 { return 0; }
fn bad_body<T>(a: T) -> T { let x: u8 = true; return a; }
fn bad_struct_only<T>(a: T) -> T { let f = a.x; return a; }
fn outer_gen<T>(a: T) -> T { return inner_gen(a); }
fn inner_gen<T>(a: T) -> T { let b: T = a; return b; }
fn wrap_enum<T>(a: T) -> O<T> { return O<T>.Some(a); }
fn unwrap_gen<T>(o: O<T>, d: T) -> T { match o { O<T>.Some(v) => { return v; } O<T>.None => { return d; } } }
fn wrong_err<T>() -> Result<i64, u8> { let v = try may_i(); return Result<i64, u8>.Ok(v); }
fn try_u() -> Result<i64, i64> { let v = try may_u(); return Result<i64, i64>.Ok(v as i64); }
fn try_in_generic<T>() -> Result<i64, i64> { let v = try may_i(); return Result<i64, i64>.Ok(v); }
fn destroy_param(r: R) -> void { }
struct CbHolder { f: fn() -> void }
fn make_cb_holder() -> fn() -> void { return g4; }
enum M { A, B, C, D }
type AU32 = u32;
type ATuple = (u8, i64);
type AArr = [i64; 2];
type AIntPtr = *i64;
type AFn = fn(i64) -> i64;
module_stub_not_a_decl_marker
""".replace("module_stub_not_a_decl_marker\n", "")


def full_body(text):
    return f"{BODY_PRELUDE}\n{T5_PRELUDE}\n{T5_MAIN_HELPERS}\nfn main() -> int {{\n{text}\nreturn 0;\n}}\n"


def test_case_counts():
    assert len(EXPR_CASES) >= 400
    assert len(FN_ONLY_CASES) >= 45


def test_corpus_is_not_empty():
    assert len(REPO_FILES) >= 100


def test_cases_exercise_the_checker(tmp_path):
    """A prelude that does not compile makes every case agree on one error and
    prove nothing, which is exactly how this suite first passed."""
    outcomes = collections.Counter()
    errors = collections.Counter()
    path = tmp_path / "input.mx"
    for text in EXPR_CASES.values():
        path.write_bytes(full_body(text).encode("utf-8"))
        result = py_check_dump.run_reference(path, "hosted", exact=True)
        outcomes[result.outcome] += 1
        if result.error is not None:
            errors[result.error.split("error: ", 1)[-1]] += 1
    assert outcomes["ok"] >= 150, outcomes
    assert outcomes["error"] >= 200, outcomes
    assert errors.most_common(1)[0][1] <= len(EXPR_CASES) // 10, errors.most_common(3)


def test_dump_covers_every_field_the_checker_assigns():
    """A static audit of mort/typechecker.py: every attribute it writes on a
    node is in the checked dump, either as a field of its own or as a parser
    field the dump already prints (aliases and substitution rewrite those)."""
    tree = ast.parse((ROOT / "mort" / "typechecker.py").read_text(encoding="utf-8"))
    written = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            items = target.elts if isinstance(target, ast.Tuple) else [target]
            for item in items:
                if (isinstance(item, ast.Attribute)
                        and isinstance(item.value, ast.Name)
                        and item.value.id != "self"):
                    written.add(item.attr)
    dumped = set(py_check_dump.DYNAMIC_FIELDS)
    for fields in py_check_dump.CHECKED_SUFFIX.values():
        dumped.update(fields)
    dumped.update(py_ast_dump.BASE_NODE_FIELDS)
    parser_rewrites = {
        "typ", "target", "ret", "name", "payload_types", "payload_type",
        "decl_type", "target_type", "type_args", "generic_params", "symbol_name",
        "generic_params",
    }
    # Only where a diagnostic is reported, which every test here compares.
    location_only = {"filename"}
    missing = written - dumped - parser_rewrites - location_only
    assert not missing, f"checker fields missing from the dump: {sorted(missing)}"
    assert "drop_destructors" not in missing


def test_dump_reports_a_field_it_does_not_know():
    """A new attribute must not vanish: `_describe` prints unknown fields."""
    from mort import mort_ast as A
    node = A.Var("x", 1)
    node.brand_new = "value"
    line, _children = py_check_dump._describe(node)
    assert "brand_new" in line


@pytest.mark.parametrize(
    "path", REPO_FILES, ids=[p.relative_to(ROOT).as_posix() for p in REPO_FILES])
def test_repo_file(expr_exe, tmp_path, path):
    for mode in MODES:
        compare(expr_exe, path, mode, tmp_path)


@pytest.mark.parametrize("name", sorted(EXPR_CASES))
def test_expression(expr_exe, tmp_path, name):
    assert_source_agrees(expr_exe, tmp_path, full_body(EXPR_CASES[name]))


@pytest.mark.parametrize("name", sorted(FN_ONLY_CASES))
@pytest.mark.parametrize("mode", MODES)
def test_function_level(expr_exe, tmp_path, name, mode):
    assert_source_agrees(expr_exe, tmp_path, FN_ONLY_CASES[name] + "\n" + OK_MAIN, mode=mode)


@pytest.mark.parametrize("name", sorted(BODY_CASES))
def test_t4_bodies_are_fully_compared(expr_exe, tmp_path, name):
    source = f"{BODY_PRELUDE}\nfn main() -> int {{\n{BODY_CASES[name]}\nreturn 0;\n}}\n"
    assert_source_agrees(expr_exe, tmp_path, source)


@pytest.mark.parametrize("name", sorted(DECL_CASES))
def test_t4_declarations_are_fully_compared(expr_exe, tmp_path, name):
    assert_source_agrees(expr_exe, tmp_path, DECL_CASES[name])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", sorted(TEST_BLOCK_CASES))
def test_t4_test_blocks_are_fully_compared(expr_exe, tmp_path, name, mode):
    assert_source_agrees(expr_exe, tmp_path, TEST_BLOCK_CASES[name], mode=mode)


@pytest.mark.parametrize("name", sorted(EXPR_CASES)[::7])
def test_expression_freestanding(expr_exe, tmp_path, name):
    assert_source_agrees(expr_exe, tmp_path, full_body(EXPR_CASES[name]), mode="freestanding")


# ----- the generated corpus -----

SPECS = gen.default_corpus() + gen.full_corpus()


@pytest.mark.parametrize("spec", SPECS, ids=[spec.name for spec in SPECS])
def test_generated_program(expr_exe, tmp_path, spec):
    program = gen.generate(spec)
    root = gen.write_program(program, tmp_path / "program")
    for mode in MODES:
        compare(expr_exe, root, mode, tmp_path)


def test_stubbed_kernel(expr_exe, tmp_path):
    path = corpus.write_stubbed_kernel(tmp_path)
    assert corpus.line_count(path) >= corpus.MIN_KERNEL_LINES
    assert compare(expr_exe, path, "freestanding", tmp_path) == "ok"


def test_selfhost_sources_check_as_themselves(expr_exe, tmp_path):
    """The compiler's own sources are the largest real programs here."""
    roots = sorted(path for path in HERE.glob("*.mx") if path.name.endswith("_main.mx"))
    assert len(roots) >= 5
    outcomes = {compare(expr_exe, path, "hosted", tmp_path) for path in roots}
    assert outcomes == {"ok"}


# ----- long chains -----

def test_deep_left_chain_folds(expr_exe, tmp_path):
    chain = " + 1" * 4000
    source = f"fn main() -> int {{\nlet x: u16 = 1{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "ok"


def test_deep_chain_reports_its_overflow(expr_exe, tmp_path):
    chain = " + 100" * 4000
    source = f"fn main() -> int {{\nlet x: u8 = 1{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "error"


def test_deep_shift_chain_folds_exactly(expr_exe, tmp_path):
    chain = " << 1" * 2000
    source = f"fn main() -> int {{\nlet x: u32 = 1{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "error"


def test_deep_cast_chain(expr_exe, tmp_path):
    chain = " as u32" * 3000
    source = f"fn main() -> int {{\nlet x = 1{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "ok"


def test_deep_index_chain(expr_exe, tmp_path):
    stars = "*" * 900
    chain = "[0]" * 899
    source = f"fn main() -> int {{\nlet p: {stars}u8 = null;\nlet x = p{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "ok"


def test_deep_compound_chain_in_right_side(expr_exe, tmp_path):
    chain = " + 1" * 3000
    source = f"fn main() -> int {{\nlet x = 1;\nx += 1{chain};\nreturn 0;\n}}\n"
    assert assert_source_agrees(expr_exe, tmp_path, source) == "ok"


def test_very_deep_folding_chain_is_linear(expr_exe, tmp_path):
    """A 300,000 operand constant chain, beyond what Python runs in reasonable
    time: the Mort checker must finish and fold it without using the C stack."""
    operands = 300_000
    path = tmp_path / "input.mx"
    path.write_text(
        "fn main() -> int {\nlet x = 1" + " + 1" * (operands - 1)
        + ";\nreturn 0;\n}\n", encoding="utf-8")
    checked = tmp_path / "checked.txt"
    code, stderr = run_mort_bounded(
        expr_exe, f"MODE hosted\nFILE {path}\n", tmp_path, extra=[checked])
    assert code == 0, stderr
    assert f"const_val={operands}" in checked.read_text(encoding="ascii")


def test_deepest_nesting_the_parser_allows_does_not_overflow_the_stack(expr_exe, tmp_path):
    """Mixed expression nesting at the parser's limit, on the checker's
    largest recursive frames (calls with array and tuple arguments)."""
    depth = 200
    expr = "1"
    for _ in range(depth):
        expr = f"id2(({expr}, 1).0)"
    source = "fn id2<T>(a: T) -> T { return a; }\n" + (
        f"fn main() -> int {{\nlet x: i64 = {expr};\nreturn 0;\n}}\n")
    outcome = assert_source_agrees(expr_exe, tmp_path, source)
    assert outcome == "ok", outcome


def test_nested_parens_at_the_limit(expr_exe, tmp_path):
    depth = 300
    source = "fn main() -> int {\nlet x = " + "(" * depth + "1" + ")" * depth + ";\nreturn 0;\n}\n"
    assert_source_agrees(expr_exe, tmp_path, source)


# ----- hostile programs: limits of fix/frontend-crashes, Mort only -----

def assert_rejected_within_limits(exe, tmp_path, source, expected_tail):
    path = tmp_path / "input.mx"
    path.write_text(source, encoding="utf-8")
    code, stderr = run_mort_bounded(
        exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1, stderr
    assert stderr.decode("utf-8").endswith(expected_tail), stderr


GEN_NESTING = "has a type argument nested more than 256 levels deep"


def test_polymorphic_recursion_is_bounded(expr_exe, tmp_path):
    assert_rejected_within_limits(
        expr_exe, tmp_path,
        "fn f<T>(x: *T) { let y = &x; f(y); }\nfn main() -> int { let a = 1; f(&a); return 0; }\n",
        f"generic instantiation of 'f' {GEN_NESTING}")


def test_doubling_polymorphic_recursion_is_bounded(expr_exe, tmp_path):
    assert_rejected_within_limits(
        expr_exe, tmp_path,
        "fn f<T>(x: T) { f((x, x)); }\nfn main() -> int { f(1); return 0; }\n",
        "generic instantiation of 'f' has a type argument longer than 4096 characters")


def test_too_many_function_instances_are_rejected(expr_exe, tmp_path):
    count = 16385
    decls = "".join(f"struct T{i} {{ }}\n" for i in range(count))
    calls = "".join(f"f<T{i}>(T{i} {{ }});\n" for i in range(count))
    assert_rejected_within_limits(
        expr_exe, tmp_path,
        f"fn f<T>(x: T) {{ }}\n{decls}fn main() -> int {{\n{calls}return 0;\n}}\n",
        "generic instantiation of 'f' exceeds the limit of 16384 in one program")


def test_instantiation_work_budget(expr_exe, tmp_path):
    statements = "let a = 1;\n" * 8000
    calls = "".join(f"f<T{i}>(T{i} {{ }});\n" for i in range(300))
    decls = "".join(f"struct T{i} {{ }}\n" for i in range(300))
    assert_rejected_within_limits(
        expr_exe, tmp_path,
        f"fn f<T>(x: T) {{ {statements} }}\n{decls}fn main() -> int {{\n{calls}return 0;\n}}\n",
        "exceeds the limit of 1000000 work units in one program")


def test_constant_width_is_bounded(expr_exe, tmp_path):
    chain = " << 64" * 10000
    assert_rejected_within_limits(
        expr_exe, tmp_path,
        f"fn main() -> int {{\nlet x = 1{chain};\nreturn 0;\n}}\n",
        f":2: error: constant expression needs more than {2 ** 18} bits")


def test_retained_constants_are_bounded(expr_exe, tmp_path):
    """A huge value carried along a chain would otherwise store a huge value at
    every node."""
    literal = "9" * 4000
    chain = " + 1" * 300000
    path = tmp_path / "input.mx"
    path.write_text(
        f"fn main() -> int {{\nlet x = {literal}{chain};\nreturn 0;\n}}\n",
        encoding="utf-8")
    code, stderr = run_mort_bounded(
        expr_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1, stderr
    assert b"exceeds the limit of 1000000 work units" in stderr


def test_large_program_with_big_literals_stays_in_budget(expr_exe, tmp_path):
    lines = "".join(f"let a{i}: u8 = 1 + 2;\n" for i in range(50000))
    path = tmp_path / "input.mx"
    path.write_text(f"fn main() -> int {{\n{lines}return 0;\n}}\n", encoding="utf-8")
    code, stderr = run_mort_bounded(
        expr_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr


# ----- mutation, determinism, gate -----

def _mutable_sources():
    usable = []
    for path in REPO_FILES:
        text = path.read_text(encoding="utf-8")
        relative = any(
            line.strip().startswith("import ")
            and not line.strip().startswith("import std")
            for line in text.splitlines())
        if not relative and len(text) < 6000:
            usable.append(text)
    return usable


def _mutate(rng, source):
    """Line-level and token-level mistakes, a few per mutant."""
    lines = source.split("\n")
    for _ in range(rng.randint(1, 3)):
        index = rng.randrange(len(lines))
        op = rng.randrange(6)
        if op == 0:
            del lines[index]
            if not lines:
                lines = [""]
        elif op == 1:
            lines.insert(index, lines[index])
        elif op == 2:
            other = rng.randrange(len(lines))
            lines[index], lines[other] = lines[other], lines[index]
        else:
            words = lines[index].split(" ")
            if words:
                spot = rng.randrange(len(words))
                words[spot] = rng.choice(
                    ("u8", "i64", "true", "1", "300", "null", "x", "bool", "as", "move"))
                lines[index] = " ".join(words)
    return "\n".join(lines)


def test_seeded_mutations_agree(expr_exe, tmp_path):
    import random
    rng = random.Random(0x5435)
    sources = _mutable_sources()
    assert len(sources) > 20
    counts = {"ok": 0, "error": 0, "parse": 0, "load": 0}
    for _ in range(250):
        source = _mutate(rng, rng.choice(sources))
        path = tmp_path / "mutant.mx"
        path.write_bytes(source.encode("utf-8"))
        counts[compare(expr_exe, path, rng.choice(MODES), tmp_path)] += 1
    assert counts["error"] > 40, counts
    assert counts["ok"] > 30, counts


def test_output_is_deterministic(expr_exe, tmp_path):
    path = tmp_path / "input.mx"
    path.write_text(full_body("let a = id2(1); let r = make_r(); take_r(move r);"), encoding="utf-8")
    expected = py_check_dump.run_reference(path, "hosted", exact=True)
    first = run_mort(expr_exe, expected.manifest, tmp_path)
    second = run_mort(expr_exe, expected.manifest, tmp_path)
    assert first == second
    assert first[0] == 0


def test_generated_c_of_the_checker_is_stable(tmp_path):
    env = dict(os.environ)
    outputs = set()
    for seed in ("0", "1", "12345"):
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, str(ROOT / "mortc.py"), str(HERE / "check_main.mx"),
             "--emit-c"], capture_output=True, env=env)
        assert result.returncode == 0, result.stderr
        outputs.add(result.stdout)
    assert len(outputs) == 1


def test_subset_gate_covers_the_new_files():
    result = subprocess.run(
        [sys.executable, str(HERE / "tools" / "check_subset.py")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
