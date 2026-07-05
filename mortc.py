#!/usr/bin/env python3
"""mortc — the Mort compiler driver.

Usage:
    python mortc.py program.mx              # compile to a native executable
    python mortc.py program.mx --run        # compile, then run it
    python mortc.py program.mx --emit-c     # print the generated C and stop
    python mortc.py program.mx -o out        # choose the output name

Compilation is a two-step pipeline: Mort source -> C (always), then C -> native
binary via a system C compiler (cc/gcc/clang). If no C compiler is found, the
generated C is written next to your source so you can build it yourself.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mort.lexer import Lexer          # noqa: E402
from mort.parser import Parser        # noqa: E402
from mort.typechecker import Checker  # noqa: E402
from mort.codegen import CodeGen      # noqa: E402
from mort.errors import MortError     # noqa: E402


def compile_to_c(src, freestanding=False):
    """Run the full front-end and return generated C source (or raise MortError)."""
    tokens = Lexer(src).tokenize()
    program = Parser(tokens).parse()
    Checker(program, freestanding=freestanding).check()
    return CodeGen(program, freestanding=freestanding).generate()


def is_zig(cc):
    """True if the compiler argv is Zig's clang (supports easy cross-compiles).

    Matches only a real Zig invocation — the `zig` executable itself or the
    `ziglang` Python module — not any path that merely contains 'zig'
    (e.g. C:/tools/zigzag/cc).
    """
    if "ziglang" in cc:
        return True
    exe = os.path.splitext(os.path.basename(cc[0]))[0].lower() if cc else ""
    return exe == "zig"


def find_c_compiler():
    """Return an argv prefix for a usable C compiler, or None.

    Accepts gcc/clang/cc directly, and also ``zig cc`` — Zig ships a full
    clang-based C compiler in one portable binary, the easiest option to
    install on Windows.
    """
    for cc in ("cc", "gcc", "clang"):
        found = shutil.which(cc)
        if found:
            return [found]
    if shutil.which("zig"):
        return ["zig", "cc"]
    # `pip install ziglang` puts a full C compiler behind a Python module —
    # a zero-fuss option on Windows where no system compiler is present.
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mortc", description="The Mort compiler.")
    ap.add_argument("file", help="path to a .mx source file")
    ap.add_argument("-o", "--output", help="output file name")
    ap.add_argument("--emit-c", action="store_true", help="print generated C and exit")
    ap.add_argument("--run", action="store_true", help="run the program after building")
    ap.add_argument("--freestanding", action="store_true",
                    help="compile to a bare-metal object file (no libc, no main)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.file):
        print(f"mortc: cannot find file {args.file!r}", file=sys.stderr)
        return 1
    if args.freestanding and args.run:
        print("mortc: --run cannot be used with --freestanding (nothing to run yet)",
              file=sys.stderr)
        return 1

    with open(args.file, "r", encoding="utf-8") as fh:
        src = fh.read()

    try:
        c_source = compile_to_c(src, freestanding=args.freestanding)
    except MortError as e:
        print(f"mortc: {e.format()}", file=sys.stderr)
        return 1

    if args.emit_c:
        sys.stdout.write(c_source)
        return 0

    base = os.path.splitext(os.path.basename(args.file))[0]

    cc = find_c_compiler()
    if cc is None:
        out = args.output or (base + ".o" if args.freestanding else base)
        fallback = base + ".c"
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        if args.freestanding:
            hint = f"gcc -ffreestanding -c {fallback} -o {out}"
        else:
            hint = f"gcc {fallback} -o {out}"
        print(
            f"mortc: no C compiler (cc/gcc/clang) found on PATH.\n"
            f"       Wrote generated C to {fallback!r} — compile it with e.g. "
            f"`{hint}`.",
            file=sys.stderr,
        )
        return 2

    if args.freestanding:
        out = args.output or (base + ".o")
        # Cross-compile a real x86_64 bare-metal object when Zig is the backend;
        # otherwise just build a freestanding object for the host.
        cmd = list(cc)
        if is_zig(cc):
            cmd += ["-target", "x86_64-freestanding-none"]
        cmd += ["-ffreestanding", "-O2", "-std=c11", "-c"]
    else:
        out = args.output or (base + (".exe" if os.name == "nt" else ""))
        cmd = [*cc, "-O2", "-std=c11"]

    tmp = tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8")
    try:
        tmp.write(c_source)
        tmp.close()
        try:
            subprocess.run([*cmd, tmp.name, "-o", out], check=True)
        except subprocess.CalledProcessError:
            print("mortc: the C backend failed to compile the generated code", file=sys.stderr)
            return 1
    finally:
        os.unlink(tmp.name)

    print(f"mortc: wrote {out}")
    sys.stdout.flush()  # keep our message ahead of the program's own output

    if args.run:
        exe = os.path.abspath(out)
        result = subprocess.run([exe])
        return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
