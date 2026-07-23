"""Tests for the Mort compiler.

Two layers:
  1. Front-end tests (always run): valid programs produce C; invalid programs
     raise MortError with the right message. No C compiler needed.
  2. End-to-end tests (skipped if no cc/gcc/clang): compile each example to a
     native binary and check its stdout.

Run with:  python -m pytest tests/ -v      (or)   python tests/test_mort.py
"""
import os
import json
import io
import subprocess
import struct
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mort.errors import MortError            # noqa: E402
import mortc                                 # noqa: E402
from mort.project import (                                  # noqa: E402
    ProjectError,
    load_manifest,
    parse_semver,
    resolve_project,
    select_semver,
    semver_satisfies,
)
from mort.formatter import format_source                 # noqa: E402
from mort.lsp import (                                   # noqa: E402
    Server,
    completion_items,
    diagnostics_for_document,
    document_symbols,
    hover_for_document,
    signature_help,
)
from mort.fuzz import run_fuzz                          # noqa: E402


def c_of(src):
    return mortc.compile_to_c(src)


# End-to-end tests need a C compiler; skip them cleanly when none is present.
_CC = mortc.find_c_compiler()
needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")
_UBSAN_FLAGS = (
    []
    if os.name == "nt"
    else ["-fsanitize=undefined", "-fno-sanitize-recover=all"]
)

# The kernel build needs Zig specifically (32-bit cross-compile).
_ZIG = mortc.find_zig()
needs_zig = pytest.mark.skipif(_ZIG is None, reason="kernel build needs the Zig backend")


# ---------- front-end: valid programs ----------

def test_hello_generates_c():
    c = c_of("fn main() -> int { print(42); return 0; }")
    assert "mort_print(42)" in c
    assert "int main(void)" in c
    assert "return (int)mort_main();" in c


def test_types_and_inference():
    c = c_of("fn main() -> int { let x = 3; let y: int = x + 1; print(y); return 0; }")
    assert "int64_t m_x = 3;" in c
    assert "int64_t m_y = mort_wrap_i64(" in c


@needs_cc
def test_const_local_and_global_bindings_run():
    src = (
        "const BASE: i64 = 40; "
        "fn main() -> int { const offset: i64 = 2; "
        "print(BASE + offset); return 0; }"
    )
    c_source = c_of(src)
    assert "static const int64_t m_BASE" in c_source
    assert "const int64_t m_offset" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "const.c")
        exe = os.path.join(d, "const.exe" if os.name == "nt" else "const")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_float_literals_arithmetic_casts_and_output_run():
    src = (
        "fn average(left: f64, right: f64) -> f64 { return (left + right) / 2.0; } "
        "fn main() -> int { let result = average(4e1, 45.0); "
        "let narrow: f32 = 3.5; let converted: f64 = 42 as f64; "
        "print(result); print(narrow); print(converted); return 0; }"
    )
    c_source = c_of(src)
    assert "double m_result" in c_source
    assert "float m_narrow = 3.5f" in c_source
    assert "mort_print_float" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "floats.c")
        exe = os.path.join(d, "floats.exe" if os.name == "nt" else "floats")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42.5\n3.5\n42\n"


@needs_cc
def test_extended_literals_null_and_nested_block_comments_run():
    src = (
        "/* outer comment /* nested comment */ complete */ "
        "const BINARY: i64 = 0b0010_1010; const OCTAL: i64 = 0o52; "
        "fn main() -> int { let pointer: *i64 = null; assert(pointer == null); "
        "pointer = alloc(8) as *i64; assert(pointer != null); free(pointer); "
        "print(BINARY); print(OCTAL); print('A'); print('\\n'); return 0; }"
    )
    c_source = c_of(src)
    assert "NULL" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "literals.c")
        exe = os.path.join(d, "literals.exe" if os.name == "nt" else "literals")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n42\n65\n10\n"


@pytest.mark.parametrize(
    "source",
    [
        "fn main() -> int { return 0; } /* unterminated",
        "fn main() -> int { let bad = 'ab'; return 0; }",
        "fn main() -> int { let bad = 0b102; return 0; }",
        "fn main() -> int { let bad = 1__000; return 0; }",
    ],
)
def test_malformed_extended_literals_are_rejected(source):
    with pytest.raises(MortError):
        c_of(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("fn main() -> int { let pointer = null; return 0; }",
         "null bindings require an explicit pointer type"),
        ("fn main() -> int { let equal = null == null; return 0; }",
         "can compare null only with pointers"),
        ("fn main() -> int { let value = null as i64; return 0; }",
         "cannot cast null to i64"),
    ],
)
def test_null_requires_pointer_context(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@needs_cc
def test_compound_assignments_run_and_preserve_lvalue_evaluation():
    src = (
        "fn next_index(calls: *i64) -> i64 { *calls += 1; return 0; } "
        "fn main() -> int { let value: i64 = 7; value += 5; value -= 2; "
        "value *= 3; value /= 5; value %= 5; value |= 8; value &= 11; "
        "value ^= 2; value <<= 2; value >>= 1; "
        "let calls: i64 = 0; let values: [i64; 1] = [10]; "
        "values[next_index(&calls)] += 5; print(value); print(values[0]); "
        "print(calls); return 0; }"
    )
    c_source = c_of(src)
    assert "mort_next_index((&m_calls))" in c_source
    assert "int64_t* mort_assign_" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "compound.c")
        exe = os.path.join(d, "compound.exe" if os.name == "nt" else "compound")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "22\n15\n1\n"


@needs_cc
def test_function_values_callbacks_and_generic_higher_order_calls_run():
    src = (
        "type Binary = fn(i64, i64) -> i64; "
        "struct Handler { callback: Binary } "
        "fn add(left: i64, right: i64) -> i64 { return left + right; } "
        "fn subtract(left: i64, right: i64) -> i64 { return left - right; } "
        "const DEFAULT: Binary = add; "
        "fn choose(addition: bool) -> Binary { "
        "if addition { return add; } return subtract; } "
        "fn apply<T>(operation: fn(T, T) -> T, left: T, right: T) -> T { "
        "return operation(left, right); } "
        "fn main() -> int { let operation: Binary = choose(true); "
        "print(operation(20, 22)); operation = subtract; "
        "print(apply(operation, 50, 8)); print(DEFAULT(40, 2)); "
        "let handler = Handler { callback: subtract }; "
        "let selected = handler.callback; print(selected(50, 8)); return 0; }"
    )
    c_source = c_of(src)
    assert "int64_t (* m_operation)(int64_t, int64_t)" in c_source
    assert "int64_t (* mort_choose(bool m_addition))(int64_t, int64_t)" in c_source
    assert "mort_apply_i64" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "callbacks.c")
        exe = os.path.join(d, "callbacks.exe" if os.name == "nt" else "callbacks")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n42\n42\n42\n"


def test_function_pointer_types_support_c_callback_signatures():
    c_source = c_of(
        "extern fn visit(context: *void, "
        "callback: fn(*void, i64) -> bool) -> i64; "
        "fn main() -> int { let callback: fn(*void, i64) -> bool = null; "
        "assert(callback == null); return 0; }"
    )
    assert "bool (*)(void*, int64_t)" in c_source


@needs_cc
def test_tuples_aliases_generics_arrays_slices_and_struct_fields_run():
    src = (
        "type Pair = (i64, i64); "
        "const ORIGIN: (i64, bool) = (40, true); "
        "struct Wrapper { item: Pair } "
        "struct Cell { value: i64 } "
        "fn make() -> Pair { return (20, 22); } "
        "fn first(value: (i64, bool)) -> i64 { return value.0; } "
        "fn swap<A, B>(value: (A, B)) -> (B, A) { "
        "return (value.1, value.0); } "
        "fn main() -> int { "
        "let point: Pair = make(); point.0 += 1; "
        "print(point.0 + point.1); "
        "let nested: ((i64, bool), u8) = ((40, true), 2); "
        "print(nested.0.0 + nested.1 as i64); "
        "let swapped = swap((42, false)); assert(!swapped.0); print(swapped.1); "
        "let values: [(i64, bool); 2] = [(1, true), (41, false)]; "
        "let view: [](i64, bool) = slice(&values[0], 2); "
        "print(view[1].0 + 1); "
        "let wrapper = Wrapper { item: (20, 22) }; "
        "let cell_pair: (Cell, bool) = (Cell { value: 42 }, true); "
        "let callback: fn((i64, bool)) -> i64 = first; "
        "assert(sizeof<(i64, bool)>() > 0); "
        "let mixed: (u8, bool, *u8) = (7, true, \"Mort\"); "
        "print(ORIGIN.0 + 2); print(wrapper.item.0 + wrapper.item.1); "
        "print(cell_pair.0.value); print(callback((42, true))); "
        "print(mixed.0 as i64); println(mixed.2); return 0; }"
    )
    c_source = c_of(src)
    assert "struct mort_tuple__i64_i64" in c_source
    assert ".f_0" in c_source and ".f_1" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "tuples.c")
        exe = os.path.join(d, "tuples.exe" if os.name == "nt" else "tuples")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "43\n42\n42\n42\n42\n42\n42\n42\n7\nMort\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("fn main() -> int { let bad = (1,); return 0; }",
         "tuple literals require at least two elements"),
        ("fn main() -> int { let pair: (i64, bool) = (1, 2); return 0; }",
         "type mismatch"),
        ("fn main() -> int { let pair = (1, true); print(pair.2); return 0; }",
         "tuple index 2 is out of bounds"),
        ("fn main() -> int { let pair = (1, true); print(pair.name); return 0; }",
         "tuple has no named field"),
        ("fn main() -> int { let equal = (1, true) == (1, true); return 0; }",
         "cannot compare aggregate values"),
        ("fn main() -> int { let pair = (1, true); "
         "match pair { _ => { print(1); } } return 0; }",
         "cannot match on a value"),
        ("fn main() -> int { let bad: (void, i64) = (1, 2); return 0; }",
         "unknown type"),
        ("struct Recursive { value: (Recursive, bool) } "
         "fn main() -> int { return 0; }",
         "aggregate by-value cycle"),
        ("struct Left { right: Right } struct Right { left: Left } "
         "fn main() -> int { return 0; }",
         "aggregate by-value cycle"),
    ],
)
def test_tuple_type_errors(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@needs_cc
def test_imported_public_function_can_be_used_as_callback_value():
    with tempfile.TemporaryDirectory() as d:
        helper = os.path.join(d, "math.mx")
        main = os.path.join(d, "main.mx")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write(
                "module tools.math; "
                "pub fn add(left: i64, right: i64) -> i64 { return left + right; }"
            )
        with open(main, "w", encoding="utf-8") as fh:
            fh.write(
                "import math as numbers; fn main() -> int { "
                "let callback: fn(i64, i64) -> i64 = numbers.add; "
                "print(callback(20, 22)); return 0; }"
            )
        c_source = mortc.compile_files_to_c([main])
        assert "= mort_tools__math__add;" in c_source
        cfile = os.path.join(d, "module_callback.c")
        exe = os.path.join(
            d, "module_callback.exe" if os.name == "nt" else "module_callback")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "fn add(a: i64, b: i64) -> i64 { return a + b; } "
            "fn main() -> int { let callback = add; callback(1); return 0; }",
            "expects 2 argument",
        ),
        (
            "fn main() -> int { let value = 42; value(); return 0; }",
            "is not callable",
        ),
        (
            "fn identity<T>(value: T) -> T { return value; } "
            "fn main() -> int { let callback = identity; return 0; }",
            "cannot be used directly as a value",
        ),
        (
            "fn signed(value: i64) -> i64 { return value; } "
            "fn main() -> int { let callback: fn(u64) -> u64 = signed; return 0; }",
            "type mismatch",
        ),
    ],
)
def test_invalid_function_value_usage_is_rejected(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("value %= 2.0;", "operator '%' is not defined for floats"),
        ("value &= 2.0;", "requires int operands"),
    ],
)
def test_invalid_float_compound_assignments_are_rejected(statement, message):
    source = f"fn main() -> int {{ let value: f64 = 4.0; {statement} return 0; }}"
    with pytest.raises(MortError, match=message):
        c_of(source)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("10 / 0", "integer division by zero"),
        ("10 % (2 - 2)", "integer remainder by zero"),
    ],
)
def test_invalid_constant_integer_operations_are_rejected(expression, message):
    source = f"fn main() -> int {{ let result = {expression}; return 0; }}"
    with pytest.raises(MortError, match=message):
        c_of(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("fn main() -> int { let value = 1e999; return 0; }",
         "floating-point literal is out of range"),
        ("fn main() -> int { let value: f32 = 1e100; return 0; }",
         "floating-point literal does not fit in f32"),
    ],
)
def test_out_of_range_float_literals_are_rejected(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@needs_cc
def test_type_aliases_resolve_through_structs_generics_and_variants():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "aliases.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.option; type UserId = u64; type Score = f64; "
                "type MaybeId = Option<UserId>; struct User { id: UserId } "
                "fn boosted(value: Score) -> Score { return value + 1.5; } "
                "fn main() -> int { let user = User { id: 42 }; "
                "let selected: MaybeId = MaybeId.Some(user.id); match selected { "
                "MaybeId.Some(value) => { print(value); }, "
                "MaybeId.None => { print(0); } } "
                "print(boosted(40.5)); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "UserId" not in c_source
        assert "MaybeId" not in c_source
        cfile = os.path.join(d, "aliases.c")
        exe = os.path.join(d, "aliases.exe" if os.name == "nt" else "aliases")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n42\n"


def test_condition_has_no_double_parens():
    # if/while conditions must not double-wrap (avoids -Wparentheses-equality)
    c = c_of("fn main() -> int { let x = 1; if x == 0 { print(1); } while x == 2 { print(2); } return 0; }")
    assert "if (m_x == 0)" in c
    assert "while (m_x == 2)" in c
    assert "((m_x == 0))" not in c


def test_bool_and_control_flow():
    c = c_of(
        "fn main() -> int { let b = true; if b && (1 < 2) { print(1); } else { print(0); } return 0; }"
    )
    assert "bool m_b = true;" in c
    assert "if (m_b && (1 < 2))" in c   # one outer paren pair, no clang warning


def test_recursion_prototype_emitted():
    src = "fn f(n: int) -> int { return f(n); } fn main() -> int { return 0; }"
    c = c_of(src)
    assert "int64_t mort_f(int64_t m_n);" in c  # prototype allows any call order


# ---------- Phase 2: fixed-width ints, casts, pointers ----------

def test_fixed_width_int_types():
    c = c_of("fn main() -> int { let a: u8 = 5; let b: i32 = 0 - 1; print(b); return 0; }")
    assert "uint8_t m_a = 5;" in c
    # a fully-constant expression folds to its value
    assert "int32_t m_b = ((int32_t)-1);" in c


def test_hex_literal():
    c = c_of("fn main() -> int { let m: u16 = 0xFF; print(m); return 0; }")
    assert "uint16_t m_m = 255;" in c


def test_pointer_ops_codegen():
    c = c_of(
        "fn main() -> int { let x: i32 = 5; let p: *i32 = &x; *p = 9; print(x); return 0; }"
    )
    assert "int32_t* m_p = (&m_x);" in c
    assert "(*m_p) = 9;" in c


def test_cast_codegen():
    c = c_of(
        "fn main() -> int { let x: i32 = 1; let p: *i32 = &x; let a: u64 = p as u64; print(x); return 0; }"
    )
    assert "(uintptr_t)(m_p)" in c


@needs_cc
def test_numeric_casts_have_defined_fixed_width_semantics_under_ubsan():
    src = (
        "fn main() -> int {"
        "  let positive: i64 = 128; print((positive as i8) as i64);"
        "  let maximum: u64 = 18446744073709551615; "
        "  print(maximum as i64);"
        "  let low: f64 = 127.9; print((low as i8) as i64);"
        "  let negative: f64 = 0.0 - 128.9; "
        "  print((negative as i8) as i64);"
        "  return 0;"
        "}"
    )
    c_source = c_of(src)
    assert "mort_wrap_i8" in c_source
    assert "mort_wrap_i64" in c_source
    assert "mort_float_to_i8" in c_source
    assert "18446744073709551615ULL" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "numeric_casts.c")
        exe = os.path.join(d, "numeric_casts.exe" if os.name == "nt" else "numeric_casts")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            *_UBSAN_FLAGS,
        ], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == "-128\n-1\n127\n-128\n"


