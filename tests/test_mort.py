"""Tests for the Mort compiler.

Two layers:
  1. Front-end tests (always run): valid programs produce C; invalid programs
     raise MortError with the right message. No C compiler needed.
  2. End-to-end tests (skipped if no cc/gcc/clang): compile each example to a
     native binary and check its stdout.

Run with:  python -m pytest tests/ -v      (or)   python tests/test_mort.py
"""
import os
import subprocess
import struct
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mort.errors import MortError            # noqa: E402
import mortc                                 # noqa: E402


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
    assert "int32_t m_b = (0 - 1);" in c


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
    # a folded bitwise literal that DOES fit is accepted (retagged to u8, so the
    # result is narrow-wrapped)
    c = c_of("fn main() -> int { let x: u8 = 200 | 100; print(x); return 0; }")  # 236
    assert "uint8_t m_x = ((uint8_t)(200 | 100));" in c


def test_shift_and_not_codegen():
    c = c_of("fn main() -> int { let a: u32 = 1; print((a << 4) as i64); print((~a) as i64); return 0; }")
    assert "(m_a << 4)" in c
    assert "(~m_a)" in c


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


def test_port_io_helpers_emitted_per_builtin():
    only_in = c_free("fn kmain() { let s: u8 = inb(0x60); }")
    assert "mort_inb(uint16_t" in only_in
    assert "mort_outb(uint16_t" not in only_in   # not dragged in by inb alone

    only_out = c_free("fn kmain() { outb(0x20, 0x20); }")
    assert "mort_outb(uint16_t" in only_out
    assert "mort_inb(uint16_t" not in only_out


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
}


@needs_cc
@pytest.mark.parametrize("name, expected", EXPECTED.items())
def test_examples_run(name, expected):
    src_path = os.path.join(ROOT, "examples", name)
    with open(src_path, encoding="utf-8") as fh:
        c_source = c_of(fh.read())
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
