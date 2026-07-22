# Mort

[![CI](https://github.com/0xmortuex/Mort/actions/workflows/ci.yml/badge.svg)](https://github.com/0xmortuex/Mort/actions/workflows/ci.yml)
&nbsp;![tests](https://img.shields.io/badge/tests-157%20passing-brightgreen)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue)

**A small, statically-typed programming language that compiles to C.** Written from scratch in Python — lexer, parser, type checker, and a C code generator, no libraries.

Mort exists for a bigger goal: **build a language, then write an operating system kernel in it** — and it now does exactly that. The same compiler that runs `hello.mx` also builds [MORT OS](kernel/), a multiboot kernel written in Mort that boots in QEMU **and on real hardware** (BIOS/UEFI bootable ISO), sets up an IDT, remaps the PICs, and runs a **graphical desktop with multiple apps** — a Terminal, a Files manager, and a Vex-styled browser, switched with `F1`/`F2`/`F3` — all drawn to a linear framebuffer in a bitmap font. It has an ATA disk driver, **a real filesystem (MortFS)** whose files survive reboots, and it **runs real, interactive compiled programs** through `int 0x80` syscalls (a program can ask your name and greet you). That's why Mort compiles to freestanding-friendly C instead of running on an interpreter.

> 🖥️ The kernel also has its own showcase repo: [**0xmortuex/MortOS**](https://github.com/0xmortuex/MortOS) — buildable standalone (it fetches this compiler automatically).

![MORT OS graphical desktop](kernel/docs/desktop.png)

<sub>MORT OS in graphics mode — a framebuffer desktop with the shell rendered in an 8×16 bitmap font, all written in Mort.</sub>

<sub>MORT OS booted in QEMU — the shell, keyboard driver, and command parser are all written in Mort.</sub>

```rust
// examples/fib.mx
fn fib(n: int) -> int {
    if n < 2 {
        return n;
    }
    return fib(n - 1) + fib(n - 2);
}

fn main() -> int {
    let i = 0;
    while i < 10 {
        print(fib(i));
        i = i + 1;
    }
    return 0;
}
```

```
$ python mortc.py examples/fib.mx --run
0
1
1
2
3
5
8
13
21
34
```

## How it works

Mort is a classic multi-pass compiler. Source text flows through five stages:

```
 .mx source
     │  Lexer          mort/lexer.py        text  → tokens
     │  Parser         mort/parser.py       tokens → AST   (recursive descent)
     │  Checker        mort/typechecker.py  static type checking + inference
     │  CodeGen        mort/codegen.py      AST   → C11 source
     ▼  C compiler     (cc / gcc / clang / zig)   C → native executable
 a.out
```

The type checker annotates every expression with its resolved type, and codegen
lowers each Mort function to a `mort_<name>` C function (so a Mort program can
never clash with a C standard-library symbol). Your `main` is wrapped by a real
C `main`, so the output is an ordinary native binary.

## The language (v0.10)

- **Types:** `bool`, `int` (alias for `i64`), fixed-width integers, C-ABI integer
  types (`c_int`, `c_size`, etc.), structs, and enums.
- **Strings:** string literals `"hi"` are `*u8` — a pointer to static,
  NUL-terminated bytes, with `len(text)` and indexed access.
- **Arrays:** fixed-size `[T; N]` with literal (`[1, 2, 3]`) or repeat
  (`[0; 8]`) initialisers and `a[i]` indexing (read and write).
- **Slices:** length-aware `[]T` and read-only `[]const T`, created with
  `slice(pointer, length)`, passed by value, and checked when indexed.
- **Structs:** `struct Point { x: i64, y: i64 }`, construct with
  `Point { x: 3, y: 4 }`, read/write fields with `p.x`, pass by value, and
  mutate through a pointer with `(*p).x = 1;`.
- **Pointers:** `*T` types (including FFI-friendly `*void`), address-of `&x`,
  dereference `*p`, indexed access with `p[i]`, and writing through a pointer.
- **Casts:** `expr as T` between integer types and pointers — e.g.
  `0xB8000 as *u8` to point at raw memory.
- **Inline assembly:** `asm("hlt");` — an escape hatch to real instructions,
  lowered to the C compiler's `__asm__ volatile`.
- **Functions:** `fn name(a: int, b: int) -> int { ... }`, with recursion and any call order.
- **C interoperability:** declare a native C-ABI function with
  `extern fn name(arg: i32) -> i32;`, then call it like any checked Mort function.
- **Enums and matching:** `enum State { Ready, Done }` and exhaustive
  `match state { State.Ready => { ... }, State.Done => { ... } }`.
- **Variables:** `let x = 5;` (inferred) or `let x: u32 = 5;` (annotated).
- **Control flow:** `if` / `else if` / `else`, `while`, range `for`, `break`, and
  `continue` (`for i in 0..n { ... }`, or `for i: u32 in 0..n` to fix the
  counter's type).
- **Operators:** `+ - * / %`, `== != < > <= >=`, `&& || !`, bitwise `& | ^ << >> ~`, unary `-`.
- **Literals:** decimal and hex (`0xFF`); untyped integer literals adopt the
  integer type they're used with, so `let b: u8 = a + 5;` needs no cast.
- **Globals:** top-level `let name: type = <constant>;` — file-scope state shared
  across functions (used by the kernel's interrupt handler).
- **Hosted runtime:** `print`, `println`, `assert`, `len`, `alloc`, and `free`,
  with compile-time and runtime array bounds validation.
- **Hardware builtins:** the x86 port-I/O family
  (lowered to inline `in`/`out`): `outb`/`inb` (8-bit), `outw`/`inw` (16-bit),
  and `outl`/`inl` (32-bit, for PCI config space on ports `0xCF8`/`0xCFC`).
- **Comments:** `// to end of line`.

Everything is statically type-checked before a single line of C is emitted:
mismatched types, mixing integer widths without a cast, dereferencing a
non-pointer, taking the address of a non-lvalue, undefined names, wrong argument
counts, and a non-`bool` `if` condition are all compile-time errors with line
numbers.

## Usage

```bash
python mortc.py program.mx              # compile to a native executable
python mortc.py program.mx --run        # compile, then run it
python mortc.py program.mx --emit-c     # print the generated C and stop
python mortc.py program.mx -o myprog    # choose the output name
python mortc.py main.mx math.mx --run   # one program split across source files
python mortc.py app.mx --std string     # include a bundled standard module
python mortc.py app.mx --link add.o     # link a native object/library file
python mortc.py app.mx -l sqlite3       # link a system library by name
python mortc.py kernel.mx --freestanding  # bare-metal object (no libc, no main)
```

### Projects

```bash
mortc new hello       # create mort.toml, src/, tests/, and .gitignore
cd hello
mortc build
mortc run
mortc test            # compile and run test "name" { ... } blocks
mortc fmt              # format project sources and tests
mortc fmt --check      # CI-friendly formatting check
mortc add util --path ../util  # add a local package and update mort.lock
mortc fetch            # resolve dependencies and refresh the lockfile
```

Imports are resolved recursively relative to the importing file. Bundled modules
use the `std` prefix:

```rust
import math;
import std.string;
```

Files can opt into collision-free module namespaces with private-by-default
functions and explicit public APIs:

```rust
// math.mx
module tools.math;
fn helper(x: i64) -> i64 { return x * 2; }
pub fn double(x: i64) -> i64 { return helper(x); }

// main.mx
import math as numbers;
// numbers.double(21)
```

All source files in one command form a single statically checked program and
share top-level functions, structs, and globals. Exactly one hosted `main`
function is required.

Bundled modules include `string`, `memory`, and the allocation-backed
`owned_string` module. Include legacy flat modules with a repeatable `--std`
option; each module is compiled from Mort source and remains fully type-checked.

### Calling C and native libraries

`extern fn` is Mort's bridge to the native ecosystem. It declares a symbol but
does not generate its body; the linker resolves that symbol from the platform C
runtime, a file passed with `--link`, or a library passed with `-l`.

```rust
// C runtime function
extern fn abs(value: i32) -> i32;

fn main() -> int {
    print(abs(0 - 42));
    return 0;
}
```

External calls are checked for argument count and Mort types. Fixed-width and
C-native integer types are available, along with `*void` handles and read-only
`*const T` pointers. See [`examples/interop.mx`](examples/interop.mx).

### Freestanding / bare metal

`--freestanding` is the bridge to the kernel. It drops everything that needs an
operating system underneath — no `<stdio.h>`, no `print`, no C `main` wrapper —
and emits an object file compiled with `-ffreestanding`. With the Zig backend it
cross-compiles to a real **x86-64 bare-metal ELF object** regardless of your host
OS. Addresses are computed as integers and cast to pointers, so hardware like the
VGA text buffer is reachable with no pointer-arithmetic feature:

```rust
// examples/kernel.mx — writes "Hi" to VGA memory, then halts.
fn put_cell(index: u64, ch: u8, color: u8) {
    let addr: u64 = 0xB8000 + index * 2;
    let cell: *u8 = addr as *u8;
    *cell = ch;
    let attr: *u8 = (addr + 1) as *u8;
    *attr = color;
}

fn kmain() {
    put_cell(0, 72, 15);   // 'H'
    put_cell(1, 105, 15);  // 'i'
    asm("hlt");
}
```
```
$ python mortc.py examples/kernel.mx --freestanding
mortc: wrote kernel.o          # a 64-bit x86-64 ELF object, no libc
```

### Requirements

- **Python 3.8+** — runs the compiler itself.
- **A C compiler** for the final native-build step. Mort looks for `cc`, `gcc`,
  or `clang` on your `PATH`, then falls back to Zig if it's installed.

No system compiler on Windows? The easiest option is a one-line install of Zig,
which ships a complete C compiler:

```bash
pip install ziglang        # Mort auto-detects and uses `python -m ziglang cc`
```

`--emit-c` needs no C compiler at all — it just prints the generated C.

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Front-end tests (type checking, error messages, codegen) always run. The
end-to-end tests compile each example to a real binary and check its output;
they skip automatically if no C compiler is available.

Mort projects can also declare native tests:

```rust
test "addition" {
    assert(add(2, 3) == 5);
}
```

## Roadmap

- [x] **Phase 1 — Language core:** lexer, parser, type checker, C codegen, CLI.
- [x] **Phase 2a — Memory core:** fixed-width int types, `as` casts, pointers (`&`, `*`, deref-assignment), hex literals, raw address casts.
- [x] **Phase 2b — Aggregates & asm:** structs (fields, construction, by-value, pointer mutation) and an inline-assembly escape hatch (`asm("...")`).
- [x] **Phase 3 — Freestanding mode:** `--freestanding` drops libc/`print`/`main` and emits a real x86-64 bare-metal ELF object (via the Zig backend).
- [x] **Phase 4a — It boots:** a multiboot kernel written in Mort ([`kernel/`](kernel/)) that runs in QEMU and prints to VGA text mode. `python kernel/build.py run`.
- [x] **Phase 4b — Strings:** string literals (`*u8`) in the language and a `print_string` VGA routine written in Mort, so the kernel prints real messages.
- [x] **Phase 4c — A shell:** `inb`/`outb` builtins, PS/2 keyboard, Shift/digits, Backspace, and a command parser (`help`, `clear`).
- [x] **Phase 4d — Interrupts:** global variables in the language, plus a GDT/IDT and remapped PICs so the keyboard is **interrupt-driven** (IRQ1). A PIT timer on IRQ0 drives an `uptime` command, a blinking hardware cursor, and terminal-style scrolling round out the shell.

- [x] **Phase 5 — Real-project foundations:** multi-file compilation, typed C-ABI
  `extern fn` declarations, native object/library linking, `*void`, and
  `break`/`continue` loop control.
- [x] **Phase 6a — Project workflow:** recursive imports, bundled modules,
  `mort.toml`, and `new`/`build`/`run`/`test` commands.
- [x] **Phase 6b — Safety and testing:** guaranteed returns, checked array
  indexing, allocation primitives, enums, exhaustive match, and native tests.
- [x] **Phase 7a — Core tooling:** source excerpts in diagnostics and a
  comment-preserving formatter with check mode.
- [x] **Phase 7b — Namespaces and local packages:** module aliases, `pub`
  visibility, path dependencies, dependency graphs, and deterministic lockfiles.
- [x] **Phase 8a — Safe data foundations:** typed mutable/const slices and an
  allocation-backed owned-string module.
- [ ] **Phase 8b — Ecosystem tooling:** remote registry, language server,
  debugger integration, publishing, richer containers, and generics.

## License

MIT