@needs_cc
def test_out_of_range_float_to_integer_cast_is_a_controlled_failure():
    c_source = c_of(
        "fn main() -> int { let huge: f64 = 1e30; "
        "print((huge as i32) as i64); return 0; }"
    )
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "float_cast.c")
        exe = os.path.join(d, "float_cast.exe" if os.name == "nt" else "float_cast")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            *_UBSAN_FLAGS,
        ], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode != 0
    assert "floating-point to integer cast out of range" in result.stderr
    assert "Mort line 1" in result.stderr
    assert "runtime error" not in result.stderr


def test_literal_coercion_in_arithmetic():
    # the untyped literal 5 adopts u8; the u8 result is narrowed back so C's
    # promotion to int doesn't leak (250 + 5 wraps to 255 in u8)
    c = c_of("fn main() -> int { let a: u8 = 250; let b: u8 = a + 5; print(b); return 0; }")
    assert "uint8_t m_b = " in c
    assert "(uint32_t)((uint8_t)(m_a))" in c


@needs_cc
def test_narrow_width_semantics():
    # ~u8 and u8 << 8 must observe the u8 width, not C's promoted int
    src = ("fn main() -> int {"
           "  let a: u8 = 1;"
           "  print((~a) as i64);"        # 254 (not -2)
           "  print((a << 8) as i64);"    # 0   (not 256)
           "  let b: u8 = 200;"
           "  print((b + 100) as i64);"   # 44  (300 wraps in u8)
           "  return 0; }")
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "n.c")
        exe = os.path.join(d, "n.exe" if os.name == "nt" else "n")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "254\n0\n44\n"


@needs_cc
def test_fixed_width_runtime_integer_semantics_are_defined_under_ubsan():
    src = (
        "fn main() -> int {"
        "  let max32: i32 = 2147483647; let one32: i32 = 1;"
        "  print((max32 + one32) as i64);"
        "  let min32: i32 = (0 - 1) << 31; let neg32: i32 = 0 - 1;"
        "  print((min32 / neg32) as i64); print((min32 % neg32) as i64);"
        "  let max64: i64 = 9223372036854775807; let one64: i64 = 1;"
        "  print(max64 + one64);"
        "  let min64: i64 = (0 - 1) << 63; let neg64: i64 = 0 - 1;"
        "  print(min64 / neg64); print(min64 % neg64);"
        "  let value: i16 = 30000; let multiplier: i16 = 3;"
        "  print((value * multiplier) as i64);"
        "  let left: u16 = 1; let wide: u64 = 16;"
        "  print((left << wide) as i64);"
        "  let negative: i16 = 0 - 8; let far: u32 = 40;"
        "  print((negative >> far) as i64);"
        "  let assigned: i32 = 2147483647; assigned += one32;"
        "  print(assigned as i64);"
        "  return 0;"
        "}"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "defined_ints.c")
        exe = os.path.join(d, "defined_ints.exe" if os.name == "nt" else "defined_ints")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            *_UBSAN_FLAGS,
        ], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "-2147483648\n-2147483648\n0\n"
        "-9223372036854775808\n-9223372036854775808\n0\n"
        "24464\n0\n-1\n-2147483648\n"
    )


@needs_cc
@pytest.mark.parametrize(
    ("operator", "message"),
    [
        ("/", "integer division by zero"),
        ("%", "integer remainder by zero"),
    ],
)
def test_runtime_integer_zero_divisor_is_a_controlled_failure(operator, message):
    c_source = c_of(
        "fn main() -> int { let value: i32 = 7; let zero: i32 = 0; "
        f"print((value {operator} zero) as i64); return 0; }}"
    )
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "zero_divisor.c")
        exe = os.path.join(d, "zero_divisor.exe" if os.name == "nt" else "zero_divisor")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            *_UBSAN_FLAGS,
        ], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode != 0
    assert message in result.stderr
    assert "Mort line 1" in result.stderr
    assert "runtime error" not in result.stderr


@needs_cc
def test_runtime_negative_shift_count_is_a_controlled_failure():
    c_source = c_of(
        "fn main() -> int { let value: i32 = 1; let count: i64 = 0 - 1; "
        "print((value << count) as i64); return 0; }"
    )
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "negative_shift.c")
        exe = os.path.join(d, "negative_shift.exe" if os.name == "nt" else "negative_shift")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            *_UBSAN_FLAGS,
        ], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode != 0
    assert "negative integer shift count at Mort line 1" in result.stderr
    assert "runtime error" not in result.stderr


# ---------- Phase 2b: structs and inline asm ----------

STRUCT_SRC = (
    "struct Point { x: i64, y: i64 } "
    "fn main() -> int { let p: Point = Point { x: 3, y: 4 }; p.y = 5; print(p.x); return 0; }"
)


def test_struct_codegen():
    c = c_of(STRUCT_SRC)
    assert "struct mort_Point {" in c
    assert "int64_t f_x;" in c
    assert "(struct mort_Point){ .f_x = 3, .f_y = 4 }" in c
    assert "(m_p).f_y = 5;" in c          # field assignment
    assert "(m_p).f_x" in c               # field read


def test_asm_codegen():
    c = c_of('fn main() -> int { asm("nop"); return 0; }')
    assert '__asm__ volatile ("nop");' in c


def test_string_literal_codegen():
    c = c_of('fn main() -> int { let s: *u8 = "AB"; print(*s as i64); return 0; }')
    assert 'static uint8_t mort_str_0[] = "AB";' in c   # mutable static storage
    assert "uint8_t* m_s = mort_str_0;" in c


def test_string_literal_is_writable():
    # backing storage is mutable, so writing through the *u8 is defined
    c = c_of('fn main() -> int { let s: *u8 = "AB"; *s = 67; print(*s as i64); return 0; }')
    assert "(*m_s) = 67;" in c


def test_port_io_builtins_codegen():
    # inb/outb are privileged (can't run in userspace), so verify codegen only.
    c = c_free("fn kmain() { outb(0x20, 0x20); let s: u8 = inb(0x60); }")
    assert "mort_outb(uint16_t port, uint8_t val)" in c
    assert "mort_inb(uint16_t port)" in c
    assert '"outb %0, %1"' in c
    assert "mort_outb(32, 32);" in c
    assert "uint8_t m_s = mort_inb(96);" in c


def test_port_io_word_builtins_codegen():
    # inw/outw are privileged (can't run in userspace), so verify codegen only.
    c = c_free("fn kmain() { outw(0x1F0, 0xABCD); let s: u16 = inw(0x1F0); }")
    assert "mort_outw(uint16_t port, uint16_t val)" in c
    assert "mort_inw(uint16_t port)" in c
    assert '"outw %0, %1"' in c
    assert '"inw %1, %0"' in c
    assert "mort_outw(496, 43981);" in c
    assert "uint16_t m_s = mort_inw(496);" in c


def test_port_io_long_builtins_codegen():
    # inl/outl are 32-bit port I/O (PCI config space lives on 0xCF8/0xCFC).
    # Privileged, so verify codegen only.
    c = c_free("fn kmain() { outl(0xCF8, 0x80000000); let d: u32 = inl(0xCFC); }")
    assert "mort_outl(uint16_t port, uint32_t val)" in c
    assert "mort_inl(uint16_t port)" in c
    assert '"outl %0, %1"' in c
    assert '"inl %1, %0"' in c
    assert "mort_outl(3320, 2147483648);" in c
    assert "uint32_t m_d = mort_inl(3324);" in c


def test_port_io_long_helpers_emitted_per_builtin():
    only_in = c_free("fn kmain() { let d: u32 = inl(0xCFC); }")
    assert "mort_inl(uint16_t" in only_in
    assert "mort_outl(uint16_t" not in only_in   # not dragged in by inl alone

    only_out = c_free("fn kmain() { outl(0xCF8, 0x1); }")
    assert "mort_outl(uint16_t" in only_out
    assert "mort_inl(uint16_t" not in only_out


def test_concurrency_builtins_are_typed_and_hosted_only():
    c_source = c_of(
        "fn worker(context: *void) -> i64 { return 42; } "
        "fn main() -> int { let thread = thread_spawn(worker, null); "
        "print(thread_join(thread)); let atomic = atomic_i64_create(1); "
        "atomic_i64_store(atomic, 2); print(atomic_i64_load(atomic)); "
        "atomic_i64_destroy(atomic); return 0; }"
    )
    assert "#include <stdatomic.h>" in c_source
    assert "mort_thread_spawn" in c_source
    assert "mort_atomic_i64_load" in c_source
    with pytest.raises(MortError, match="threads are not available"):
        mortc.compile_to_c(
            "fn worker(context: *void) -> i64 { return 0; } "
            "fn kmain() { thread_spawn(worker, null); }",
            freestanding=True,
        )
    with pytest.raises(MortError, match="callback must be"):
        c_of(
            "fn wrong(value: i64) -> i64 { return value; } "
            "fn main() -> int { thread_spawn(wrong, null); return 0; }"
        )


@needs_cc
def test_cross_platform_threads_mutexes_and_atomics_run():
    source = os.path.join(ROOT, "examples", "concurrency.mx")
    c_source = mortc.compile_files_to_c([source])
    assert "MORT_REQUIRES_PTHREAD" in c_source
    assert "CreateThread" in c_source
    assert "pthread_create" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "concurrency.c")
        exe = os.path.join(d, "concurrency.exe" if os.name == "nt" else "concurrency")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        command = [
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            "-Wall", "-Wextra", "-Werror",
        ]
        if os.name != "nt":
            command.append("-pthread")
        subprocess.run(command, check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "2000\n2000\n2000\n2000\n5\n7\n9\n11\n"


def test_network_builtins_are_typed_and_hosted_only():
    c_source = c_of(
        "fn main() -> int { let listener = net_tcp_listen(\"127.0.0.1\", 0, 1); "
        "let port = net_socket_local_port(listener); print(port); "
        "net_socket_close(listener); return 0; }"
    )
    assert "#define _POSIX_C_SOURCE 200809L" in c_source
    assert "MORT_REQUIRES_PTHREAD" not in c_source
    assert "MORT_REQUIRES_WINSOCK" in c_source
    assert "getaddrinfo" in c_source
    assert "mort_net_tcp_listen" in c_source
    with pytest.raises(MortError, match="networking is not available"):
        mortc.compile_to_c(
            "fn kmain() { net_tcp_connect(\"localhost\", 80); }",
            freestanding=True,
        )
    with pytest.raises(MortError, match="buffer must be"):
        c_of(
            "fn main() -> int { let socket: *void = null; "
            "let value: i64 = 0; net_socket_send(socket, &value, 8); return 0; }"
        )


@needs_cc
def test_network_only_program_compiles_under_strict_c11():
    source = (
        "fn main() -> int { "
        "let listener = net_tcp_listen(\"127.0.0.1\", 0, 1); "
        "assert(listener != null); net_socket_close(listener); return 0; }"
    )
    c_source = mortc.compile_to_c(source)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "network_only.c")
        exe = os.path.join(d, "network_only.exe" if os.name == "nt" else "network_only")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        command = [
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            "-Wall", "-Wextra", "-Werror",
        ]
        if os.name == "nt":
            command.append("-lws2_32")
        subprocess.run(command, check=True)
        result = subprocess.run([exe], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@needs_cc
def test_cross_platform_tcp_dns_loopback_runs():
    source = os.path.join(ROOT, "examples", "tcp_loopback.mx")
    c_source = mortc.compile_files_to_c([source])
    assert "getaddrinfo" in c_source
    assert "pthread_create" in c_source
    assert "CreateThread" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "tcp_loopback.c")
        exe = os.path.join(d, "tcp_loopback.exe" if os.name == "nt" else "tcp_loopback")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        command = [
            *_CC, cfile, "-o", exe, "-O2", "-std=c11",
            "-Wall", "-Wextra", "-Werror",
        ]
        command.append("-lws2_32" if os.name == "nt" else "-pthread")
        subprocess.run(command, check=True)
        result = subprocess.run([exe], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "4\n112\n111\n110\n103\n"


def test_global_variable_codegen():
    c = c_of("let counter: i64 = 0; fn main() -> int { counter = counter + 5; print(counter); return 0; }")
    assert "static int64_t m_counter = 0;" in c
    assert "m_counter = mort_wrap_i64(" in c


def test_global_string_codegen():
    c = c_of('let msg: *u8 = "hi"; fn main() -> int { print(*msg as i64); return 0; }')
    assert 'static uint8_t m_str_0[] = "hi";' in c or 'mort_str_0[] = "hi";' in c
    assert "static uint8_t* m_msg = mort_str_0;" in c


@needs_cc
def test_global_shared_across_functions():
    src = ("let n: i64 = 10; "
           "fn bump() { n = n + 1; } "
           "fn main() -> int { bump(); bump(); print(n); return 0; }")
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "g.c")
        exe = os.path.join(d, "g.exe" if os.name == "nt" else "g")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "12\n"


def test_array_codegen():
    c = c_of("fn main() -> int { let a: [i32; 3] = [10, 20, 30]; a[0] = 99; print(a[0] as i64); return 0; }")
    assert "int32_t m_a[3] = {10, 20, 30};" in c
    assert "m_a[0] = 99;" in c
    assert "m_a[0]" in c


def test_array_repeat_codegen():
    c = c_of("fn main() -> int { let a: [u8; 4] = [0; 4]; print(a[0] as i64); return 0; }")
    assert "uint8_t m_a[4] = {0, 0, 0, 0};" in c


def test_array_inferred_type():
    c = c_of("fn main() -> int { let a = [1, 2, 3]; print(a[1] as i64); return 0; }")
    assert "int64_t m_a[3] = {1, 2, 3};" in c


def test_global_array_codegen():
    c = c_of("let table: [i32; 3] = [7, 8, 9]; fn main() -> int { print(table[2] as i64); return 0; }")
    assert "static int32_t m_table[3] = {7, 8, 9};" in c


def test_bitwise_codegen():
    c = c_of("fn main() -> int { let a: u32 = 6; let b: u32 = 3; "
             "print((a & b) as i64); print((a | b) as i64); print((a ^ b) as i64); return 0; }")
    assert "(uint32_t)(m_a)" in c
    assert " & " in c
    assert " | " in c
    assert " ^ " in c


