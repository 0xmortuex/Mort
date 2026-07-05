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


def test_bool_and_control_flow():
    c = c_of(
        "fn main() -> int { let b = true; if b && (1 < 2) { print(1); } else { print(0); } return 0; }"
    )
    assert "bool m_b = true;" in c
    assert "if ((m_b && (1 < 2)))" in c


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
    # the untyped literal 5 must adopt u8, so no cast is needed
    c = c_of("fn main() -> int { let a: u8 = 250; let b: u8 = a + 5; print(b); return 0; }")
    assert "uint8_t m_b = (m_a + 5);" in c


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
