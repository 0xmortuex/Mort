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
from mort.project import load_manifest, resolve_project  # noqa: E402
from mort.formatter import format_source                 # noqa: E402
from mort.lsp import Server, diagnostics_for_document   # noqa: E402
from mort.fuzz import run_fuzz                          # noqa: E402


def c_of(src):
    return mortc.compile_to_c(src)


# End-to-end tests need a C compiler; skip them cleanly when none is present.
_CC = mortc.find_c_compiler()
needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

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
    assert "int64_t m_y = (m_x + 1);" in c


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
    assert "((uint64_t)m_p)" in c


def test_literal_coercion_in_arithmetic():
    # the untyped literal 5 adopts u8; the u8 result is narrowed back so C's
    # promotion to int doesn't leak (250 + 5 wraps to 255 in u8)
    c = c_of("fn main() -> int { let a: u8 = 250; let b: u8 = a + 5; print(b); return 0; }")
    assert "uint8_t m_b = ((uint8_t)(m_a + 5));" in c


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


def test_global_variable_codegen():
    c = c_of("let counter: i64 = 0; fn main() -> int { counter = counter + 5; print(counter); return 0; }")
    assert "static int64_t m_counter = 0;" in c
    assert "m_counter = (m_counter + 5);" in c


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
    assert "(m_a & m_b)" in c
    assert "(m_a | m_b)" in c
    assert "(m_a ^ m_b)" in c


def test_const_fold_bitwise_in_range():
    # a folded bitwise literal that DOES fit is accepted and emitted as its value
    c = c_of("fn main() -> int { let x: u8 = 200 | 100; print(x); return 0; }")  # 236
    assert "uint8_t m_x = ((uint8_t)236);" in c


def test_shift_and_not_codegen():
    # a runtime shift casts its left operand to the result type (right width)
    c = c_of("fn main() -> int { let a: u32 = 1; print((a << 4) as i64); print((~a) as i64); return 0; }")
    assert "((uint32_t)(m_a) << 4)" in c
    assert "(~m_a)" in c


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
    assert "for (int64_t m_i = 0; m_i < 5; m_i = m_i + 1)" in c


def test_for_loop_var_type_from_bound():
    # a typed (non-literal) bound gives the loop variable that type -> usable
    # with same-typed data (e.g. a u32 counter), no cast needed
    c = c_of("fn main() -> int { let n: u32 = 3; let s: u32 = 0; for i in 0..n { s = s + i; } print(s as i64); return 0; }")
    assert "for (uint32_t m_i = 0; m_i < m_n; m_i = m_i + 1)" in c


def test_for_loop_annotated_type():
    c = c_of("fn main() -> int { let s: u32 = 0; for i: u32 in 0..2000 { s = s + i; } print(s as i64); return 0; }")
    assert "for (uint32_t m_i = 0; m_i < 2000; m_i = m_i + 1)" in c


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


@needs_cc
def test_freestanding_object_builds():
    src_path = os.path.join(ROOT, "examples", "kernel.mx")
    with open(src_path, encoding="utf-8") as fh:
        c_source = c_free(fh.read())
    with tempfile.TemporaryDirectory() as d:
        cfile = os.path.join(d, "k.c")
        obj = os.path.join(d, "k.o")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        cmd = list(_CC)
        if mortc.is_zig(_CC):
            cmd += ["-target", "x86_64-freestanding-none"]
        cmd += ["-ffreestanding", "-O2", "-std=c11", "-c", cfile, "-o", obj]
        subprocess.run(cmd, check=True)
        data = open(obj, "rb").read()
    assert len(data) > 0
    # With the Zig backend we pin the exact bare-metal target, so assert the
    # object really is a 64-bit x86-64 ELF (not just "some ELF").
    if mortc.is_zig(_CC):
        assert data[:4] == b"\x7fELF"                         # ELF magic
        assert data[4] == 2                                   # ELFCLASS64
        assert struct.unpack("<H", data[18:20])[0] == 0x3E    # EM_X86_64


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
    assert changed_lock_data["lock_version"] == 2
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
    source = 'fn main() -> int {  \n// { is not structure\nprintln("}");\nreturn 0;\n}\n'
    assert format_source(source) == (
        'fn main() -> int {\n'
        '    // { is not structure\n'
        '    println("}");\n'
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


def test_deterministic_frontend_fuzzer_and_cli(capsys):
    result = run_fuzz(cases=100, seed=42)
    assert result["cases"] == 100
    assert result["accepted"] + result["rejected"] == 100
    assert result["accepted"] >= 50
    assert mortc.main(["fuzz", "--cases", "20", "--seed", "7"]) == 0
    assert "fuzzed 20 case(s)" in capsys.readouterr().out


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
            "try is currently allowed only as a let initializer",
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