def test_const_fold_bitwise_in_range():
    # a folded bitwise literal that DOES fit is accepted and emitted as its value
    c = c_of("fn main() -> int { let x: u8 = 200 | 100; print(x); return 0; }")  # 236
    assert "uint8_t m_x = ((uint8_t)236);" in c


def test_shift_and_not_codegen():
    # Runtime shifts use width-aware helpers and bitwise-not uses unsigned bits.
    c = c_of("fn main() -> int { let a: u32 = 1; print((a << 4) as i64); print((~a) as i64); return 0; }")
    assert "mort_shl_u32((uint32_t)(m_a), mort_shift_count_signed(" in c
    assert "~(uint32_t)((uint32_t)(m_a))" in c


def test_constant_shift_folded():
    # a constant shift is emitted as its folded value, not a C shift (avoids UB)
    c = c_of("fn main() -> int { let x: u64 = 1 << 63; print(x as i64); return 0; }")
    assert "(1 << 63)" not in c
    assert "9223372036854775808ULL" in c


def test_nested_constant_shift_no_giant_literal():
    # (1 << 64) - 1 folds to u64::MAX; the inner 2^64 must never be emitted as a
    # literal (it exceeds every C integer type)
    c = c_of("fn main() -> int { let x: u64 = (1 << 64) - 1; print(x as i64); return 0; }")
    assert "18446744073709551616" not in c          # 2^64 — invalid C literal
    assert "((uint64_t)18446744073709551615ULL)" in c  # u64::MAX


def test_int64_min_literal_is_clean():
    # INT64_MIN must not be spelled -9223372036854775808LL (that magnitude isn't a
    # signed literal); use the unsigned bit pattern instead
    c = c_of("fn main() -> int { let x: i64 = (0 - 1) << 63; print(x); return 0; }")
    assert "-9223372036854775808LL" not in c
    assert "((int64_t)9223372036854775808ULL)" in c


@needs_cc
def test_shift_width_semantics():
    # 1 << 63 must be the real 2^63 in u64 (not UB), and 0 << huge is 0
    src = ("fn main() -> int {"
           "  let x: u64 = 1 << 63;"
           "  print((x >> 60) as i64);"    # 8
           "  let z: u8 = 0 << 1000000;"
           "  print(z as i64);"            # 0
           "  let a: u32 = 1;"
           "  print((a << 20) as i64);"    # 1048576
           "  return 0; }")
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "s.c")
        exe = os.path.join(d, "s.exe" if os.name == "nt" else "s")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "8\n0\n1048576\n"


@needs_cc
def test_extreme_shift_literals_run_and_are_clean():
    # nested/overflowing constant shifts must compile clean under -Wall -Werror
    # and evaluate correctly
    src = ("fn main() -> int {"
           "  let a: u64 = (1 << 64) - 1;"    # u64::MAX
           "  print((a >> 32) as i64);"       # 4294967295
           "  let b: i64 = (0 - 1) << 63;"    # INT64_MIN
           "  print(b);"                       # -9223372036854775808
           "  return 0; }")
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "e.c")
        exe = os.path.join(d, "e.exe" if os.name == "nt" else "e")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11",
                        "-Wall", "-Werror"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "4294967295\n-9223372036854775808\n"


@needs_cc
def test_bitwise_runs():
    src = ("fn main() -> int {"
           "  let a: u32 = 0xF0; let b: u32 = 0x0F;"
           "  print((a | b) as i64);"     # 255
           "  print((a & 0x30) as i64);"  # 48
           "  print((1 << 5) as i64);"    # 32
           "  print((a >> 4) as i64);"    # 15
           "  return 0; }")
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "b.c")
        exe = os.path.join(d, "b.exe" if os.name == "nt" else "b")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "255\n48\n32\n15\n"


def test_for_loop_codegen():
    # both bounds are literals -> i is i64 (Mort's default integer)
    c = c_of("fn main() -> int { let s: i64 = 0; for i in 0..5 { s = s + i; } print(s); return 0; }")
    assert "int64_t mort_range_start_0 = 0;" in c
    assert "int64_t mort_range_end_0 = 5;" in c
    assert "m_i < mort_range_end_0" in c


def test_for_loop_var_type_from_bound():
    # a typed (non-literal) bound gives the loop variable that type -> usable
    # with same-typed data (e.g. a u32 counter), no cast needed
    c = c_of("fn main() -> int { let n: u32 = 3; let s: u32 = 0; for i in 0..n { s = s + i; } print(s as i64); return 0; }")
    assert "uint32_t mort_range_start_0 = 0;" in c
    assert "uint32_t mort_range_end_0 = m_n;" in c
    assert "m_i < mort_range_end_0" in c


def test_for_loop_annotated_type():
    c = c_of("fn main() -> int { let s: u32 = 0; for i: u32 in 0..2000 { s = s + i; } print(s as i64); return 0; }")
    assert "uint32_t mort_range_end_0 = 2000;" in c
    assert "m_i < mort_range_end_0" in c


@needs_cc
def test_for_loop_runs():
    src = ("fn main() -> int {"
           "  let a: [i32; 5] = [2, 4, 6, 8, 10];"
           "  let s: i32 = 0;"
           "  for i in 0..5 { s = s + a[i]; }"
           "  print(s as i64); return 0; }")   # 30
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "f.c")
        exe = os.path.join(d, "f.exe" if os.name == "nt" else "f")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "30\n"


@needs_cc
def test_ranges_cache_bounds_and_inclusive_max_is_overflow_safe():
    src = (
        "fn upper(calls: *i64) -> u8 { *calls += 1; return 3; } "
        "fn main() -> int { let calls: i64 = 0; let exclusive: i64 = 0; "
        "for i: u8 in 0..upper(&calls) { exclusive += i as i64; } "
        "let inclusive: u64 = 0; for i: u8 in 254..=255 { "
        "if i == 254 { continue; } inclusive += i as u64; } "
        "let empty: i64 = 0; for i: u8 in 9..=3 { empty += i as i64; } "
        "print(exclusive); print(calls); print(inclusive as i64); print(empty); "
        "return 0; }"
    )
    c_source = c_of(src)
    assert c_source.count("mort_upper((&m_calls))") == 1
    assert "mort_range_has_next_1" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "ranges.c")
        exe = os.path.join(d, "ranges.exe" if os.name == "nt" else "ranges")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "3\n1\n255\n0\n"


@needs_cc
def test_loop_statement_runs_with_break_and_continue():
    src = (
        "fn main() -> int { let count: i64 = 0; let total: i64 = 0; loop { "
        "count += 1; if count == 2 { continue; } total += count; "
        "if count == 4 { break; } } print(total); return 0; }"
    )
    c_source = c_of(src)
    assert "while (true)" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "loop.c")
        exe = os.path.join(d, "loop.exe" if os.name == "nt" else "loop")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "8\n"


def test_unbroken_loop_is_non_fallthrough_for_return_analysis():
    c_of("fn forever() -> i64 { loop {} } fn main() -> int { return 0; }")
    with pytest.raises(MortError, match="may finish without returning"):
        c_of(
            "fn maybe(done: bool) -> i64 { loop { if done { break; } } } "
            "fn main() -> int { return 0; }"
        )


def test_for_loop_var_scoped():
    # the loop variable is not visible after the loop
    with pytest.raises(MortError) as exc:
        c_of("fn main() -> int { for i in 0..3 { print(i); } print(i); return 0; }")
    assert "undefined variable" in exc.value.msg


def test_struct_array_field():
    src = ("struct S { xs: [i32; 2] } "
           "fn main() -> int { let s: S = S { xs: [10, 20] }; print(s.xs[1] as i64); return 0; }")
    c = c_of(src)
    assert "int32_t f_xs[2];" in c
    assert ".f_xs = {10, 20}" in c
    assert "(m_s).f_xs[1]" in c


@needs_cc
def test_array_sum_runs():
    src = ("fn main() -> int {"
           "  let a: [i32; 4] = [10, 20, 30, 40];"
           "  a[1] = 5;"
           "  let sum: i32 = 0; let i: u32 = 0;"
           "  while i < 4 { sum = sum + a[i]; i = i + 1; }"
           "  print(sum as i64); return 0; }")   # 10 + 5 + 30 + 40 = 85
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "a.c")
        exe = os.path.join(d, "a.exe" if os.name == "nt" else "a")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "85\n"


def test_port_io_helpers_only_when_used():
    c = c_free("fn kmain() { let x: u8 = 1; }")
    assert "mort_inb" not in c and "mort_outb" not in c
    assert "mort_inw" not in c and "mort_outw" not in c


def test_port_io_helpers_emitted_per_builtin():
    only_in = c_free("fn kmain() { let s: u8 = inb(0x60); }")
    assert "mort_inb(uint16_t" in only_in
    assert "mort_outb(uint16_t" not in only_in   # not dragged in by inb alone

    only_out = c_free("fn kmain() { outb(0x20, 0x20); }")
    assert "mort_outb(uint16_t" in only_out
    assert "mort_inb(uint16_t" not in only_out


def test_port_io_word_helpers_emitted_per_builtin():
    only_in = c_free("fn kmain() { let s: u16 = inw(0x1F0); }")
    assert "mort_inw(uint16_t" in only_in
    assert "mort_outw(uint16_t" not in only_in   # not dragged in by inw alone
    assert "mort_inb(uint16_t" not in only_in    # 8-bit helpers stay out too

    only_out = c_free("fn kmain() { outw(0x1F0, 0xABCD); }")
    assert "mort_outw(uint16_t" in only_out
    assert "mort_inw(uint16_t" not in only_out
    assert "mort_outb(uint16_t" not in only_out


def test_literal_range_check():
    # value fits: fine
    c_of("fn main() -> int { let x: u8 = 255; print(x); return 0; }")
    # constant-folded literal that fits
    c_of("fn main() -> int { let x: u8 = 200 + 55; print(x); return 0; }")


def test_const_fold_matches_c_semantics():
    # u64 max / 1 must fold with integer math (not float), so it still fits u64
    c_of("fn main() -> int { let x: u64 = 18446744073709551615 / 1; return 0; }")
    # C-style division truncates toward zero: -7 / 2 == -3 (fits i8)
    c_of("fn main() -> int { let x: i8 = (0 - 7) / 2; print(x as i64); return 0; }")


@needs_cc
def test_string_literal_runs():
    # walk a string literal's bytes: cast ptr->int, offset, cast back, deref.
    src = (
        'fn main() -> int {'
        '  let s: *u8 = "AB";'
        '  let a: *u8 = (s as u64 + 0) as *u8;'
        '  let b: *u8 = (s as u64 + 1) as *u8;'
        '  print(*a as i64);'   # 65
        '  print(*b as i64);'   # 66
        '  return 0; }'
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "s.c")
        exe = os.path.join(d, "s.exe" if os.name == "nt" else "s")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "65\n66\n"


def test_struct_pointer_field_write():
    src = (
        "struct P { x: i64 } "
        "fn main() -> int { let p: P = P { x: 1 }; let q: *P = &p; (*q).x = 9; print(p.x); return 0; }"
    )
    c = c_of(src)
    assert "((*m_q)).f_x = 9;" in c


# ---------- Phase 3: freestanding mode ----------

def c_free(src):
    return mortc.compile_to_c(src, freestanding=True)


def test_freestanding_omits_libc_and_main():
    c = c_free("fn kmain() { let p: *u8 = 0xB8000 as *u8; *p = 65; }")
    assert "#include <stdio.h>" not in c
    assert "#include <stdint.h>" in c        # stdint is freestanding-safe
    assert "mort_print" not in c
    assert "int main(void)" not in c
    assert "void mort_kmain(void)" in c


def test_freestanding_needs_no_main():
    # would be a "no 'main'" error in hosted mode — fine when freestanding
    c_free("fn kmain() { return; }")


def test_print_banned_in_freestanding():
    with pytest.raises(MortError) as exc:
        c_free("fn kmain() { print(1); }")
    assert "freestanding" in exc.value.msg


@needs_zig
def test_freestanding_object_builds():
    src_path = os.path.join(ROOT, "examples", "kernel.mx")
    with open(src_path, encoding="utf-8") as fh:
        c_source = c_free(fh.read())
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "k.c")
        obj = os.path.join(d, "k.o")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        cmd = [*_ZIG, "-target", "x86_64-freestanding-none"]
        cmd += ["-ffreestanding", "-O2", "-std=c11", "-c", cfile, "-o", obj]
        subprocess.run(cmd, check=True)
        data = open(obj, "rb").read()
    assert len(data) > 0
    # The backend pins the exact bare-metal target, so this must be a 64-bit
    # x86-64 ELF rather than merely an object for the host architecture.
    assert data[:4] == b"\x7fELF"                         # ELF magic
    assert data[4] == 2                                   # ELFCLASS64
    assert struct.unpack("<H", data[18:20])[0] == 0x3E    # EM_X86_64


def test_freestanding_cli_always_uses_zig_cross_target(
        tmp_path, monkeypatch):
    source = tmp_path / "kernel.mx"
    output = tmp_path / "kernel.o"
    source.write_text("fn kmain() { asm(\"hlt\"); }", encoding="utf-8")
    commands = []

    def record(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(mortc, "find_zig", lambda: ["zig", "cc"])
    monkeypatch.setattr(
        mortc, "find_c_compiler",
        lambda: pytest.fail("host compiler selected for freestanding build"))
    monkeypatch.setattr(mortc.subprocess, "run", record)
    assert mortc.main([
        str(source), "--freestanding", "-o", str(output)]) == 0
    assert commands[0][:4] == [
        "zig", "cc", "-target", "x86_64-freestanding-none"]


# ---------- Phase 4: the bootable kernel ----------

@needs_zig
def test_kernel_builds_multiboot_elf():
    build_py = os.path.join(ROOT, "kernel", "build.py")
    r = subprocess.run([sys.executable, build_py, "check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "multiboot ELF" in r.stdout


# ---------- Phase 5: real-project foundations ----------

def test_extern_function_codegen_and_typechecking():
    c = c_of(
        "extern fn triple(value: i64) -> i64; "
        "fn main() -> int { print(triple(14)); return 0; }"
    )
    assert "extern int64_t triple(int64_t);" in c
    assert "mort_print(triple(14));" in c
    assert "mort_triple" not in c


def test_void_pointer_is_available_for_ffi():
    c = c_of(
        "extern fn release(ptr: *void); "
        "fn main() -> int { let p: *void = 0 as *void; release(p); return 0; }"
    )
    assert "extern void release(void*);" in c


def test_pointer_indexing_codegen():
    c = c_of(
        "fn main() -> int { let a: [i32; 2] = [1, 2]; "
        "let p: *i32 = &a[0]; p[1] = 9; print(p[1] as i64); return 0; }"
    )
    assert "int32_t* m_p = (&m_a[0]);" in c
    assert "m_p[1] = 9;" in c


def test_multiple_sources_share_one_checked_namespace():
    c = mortc.compile_sources_to_c([
        "fn twice(x: i64) -> i64 { return x * 2; }",
        "fn main() -> int { print(twice(21)); return 0; }",
    ])
    assert "int64_t mort_twice(int64_t m_x);" in c
    assert "mort_print(mort_twice(21));" in c


def test_multiple_sources_report_the_failing_filename():
    with pytest.raises(MortError) as exc:
        mortc.compile_sources_to_c(
            ["fn helper() { missing(); }", "fn main() -> int { return 0; }"],
            filenames=["src/helper.mx", "src/main.mx"],
        )
    assert exc.value.format().startswith("src/helper.mx:1: error:")


def test_standard_library_modules_typecheck_together():
    paths = [
        os.path.join(ROOT, "std", "string.mx"),
        os.path.join(ROOT, "std", "memory.mx"),
    ]
    sources = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            sources.append(fh.read())
    sources.append(
        "fn main() -> int { let a: [u8; 4] = [0; 4]; "
        "mem_set(&a[0], 7, 4); print(str_len(\"Mort\")); return 0; }"
    )
    c = mortc.compile_sources_to_c(sources)
    assert "mort_mem_set" in c
    assert "mort_str_len" in c


@needs_cc
def test_std_cli_module_runs():
    source = os.path.join(ROOT, "examples", "stdlib.mx")
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, "stdlib.exe" if os.name == "nt" else "stdlib")
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "mortc.py"), source,
             "--std", "string", "--run", "-o", exe],
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("4\n1\n")


