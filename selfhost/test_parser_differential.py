"""Differential test: selfhost/parser.mx must agree with mort/parser.py.

Run from the repository root:

    python -m pytest selfhost/test_parser_differential.py -q

Every expected value comes from running the Python parser; see py_ast_dump.py
for the dump format and for how the nesting limit is applied to the reference.
"""
import collections
import functools
import os
import random
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import mortc  # noqa: E402
import py_ast_dump  # noqa: E402
from mort import mort_ast as A  # noqa: E402
from mort.errors import MortError  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.parser import Parser  # noqa: E402
from test_lexer_differential import repo_mx_files  # noqa: E402

REPO_FILES = repo_mx_files()
MAX_NESTING = py_ast_dump.max_nesting()


@pytest.fixture(scope="session")
def parse_exe(tmp_path_factory):
    build = tmp_path_factory.mktemp("selfhost-parse-build")
    exe = build / ("parse_main.exe" if os.name == "nt" else "parse_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "parse_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, (
        f"compiling parse_main.mx failed:\n{result.stdout}\n{result.stderr}")
    assert exe.exists()
    return exe


def run_mort_parser(exe, source_path, out_path, timeout=120):
    """Run the Mort parser; returns (exit code, stderr bytes, dump or None)."""
    if out_path.exists():
        out_path.unlink()
    result = subprocess.run(
        [str(exe), str(source_path), str(out_path)],
        capture_output=True, timeout=timeout)
    dump = out_path.read_bytes() if out_path.exists() else None
    return result.returncode, result.stderr, dump


def first_difference(expected, actual):
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for index, (want, got) in enumerate(zip(expected_lines, actual_lines)):
        if want != got:
            return f"line {index + 1}\n  python: {want}\n  mort:   {got}"
    return (f"line counts differ: python {len(expected_lines)}, "
            f"mort {len(actual_lines)}")


def normalized(stderr):
    return stderr.replace(b"\r\n", b"\n").rstrip(b"\n")


def assert_agrees(exe, source_path, out_path):
    expected = py_ast_dump.dump_file(source_path)
    code, stderr, dump = run_mort_parser(exe, source_path, out_path)
    if expected.dump is None:
        assert code == 1, (
            f"{source_path}: expected exit 1, got {code}: {stderr!r}\n"
            f"python said: {expected.diagnostic!r}")
        assert normalized(stderr) == expected.diagnostic, source_path
        assert dump is None, f"{source_path}: a dump was written for bad input"
        return expected
    assert code == 0, f"{source_path}: expected exit 0, got {code}: {stderr!r}"
    assert stderr == b"", source_path
    assert dump is not None, f"{source_path}: no dump written"
    text = dump.decode("ascii")
    assert expected.dump == text, (
        f"{source_path}: {first_difference(expected.dump, text)}")
    return expected


def assert_source_agrees(exe, tmp_path, source):
    path = tmp_path / "input.mx"
    path.write_bytes(source)
    return assert_agrees(exe, path, tmp_path / "out.txt")


def test_corpus_is_not_empty():
    assert len(REPO_FILES) >= 100


@pytest.mark.parametrize(
    "path", REPO_FILES, ids=[p.relative_to(ROOT).as_posix() for p in REPO_FILES])
def test_repo_file(parse_exe, tmp_path, path):
    assert_agrees(parse_exe, path, tmp_path / "out.txt")


def fragment(body):
    return f"fn f() {{\n{body}\n}}\n".encode()


def type_case(text):
    return f"let x: {text} = 0;".encode()


EDGE_CASES = {
    # --- call name positions
    "call_name_pos_split_line": fragment("    g\n  (1);"),
    "call_name_pos_parenthesized": fragment("  (g)(1);"),
    "call_name_pos_qualified": fragment("    a.b.c(1);"),
    "call_name_pos_generic_split": fragment("  g\n<u8>\n(1);"),
    "call_name_pos_nested": fragment("g(\n  h(\n    k()));"),
    "call_name_pos_after_non_ascii": fragment("/* é世 */ g();"),
    # --- top level
    "empty": b"",
    "comment_only": b"// nothing\n/* here */",
    "module": b"module a.b.c;",
    "module_then_decl": b"module a;\nfn f() {}",
    "module_second": b"module a;\nmodule b;",
    "module_after_fn": b"fn f() {}\nmodule a;",
    "module_after_import": b"import a;\nmodule b;",
    "module_no_name": b"module ;",
    "module_no_semi": b"module a",
    "module_keyword_name": b"module fn;",
    "module_trailing_dot": b"module a.;",
    "import_plain": b"import a;",
    "import_dotted": b"import a.b.c;",
    "import_alias": b"import a.b as c;",
    "import_alias_keyword": b"import a as fn;",
    "import_no_semi": b"import a",
    "import_number": b"import 1;",
    "pub_fn": b"pub fn f() {}",
    "pub_struct": b"pub struct S {}",
    "pub_eof": b"pub",
    "resource_struct": b"resource struct R { x: u8 }",
    "resource_fn": b"resource fn f() {}",
    "resource_eof": b"resource",
    "garbage_top": b"42",
    "garbage_top_ident": b"hello",
    "top_semi": b";",
    "top_brace": b"}",
    # --- structs
    "struct_empty": b"struct S {}",
    "struct_fields": b"struct S { a: u8, b: *S, c: [u8; 4] }",
    "struct_trailing_comma": b"struct S { a: u8, }",
    "struct_generic": b"struct S<T, U> { a: T, b: U }",
    "struct_generic_empty": b"struct S<> {}",
    "struct_generic_unclosed": b"struct S<T {}",
    "struct_no_comma": b"struct S { a: u8 b: u8 }",
    "struct_no_colon": b"struct S { a u8 }",
    "struct_no_name": b"struct {}",
    "struct_no_brace": b"struct S",
    "struct_field_keyword": b"struct S { fn: u8 }",
    "struct_eof_in_fields": b"struct S { a: u8,",
    "struct_bad_type": b"struct S { a: 1 }",
    # --- enums
    "enum_empty": b"enum E {}",
    "enum_units": b"enum E { A, B, C }",
    "enum_trailing_comma": b"enum E { A, }",
    "enum_payload": b"enum E { A(u8), B(u8, *u8), C }",
    "enum_payload_tuple": b"enum E { A((u8, u8)) }",
    "enum_generic": b"enum E<T> { A(T), B }",
    "enum_empty_payload": b"enum E { A() }",
    "enum_no_comma": b"enum E { A B }",
    "enum_unclosed_payload": b"enum E { A(u8 }",
    "enum_no_name": b"enum { A }",
    "enum_generic_unclosed": b"enum E<T { A }",
    # --- aliases, tests, globals, externs
    "alias": b"type A = *u8;",
    "alias_no_eq": b"type A *u8;",
    "alias_no_semi": b"type A = u8",
    "alias_no_name": b"type = u8;",
    "test_decl": b'test "name" { let x = 1; }',
    "test_escapes": b'test "a\\"b\\n" { }',
    "test_no_name": b"test { }",
    "test_no_block": b'test "t"',
    "global_let": b"let x: u8 = 1;",
    "global_const": b"const X: u8 = 1;",
    "global_no_type": b"let x = 1;",
    "global_array": b"let a: [u8; 3] = [1, 2, 3];",
    "extern_fn": b"extern fn f(a: u8, b: *u8) -> c_int;",
    "extern_void": b"extern fn f();",
    "extern_null_name": b"extern fn null();",
    "extern_with_body": b"extern fn f() {}",
    "extern_no_fn": b"extern f();",
    "extern_bad_name": b"extern fn 1();",
    "extern_no_semi": b"extern fn f()",
    # --- functions
    "fn_plain": b"fn f() {}",
    "fn_params": b"fn f(a: u8, b: *const u8) -> *u8 { return b; }",
    "fn_generic": b"fn f<T, U>(a: T) -> U { }",
    "fn_null_name": b"fn null() {}",
    "fn_keyword_name": b"fn let() {}",
    "fn_number_name": b"fn 1() {}",
    "fn_eof_name": b"fn",
    "fn_no_paren": b"fn f {}",
    "fn_trailing_comma_param": b"fn f(a: u8,) {}",
    "fn_param_no_colon": b"fn f(a u8) {}",
    "fn_param_keyword": b"fn f(if: u8) {}",
    "fn_no_body": b"fn f();",
    "fn_arrow_no_type": b"fn f() -> {}",
    "fn_generic_no_close": b"fn f<T() {}",
    "fn_eof_in_block": b"fn f() {",
    # --- types
    "type_int": type_case("int"),
    "type_bool": type_case("bool"),
    "type_void": type_case("void"),
    "type_fixed": b"\n".join(type_case(t) for t in (
        "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "c_char",
        "c_uchar", "c_short", "c_ushort", "c_int", "c_uint", "c_long",
        "c_ulong", "c_size", "f32", "f64")),
    "type_fixed_generic_like": type_case("u8<T>"),
    "type_nominal": type_case("Foo"),
    "type_pointer": type_case("*u8"),
    "type_pointer_const": type_case("*const u8"),
    "type_pointer_pointer": type_case("**const *u8"),
    "type_slice": type_case("[]u8"),
    "type_slice_const": type_case("[]const u8"),
    "type_array": type_case("[u8; 4]"),
    "type_array_hex": type_case("[u8; 0x10]"),
    "type_array_nested": type_case("[[u8; 2]; 3]"),
    "type_array_name_size": type_case("[u8; N]"),
    "type_array_no_size": type_case("[u8;]"),
    "type_array_no_semi": type_case("[u8 4]"),
    "type_array_unclosed": b"let x: [u8; 4 = 0;",
    "type_generic": type_case("Foo<u8>"),
    "type_generic_two": type_case("Foo<u8, Bar>"),
    "type_generic_nested_shr": type_case("Foo<Bar<u8>>"),
    "type_generic_triple": type_case("A<B<C<u8>>>"),
    "type_generic_quad": type_case("A<B<C<D<u8>>>>"),
    "type_generic_ge_split": b"let x: Foo<u8>= 0;",
    "type_generic_nested_ge": b"let x: A<B<u8>>= 0;",
    "type_generic_triple_ge": b"let x: A<B<C<u8>>>= 0;",
    "type_generic_shr_assign": b"let x: A<B<u8>>>= 0;",
    "type_generic_empty": type_case("Foo<>"),
    "type_generic_unclosed": b"let x: Foo<u8 = 0;",
    "type_generic_trailing_comma": type_case("Foo<u8,>"),
    "type_fn": type_case("fn(u8, *u8) -> bool"),
    "type_fn_no_params": type_case("fn() -> void"),
    "type_fn_nested": type_case("fn(fn(u8) -> u8) -> fn() -> u8"),
    "type_fn_no_arrow": type_case("fn(u8)"),
    "type_fn_no_paren": type_case("fn u8"),
    "type_fn_trailing_comma": type_case("fn(u8,) -> u8"),
    "type_tuple": type_case("(u8, bool)"),
    "type_tuple_three": type_case("(u8, bool, *u8)"),
    "type_tuple_trailing": type_case("(u8, bool,)"),
    "type_tuple_nested": type_case("((u8, u8), u8)"),
    "type_tuple_one": type_case("(u8)"),
    "type_tuple_one_comma": type_case("(u8,)"),
    "type_tuple_empty": type_case("()"),
    "type_tuple_unclosed": b"let x: (u8, bool = 0;",
    "type_number": type_case("1"),
    "type_keyword": type_case("if"),
    "type_string": b'let x: "s" = 0;',
    "type_eof": b"let x:",
    "type_generic_of_everything": type_case(
        "Foo<*u8, [u8; 2], []const u8, fn(u8) -> u8, (u8, u8), Bar<u8>>"),
    # --- let, return, simple statements
    "let_no_eq": fragment("let x;"),
    "let_no_value": fragment("let x = ;"),
    "let_no_name": fragment("let = 1;"),
    "let_no_semi": fragment("let x = 1"),
    "let_type_no_eq": fragment("let x: u8;"),
    "const_local": fragment("const x: u8 = 1;"),
    "return_void": fragment("return;"),
    "return_value": fragment("return 1 + 2;"),
    "return_no_semi": fragment("return 1"),
    "break_stmt": fragment("break;"),
    "break_no_semi": fragment("break"),
    "continue_stmt": fragment("continue;"),
    "continue_no_semi": fragment("continue"),
    "defer_stmt": fragment("defer free(p);"),
    "defer_no_semi": fragment("defer free(p)"),
    "block_stmt": fragment("{ { let a = 1; } { } }"),
    "asm_stmt": fragment('asm("nop");'),
    "asm_escapes": fragment('asm("a\\tb\\"c");'),
    "asm_not_string": fragment("asm(nop);"),
    "asm_no_paren": fragment('asm "nop";'),
    "asm_no_close": fragment('asm("nop";'),
    "asm_no_semi": fragment('asm("nop")'),
    "stray_semi": fragment(";"),
    # --- if, while, loop, for
    "if_plain": fragment("if x { }"),
    "if_else": fragment("if x { } else { }"),
    "if_else_if": fragment("if x { } else if y { } else if z { } else { }"),
    "if_no_block": fragment("if x y;"),
    "if_else_stmt": fragment("if x { } else y;"),
    "if_no_cond": fragment("if { }"),
    "if_struct_lit_banned": fragment("if p { }"),
    "if_struct_lit_paren": fragment("if (P { a: 1 }).a { }"),
    "if_struct_lit_in_call": fragment("if f(P { a: 1 }) { }"),
    "if_struct_lit_in_index": fragment("if a[P { a: 1 }] { }"),
    "if_nested_paren_restore": fragment("if (a) { } if b { }"),
    "while_plain": fragment("while x { break; }"),
    "while_no_block": fragment("while x;"),
    "while_struct_lit": fragment("while p { }"),
    "loop_plain": fragment("loop { break; }"),
    "loop_no_block": fragment("loop x;"),
    "for_plain": fragment("for i in 0..10 { }"),
    "for_inclusive": fragment("for i in 0..=10 { }"),
    "for_typed": fragment("for i: u64 in 0..n { }"),
    "for_exprs": fragment("for i in a + 1..f(b) * 2 { }"),
    "for_struct_banned": fragment("for i in a..b { }"),
    "for_paren_struct": fragment("for i in (P { a: 1 }).a..2 { }"),
    "for_no_in": fragment("for i 0..1 { }"),
    "for_no_range": fragment("for i in 5 { }"),
    "for_no_var": fragment("for in 0..1 { }"),
    "for_no_block": fragment("for i in 0..1;"),
    "for_bad_type": fragment("for i: 1 in 0..1 { }"),
    # --- match
    "match_plain": fragment("match x { A => { } B => { } }"),
    "match_commas": fragment("match x { A => { }, B => { }, }"),
    "match_wild": fragment("match x { _ => { } }"),
    "match_enum": fragment("match x { E.A => { return; } E.B(y) => { } _ => { } }"),
    "match_payload": fragment("match x { E.A(a, b) => { } }"),
    "match_int": fragment("match x { 1 => { } 2 => { } }"),
    "match_empty": fragment("match x { }"),
    "match_no_arrow": fragment("match x { A { } }"),
    "match_no_block": fragment("match x { A => 1 }"),
    "match_no_brace": fragment("match x"),
    "match_unclosed": fragment("match x { A => { }"),
    "match_struct_lit": fragment("match P { a: 1 } { }"),
    "match_wild_call": fragment("match x { _ (y) => { } }"),
    # --- assignment
    "assign_ops": fragment(
        "a = 1; a += 1; a -= 1; a *= 1; a /= 1; a %= 1; a &= 1; a |= 1; "
        "a ^= 1; a <<= 1; a >>= 1;"),
    "assign_targets": fragment("*p = 1; s.x = 1; a[0] = 1; s.a.b = 1; (*p).x = 1;"),
    "assign_paren_var": fragment("(a) = 1;"),
    "assign_call_target": fragment("f() = 1;"),
    "assign_lit_target": fragment("1 = 2;"),
    "assign_binary_target": fragment("a + b = 2;"),
    "assign_unary_minus": fragment("-a = 2;"),
    "assign_no_value": fragment("a = ;"),
    "assign_no_semi": fragment("a = 1"),
    "assign_multiline": fragment("a\n+\nb\n=\n2;"),
    "expr_no_semi": fragment("a"),
    # --- operators
    "prec_all": fragment(
        "let v = a || b && c | d ^ e & f == g != h < i > j <= k >= l << m >> n "
        "+ o - p * q / r % s;"),
    "prec_reversed": fragment(
        "let v = s % r / q * p - o + n >> m << l >= k <= j > i < h != g == f & e "
        "^ d | c && b || a;"),
    "left_assoc": fragment("let v = a - b - c; let w = a / b / c;"),
    "cast_binds_tight": fragment("let v = a + b as u8 * c as *u8;"),
    "cast_chain": fragment("let v = a as u8 as u32 as u64;"),
    "cast_unary": fragment("let v = -a as u8; let w = *p as u8; let x = !b as u8;"),
    "cast_no_type": fragment("let v = a as ;"),
    "cast_generic": fragment("let v = a as Foo<u8>;"),
    "cast_then_lt": fragment("let v = a as u8 < b;"),
    "unary_all": fragment("let v = !a; let w = -a; let x = ~a; let y = &a; let z = *a;"),
    "unary_stack": fragment("let v = !-~&*a;"),
    "unary_double_amp": fragment("let v = &&a;"),
    "unary_plus": fragment("let v = +a;"),
    "try_expr": fragment("let v = try f();"),
    "try_stack": fragment("let v = try try f(); let w = -try f();"),
    "move_expr": fragment("let v = move a;"),
    "move_stack": fragment("let v = move -a;"),
    "binary_multiline_line": fragment("let v = a\n+ b\n* c;"),
    "binary_line_is_the_operator": fragment("let v = a +\nb;"),
    "cast_line_is_the_as": fragment("let v = a\nas u8;"),
    "shr_split_leaves_one_angle": b"let x: A<u8>> = 0;",
    "binary_missing_rhs": fragment("let v = a + ;"),
    "binary_missing_lhs": fragment("let v = * ;"),
    "binary_double_op": fragment("let v = a + * b;"),
    "comparison_chain": fragment("let v = a < b < c > d;"),
    "shift_in_expr": fragment("let v = a >> b >> c;"),
    # --- primaries
    "lit_int_forms": fragment("let v = 0x10 + 0b11 + 0o7 + 1_000;"),
    "lit_huge_int": fragment("let v = 123456789012345678901234567890;"),
    "lit_float_forms": fragment("let v = 1.5 + 2e3 + 0.1;"),
    "lit_char_forms": fragment("let v = 'a' + '\\n' + '\\x41' + '\\'';"),
    "lit_string_forms": fragment('let v = "a\\n\\"b";'),
    "lit_string_unicode": fragment('let v = "héllo € \U0001f600";'),
    "lit_bools": fragment("let v = true; let w = false;"),
    "lit_null": fragment("let v = null;"),
    "null_call": fragment("let v = null();"),
    "null_generic_call": fragment("let v = null<u8>();"),
    "null_generic_no_call": fragment("let v = null<u8;"),
    "null_field": fragment("let v = null.x;"),
    "paren": fragment("let v = (1 + 2) * 3;"),
    "paren_unclosed": fragment("let v = (1 + 2;"),
    "paren_empty": fragment("let v = ();"),
    "tuple": fragment("let v = (1, 2);"),
    "tuple_three": fragment("let v = (1, 2, 3);"),
    "tuple_trailing": fragment("let v = (1, 2,);"),
    "tuple_one_comma": fragment("let v = (1,);"),
    "tuple_unclosed": fragment("let v = (1, 2;"),
    "tuple_multiline": fragment("let v = (\n1,\n2\n);"),
    "array": fragment("let v = [1, 2, 3];"),
    "array_one": fragment("let v = [1];"),
    "array_trailing": fragment("let v = [1, 2,];"),
    "array_empty": fragment("let v = [];"),
    "array_repeat": fragment("let v = [0; 8];"),
    "array_repeat_hex": fragment("let v = [a + 1; 0x10];"),
    "array_repeat_name": fragment("let v = [0; n];"),
    "array_repeat_no_close": fragment("let v = [0; 8;"),
    "array_unclosed": fragment("let v = [1, 2;"),
    "array_missing_comma": fragment("let v = [1 2];"),
    "array_of_arrays": fragment("let v = [[1, 2], [3, 4]];"),
    # --- postfix
    "call_plain": fragment("f();"),
    "call_args": fragment("f(1, a + 2, g(3));"),
    "call_trailing_comma": fragment("f(1,);"),
    "call_unclosed": fragment("f(1;"),
    "call_qualified": fragment("a.b.c(1);"),
    "call_on_call": fragment("f()();"),
    "call_on_paren": fragment("(f)();"),
    "call_on_index": fragment("a[0]();"),
    "call_on_literal": fragment("1();"),
    "call_on_method_result": fragment("a.b().c();"),
    "call_multiline_line": fragment("f\n(\n1\n);"),
    "field": fragment("let v = a.b.c;"),
    "field_tuple_index": fragment("let v = a.0.1;"),
    "field_tuple_zeros": fragment("let v = a.007;"),
    "field_keyword": fragment("let v = a.if;"),
    "field_missing": fragment("let v = a.;"),
    "field_on_literal": fragment("let v = 1 .b;"),
    "field_multiline_line": fragment("let v = a\n.\nb;"),
    "index": fragment("let v = a[0][b + 1];"),
    "index_unclosed": fragment("let v = a[0;"),
    "index_empty": fragment("let v = a[];"),
    "index_multiline_line": fragment("let v = a\n[\n0\n];"),
    "postfix_mix": fragment("let v = a.b[0].c.d(1)[2];"),
    # --- struct literals
    "struct_lit": fragment("let v = P { a: 1, b: 2 };"),
    "struct_lit_empty": fragment("let v = P { };"),
    "struct_lit_trailing": fragment("let v = P { a: 1, };"),
    "struct_lit_generic": fragment("let v = P<u8> { a: 1 };"),
    "struct_lit_generic_nested": fragment("let v = P<Q<u8>> { a: 1 };"),
    "struct_lit_nested": fragment("let v = P { a: Q { b: 1 } };"),
    "struct_lit_no_colon": fragment("let v = P { a 1 };"),
    "struct_lit_no_comma": fragment("let v = P { a: 1 b: 2 };"),
    "struct_lit_keyword_field": fragment("let v = P { if: 1 };"),
    "struct_lit_unclosed": fragment("let v = P { a: 1"),
    "struct_lit_multiline_line": fragment("let v = P\n{\na: 1\n};"),
    "struct_lit_in_condition_field": fragment("if P { a: 1 }.a { }"),
    "generic_struct_lit_in_cond": fragment("if a<b> { c; }"),
    # --- generic call lookahead
    "generic_call": fragment("f<u8>(1);"),
    "generic_call_nested": fragment("f<Vec<u8>>(1);"),
    "generic_call_two": fragment("f<u8, u16>(1, 2);"),
    "generic_call_qualified": fragment("a.b<u8>(1);"),
    "generic_call_type_shapes": fragment("f<*u8, [u8; 2], fn() -> u8, (u8, u8)>();"),
    "generic_call_unclosed_args": fragment("f<u8>(1;"),
    "generic_call_on_call": fragment("f()<u8>();"),
    "generic_call_on_index": fragment("a[0]<u8>();"),
    "generic_call_on_literal": fragment("1<u8>();"),
    "lt_is_comparison": fragment("let v = a < b;"),
    "lt_then_gt_no_paren": fragment("let v = a < b > c;"),
    "lt_then_gt_paren": fragment("let v = a < b > (c);"),
    "lt_semicolon_stops": fragment("let v = a < b; let w = c > (d);"),
    "lt_shr_mismatch": fragment("let v = a < b >> (c);"),
    "lt_shr_balanced": fragment("let v = a < b < c >> (d);"),
    "lt_shr_deep": fragment("let v = a < b < c < d >> (e);"),
    "lt_ge_no_split": fragment("let v = a<b>=(c);"),
    "lt_bad_type_inside": fragment("let v = a < 1 > (2);"),
    "generic_qualifier": fragment("let v = Foo<u8>.new(1);"),
    "generic_qualifier_nested": fragment("let v = Foo<Bar<u8>>.new(1);"),
    "generic_qualifier_field": fragment("let v = Foo<u8>.x;"),
    "generic_qualifier_triple": fragment("let v = A<B<C<u8>>>.x;"),
    "generic_qualifier_shr_short": fragment("let v = A<u8>>.x;"),
    "generic_struct_shr_ignored": fragment("let v = A<B<u8>> { a: 1 };"),
    "generic_struct_mismatch": fragment("let v = A<u8 { a: 1 };"),
    "generic_multiline_positions": fragment("let v = f<\nu8\n>(\n1\n);"),
    "type_close_token_reported": fragment("f<u8 1>(1);"),
    "shr_split_then_error": b"let x: A<B<u8>> 1;",
    "ge_split_reports_equals": b"let x: A<u8>= ;",
    "ge_split_then_semicolon": b"let x: A<u8>=;",
    # --- diagnostics that print a token value
    "got_ident": fragment("let 1;"),
    "got_keyword": b"fn f(fn) {}",
    "got_string": b'import "a b";',
    "got_string_single_quote": b"import \"it's\";",
    "got_string_double_quote": b'import "say \\"hi\\"";',
    "got_string_both_quotes": b"import \"it's \\\"x\\\"\";",
    "got_string_backslash": b'import "a\\\\b";',
    "got_string_newline_escape": b'import "a\\nb";',
    "got_string_tab": b'import "a\tb";',
    "got_string_control": b'import "a\x01b\x7fc";',
    "got_string_unicode": 'import "héllo € \U0001f600";'.encode(),
    "got_string_nonprintable_unicode": (
        'import "\u0085 ­ ​    \U0010ffff";'.encode()),
    "got_string_backslash_newline": b'import "a\\\nb";',
    "got_int": b"import 42;",
    "got_int_huge": b"import 123456789012345678901234567890;",
    "got_int_hex": b"import 0xff;",
    "got_char": b"import 'a';",
    "got_char_escape": b"import '\\n';",
    "got_float": b"import 1.5;",
    "got_eof": b"import",
    "got_punct": b"import (;",
    "got_shift": b"import <<;",
    "got_arrow": b"import =>;",
    "got_ident_unicode": "import a; café".encode(),
    "unexpected_eof_expr": fragment("let v = "),
    "unexpected_keyword_expr": fragment("let v = fn;"),
    "unexpected_string_expr": b"fn f() { let v = ; }",
    "unexpected_after_lines": b"\n\n\nfn f() {\n\n  let v = ;\n}\n",
    # --- lexical errors win over everything, and carry a column
    "lex_error": b"fn f() { let @ = 1; }",
    "lex_error_late": b"fn f( { }\nlet a = $;",
    "lex_invalid_utf8": b"fn f() { \xff }",
    "lex_unterminated": b'fn f() { let s = "abc',
    # --- newlines
    "crlf": b"fn f() {\r\n  let a = 1;\r\n}\r\n",
    "lone_cr": b"fn f() {\r  let @;\r}",
}


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_edge_case(parse_exe, tmp_path, name):
    assert_source_agrees(parse_exe, tmp_path, EDGE_CASES[name])


# ----- float values in diagnostics: repr() is reproduced exactly -----

FLOAT_TEXTS = [
    "0.0", "1.0", "1.5", "0.1", "0.2", "0.3", "0.30000000000000004", "100.0",
    "1e0", "1e1", "1e15", "1e16", "1e17", "1e22", "1e23", "1.5e16", "1.5e15",
    "9999999999999998.0", "9999999999999999.0", "12345678901234567.0",
    "123456789012345680.0", "0.001", "0.0001", "0.00001", "0.00012345",
    "0.000012345", "1e-3", "1e-4", "1e-5", "1e-7", "1.5e-7", "2.5e-300",
    "5e-324", "4.9e-324", "2.2250738585072014e-308", "2.225073858507201e-308",
    "1.7976931348623157e308", "9007199254740993.0", "9007199254740992.0",
    "4503599627370497.5", "3.141592653589793", "2.718281828459045",
    "1.0000000000000002", "0.9999999999999999", "123.456", "1e100",
    "1.2345678901234567e100", "8.41e21", "2e22", "1_000.5",
    "7.120236347223045e-307",
]


@pytest.mark.parametrize("text", FLOAT_TEXTS)
def test_float_value_in_diagnostic(parse_exe, tmp_path, text):
    result = assert_source_agrees(parse_exe, tmp_path, f"import {text};".encode())
    assert result.dump is None


def test_float_repr_random_doubles(parse_exe, tmp_path):
    rng = random.Random(20261108)
    for _ in range(300):
        bits = rng.getrandbits(64) & ~(1 << 63)
        if (bits >> 52) & 0x7FF == 0x7FF:
            continue
        value = struct.unpack(">d", struct.pack(">Q", bits))[0]
        text = f"{value:.17e}"
        result = assert_source_agrees(
            parse_exe, tmp_path, f"import {text};".encode())
        assert result.dump is None


@pytest.fixture(scope="session")
def float_repr_exe(tmp_path_factory):
    build = tmp_path_factory.mktemp("selfhost-float-build")
    exe = build / ("float_repr_main.exe" if os.name == "nt" else "float_repr_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "float_repr_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


def assert_reprs_match(exe, tmp_path, patterns):
    """Compare the Mort repr of each binary64 bit pattern with Python's."""
    source = tmp_path / "bits.bin"
    source.write_bytes(b"".join(struct.pack("<Q", bits) for bits in patterns))
    out = tmp_path / "reprs.txt"
    subprocess.run([str(exe), str(source), str(out)], check=True, timeout=300)
    actual = out.read_text(encoding="ascii").splitlines()
    expected = [repr(struct.unpack("<d", struct.pack("<Q", bits))[0])
                for bits in patterns]
    assert len(actual) == len(expected)
    wrong = [(hex(bits), want, got) for bits, want, got
             in zip(patterns, expected, actual) if want != got]
    assert not wrong, f"{len(wrong)} of {len(patterns)} differ, first: {wrong[:5]}"


MAX_FINITE_BITS = 0x7FEFFFFFFFFFFFFF


def power_of_two_bits():
    """Every power of two representable as a double: 2^-1074 to 2^1023."""
    subnormal = [1 << shift for shift in range(52)]
    normal = [biased << 52 for biased in range(1, 2047)]
    return subnormal + normal


def test_float_repr_every_power_of_two(float_repr_exe, tmp_path):
    patterns = power_of_two_bits()
    assert len(patterns) == 2098
    assert_reprs_match(float_repr_exe, tmp_path, patterns)


def test_float_repr_neighbours_of_powers_of_two(float_repr_exe, tmp_path):
    patterns = sorted({
        neighbour for bits in power_of_two_bits()
        for neighbour in (bits - 1, bits + 1)
        if 0 <= neighbour <= MAX_FINITE_BITS
    })
    assert_reprs_match(float_repr_exe, tmp_path, patterns)


def test_float_repr_random_bit_patterns(float_repr_exe, tmp_path):
    rng = random.Random(20261108)
    patterns = [rng.randrange(MAX_FINITE_BITS + 1) for _ in range(20_000)]
    assert_reprs_match(float_repr_exe, tmp_path, patterns)


def test_float_repr_short_decimals_and_integers(float_repr_exe, tmp_path):
    rng = random.Random(20261109)
    values = [float(f"{rng.randrange(1, 10**rng.randint(1, 17))}e{rng.randint(-320, 290)}")
              for _ in range(10_000)]
    values += [float(rng.randrange(1, 1 << 63)) for _ in range(5_000)]
    values += [10.0 ** power for power in range(-307, 309)] + [0.0]
    patterns = [struct.unpack("<Q", struct.pack("<d", value))[0]
                for value in values if value != float("inf")]
    assert_reprs_match(float_repr_exe, tmp_path, patterns)


# ----- deep nesting -----
#
# Each shape spends a known number of nesting units per level, so these reach
# the limit exactly: one below it, at it, and one past it.

def wrap(opening, middle, closing, levels):
    return opening * levels + middle + closing * levels


NESTED_SHAPES = {
    # name: (units per level, function from levels to source)
    "paren": (2, lambda n: "fn f() { let v = " + wrap("(", "1", ")", n) + "; }"),
    "unary": (1, lambda n: "fn f() { let v = " + "-" * n + "1; }"),
    "array": (2, lambda n: "fn f() { let v = " + wrap("[", "1", "]", n) + "; }"),
    "call_arg": (2, lambda n: "fn f() { g(" * 1 + wrap("g(", "1", ")", n) + "); }"),
    "index": (2, lambda n: "fn f() { let v = " + wrap("a[", "1", "]", n) + "; }"),
    "struct_lit": (2, lambda n: "fn f() { let v = " + wrap("P { a: ", "1", " }", n) + "; }"),
    "tuple": (2, lambda n: "fn f() { let v = " + wrap("(1, ", "1", ")", n) + "; }"),
    "pointer_type": (1, lambda n: "let x: " + "*" * n + "u8 = 0;"),
    "array_type": (1, lambda n: "let x: " + wrap("[", "u8", "; 1]", n) + " = 0;"),
    "generic_type": (1, lambda n: "let x: " + wrap("A<", "u8", ">", n) + " = 0;"),
    "fn_type": (1, lambda n: "let x: " + wrap("fn(", "u8", ") -> u8", n) + " = 0;"),
    "fn_result_type": (1, lambda n: "let x: " + "fn() -> " * n + "u8 = 0;"),
    "tuple_type": (1, lambda n: "let x: " + wrap("(u8, ", "u8", ")", n) + " = 0;"),
    "block": (1, lambda n: "fn f() " + wrap("{ ", "", "} ", n)),
    "if": (2, lambda n: "fn f() { " + wrap("if a { ", "", "} ", n) + "}"),
    "else_if": (1, lambda n: "fn f() { if a { } " + "else if a { } " * n + "}"),
    "while": (1, lambda n: "fn f() { " + wrap("while a { ", "", "} ", n) + "}"),
    "for": (1, lambda n: "fn f() { " + wrap("for i in 0..1 { ", "", "} ", n) + "}"),
    "loop": (1, lambda n: "fn f() { " + wrap("loop { ", "", "} ", n) + "}"),
    "match": (1, lambda n: "fn f() { " + wrap("match a { _ => { ", "", "} } ", n) + "}"),
    "operator_ladder": (2, lambda n: (
        "fn f() { let v = "
        + wrap("a||a&&a|a^a&a==a<a<<a+a*(", "1", ")", n) + "; }")),
}

@functools.cache
def admitted_levels(name):
    """The deepest nesting of this shape that the reference accepts."""
    units, build = NESTED_SHAPES[name]
    levels = MAX_NESTING // units + 2
    while levels > 0:
        try:
            py_ast_dump.parse_source(build(levels))
        except MortError:
            levels -= 1
            continue
        assert levels >= MAX_NESTING // units - 8, name
        return levels
    raise AssertionError(f"{name}: no depth was accepted")


DEEP_CASES = [
    (name, offset) for name in sorted(NESTED_SHAPES) for offset in (-1, 0, 1)
]


@pytest.mark.parametrize("name,offset", DEEP_CASES)
def test_nesting_at_the_limit(parse_exe, tmp_path, name, offset):
    """Offset 0 is the deepest accepted nesting of this shape; 1 is one more."""
    levels = admitted_levels(name) + offset
    source = NESTED_SHAPES[name][1](levels).encode()
    result = assert_source_agrees(parse_exe, tmp_path, source)
    if offset <= 0:
        assert result.dump is not None
    else:
        assert b"exceeds the compiler safety limit" in result.diagnostic


# Python's `<` lookahead is quadratic, so the ladder gets a smaller multiple.
FAR_PAST_MULTIPLE = {"operator_ladder": 4}


@pytest.mark.parametrize("name", sorted(NESTED_SHAPES))
def test_far_past_the_limit_is_a_diagnostic(parse_exe, tmp_path, name):
    units, build = NESTED_SHAPES[name]
    multiple = FAR_PAST_MULTIPLE.get(name, 50)
    source = build((MAX_NESTING // units) * multiple + 7).encode()
    result = assert_source_agrees(parse_exe, tmp_path, source)
    assert result.dump is None
    assert b"exceeds the compiler safety limit" in result.diagnostic


LONG_CHAINS = {
    "binary": lambda n: "fn f() { let v = 1" + " + 1" * n + "; }",
    "mixed_binary": lambda n: "fn f() { let v = a" + " * b + c" * n + "; }",
    "field": lambda n: "fn f() { let v = a" + ".b" * n + "; }",
    "field_then_call": lambda n: "fn f() { a" + ".b" * n + "(); }",
    "index": lambda n: "fn f() { let v = a" + "[0]" * n + "; }",
    "cast": lambda n: "fn f() { let v = a" + " as u8" * n + "; }",
    "comparison": lambda n: "fn f() { let v = a" + " < b" * n + "; }",
    "statements": lambda n: "fn f() { " + "a;" * n + " }",
    "functions": lambda n: "fn f() { }\n" * n,
    "args": lambda n: "fn f() { g(" + "1, " * n + "1); }",
    "params": lambda n: "fn f(" + ", ".join(f"p{i}: u8" for i in range(n)) + ") { }",
    "fields": lambda n: "struct S { " + "a: u8, " * n + "}",
    "variants": lambda n: "enum E { " + "A, " * n + "}",
    "generic_args": lambda n: "let x: A<" + "u8, " * n + "u8> = 0;",
    "fn_type_params": lambda n: "let x: fn(" + "u8, " * n + "u8) -> u8 = 0;",
    "string_length": lambda n: 'fn f() { let s = "' + "a" * n + '"; }',
    "string_in_diagnostic": lambda n: 'import "' + "a" * n + '";',
}


@pytest.mark.parametrize("name", sorted(LONG_CHAINS))
def test_long_chains_agree(parse_exe, tmp_path, name):
    """Chains build deep trees without recursing; both parsers must take them."""
    size = 300 if name == "comparison" else 4000
    assert_source_agrees(parse_exe, tmp_path, LONG_CHAINS[name](size).encode())


def elapsed(exe, tmp_path, source):
    path = tmp_path / "input.mx"
    path.write_bytes(source)
    out = tmp_path / "out.txt"
    start = time.perf_counter()
    code, stderr, _ = run_mort_parser(exe, path, out, timeout=120)
    took = time.perf_counter() - start
    assert code == 0, stderr
    return took


# Sized for a machine with little memory: the largest run is about 200k tokens.
LINEARITY_SIZES = {
    "binary": 25_000,
    "field": 25_000,
    "field_then_call": 25_000,
    "cast": 25_000,
    "comparison": 25_000,
    "statements": 25_000,
    "functions": 10_000,
    "args": 25_000,
    "generic_args": 25_000,
    "string_length": 250_000,
}


@pytest.mark.parametrize("name", sorted(LINEARITY_SIZES))
def test_time_is_linear_in_input_size(parse_exe, tmp_path, name):
    base = LINEARITY_SIZES[name]
    build = LONG_CHAINS[name]
    small = min(elapsed(parse_exe, tmp_path, build(base).encode())
                for _ in range(2))
    large = elapsed(parse_exe, tmp_path, build(base * 8).encode())
    # Eight times the input; a quadratic parser would take about 64 times as
    # long. The floor keeps timer noise on a near-instant run out of it.
    assert large <= max(small, 0.02) * 24, f"{name}: {small:.3f}s -> {large:.3f}s"


# ----- mutated inputs -----

TOKEN_ALPHABET = [
    b"(", b")", b"{", b"}", b"[", b"]", b"<", b">", b">>", b">=", b">>=", b"<<",
    b",", b";", b":", b".", b"..", b"..=", b"->", b"=>", b"=", b"+=", b"==", b"!=",
    b"+", b"-", b"*", b"/", b"%", b"&", b"&&", b"|", b"||", b"^", b"~", b"!",
    b"as", b"fn", b"let", b"const", b"if", b"else", b"while", b"loop", b"for",
    b"in", b"return", b"break", b"continue", b"match", b"defer", b"try", b"move",
    b"asm", b"struct", b"enum", b"extern", b"import", b"module", b"pub", b"type",
    b"test", b"resource", b"int", b"bool", b"void", b"null", b"true", b"false",
    b"_", b"x", b"Foo", b"u8", b"*const", b"0", b"1", b"0x10", b"1.5", b"'a'",
    b'"s"', b'"it\'s"',
]


def mutate_tokens(rng, data):
    """Token-level edits, which mostly reach the parser instead of the lexer."""
    pieces = [piece for piece in data.split(b" ") if piece != b""]
    for _ in range(rng.randint(1, 8)):
        operation = rng.choice(("insert", "delete", "replace", "dup", "swap"))
        position = rng.randrange(len(pieces) + 1)
        if operation == "insert" or not pieces:
            pieces.insert(position, rng.choice(TOKEN_ALPHABET))
            continue
        position = min(position, len(pieces) - 1)
        if operation == "delete":
            del pieces[position]
        elif operation == "replace":
            pieces[position] = rng.choice(TOKEN_ALPHABET)
        elif operation == "dup":
            pieces[position:position] = pieces[position:position + rng.randint(1, 6)]
        else:
            other = rng.randrange(len(pieces))
            pieces[position], pieces[other] = pieces[other], pieces[position]
    return b" ".join(pieces)


def mutate_bytes(rng, data):
    alphabet = b"(){}[]<>,;:.=+-*/%&|^~!\"'_ax01\n \t"
    text = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        operation = rng.choice(("insert", "delete", "replace", "truncate"))
        position = rng.randrange(len(text) + 1)
        if operation == "insert":
            text[position:position] = bytes([rng.choice(alphabet)])
        elif operation == "delete" and text:
            del text[min(position, len(text) - 1)]
        elif operation == "replace" and text:
            text[min(position, len(text) - 1)] = rng.choice(alphabet)
        elif operation == "truncate" and text and rng.random() < 0.3:
            del text[position:]
    return bytes(text)


FUZZ_CASES = int(os.environ.get("MORT_SELFHOST_FUZZ_CASES", "400"))
FUZZ_SEED = int(os.environ.get("MORT_SELFHOST_FUZZ_SEED", "20261108"))


def spaced(data):
    """Separate punctuation so that token mutation has pieces to work with."""
    out = bytearray()
    for byte in data:
        if chr(byte) in "(){}[],;:":
            out += b" " + bytes([byte]) + b" "
        else:
            out.append(byte)
    return bytes(out)


def fuzz_seeds():
    seeds = [spaced(path.read_bytes()[:3000]) for path in REPO_FILES[:40]]
    seeds += [spaced(value) for value in EDGE_CASES.values() if len(value) < 3000]
    return seeds


def outcome_of(result, path):
    if result.dump is not None:
        return "parsed"
    # Lexical errors carry a column, or no line at all for invalid UTF-8.
    after_path = result.diagnostic[len(str(path).encode("utf-8")):]
    if re.match(rb":\d+: error: ", after_path):
        return "parse error"
    return "lexical error"


@pytest.mark.parametrize("mutator", ["tokens", "bytes"])
def test_mutation_differential(parse_exe, tmp_path, mutator):
    rng = random.Random(f"{FUZZ_SEED}:{mutator}")
    seeds = fuzz_seeds()
    mutate = mutate_tokens if mutator == "tokens" else mutate_bytes
    outcomes = collections.Counter()
    for index in range(FUZZ_CASES):
        data = mutate(rng, rng.choice(seeds))
        try:
            result = assert_source_agrees(parse_exe, tmp_path, data)
        except AssertionError as error:
            raise AssertionError(
                f"fuzz case {index} ({mutator}, seed {FUZZ_SEED}), "
                f"input {data!r}:\n{error}") from error
        outcomes[outcome_of(result, tmp_path / "input.mx")] += 1
    print(f"\nmutation outcomes ({mutator}): {dict(sorted(outcomes.items()))}")
    if FUZZ_CASES >= 100:
        # Mutants must exercise the parser, not stop at the lexer. Most break
        # the program; valid parses are covered mainly by the corpus above.
        assert outcomes["parsed"] > 0, outcomes
        assert outcomes["parse error"] >= FUZZ_CASES // 4, outcomes


# ----- the reference itself -----


def test_reference_guard_changes_nothing_on_the_corpus():
    """Below the limit, the guarded parser is the plain Python parser."""
    deepest = sys.getrecursionlimit()
    assert deepest >= 1000
    for path in REPO_FILES:
        try:
            source = path.read_text(encoding="utf-8")
            plain = Parser(Lexer(source).tokenize()).parse()
        except (MortError, UnicodeDecodeError):
            continue
        assert py_ast_dump.dump_program(plain) == py_ast_dump.dump_file(path).dump, path


def test_every_ast_class_is_exercised():
    """Differential coverage must reach every node kind the parser can build."""
    classes = {
        name for name, value in vars(A).items()
        if isinstance(value, type) and value.__module__ == A.__name__
        and name != "Node"
    }
    seen = set()
    sources = [path.read_text(encoding="utf-8") for path in REPO_FILES]
    sources += [value.decode("utf-8", "replace") for value in EDGE_CASES.values()]
    for source in sources:
        try:
            program = py_ast_dump.parse_source(source)
        except MortError:
            continue
        stack = [program]
        while stack:
            item = stack.pop()
            if isinstance(item, tuple):
                stack.extend(item)
                continue
            if not py_ast_dump.is_ast_object(item):
                continue
            seen.add(type(item).__name__)
            for value in vars(item).values():
                if isinstance(value, list):
                    stack.extend(value)
                else:
                    stack.append(value)
    assert classes - seen == set()


def test_reference_dumps_the_call_name_position():
    program = py_ast_dump.parse_source("fn f() {\n    g\n  (1);\n}\n")
    dump = py_ast_dump.dump_program(program)
    assert 'Call name="g" args=[1 line=#3 type_args=[0 name_pos=2:5\n' in dump


def test_reference_rejects_a_parser_set_checker_field():
    program = py_ast_dump.parse_source("fn f() { let a = 1; }")
    program.funcs[0].body.stmts[0].var_type = "i64"
    with pytest.raises(AssertionError, match="var_type"):
        py_ast_dump.dump_program(program)


# ----- the bootstrap subset -----


def f64_locals(root, monkeypatch):
    """Locals the checker typed f64, as (file name, line), for one program.

    Reads the checker's result, so an inferred type is seen as well as a
    written one.
    """
    captured = []

    class CaptureProgram:
        def __init__(self, program, **options):
            captured.append(program)

        def generate(self):
            return ""

    monkeypatch.setattr(mortc, "CodeGen", CaptureProgram)
    mortc.compile_files_to_c([str(root)])
    found = set()
    stack = list(captured[0].funcs)
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, A.Node):
            if isinstance(item, A.Let) and item.var_type == "f64":
                found.add((Path(item.filename).name, item.line))
            stack.extend(value for field, value in vars(item).items()
                         if isinstance(value, (A.Node, list, tuple)))
    return sorted(found)


@pytest.mark.parametrize("root", ["parse_main.mx", "float_repr_main.mx"])
def test_no_f64_locals(root, monkeypatch):
    assert f64_locals(HERE / root, monkeypatch) == []


# ----- driver behaviour -----


def test_dump_is_deterministic(parse_exe, tmp_path):
    """Same bytes across runs, working directories and environments."""
    sample = [path for path in REPO_FILES if path.parent.name == "selfhost"][:8]
    for path in sample:
        runs = []
        for attempt in range(3):
            cwd = tmp_path / f"cwd{attempt}"
            cwd.mkdir(exist_ok=True)
            out = cwd / "out.txt"
            env = dict(os.environ, PYTHONHASHSEED=str(attempt),
                       MORT_NOISE="x" * (attempt * 64))
            result = subprocess.run(
                [str(parse_exe), str(path), str(out)],
                capture_output=True, cwd=cwd, env=env, timeout=120)
            assert result.returncode == 0, result.stderr
            runs.append(out.read_bytes())
        assert runs[0] == runs[1] == runs[2], path


def test_diagnostic_has_file_and_line(parse_exe, tmp_path):
    source = tmp_path / "input.mx"
    source.write_bytes(b"fn main() {\n  let = 1;\n}\n")
    code, stderr, dump = run_mort_parser(parse_exe, source, tmp_path / "out.txt")
    assert code == 1
    assert dump is None
    assert normalized(stderr) == (
        f"{source}:2: error: expected variable name, got '='".encode())


def test_usage_is_a_clean_failure(parse_exe):
    result = subprocess.run([str(parse_exe)], capture_output=True, timeout=60)
    assert result.returncode == 2
    assert b"usage" in result.stderr


def test_missing_input_is_a_clean_failure(parse_exe, tmp_path):
    result = subprocess.run(
        [str(parse_exe), str(tmp_path / "does-not-exist.mx"),
         str(tmp_path / "out.txt")],
        capture_output=True, timeout=60)
    assert result.returncode == 2
    assert b"cannot read" in result.stderr


def test_unwritable_output_is_a_clean_failure(parse_exe, tmp_path):
    source = tmp_path / "input.mx"
    source.write_bytes(b"fn f() { }")
    result = subprocess.run(
        [str(parse_exe), str(source), str(tmp_path / "no-such-dir" / "out.txt")],
        capture_output=True, timeout=60)
    assert result.returncode == 2
    assert b"cannot write" in result.stderr