def test_project_manifest_and_source_discovery():
    with tempfile.TemporaryDirectory() as d:
        project_dir = os.path.join(d, "demo")
        assert mortc.main(["new", project_dir]) == 0
        manifest_path = os.path.join(project_dir, "mort.toml")
        manifest = load_manifest(manifest_path)
        project = resolve_project(manifest_path)
    assert manifest["package"]["name"] == "demo"
    assert len(project["sources"]) == 1
    assert project["std"] == []


def test_project_sanitizers_are_validated_and_deduplicated():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "src"))
        manifest_path = os.path.join(d, "mort.toml")
        with open(os.path.join(d, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "sanitized"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n'
                'sanitizers = ["address", "undefined", "address"]\n')
        assert resolve_project(manifest_path)["sanitizers"] == [
            "address", "undefined"]
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "sanitized"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n'
                'sanitizers = ["imaginary"]\n')
        with pytest.raises(ProjectError, match="unsupported sanitizer"):
            resolve_project(manifest_path)
        with open(manifest_path, "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "sanitized"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n'
                'sanitizers = ["thread", "address"]\n')
        with pytest.raises(ProjectError, match="cannot be combined"):
            resolve_project(manifest_path)


def test_file_imports_resolve_local_and_standard_modules():
    with tempfile.TemporaryDirectory() as d:
        main_path = os.path.join(d, "main.mx")
        math_path = os.path.join(d, "math.mx")
        with open(main_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import math; import std.string; "
                "fn main() -> int { print(double(str_len(\"Mort\"))); return 0; }"
            )
        with open(math_path, "w", encoding="utf-8") as fh:
            fh.write("fn double(value: u64) -> u64 { return value * 2; }")
        c = mortc.compile_files_to_c([main_path])
    assert "mort_double" in c
    assert "mort_str_len" in c


def test_missing_import_names_source_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("import missing; fn main() -> int { return 0; }")
        with pytest.raises(MortError) as exc:
            mortc.compile_files_to_c([path])
    assert exc.value.filename == path
    assert "cannot find imported module" in exc.value.msg


@needs_cc
def test_namespaced_module_alias_and_pub_visibility_run():
    with tempfile.TemporaryDirectory() as d:
        main_path = os.path.join(d, "main.mx")
        math_path = os.path.join(d, "math.mx")
        with open(main_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import math as numbers; "
                "fn main() -> int { print(numbers.double(21)); return 0; }"
            )
        with open(math_path, "w", encoding="utf-8") as fh:
            fh.write(
                "module utilities.math; "
                "fn add(value: i64) -> i64 { return value + value; } "
                "pub fn double(value: i64) -> i64 { return add(value); }"
            )
        c_source = mortc.compile_files_to_c([main_path])
        assert "mort_utilities__math__double" in c_source
        cfile = os.path.join(d, "modules.c")
        exe = os.path.join(d, "modules.exe" if os.name == "nt" else "modules")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "42\n"


def test_private_module_function_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        main_path = os.path.join(d, "main.mx")
        hidden_path = os.path.join(d, "hidden.mx")
        with open(main_path, "w", encoding="utf-8") as fh:
            fh.write("import hidden; fn main() -> int { return hidden.secret(); }")
        with open(hidden_path, "w", encoding="utf-8") as fh:
            fh.write("module hidden; fn secret() -> i64 { return 1; }")
        with pytest.raises(MortError) as exc:
            mortc.compile_files_to_c([main_path])
    assert "private to module" in exc.value.msg


@needs_cc
def test_local_package_dependency_and_lockfile_run():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        library = os.path.join(d, "utility")
        assert mortc.main(["new", app]) == 0
        os.makedirs(os.path.join(library, "src"))
        with open(os.path.join(library, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "utility"\nversion = "0.1.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\nentry = "src/lib.mx"\n'
            )
        with open(os.path.join(library, "src", "lib.mx"), "w", encoding="utf-8") as fh:
            fh.write("module utility; pub fn answer() -> i64 { return 42; }")
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write(
                "import utility; "
                "fn main() -> int { print(utility.answer()); return 0; }"
            )
        assert mortc.main(["add", "utility", "--path", library, "--project", app]) == 0
        assert mortc.main(["fetch", app]) == 0
        assert mortc.main(["run", app]) == 0
        lock_path = os.path.join(app, "mort.lock")
        assert os.path.isfile(lock_path)
        with open(lock_path, encoding="utf-8") as fh:
            lock_data = json.load(fh)
        original_digest = lock_data["packages"][0]["content_sha256"]
        with open(os.path.join(library, "src", "lib.mx"), "a", encoding="utf-8") as fh:
            fh.write("\n// package content changed\n")
        assert mortc.main(["fetch", app, "--locked"]) == 1
        with open(lock_path, encoding="utf-8") as fh:
            assert json.load(fh)["packages"][0]["content_sha256"] == original_digest
        assert mortc.main(["fetch", app]) == 0
        with open(lock_path, encoding="utf-8") as fh:
            changed_lock_data = json.load(fh)
        lock = json.dumps(changed_lock_data)
    assert '"name": "utility"' in lock
    assert changed_lock_data["lock_version"] == 3
    assert changed_lock_data["packages"][0]["content_sha256"] != original_digest


@needs_cc
def test_git_package_dependency_is_cached_and_revision_locked():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        library = os.path.join(d, "git_utility")
        os.makedirs(os.path.join(library, "src"))
        with open(os.path.join(library, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "git_utility"\nversion = "0.1.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\nentry = "src/lib.mx"\n'
            )
        with open(os.path.join(library, "src", "lib.mx"), "w", encoding="utf-8") as fh:
            fh.write("module git_utility; pub fn answer() -> i64 { return 42; }")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=library, check=True)
        subprocess.run(["git", "add", "."], cwd=library, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Mort Tests", "-c", "user.email=mort@example.test",
             "commit", "-q", "-m", "fixture"], cwd=library, check=True)

        assert mortc.main(["new", app]) == 0
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write(
                "import git_utility; fn main() -> int { "
                "print(git_utility.answer()); return 0; }"
            )
        assert mortc.main([
            "add", "git_utility", "--git", library, "--ref", "main", "--project", app
        ]) == 0
        assert mortc.main(["run", app]) == 0
        cache = os.path.join(app, ".mort", "deps", "git_utility", ".git")
        assert os.path.isdir(cache)
        with open(os.path.join(app, "mort.lock"), encoding="utf-8") as fh:
            lock = fh.read()
    assert '"revision"' in lock
    assert os.path.abspath(library) not in lock


def test_cached_git_dependency_refreshes_requested_branch():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        library = os.path.join(d, "library")
        os.makedirs(os.path.join(app, "src"))
        os.makedirs(os.path.join(library, "src"))
        with open(os.path.join(library, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "library"\nversion = "1.0.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\nentry = "src/lib.mx"\n')
        library_source = os.path.join(library, "src", "lib.mx")
        with open(library_source, "w", encoding="utf-8") as fh:
            fh.write("module library; pub fn answer() -> i64 { return 1; }")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=library, check=True)
        subprocess.run(["git", "add", "."], cwd=library, check=True)
        commit = [
            "git", "-c", "user.name=Mort Tests", "-c",
            "user.email=mort@example.test", "commit", "-qm",
        ]
        subprocess.run([*commit, "first"], cwd=library, check=True)
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\nversion = "0.1.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                '[dependencies]\nlibrary = "git+../library#main"\n')
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        manifest = os.path.join(app, "mort.toml")
        first = resolve_project(manifest)
        with open(first["packages"]["library"], encoding="utf-8") as fh:
            assert "return 1" in fh.read()

        with open(library_source, "w", encoding="utf-8") as fh:
            fh.write("module library; pub fn answer() -> i64 { return 2; }")
        subprocess.run(["git", "add", "."], cwd=library, check=True)
        subprocess.run([*commit, "second"], cwd=library, check=True)
        expected = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=library, check=True,
            capture_output=True, text=True).stdout.strip()

        refreshed = resolve_project(manifest)
        with open(refreshed["packages"]["library"], encoding="utf-8") as fh:
            assert "return 2" in fh.read()
        cached_root = os.path.join(app, ".mort", "deps", "library")
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cached_root, check=True,
            capture_output=True, text=True).stdout.strip()
    assert actual == expected


def test_dependency_sources_and_entry_cannot_escape_package_root():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        library = os.path.join(d, "library")
        os.makedirs(os.path.join(app, "src"))
        os.makedirs(library)
        outside = os.path.join(d, "outside.mx")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("module outside; pub fn answer() -> i64 { return 42; }")
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                '[dependencies]\nlibrary = "../library"\n')
        dependency_manifest = os.path.join(library, "mort.toml")
        with open(dependency_manifest, "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "library"\n\n'
                '[build]\nsources = ["../outside.mx"]\n')
        with pytest.raises(ProjectError, match="source escapes its package root"):
            resolve_project(os.path.join(app, "mort.toml"))

        os.makedirs(os.path.join(library, "src"))
        with open(os.path.join(library, "src", "lib.mx"), "w", encoding="utf-8") as fh:
            fh.write("module library; pub fn answer() -> i64 { return 1; }")
        with open(dependency_manifest, "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "library"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\nentry = "../outside.mx"\n')
        with pytest.raises(ProjectError, match="entry escapes its package root"):
            resolve_project(os.path.join(app, "mort.toml"))


@pytest.mark.parametrize(
    ("version", "constraint", "matches"),
    [
        ("1.2.3", "^1.2.0", True),
        ("1.9.9", "^1.2.0", True),
        ("2.0.0", "^1.2.0", False),
        ("0.2.5", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        ("2.4.0", ">=2.0.0,<3.0.0", True),
        ("3.0.0", ">=2.0.0,<3.0.0", False),
        ("1.5.0", "1.x", True),
        ("1.5.0", "1.4.x", False),
        ("1.0.0-beta.2", "<1.0.0", True),
    ],
)
def test_semver_constraints(version, constraint, matches):
    assert semver_satisfies(version, constraint) is matches


def test_semver_selection_and_validation():
    assert select_semver(
        ["1.0.0", "1.5.0", "1.5.0-beta.1", "2.0.0"], "^1.0.0"
    ) == "1.5.0"
    with pytest.raises(ProjectError, match="invalid semantic version"):
        parse_semver("01.2.3")
    with pytest.raises(ProjectError, match="no published version"):
        select_semver(["1.0.0"], "^2.0.0")
    with pytest.raises(ProjectError, match="invalid semantic version constraint"):
        semver_satisfies("1.0.0", ",")


@needs_cc
def test_git_wildcard_dependency_selects_highest_compatible_tag():
    with tempfile.TemporaryDirectory() as d:
        library = os.path.join(d, "versioned")
        app = os.path.join(d, "app")
        os.makedirs(os.path.join(library, "src"))
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=library, check=True)
        for version, answer in (("1.0.0", 10), ("1.5.0", 15), ("2.0.0", 20)):
            with open(os.path.join(library, "mort.toml"), "w", encoding="utf-8") as fh:
                fh.write(
                    f'[package]\nname = "versioned"\nversion = "{version}"\n\n'
                    '[build]\nentry = "src/lib.mx"\nsources = ["src/**/*.mx"]\n'
                )
            with open(os.path.join(library, "src", "lib.mx"), "w", encoding="utf-8") as fh:
                fh.write(
                    f"module versioned; pub fn answer() -> i64 {{ return {answer}; }}")
            subprocess.run(["git", "add", "."], cwd=library, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Mort Tests",
                 "-c", "user.email=mort@example.test", "commit", "-qm", version],
                cwd=library, check=True,
            )
            subprocess.run(["git", "tag", "v" + version], cwd=library, check=True)
        os.makedirs(os.path.join(app, "src"))
        git_url = library.replace("\\", "/")
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\nversion = "0.1.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                f'[dependencies]\nversioned = "git+{git_url}#1.x"\n'
            )
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write(
                "import versioned; fn main() -> int { "
                "print(versioned.answer()); return 0; }")
        assert mortc.main(["run", app]) == 0
        with open(os.path.join(app, "mort.lock"), encoding="utf-8") as fh:
            lock = json.load(fh)
    assert lock["packages"][0]["version"] == "1.5.0"


def test_registry_dependency_resolves_from_offline_mirror():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        mirror = os.path.join(d, "mirror")
        package = os.path.join(mirror, "utility", "1.4.0")
        os.makedirs(os.path.join(app, "src"))
        os.makedirs(os.path.join(package, "src"))
        with open(os.path.join(package, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "utility"\nversion = "1.4.0"\n\n'
                '[build]\nentry = "src/lib.mx"\nsources = ["src/**/*.mx"]\n')
        with open(os.path.join(package, "src", "lib.mx"), "w", encoding="utf-8") as fh:
            fh.write("module utility; pub fn answer() -> i64 { return 42; }")
        index_path = os.path.join(d, "index.json")
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump({
                "format": 1,
                "packages": {
                    "utility": {
                        "versions": {
                            "1.0.0": {"git": "unused"},
                            "1.4.0": {"git": "unused"},
                            "2.0.0": {"git": "unused"},
                        }
                    }
                },
            }, fh)
        mirror_value = mirror.replace("\\", "/")
        index_value = index_path.replace("\\", "/")
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\nversion = "0.1.0"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                f'[registry]\nurl = "{index_value}"\n'
                f'mirrors = ["{mirror_value}"]\n\n'
                '[dependencies]\nutility = "registry:utility@^1.0.0"\n')
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        project = resolve_project(
            os.path.join(app, "mort.toml"), offline=True)
    assert project["packages"]["utility"].endswith(
        os.path.join("utility", "1.4.0", "src", "lib.mx"))


@pytest.mark.parametrize(
    ("index", "message"),
    [
        ({"format": 2, "packages": {}}, "must use format 1"),
        ({"format": True, "packages": {}}, "must use format 1"),
        ({"format": 1, "packages": []}, "packages object"),
        (
            {"format": 1, "packages": {"utility": {"versions": []}}},
            "versions object",
        ),
        (
            {
                "format": 1,
                "packages": {
                    "utility": {"versions": {"not-semver": {"git": "unused"}}}
                },
            },
            "invalid semantic version",
        ),
        (
            {
                "format": 1,
                "packages": {"utility": {"versions": {"1.0.0": "not-an-object"}}},
            },
            "must be an object",
        ),
    ],
)
def test_registry_rejects_malformed_indexes_cleanly(index, message):
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        os.makedirs(os.path.join(app, "src"))
        index_path = os.path.join(d, "index.json")
        with open(index_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh)
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                f'[registry]\nurl = "{index_path.replace(chr(92), "/")}"\n\n'
                '[dependencies]\nutility = "registry:utility@^1.0.0"\n')
        with pytest.raises(ProjectError, match=message):
            resolve_project(os.path.join(app, "mort.toml"))


def test_registry_rejects_package_path_traversal_before_loading_index():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        os.makedirs(os.path.join(app, "src"))
        with open(os.path.join(app, "src", "main.mx"), "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        with open(os.path.join(app, "mort.toml"), "w", encoding="utf-8") as fh:
            fh.write(
                '[package]\nname = "app"\n\n'
                '[build]\nsources = ["src/**/*.mx"]\n\n'
                '[dependencies]\nutility = "registry:../outside@^1.0.0"\n')
        with pytest.raises(ProjectError, match="invalid registry package name"):
            resolve_project(os.path.join(app, "mort.toml"))


@needs_cc
def test_typed_slices_and_owned_strings_run():
    slice_src = (
        "fn sum(values: []i32) -> i64 { let total: i64 = 0; "
        "for index: u64 in 0..values.len { total = total + (values[index] as i64); } "
        "return total; } "
        "fn main() -> int { let values: [i32; 3] = [10, 20, 12]; "
        "let view: []i32 = slice(&values[0], len(values)); "
        "print(sum(view)); return 0; }"
    )
    with tempfile.TemporaryDirectory() as d:
        slice_path = os.path.join(d, "slices.mx")
        string_path = os.path.join(d, "strings.mx")
        with open(slice_path, "w", encoding="utf-8") as fh:
            fh.write(slice_src)
        with open(string_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.owned_string as strings; "
                "fn main() -> int { let left: String = strings.from(\"Mort\"); "
                "let right: String = strings.from(\" language\"); "
                "let combined: String = strings.concat(&left, &right); "
                "println(combined.data); strings.destroy(&left); "
                "strings.destroy(&right); strings.destroy(&combined); return 0; }"
            )
        outputs = []
        for index, path in enumerate((slice_path, string_path)):
            c_source = mortc.compile_files_to_c([path])
            cfile = os.path.join(d, f"safe{index}.c")
            exe = os.path.join(d, f"safe{index}.exe" if os.name == "nt" else f"safe{index}")
            with open(cfile, "w", encoding="utf-8") as fh:
                fh.write(c_source)
            subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
            outputs.append(subprocess.run([exe], capture_output=True, text=True))
    assert outputs[0].stdout == "42\n"
    assert outputs[1].stdout == "Mort language\n"


@needs_cc
def test_project_new_build_run_and_test_commands(capsys):
    with tempfile.TemporaryDirectory() as d:
        project_dir = os.path.join(d, "hello_mort")
        assert mortc.main(["new", project_dir]) == 0
        assert mortc.main(["build", project_dir]) == 0
        project = resolve_project(os.path.join(project_dir, "mort.toml"))
        assert os.path.isfile(project["output"])
        assert mortc.main(["run", project_dir]) == 0
        assert mortc.main(["test", project_dir]) == 0
    output = capsys.readouterr().out
    assert "created project" in output
    assert "1 test file(s) passed" in output


@needs_cc
def test_project_build_cache_hits_and_invalidates(capsys):
    with tempfile.TemporaryDirectory() as d:
        project_dir = os.path.join(d, "cached_project")
        assert mortc.main(["new", project_dir]) == 0
        capsys.readouterr()
        assert mortc.main(["build", project_dir]) == 0
        first = capsys.readouterr().out
        cache_path = os.path.join(project_dir, ".mort", "build-cache.json")
        with open(cache_path, "r", encoding="utf-8") as fh:
            first_cache = json.load(fh)
        assert "wrote" in first
        assert mortc.main(["build", project_dir]) == 0
        second = capsys.readouterr().out
        assert "build cache hit" in second
        source = os.path.join(project_dir, "src", "main.mx")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { print(7); return 0; }\n")
        assert mortc.main(["build", project_dir]) == 0
        third = capsys.readouterr().out
        with open(cache_path, "r", encoding="utf-8") as fh:
            second_cache = json.load(fh)
        manifest = os.path.join(project_dir, "mort.toml")
        with open(manifest, "r", encoding="utf-8") as fh:
            manifest_text = fh.read()
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write(manifest_text.replace(
                "std = []", 'std = []\nopt_level = "0"\ndebug = true'))
        assert mortc.main(["build", project_dir]) == 0
        fourth = capsys.readouterr().out
        configured = resolve_project(manifest)
        with open(cache_path, "r", encoding="utf-8") as fh:
            third_cache = json.load(fh)
    assert "build cache hit" not in third
    assert "wrote" in third
    assert first_cache["fingerprint"] != second_cache["fingerprint"]
    assert "build cache hit" not in fourth
    assert configured["opt_level"] == "0"
    assert configured["debug"] is True
    assert second_cache["fingerprint"] != third_cache["fingerprint"]


@needs_cc
def test_first_class_test_blocks_run_with_project_code():
    with tempfile.TemporaryDirectory() as d:
        library = os.path.join(d, "math.mx")
        tests_path = os.path.join(d, "math_test.mx")
        with open(library, "w", encoding="utf-8") as fh:
            fh.write("fn double(value: i64) -> i64 { return value * 2; }")
        with open(tests_path, "w", encoding="utf-8") as fh:
            fh.write('test "double" { assert(double(21) == 42); }')
        c_source = mortc.compile_files_to_c([library, tests_path], test_mode=True)
        assert "static void mort_test_0" in c_source
        cfile = os.path.join(d, "tests.c")
        exe = os.path.join(d, "tests.exe" if os.name == "nt" else "tests")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0


def test_formatter_preserves_comments_strings_and_indents():
    source = (
        "fn main() -> int {  \n"
        "// { is not structure\n"
        "/* } is ignored\n"
        "   /* nested { too */\n"
        "*/\n"
        "println(\"}\");\n"
        "print('{');\n"
        "return 0;\n"
        "}\n"
    )
    assert format_source(source) == (
        'fn main() -> int {\n'
        '    // { is not structure\n'
        '    /* } is ignored\n'
        '    /* nested { too */\n'
        '    */\n'
        '    println("}");\n'
        "    print('{');\n"
        '    return 0;\n'
        '}\n'
    )


def test_fmt_check_and_write_commands():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int {\nreturn 0;\n}\n")
        assert mortc.main(["fmt", "--check", path]) == 1
        assert mortc.main(["fmt", path]) == 0
        assert mortc.main(["fmt", "--check", path]) == 0


def test_diagnostic_render_includes_source_excerpt():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bad.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int {\n    return nope;\n}\n")
        with pytest.raises(MortError) as exc:
            mortc.compile_files_to_c([path])
        rendered = exc.value.render()
    assert "return nope;" in rendered
    assert "| ^" in rendered


def test_json_diagnostics_and_frontend_only_check(capsys):
    with tempfile.TemporaryDirectory() as d:
        valid = os.path.join(d, "valid.mx")
        invalid = os.path.join(d, "invalid.mx")
        with open(valid, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        with open(invalid, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int {\n    return missing;\n}\n")
        assert mortc.main([valid, "--check"]) == 0
        assert "check passed" in capsys.readouterr().out
        assert mortc.main([
            invalid,
            "--check",
            "--diagnostic-format",
            "json",
        ]) == 1
        diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["severity"] == "error"
    assert diagnostic["file"] == invalid
    assert diagnostic["line"] == 2
    assert diagnostic["range"]["start"] == {"line": 2, "column": 1}
    assert diagnostic["source"] == "    return missing;"
    assert "undefined variable" in diagnostic["message"]


def test_unused_binding_warnings_are_structured_and_deniable(capsys):
    source = (
        "fn helper(value: i64, _ignored: i64) -> i64 { "
        "let never = 1; return value; } "
        "fn main() -> int { let answer = helper(1, 2); return 0; }"
    )
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "warnings.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        assert mortc.main([
            path,
            "--check",
            "--warn-unused",
            "--diagnostic-format",
            "json",
        ]) == 0
        captured = capsys.readouterr()
        warnings = [json.loads(line) for line in captured.err.splitlines()]
        assert mortc.main([path, "--check", "--deny-warnings"]) == 1
        denied = capsys.readouterr()
    assert {item["code"] for item in warnings} == {"unused-binding"}
    assert {item["message"] for item in warnings} == {
        "unused variable 'never'",
        "unused variable 'answer'",
    }
    assert all(item["severity"] == "warning" for item in warnings)
    assert "warning[unused-binding]" in denied.err


def test_lsp_checks_unsaved_documents_with_imports():
    with tempfile.TemporaryDirectory() as d:
        helper = os.path.join(d, "helper.mx")
        main = os.path.join(d, "main.mx")
        with open(helper, "w", encoding="utf-8") as fh:
            fh.write(
                "module helper; pub fn answer() -> i64 { return 42; }"
            )
        with open(main, "w", encoding="utf-8") as fh:
            fh.write("fn main() -> int { return 0; }")
        uri = Path(main).as_uri()
        diagnostics = diagnostics_for_document(
            uri,
            "import helper;\nfn main() -> int { return missing; }\n",
        )
        warnings = diagnostics_for_document(
            uri,
            "import helper;\nfn main() -> int { let unused = helper.answer(); return 0; }\n",
        )
    assert len(diagnostics) == 1
    assert diagnostics[0]["severity"] == 1
    assert diagnostics[0]["range"]["start"]["line"] == 1
    assert "undefined variable" in diagnostics[0]["message"]
    assert len(warnings) == 1
    assert warnings[0]["severity"] == 2
    assert warnings[0]["code"] == "unused-binding"


def test_lsp_stdio_initialize_shutdown_cycle():
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"jsonrpc": "2.0", "method": "exit", "params": {}},
    ]
    framed = b""
    for message in messages:
        payload = json.dumps(message).encode("utf-8")
        framed += f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
    output = io.BytesIO()
    assert Server(io.BytesIO(framed), output).run() == 0
    protocol_output = output.getvalue().decode("utf-8")
    assert '"name":"mort-lsp"' in protocol_output
    assert '"id":2,"result":null' in protocol_output


def test_lsp_completion_symbols_and_hover_use_language_structure():
    source = (
        "module app; type Count = u64; struct Point<T> { value: T } "
        "enum State { Ready, Done } const LIMIT: Count = 4; "
        "extern fn native(value: i32) -> i32; "
        "fn add(left: i64, right: i64) -> i64 { return left + right; }"
    )
    completions = completion_items(source)
    labels = {item["label"] for item in completions}
    assert {"match", "i64", "print", "Point", "State", "LIMIT", "add"} <= labels
    symbols = document_symbols(source)
    by_name = {item["name"]: item for item in symbols}
    assert by_name["app"]["kind"] == 3
    assert by_name["Point"]["kind"] == 23
    assert by_name["State"]["kind"] == 10
    assert by_name["LIMIT"]["kind"] == 14
    assert by_name["add"]["detail"] == "fn add(left: i64, right: i64) -> i64"
    line = "fn main() -> int { return add(1, 2); }"
    hover = hover_for_document(source + "\n" + line, 1, line.index("add") + 1)
    assert "fn add(left: i64, right: i64) -> i64" in hover["contents"]["value"]
    assert document_symbols("fn broken(") == []
    assert {item["label"] for item in completion_items("fn broken(")} >= {"fn", "print"}


def test_lsp_signature_help_tracks_nested_arguments():
    source = (
        "fn add(left: i64, right: i64) -> i64 { return left + right; }\n"
        "fn main() -> int { return add(add(1, 2), 3); }"
    )
    line = source.splitlines()[1]
    inner_position = line.index("2)")
    outer_position = line.index(", 3") + 2
    inner = signature_help(source, 1, inner_position)
    outer = signature_help(source, 1, outer_position)
    assert inner["signatures"][0]["label"] == "fn add(left: i64, right: i64) -> i64"
    assert inner["activeParameter"] == 1
    assert outer["activeParameter"] == 1
    builtin_source = "fn main() -> int { print(42); return 0; }"
    builtin = signature_help(builtin_source, 0, builtin_source.index("42") + 1)
    assert builtin["signatures"][0]["label"] == "fn print(value) -> void"


def test_deterministic_frontend_fuzzer_and_cli(capsys):
    result = run_fuzz(cases=100, seed=42)
    assert result["cases"] == 100
    assert result["accepted"] + result["rejected"] == 100
    assert result["accepted"] >= 45
    assert mortc.main(["fuzz", "--cases", "20", "--seed", "7"]) == 0
    assert "fuzzed 20 case(s)" in capsys.readouterr().out


def test_deep_source_nesting_is_a_controlled_diagnostic(tmp_path, capsys):
    expression = "(" * 160 + "1" + ")" * 160
    source = f"fn main() -> int {{ return {expression}; }}"
    with pytest.raises(MortError, match="compiler safety limit"):
        c_of(source)
    path = tmp_path / "deep.mx"
    path.write_text(source, encoding="utf-8")
    assert mortc.main([str(path), "--check"]) == 1
    assert "compiler safety limit" in capsys.readouterr().err


def test_coverage_guided_fuzz_corpus_is_valid():
    corpus = os.path.join(ROOT, "fuzz", "corpus")
    cases = sorted(
        os.path.join(corpus, name)
        for name in os.listdir(corpus) if name.endswith(".mx"))
    assert len(cases) >= 4
    for path in cases:
        with open(path, encoding="utf-8") as handle:
            assert "int main(void)" in c_of(handle.read()), path


def test_std_and_doctor_commands_report_installed_toolchain(capsys):
    assert mortc.main(["std", "--path"]) == 0
    standard = capsys.readouterr().out
    assert mortc.STDLIB_DIR in standard
    assert "algorithm\n" in standard
    assert "random\n" in standard
    assert mortc.main(["doctor"]) == 0
    doctor = capsys.readouterr()
    assert f"Mort {mortc.__version__}" in doctor.out
    assert "Standard library:" in doctor.out
    assert "C backend:" in doctor.out


def test_configured_c_backend_and_sanitizer_flags(
        tmp_path, monkeypatch, capsys):
    configured = f'"{sys.executable}" -m ziglang cc'
    monkeypatch.setenv("MORT_CC", configured)
    assert mortc.find_c_compiler() == [
        sys.executable, "-m", "ziglang", "cc"]

    source = tmp_path / "main.mx"
    output = tmp_path / "main"
    source.write_text("fn main() -> int { return 0; }", encoding="utf-8")
    commands = []

    def record(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(mortc, "find_c_compiler", lambda: ["test-cc"])
    monkeypatch.setattr(mortc.subprocess, "run", record)
    assert mortc.main([
        str(source), "-o", str(output),
        "--sanitize", "address", "--sanitize", "undefined",
    ]) == 0
    assert "-fsanitize=address,undefined" in commands[0]
    assert "-fno-omit-frame-pointer" in commands[0]
    assert mortc.main([
        str(source), "--freestanding", "--sanitize", "address",
    ]) == 1
    assert "unavailable in freestanding mode" in capsys.readouterr().err
    assert mortc.main([
        str(source), "--sanitize", "thread", "--sanitize", "address",
    ]) == 1
    assert "cannot be combined" in capsys.readouterr().err


def test_packaging_version_matches_compiler_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        project_text = handle.read()
    assert f'version = "{mortc.__version__}"' in project_text
    assert 'mortc = "mortc:main"' in project_text
    assert "std/random.mx" in project_text
    assert "std/net.mx" in project_text
    with open(
            os.path.join(ROOT, "conformance", "manifest.json"),
            encoding="utf-8") as handle:
        conformance = json.load(handle)
    assert conformance["language_version"] == mortc.__language_version__


@needs_cc
def test_extern_function_links_and_runs(capsys):
    with tempfile.TemporaryDirectory() as d:
        helper_c = os.path.join(d, "helper.c")
        helper_o = os.path.join(d, "helper.o")
        main_mx = os.path.join(d, "main.mx")
        exe = os.path.join(d, "ffi.exe" if os.name == "nt" else "ffi")
        with open(helper_c, "w", encoding="utf-8") as fh:
            fh.write("#include <stdint.h>\nint64_t triple(int64_t x) { return x * 3; }\n")
        subprocess.run([*_CC, "-O2", "-c", helper_c, "-o", helper_o], check=True)
        with open(main_mx, "w", encoding="utf-8") as fh:
            fh.write(
                "extern fn triple(value: i64) -> i64; "
                "fn main() -> int { print(triple(14)); return 0; }"
            )
        assert mortc.main([main_mx, "--link", helper_o, "-o", exe]) == 0
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "42\n"
    assert "mortc: wrote" in capsys.readouterr().out


@needs_cc
def test_break_and_continue_run():
    src = (
        "fn main() -> int { let i = 0; let total = 0; "
        "while true { i = i + 1; if i == 2 { continue; } "
        "if i == 6 { break; } total = total + i; } "
        "print(total); return 0; }"
    )
    c_source = c_of(src)
    assert "continue;" in c_source
    assert "break;" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "loops.c")
        exe = os.path.join(d, "loops.exe" if os.name == "nt" else "loops")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "13\n"


@needs_cc
def test_hosted_runtime_string_assert_and_allocation():
    src = (
        "fn main() -> int { let text: *u8 = alloc(2) as *u8; "
        "text[0] = 65; text[1] = 0; println(text); "
        "assert(text[0] == 65); free(text); return 0; }"
    )
    c_source = c_of(src)
    assert "malloc" in c_source
    assert "mort_assert" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "runtime.c")
        exe = os.path.join(d, "runtime.exe" if os.name == "nt" else "runtime")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "A\n"


@needs_cc
def test_len_and_runtime_array_bounds_checks():
    good = c_of(
        "fn main() -> int { let values: [i32; 3] = [1, 2, 3]; "
        "print(len(values)); print(len(\"Mort\")); let index = 1; "
        "print(values[index] as i64); return 0; }"
    )
    bad = c_of(
        "fn main() -> int { let values: [i32; 2] = [1, 2]; "
        "let index = 3; print(values[index] as i64); return 0; }"
    )
    with tempfile.TemporaryDirectory() as d:
        results = []
        for number, source in enumerate((good, bad)):
            cfile = os.path.join(d, f"bounds{number}.c")
            exe = os.path.join(d, f"bounds{number}.exe" if os.name == "nt" else f"bounds{number}")
            with open(cfile, "w", encoding="utf-8") as fh:
                fh.write(source)
            subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
            results.append(subprocess.run([exe], capture_output=True, text=True))
    assert results[0].stdout == "3\n4\n2\n"
    assert results[1].returncode != 0
    assert "index out of bounds" in results[1].stderr


@needs_cc
def test_typed_slice_index_object_is_evaluated_once():
    src = (
        "fn view(values: *i64, calls: *i64) -> []i64 { "
        "*calls += 1; return slice(values, 1); } "
        "fn main() -> int { let values: [i64; 1] = [42]; let calls: i64 = 0; "
        "print(view(&values[0], &calls)[0]); print(calls); return 0; }"
    )
    c_source = c_of(src)
    assert c_source.count("mort_view(") == 3  # prototype, definition, one call
    assert "mort_index_obj_" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "slice_once.c")
        exe = os.path.join(d, "slice_once.exe" if os.name == "nt" else "slice_once")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "42\n1\n"


@needs_cc
def test_enum_and_exhaustive_match_run():
    src = (
        "enum State { Idle, Running, Done } "
        "fn state_code(state: State) -> i64 { "
        "match state { "
        "State.Idle => { return 0; }, "
        "State.Running => { return 1; }, "
        "State.Done => { return 2; } "
        "} } "
        "fn main() -> int { let state: State = State.Running; "
        "print(state_code(state)); return 0; }"
    )
    c_source = c_of(src)
    assert "enum mort_State" in c_source
    assert "MORT_State_Running" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "enum.c")
        exe = os.path.join(d, "enum.exe" if os.name == "nt" else "enum")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "1\n"


@needs_cc
def test_payload_enum_destructuring_run():
    src = (
        "enum ParseResult { Value(i64), Error(*u8) } "
        "fn unwrap(result: ParseResult) -> i64 { "
        "match result { "
        "ParseResult.Value(value) => { return value; }, "
        "ParseResult.Error(message) => { println(message); return 0; } "
        "} } "
        "fn main() -> int { let result: ParseResult = ParseResult.Value(42); "
        "print(unwrap(result)); return 0; }"
    )
    c_source = c_of(src)
    assert "struct mort_ParseResult" in c_source
    assert "v_Value" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "payload_enum.c")
        exe = os.path.join(d, "payload_enum.exe" if os.name == "nt" else "payload_enum")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "42\n"


@needs_cc
def test_multi_payload_enums_generic_construction_and_destructuring_run():
    src = (
        "enum Shape { Point(i64, i64), Label(*u8, bool), Empty } "
        "enum Pairing<Left, Right> { Pair(Left, Right), Empty } "
        "enum Wrapped { Pair((i64, bool)) } "
        "fn shape_value(shape: Shape) -> i64 { match shape { "
        "Shape.Point(x, y) => { return x + y; }, "
        "Shape.Label(text, visible) => { "
        "if visible { return len(text) as i64; } return 0; }, "
        "Shape.Empty => { return 0; } } } "
        "fn pair_value(value: Pairing<i64, bool>) -> i64 { match value { "
        "Pairing<i64, bool>.Pair(number, _) => { return number; }, "
        "Pairing<i64, bool>.Empty => { return 0; } } } "
        "fn wrapped_value(value: Wrapped) -> i64 { match value { "
        "Wrapped.Pair(pair) => { if pair.1 { return pair.0; } return 0; } } } "
        "fn main() -> int { "
        "print(shape_value(Shape.Point(20, 22))); "
        "print(shape_value(Shape.Label(\"Mort\", true)) + 38); "
        "let pair: Pairing<i64, bool> = Pairing<i64, bool>.Pair(42, true); "
        "print(pair_value(pair)); "
        "print(wrapped_value(Wrapped.Pair((42, true)))); return 0; }"
    )
    c_source = c_of(src)
    assert ".data.v_Point" in c_source
    assert ".f_0" in c_source and ".f_1" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "multi_payload.c")
        exe = os.path.join(
            d, "multi_payload.exe" if os.name == "nt" else "multi_payload")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n42\n42\n42\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("enum Pair { Value(i64, bool) } fn main() -> int { "
         "let value: Pair = Pair.Value(1); return 0; }",
         "expects 2 payloads"),
        ("enum Pair { Value(i64, bool) } fn main() -> int { "
         "let value: Pair = Pair.Value(true, false); return 0; }",
         "payload 1 of Pair.Value expects i64"),
        ("enum Pair { Value(i64, bool) } fn main() -> int { "
         "let value = Pair.Value(1, true); match value { "
         "Pair.Value(item) => { print(item); } } return 0; }",
         "require 2 binding names"),
        ("enum Pair { Value(i64, bool) } fn main() -> int { "
         "let value = Pair.Value(1, true); match value { "
         "Pair.Value(left, left) => { print(left); } } return 0; }",
         "must have unique names"),
        ("enum Pair { Value(i64, bool) } fn main() -> int { "
         "let value = Pair.Value(1, true); match value { "
         "Pair.Value(left, 1) => { print(left); } } return 0; }",
         "require 2 binding names"),
    ],
)
def test_multi_payload_enum_errors(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@needs_cc
def test_resource_struct_explicit_moves_run():
    src = (
        "resource struct Buffer { data: *u8, length: u64 } "
        "fn destroy(value: *Buffer) -> void { "
        "free((*value).data); (*value).data = null; (*value).length = 0; } "
        "fn make() -> Buffer { let data = alloc(1) as *u8; data[0] = 42; "
        "return Buffer { data: data, length: 1 }; } "
        "fn consume(value: Buffer) -> i64 { let answer = value.data[0] as i64; "
        "destroy(&value); return answer; } "
        "fn main() -> int { let original = make(); "
        "let transferred = move original; print(consume(move transferred)); "
        "return 0; }"
    )
    c_source = c_of(src)
    assert "struct mort_Buffer" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "resource_move.c")
        exe = os.path.join(
            d, "resource_move.exe" if os.name == "nt" else "resource_move")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_resource_structs_destroy_automatically_on_nested_return():
    src = (
        "resource struct Ticket { label: *u8 } "
        "fn destroy(value: *Ticket) -> void { println((*value).label); } "
        "fn answer() -> i64 { const outer = Ticket { label: \"outer\" }; "
        "if true { let inner = Ticket { label: \"inner\" }; return 42; } "
        "return 0; } "
        "fn main() -> int { print(answer()); return 0; }"
    )
    c_source = c_of(src)
    assert "mort_live_m_outer" in c_source
    assert "mort_live_m_inner" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "resource_cleanup.c")
        exe = os.path.join(
            d, "resource_cleanup.exe" if os.name == "nt" else "resource_cleanup")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "inner\nouter\n42\n"


@needs_cc
def test_resource_can_move_once_across_exclusive_branches():
    src = (
        "resource struct Value { number: i64 } "
        "fn destroy(value: *Value) -> void { print((*value).number); } "
        "fn consume(value: Value) -> void {} "
        "fn main() -> int { let value = Value { number: 42 }; "
        "let choose_left = true; "
        "if choose_left { consume(move value); } "
        "else { consume(move value); } return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "resource_branches.c")
        exe = os.path.join(
            d, "resource_branches.exe" if os.name == "nt" else "resource_branches")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_resources_compose_through_structs_tuples_enums_and_arrays():
    src = (
        "resource struct Leaf { label: *u8 } "
        "fn destroy(value: *Leaf) -> void { println((*value).label); } "
        "struct Pair { left: Leaf, right: Leaf } "
        "enum MaybeLeaf { Some(Leaf), None } "
        "fn main() -> int { "
        "let pair = Pair { left: Leaf { label: \"pair-left\" }, "
        "right: Leaf { label: \"pair-right\" } }; "
        "let tuple = (Leaf { label: \"tuple-left\" }, "
        "Leaf { label: \"tuple-right\" }); "
        "let maybe: MaybeLeaf = MaybeLeaf.Some(Leaf { label: \"enum\" }); "
        "let array: [Leaf; 2] = [Leaf { label: \"array-left\" }, "
        "Leaf { label: \"array-right\" }]; "
        "print(42); return 0; }"
    )
    c_source = c_of(src)
    assert "mort_drop_Pair" in c_source
    assert "mort_drop__Leaf_Leaf" in c_source
    assert "mort_drop_MaybeLeaf" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "resource_composites.c")
        exe = os.path.join(
            d, "resource_composites.exe" if os.name == "nt"
            else "resource_composites")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == (
        "42\narray-right\narray-left\nenum\n"
        "tuple-right\ntuple-left\npair-right\npair-left\n"
    )


@needs_cc
def test_resource_can_move_once_across_exclusive_match_arms():
    src = (
        "enum Choice { Left, Right } "
        "resource struct Value { number: i64 } "
        "fn destroy(value: *Value) -> void { print((*value).number); } "
        "fn consume(value: Value) -> void {} "
        "fn main() -> int { let value = Value { number: 42 }; "
        "let choice: Choice = Choice.Left; match choice { "
        "Choice.Left => { consume(move value); }, "
        "Choice.Right => { consume(move value); } } return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "resource_match.c")
        exe = os.path.join(
            d, "resource_match.exe" if os.name == "nt" else "resource_match")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_owning_enum_match_move_transfers_payload_to_arm():
    src = (
        "resource struct Leaf { label: *u8 } "
        "fn destroy(value: *Leaf) -> void { println((*value).label); } "
        "enum Owned { Some(Leaf), Empty } "
        "fn consume(value: Owned) -> void { match move value { "
        "Owned.Some(leaf) => { println(\"matched\"); }, "
        "Owned.Empty => {} } } "
        "fn main() -> int { let value: Owned = "
        "Owned.Some(Leaf { label: \"destroyed\" }); "
        "consume(move value); return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "owning_match.c")
        exe = os.path.join(
            d, "owning_match.exe" if os.name == "nt" else "owning_match")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "matched\ndestroyed\n"


@needs_cc
def test_loop_local_resources_can_move_each_iteration():
    src = (
        "resource struct Value { number: i64 } "
        "fn destroy(value: *Value) -> void { print((*value).number); } "
        "fn consume(value: Value) -> void {} "
        "fn main() -> int { for index: i64 in 40..42 { "
        "let value = Value { number: index }; consume(move value); } return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "loop_resource.c")
        exe = os.path.join(
            d, "loop_resource.exe" if os.name == "nt" else "loop_resource")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "40\n41\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("resource struct Handle { value: i64 } "
         "fn main() -> int { return 0; }",
         "requires exactly one fn destroy"),
        ("resource struct Handle { value: i64 } "
         "fn destroy(value: Handle) -> void {} "
         "fn main() -> int { return 0; }",
         "requires exactly one fn destroy"),
        ("resource struct Handle { value: i64 } "
         "fn destroy(value: *Handle) -> void {} "
         "fn main() -> int { let first = Handle { value: 1 }; "
         "let second = first; return 0; }",
         "must be transferred with 'move first'"),
        ("resource struct Handle { value: i64 } "
         "fn destroy(value: *Handle) -> void {} "
         "fn main() -> int { let first = Handle { value: 1 }; "
         "let second = move first; print(first.value); return 0; }",
         "use of moved resource"),
        ("resource struct Handle { value: i64 } "
         "fn destroy(value: *Handle) -> void {} "
         "fn main() -> int { let first = Handle { value: 1 }; "
         "let second = move first; let third = move first; return 0; }",
         "already moved"),
    ],
)
def test_resource_move_errors(source, message):
    with pytest.raises(MortError, match=message):
        c_of(source)


@needs_cc
def test_generic_struct_monomorphization_run():
    src = (
        "struct Pair<Left, Right> { first: Left, second: Right } "
        "fn first_value(pair: Pair<i64, u8>) -> i64 { return pair.first; } "
        "fn main() -> int { let pair: Pair<i64, u8> = "
        "Pair<i64, u8> { first: 42, second: 7 }; "
        "print(first_value(pair)); return 0; }"
    )
    c_source = c_of(src)
    assert "struct mort_Pair_i64_u8" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "generic.c")
        exe = os.path.join(d, "generic.exe" if os.name == "nt" else "generic")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "42\n"


@needs_cc
def test_generic_function_inference_and_monomorphization_run():
    src = (
        "struct Box<T> { value: T } "
        "fn identity<T>(value: T) -> T { return value; } "
        "fn choose<Left, Right>(first: Left, ignored: Right) -> Left { "
        "return first; } "
        "fn boxed<T>(value: T) -> Box<T> { return Box<T> { value: value }; } "
        "fn unbox<T>(value: Box<T>) -> T { return value.value; } "
        "fn null<T>() -> *T { return 0 as *T; } "
        "fn sum_to<T>(value: T) -> T { if value == 0 { return 0; } "
        "return value + sum_to(value - 1); } "
        "fn main() -> int { let enabled = identity(true); "
        "let pointer: *i64 = null<i64>(); let small: u8 = identity<u8>(1); "
        "print(small); "
        "if enabled { let value = identity(20); let wrapped = boxed(21); "
        "print(value + unbox(wrapped) + choose(1, false)); } "
        "print(sum_to(6)); return 0; }"
    )
    c_source = c_of(src)
    assert "mort_identity_i64" in c_source
    assert "mort_identity_bool" in c_source
    assert "mort_boxed_i64" in c_source
    assert "mort_null_i64" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "generic_functions.c")
        exe = os.path.join(d, "generic_functions.exe" if os.name == "nt" else "generic_functions")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "1\n42\n21\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "fn same<T>(left: T, right: T) -> T { return left; } "
            "fn main() -> int { let value = same(1, true); return 0; }",
            "cannot consistently infer generic type",
        ),
        (
            "fn make<T>() -> T { return 1; } "
            "fn main() -> int { let value = make(); return 0; }",
            "cannot infer generic parameter(s) T",
        ),
        (
            "fn bad<T, T>(value: T) -> T { return value; } "
            "fn main() -> int { return 0; }",
            "has a duplicate generic parameter",
        ),
        (
            "fn id<T>(value: T) -> T { return value; } "
            "fn main() -> int { let value = id<i64, u8>(1); return 0; }",
            "expects 1 type argument(s), got 2",
        ),
        (
            "fn id(value: i64) -> i64 { return value; } "
            "fn main() -> int { return id<i64>(1); }",
            "is not generic and cannot take type arguments",
        ),
    ],
)
def test_generic_function_rejects_invalid_inference(source, message):
    with pytest.raises(MortError) as exc:
        c_of(source)
    assert message in str(exc.value)


@needs_cc
def test_generic_vec_grows_indexes_and_cleans_up():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "vec.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.vec; "
                "fn unwrap(value: Option<i64>) -> i64 { match value { "
                "Option<i64>.Some(item) => { return item; }, "
                "Option<i64>.None => { return 0; } } } "
                "fn main() -> int { let values: Vec<i64> = vec.new<i64>(); "
                "defer vec.destroy(&values); "
                "for index: u64 in 0..10 { vec.push(&values, (index as i64) + 1); } "
                "let changed = vec.set(&values, 1, 32); assert(changed); "
                "let selected = unwrap(vec.get(&values, 1)); "
                "let popped = unwrap(vec.pop(&values)); "
                "let view: []const i64 = vec.as_const_slice(&values); "
                "assert(view.len == 9); print(selected + popped); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "sizeof(int64_t)" in c_source
        assert "mort_std__vec__push_i64" in c_source
        cfile = os.path.join(d, "vec.c")
        exe = os.path.join(d, "vec.exe" if os.name == "nt" else "vec")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_generic_map_grows_updates_and_looks_up():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "map.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.map; "
                "fn unwrap(value: Option<i64>) -> i64 { match value { "
                "Option<i64>.Some(item) => { return item; }, "
                "Option<i64>.None => { return 0; } } } "
                "fn main() -> int { let values: Map<i64, i64> = map.new<i64, i64>(); "
                "defer map.destroy(&values); "
                "for index: i64 in 0..10 { "
                "map.insert(&values, index, index * 2); } "
                "let created = map.insert(&values, 2, 40); assert(!created); "
                "assert(map.contains(&values, 2)); "
                "assert(!map.contains(&values, 99)); "
                "print(unwrap(map.get(&values, 1)) + unwrap(map.get(&values, 2))); "
                "return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_Map_i64_i64" in c_source
        assert "mort_std__map__insert_i64_i64" in c_source
        cfile = os.path.join(d, "map.c")
        exe = os.path.join(d, "map.exe" if os.name == "nt" else "map")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_portable_env_process_and_generic_math_modules():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "portable_std.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.env; import std.math; import std.process; "
                "fn main() -> int { if env.exists(\"PATH\") { "
                "let value = math.gcd(84, 30) + math.pow(2, 3) + "
                "math.abs(0 - 4) + math.clamp(10, 0, 2) + math.max(1, 22); "
                "print(value); } else { print(0); } return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "getenv(const char*)" in c_source
        assert "system(const char*)" in c_source
        assert "mort_std__math__gcd_i64" in c_source
        cfile = os.path.join(d, "portable_std.c")
        exe = os.path.join(d, "portable_std.exe" if os.name == "nt" else "portable_std")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "42\n"


@needs_cc
def test_portable_file_and_time_modules():
    with tempfile.TemporaryDirectory() as d:
        data_path = os.path.join(d, "mort_io.txt").replace("\\", "/")
        path = os.path.join(d, "file_time.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.fs; import std.time; "
                "fn save(path: *u8) -> void { let file = fs.open(path, \"wb\"); "
                "defer fs.close(&file); assert(fs.is_open(&file)); "
                "const text: *u8 = \"Mort\"; "
                "let bytes: []const u8 = slice(text as *const u8, 4); "
                "assert(fs.write(&file, bytes) == 4); assert(fs.flush(&file)); } "
                "fn load(path: *u8) -> void { let file = fs.open(path, \"rb\"); "
                "defer fs.close(&file); let data = alloc(5) as *u8; defer free(data); "
                "let buffer: []u8 = slice(data, 5); let count = fs.read(&file, buffer); "
                "data[count] = 0; println(data); } "
                f"fn main() -> int {{ save(\"{data_path}\"); load(\"{data_path}\"); "
                "assert(time.unix_seconds() > 0); "
                "assert(time.cpu_milliseconds() >= 0); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_file_open" in c_source
        assert "#include <time.h>" in c_source
        cfile = os.path.join(d, "file_time.c")
        exe = os.path.join(d, "file_time.exe" if os.name == "nt" else "file_time")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "Mort\n"


@needs_cc
def test_portable_random_and_byte_slice_modules():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "random_bytes.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.bytes; import std.random; "
                "fn main() -> int { let first = random.seeded(42); "
                "let second = random.seeded(42); "
                "assert(random.next_u64(&first) == random.next_u64(&second)); "
                "assert(random.next_u32(&first) == random.next_u32(&second)); "
                "assert(random.between(&first, 10, 20) >= 10); "
                "assert(random.between(&second, 10, 20) < 20); "
                "let left: [u8; 8] = [0; 8]; let right: [u8; 8] = [0; 8]; "
                "let left_slice: []u8 = slice(&left[0], 8); "
                "let right_slice: []u8 = slice(&right[0], 8); "
                "random.fill(&first, left_slice); random.fill(&second, right_slice); "
                "let left_const: []const u8 = slice(&left[0] as *const u8, 8); "
                "let right_const: []const u8 = slice(&right[0] as *const u8, 8); "
                "assert(bytes.equal(left_const, right_const)); bytes.zero(right_slice); "
                "assert(bytes.copy(right_slice, left_const) == 8); "
                "assert(bytes.equal(left_const, right_const)); print(left[0] as i64); "
                "return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_std__random__next_u64" in c_source
        assert "mort_std__bytes__copy" in c_source
        cfile = os.path.join(d, "random_bytes.c")
        exe = os.path.join(d, "random_bytes.exe" if os.name == "nt" else "random_bytes")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip().isdigit()


@needs_cc
def test_generic_slice_algorithm_module():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "algorithms.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.algorithm; import std.option; "
                "fn found(value: Option<u64>) -> u64 { match value { "
                "Option<u64>.Some(index) => { return index; }, "
                "Option<u64>.None => { return 99; } } } "
                "fn main() -> int { let values: [i64; 6] = [4, 1, 6, 2, 5, 3]; "
                "let mutable: []i64 = slice(&values[0], 6); algorithm.sort(mutable); "
                "let view: []const i64 = slice(&values[0] as *const i64, 6); "
                "assert(algorithm.contains(view, 4)); "
                "assert(found(algorithm.index_of(view, 4)) == 3); "
                "algorithm.reverse(mutable); print(values[0]); print(values[5]); "
                "return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_std__algorithm__sort_i64" in c_source
        assert "mort_std__algorithm__index_of_i64" in c_source
        cfile = os.path.join(d, "algorithms.c")
        exe = os.path.join(d, "algorithms.exe" if os.name == "nt" else "algorithms")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "6\n1\n"


@needs_cc
def test_generic_option_and_result_enums_run():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "generic_enums.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.option; import std.result; "
                "fn option_value(value: Option<i64>) -> i64 { match value { "
                "Option<i64>.Some(inner) => { return inner; }, "
                "Option<i64>.None => { return 0; } } } "
                "fn result_value(value: Result<i64, *u8>) -> i64 { match value { "
                "Result<i64, *u8>.Ok(inner) => { return inner; }, "
                "Result<i64, *u8>.Err(message) => { println(message); return 0; } } } "
                "fn nested_value(value: Option<Result<i64, *u8>>) -> i64 { match value { "
                "Option<Result<i64, *u8>>.Some(inner) => { return result_value(inner); }, "
                "Option<Result<i64, *u8>>.None => { return 0; } } } "
                "fn main() -> int { "
                "let option: Option<i64> = Option<i64>.Some(20); "
                "let result: Result<i64, *u8> = Result<i64, *u8>.Ok(22); "
                "let nested: Option<Result<i64, *u8>> = "
                "Option<Result<i64, *u8>>.Some(Result<i64, *u8>.Ok(42)); "
                "print(option_value(option) + result_value(result)); "
                "print(nested_value(nested)); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_Option_i64" in c_source
        assert "mort_Result_i64_ptr_u8" in c_source
        cfile = os.path.join(d, "generic_enums.c")
        exe = os.path.join(d, "generic_enums.exe" if os.name == "nt" else "generic_enums")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "42\n42\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "enum Empty<T> {} fn main() -> int { return 0; }",
            "must have at least one variant",
        ),
        (
            "enum Duplicate<T> { Value(T), Value(T) } "
            "fn main() -> int { return 0; }",
            "has a duplicate variant",
        ),
        (
            "enum Box<T> { Value(Missing) } fn use(value: Box<i64>) -> void {} "
            "fn main() -> int { return 0; }",
            "has unknown payload type Missing",
        ),
        (
            "enum Loop<T> { More(Loop<T>), Done } "
            "fn use(value: Loop<i64>) -> void {} fn main() -> int { return 0; }",
            "cannot contain itself by value",
        ),
    ],
)
def test_generic_enum_rejects_invalid_definitions(source, message):
    with pytest.raises(MortError) as exc:
        c_of(source)
    assert message in str(exc.value)


@needs_cc
def test_result_try_propagates_success_and_error():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "result_try.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.result; "
                "fn parse(ok: bool) -> Result<i64, *u8> { "
                "if ok { return Result<i64, *u8>.Ok(21); } "
                "return Result<i64, *u8>.Err(\"bad\"); } "
                "fn twice(ok: bool) -> Result<i64, *u8> { "
                "defer println(\"outer cleanup\"); if true { "
                "defer println(\"inner cleanup\"); let value = try parse(ok); "
                "return Result<i64, *u8>.Ok(value * 2); } "
                "return Result<i64, *u8>.Err(\"unreachable\"); } "
                "fn show(value: Result<i64, *u8>) -> void { match value { "
                "Result<i64, *u8>.Ok(inner) => { print(inner); }, "
                "Result<i64, *u8>.Err(message) => { println(message); } } } "
                "fn main() -> int { show(twice(true)); show(twice(false)); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_try_0" in c_source
        cfile = os.path.join(d, "result_try.c")
        exe = os.path.join(d, "result_try.exe" if os.name == "nt" else "result_try")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == (
        "inner cleanup\nouter cleanup\n42\n"
        "inner cleanup\nouter cleanup\nbad\n"
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "fn main() -> int { let value = try 1; return value; }",
            "try expects a Result<Value, Error> expression",
        ),
        (
            "import std.result; fn get() -> Result<i64, *u8> { "
            "return Result<i64, *u8>.Ok(1); } "
            "fn main() -> int { return try get(); }",
            "try requires the enclosing function to return Result",
        ),
        (
            "import std.result; fn get() -> Result<i64, *u8> { "
            "return Result<i64, *u8>.Ok(1); } "
            "fn convert() -> Result<i64, i64> { let value = try get(); "
            "return Result<i64, i64>.Ok(value); } "
            "fn main() -> int { return 0; }",
            "try error type *u8 does not match return error type i64",
        ),
    ],
)
def test_result_try_rejects_invalid_use(source, message):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "invalid_try.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        with pytest.raises(MortError) as exc:
            mortc.compile_files_to_c([path])
    assert message in str(exc.value)


@needs_cc
def test_try_operates_in_eager_expression_and_loop_contexts():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "general_try.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.result; struct Pair { value: i64 } "
                "fn number(ok: bool, value: i64) -> Result<i64, *u8> { "
                "if ok { return Result<i64, *u8>.Ok(value); } "
                "return Result<i64, *u8>.Err(\"bad\"); } "
                "fn flag(ok: bool) -> Result<bool, *u8> { "
                "if ok { return Result<bool, *u8>.Ok(true); } "
                "return Result<bool, *u8>.Err(\"bad\"); } "
                "fn condition(ok: bool, count: *i64) -> Result<bool, *u8> { "
                "if !ok { return Result<bool, *u8>.Err(\"bad\"); } "
                "let keep = *count < 2; *count += 1; "
                "return Result<bool, *u8>.Ok(keep); } "
                "fn accept(value: i64) -> i64 { return value; } "
                "fn calculate(ok: bool) -> Result<i64, *u8> { "
                "defer println(\"cleanup\"); let total = try number(ok, 1); "
                "total += try number(ok, 2); "
                "total = total + (try number(ok, 3)); "
                "let pair = Pair { value: try number(ok, 4) }; "
                "total += pair.value; "
                "let values: [i64; 2] = [try number(ok, 5), try number(ok, 6)]; "
                "total += values[0] + values[1]; "
                "total += values[try number(ok, 0)]; "
                "match try number(ok, 8) { "
                "8 => { total += 8; }, _ => {} } "
                "for index in 0..try number(ok, 3) { total += index; } "
                "let count: i64 = 0; while try condition(ok, &count) { total += 1; } "
                "if try flag(ok) { total += 1; } "
                "total += accept(try number(ok, 7)); "
                "return Result<i64, *u8>.Ok(total); } "
                "fn show(value: Result<i64, *u8>) -> void { match value { "
                "Result<i64, *u8>.Ok(inner) => { print(inner); }, "
                "Result<i64, *u8>.Err(message) => { println(message); } } } "
                "fn main() -> int { show(calculate(true)); show(calculate(false)); "
                "return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert c_source.count("mort_try_") >= 10
        assert "while (true)" in c_source
        cfile = os.path.join(d, "general_try.c")
        exe = os.path.join(d, "general_try.exe" if os.name == "nt" else "general_try")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "cleanup\n47\ncleanup\nbad\n"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "import std.result; fn get() -> Result<i64, *u8> { "
            "return Result<i64, *u8>.Ok(1); } "
            "fn check() -> Result<i64, *u8> { defer print(try get()); "
            "return Result<i64, *u8>.Ok(0); } fn main() -> int { return 0; }",
            "try is not allowed inside defer",
        ),
    ],
)
def test_try_rejects_contexts_with_deferred_or_conditional_evaluation(source, message):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "try_context.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        with pytest.raises(MortError, match=message):
            mortc.compile_files_to_c([path])


@needs_cc
def test_try_preserves_short_circuit_evaluation():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "short_try.mx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "import std.result; "
                "fn flag(ok: bool, calls: *i64) -> Result<bool, *u8> { "
                "*calls += 1; if ok { return Result<bool, *u8>.Ok(true); } "
                "return Result<bool, *u8>.Err(\"called\"); } "
                "fn check(run: bool, ok: bool) -> Result<i64, *u8> { "
                "let calls: i64 = 0; let both = run && try flag(ok, &calls); "
                "let either = true || try flag(false, &calls); "
                "if both || either { return Result<i64, *u8>.Ok(calls); } "
                "return Result<i64, *u8>.Ok(99); } "
                "fn show(value: Result<i64, *u8>) -> void { match value { "
                "Result<i64, *u8>.Ok(inner) => { print(inner); }, "
                "Result<i64, *u8>.Err(message) => { println(message); } } } "
                "fn main() -> int { show(check(false, false)); "
                "show(check(true, true)); show(check(true, false)); return 0; }"
            )
        c_source = mortc.compile_files_to_c([path])
        assert "mort_short_" in c_source
        cfile = os.path.join(d, "short_try.c")
        exe = os.path.join(d, "short_try.exe" if os.name == "nt" else "short_try")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run(
            [*_CC, cfile, "-o", exe, "-O2", "-std=c11", "-Wall", "-Werror"],
            check=True,
        )
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "0\n1\ncalled\n"


@needs_cc
def test_defer_runs_on_function_return():
    src = (
        "fn choose(flag: bool) -> i64 { "
        "defer println(\"cleanup\"); "
        "if flag { return 42; } return 0; } "
        "fn main() -> int { print(choose(true)); return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "defer.c")
        exe = os.path.join(d, "defer.exe" if os.name == "nt" else "defer")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "cleanup\n42\n"


@needs_cc
def test_lexical_defer_cleans_up_return_break_and_continue():
    src = (
        "fn value() -> i64 { println(\"evaluate\"); return 42; } "
        "fn returned() -> i64 { defer println(\"outer-return\"); "
        "if true { defer println(\"inner-return\"); return value(); } return 0; } "
        "fn loops() -> void { let index = 0; while index < 3 { "
        "defer println(\"iteration\"); index = index + 1; "
        "if index == 1 { continue; } if index == 2 { break; } } } "
        "fn main() -> int { loops(); print(returned()); return 0; }"
    )
    c_source = c_of(src)
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "lexical_defer.c")
        exe = os.path.join(d, "lexical_defer.exe" if os.name == "nt" else "lexical_defer")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == (
        "iteration\niteration\nevaluate\ninner-return\nouter-return\n42\n"
    )


@needs_cc
def test_c_abi_types_and_const_pointer_run():
    src = (
        "extern fn strlen(text: *const c_char) -> c_size; "
        "fn main() -> int { let size: c_size = strlen(\"Mort\" as *const c_char); "
        "print(size); return 0; }"
    )
    c_source = c_of(src)
    assert "extern size_t strlen(const char*);" in c_source
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "ffi_types.c")
        exe = os.path.join(d, "ffi_types.exe" if os.name == "nt" else "ffi_types")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == "4\n"


# ---------- front-end: errors ----------

@pytest.mark.parametrize("src, needle", [
    ("fn main() -> int { return true; }", "return type mismatch"),
    ("fn main() -> int { let x = 1; if x { return 0; } return 0; }", "must be a bool"),
    ("fn main() -> int { print(true); return 0; }", "print expects an integer"),
    ("fn main() -> int { return y; }", "undefined variable"),
    ("fn main() -> int { let x = 1; let x = 2; return 0; }", "already declared"),
    ("fn f() -> int { return 0; }", "no 'main'"),
    ("fn main() -> bool { return true; }", "'main' must return int"),
    ("fn main() -> int { return 1 + true; }", "requires int operands"),
    ("fn main() -> int { let x = 1; let y = *x; return 0; }", "dereference"),
    ("fn main() -> int { let p = &5; return 0; }", "address of"),
    ("fn main() -> int { let a: u8 = 1; let b: i32 = 2; let c = a + b; return 0; }",
     "mismatched integer types"),
    ("fn main() -> int { let b = true; let x = b as i32; return 0; }", "cannot cast"),
    ("fn main() -> int { let value = 1.5 + 1; return 0; }",
     "cannot mix integer and float operands"),
    ("fn main() -> int { let left: f32 = 1.0; let right: f64 = 2.0; "
     "let value = left + right; return 0; }", "mismatched float types"),
    ("fn main() -> int { let value = 5.0 % 2.0; return 0; }",
     "operator '%' is not defined for floats"),
    ("type First = Second; type Second = First; "
     "fn main() -> int { return 0; }", "cyclic type alias"),
    ("type Missing = Nope; fn main() -> int { return 0; }",
     "type alias 'Missing' has invalid target Nope"),
    ("type Value = i64; struct Value { item: i64 } "
     "fn main() -> int { return 0; }", "struct 'Value' is already defined"),
    ("fn main() -> int { let p: *i32 = 0 as *i32; print(p); return 0; }",
     "print expects an integer"),
    ("struct P { x: i64 } fn main() -> int { let p: P = P { x: 1, y: 2 }; return 0; }",
     "no field 'y'"),
    ("struct P { x: i64, y: i64 } fn main() -> int { let p: P = P { x: 1 }; return 0; }",
     "missing field"),
    ("struct P { x: i64 } fn main() -> int { let p: P = P { x: 1 }; print(p.z); return 0; }",
     "no field 'z'"),
    ("struct P { x: i64 } fn main() -> int { let p: P = P { x: 1 }; let q: *P = &p; print(q.x); return 0; }",
     "through a pointer"),
    ("fn main() -> int { let a: Nope = 0; return 0; }", "unknown type"),
    ("fn main() -> int { outb(0x20); return 0; }", "outb expects 2 arguments"),
    ("fn main() -> int { let x: u8 = inb(1, 2); return 0; }", "inb expects 1 argument"),
    ("fn outb() { } fn main() -> int { return 0; }", "already defined"),
    ("fn main() -> int { outb(0x12345, 0x20); return 0; }", "does not fit in u16"),
    ("fn main() -> int { outb(0x20, 0x123); return 0; }", "does not fit in u8"),
    ("fn main() -> int { outw(0x1F0); return 0; }", "outw expects 2 arguments"),
    ("fn main() -> int { let x: u16 = inw(1, 2); return 0; }", "inw expects 1 argument"),
    ("fn main() -> int { outl(0xCF8); return 0; }", "outl expects 2 arguments"),
    ("fn main() -> int { let x: u32 = inl(1, 2); return 0; }", "inl expects 1 argument"),
    ("fn main() -> int { outl(0x12345, 0x20); return 0; }", "does not fit in u16"),
    ("fn inw() { } fn main() -> int { return 0; }", "already defined"),
    ("fn main() -> int { outw(0x12345, 0x20); return 0; }", "does not fit in u16"),
    ("fn main() -> int { outw(0x1F0, 0x12345); return 0; }", "does not fit in u16"),
    ("fn main() -> int { let x: u8 = 300; print(x); return 0; }", "does not fit in u8"),
    ("fn main() -> int { let x: u8 = 200 + 100; print(x); return 0; }", "does not fit in u8"),
    ("fn main() -> int { let x: i8 = 0 - 200; print(x); return 0; }", "does not fit in i8"),
    # C's % takes the dividend's sign: (0-129) % 256 == -129 in C, not 127
    ("fn main() -> int { let x: i8 = (0 - 129) % 256; return 0; }", "does not fit in i8"),
    # a global must be initialised with a constant, not another variable
    ("let a: i64 = 1; let b: i64 = a; fn main() -> int { return 0; }", "must be initialised with a constant"),
    ("let x: i64 = 0; fn x() { } fn main() -> int { return 0; }", "conflicts with another name"),
    # arrays
    ("fn main() -> int { let x: i32 = 1; print(x[0] as i64); return 0; }", "not an array"),
    ("fn main() -> int { let a: [i32; 3] = [1, 2]; return 0; }", "expects 3 elements"),
    ("fn main() -> int { let a: [i32; 2] = [1, 2]; let b: [i32; 2] = [3, 4]; a = b; return 0; }",
     "cannot assign to a whole array"),
    ("fn main() -> int { let a: [u8; 2] = [1, 300]; return 0; }", "does not fit in u8"),
    ("fn main() -> int { let a: [i32; 2] = [1, 2]; print(a[true] as i64); return 0; }",
     "index must be an integer"),
    ("fn f(a: [i32; 2]) { } fn main() -> int { return 0; }", "cannot be an array"),
    ("fn f() -> [i32; 2] { let a: [i32; 2] = [1, 2]; return a; } fn main() -> int { return 0; }",
     "cannot return an array"),
    ("fn main() -> int { let x = true & false; return 0; }", "requires int operands"),
    # a literal operand unified to a narrow type must still fit it
    ("fn main() -> int { let a: u8 = 1; print((a | 300) as i64); return 0; }", "does not fit in u8"),
    ("fn main() -> int { let a: u8 = 1; let b = a + 300; return 0; }", "does not fit in u8"),
    # composed literal expressions are folded, so overflow can't sneak through
    ("fn main() -> int { let x: u8 = 1 << 8; print(x); return 0; }", "does not fit in u8"),
    ("fn main() -> int { let a: u8 = 1; print((a | (200 << 4)) as i64); return 0; }", "does not fit in u8"),
    ("fn main() -> int { let x: u8 = 1 << (0 - 1); print(x); return 0; }", "shift count cannot be negative"),
    ("fn main() -> int { let x: u8 = 1 << 1000000; print(x); return 0; }", "does not fit in u8"),
    ("extern fn f(x: void); fn main() -> int { return 0; }", "cannot have type void"),
    ("extern fn f() -> i64; fn f() -> i64 { return 1; } fn main() -> int { return 0; }",
     "already defined"),
    ("extern fn main() -> i64;", "no 'main' function defined"),
    ("fn main() -> int { break; return 0; }", "only allowed inside a loop"),
    ("fn main() -> int { continue; return 0; }", "only allowed inside a loop"),
    ("extern fn auto() -> i32; fn main() -> int { return 0; }", "reserved by C"),
    ("fn main() -> int { let p: *void = 0 as *void; let x = p[0]; return 0; }",
     "cannot index a *void pointer"),
    ("fn value() -> i64 { let x = 1; } fn main() -> int { return 0; }",
     "may finish without returning i64"),
    ("fn main() -> int { println(1); return 0; }", "println expects a string"),
    ("fn main() -> int { assert(1); return 0; }", "assert expects a bool"),
    ("fn main() -> int { let a: [u8; 2] = [1, 2]; print(a[2] as i64); return 0; }",
     "out of bounds for length 2"),
    ("enum State { A, B } fn main() -> int { let s: State = State.A; "
     "match s { State.A => { print(1); } } return 0; }", "non-exhaustive match"),
    ("enum State { A } fn main() -> int { let s: State = State.Nope; return 0; }",
     "has no variant"),
    ("enum Value { Some(i64), None } fn main() -> int { "
     "let value: Value = Value.Some(); return 0; }", "expects 1 payload"),
    ("enum Value { Some(i64), None } fn main() -> int { "
     "let value: Value = Value.Some(1); match value { "
     "Value.Some(1) => { print(1); }, Value.None => { print(0); } } return 0; }",
     "payload match patterns require one binding name"),
    ("struct Box<T> { value: T } fn main() -> int { "
     "let box: Box<i64, u8> = 0; return 0; }", "unknown type"),
    ("fn main() -> int { let p: *const u8 = \"Mort\"; p[0] = 0; return 0; }",
     "cannot assign through a const pointer"),
    ("fn main() -> int { const value = 1; value = 2; return 0; }",
     "cannot assign to const binding 'value'"),
    ("struct Point { x: i64 } fn main() -> int { "
     "const point = Point { x: 1 }; point.x = 2; return 0; }",
     "cannot assign to const binding 'point'"),
    ("fn main() -> int { const values: [i64; 2] = [1, 2]; "
     "values[0] = 3; return 0; }", "cannot assign to const binding 'values'"),
    ("const GLOBAL: i64 = 1; fn main() -> int { GLOBAL = 2; return 0; }",
     "cannot assign to const binding 'GLOBAL'"),
    ("fn main() -> int { const value = 1; let pointer = &value; "
     "*pointer = 2; return 0; }", "cannot assign through a const pointer"),
    ("fn main() -> int { let s: []const u8 = slice(\"Mort\" as *const u8, 4); "
     "s[0] = 0; return 0; }", "cannot assign through a const slice"),
])
def test_type_errors(src, needle):
    with pytest.raises(MortError) as exc:
        c_of(src)
    assert needle in exc.value.msg


# ---------- end-to-end (needs a C compiler) ----------

EXPECTED = {
    "hello.mx": "42\n",
    "fib.mx": "0\n1\n1\n2\n3\n5\n8\n13\n21\n34\n",
    "factorial.mx": "120\n",
    "pointers.mx": "99\n99\n",
    "types.mx": "255\n1000000\n-300\n255\n",
    "structs.mx": "3\n7\n13\n100\n",
    "asm.mx": "1\n2\n",
    "arrays.mx": "0\n55\n100\n",
    "interop.mx": "42\n",
    "loops.mx": "13\n",
    "enums.mx": "1\n",
    "slices.mx": "42\n",
    "result.mx": "42\ninvalid input\n",
    "generics.mx": "42\n",
    "defer.mx": "inner cleanup\nouter cleanup\n42\n",
    "collections.mx": "42\n42\n",
    "floats.mx": "42.5\n",
    "tuples.mx": "43\n42\n",
    "resources.mx": "42\nreleased\n",
}


@needs_cc
@pytest.mark.parametrize("name, expected", EXPECTED.items())
def test_examples_run(name, expected):
    src_path = os.path.join(ROOT, "examples", name)
    c_source = mortc.compile_files_to_c([src_path])
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "out.c")
        exe = os.path.join(d, "out.exe" if os.name == "nt" else "out")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        subprocess.run([*_CC, cfile, "-o", exe, "-O2", "-std=c11"], check=True)
        result = subprocess.run([exe], capture_output=True, text=True)
    assert result.stdout == expected


# ---------- allow running without pytest ----------

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
