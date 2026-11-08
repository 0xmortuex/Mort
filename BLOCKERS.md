# Blockers

## Self-hosting

Language and stdlib gaps found while writing the Mort lexer (`selfhost/`). None
stopped the lexer. Plan and ranking: `selfhost/PLAN.md` section 8. Each is a
separate task and is not fixed on this branch.

### G1: no command-line access

A Mort program cannot read its arguments.

```
fn main(argc: i32, argv: **u8) -> int { ... }
mortc: error: 'main' must take no parameters

let a = args();
error: call to undefined function 'args'
```

Interim: `selfhost/lex_main.mx` reads `MORT_LEX_IN` and `MORT_LEX_OUT` from the
environment.

### G2: no way to write to stderr

No builtin exists, and C's `stderr` cannot be reached from Mort.

```
eprintln("x");
error: call to undefined function 'eprintln'

extern let stderr: *void;
error: expected 'fn', got 'let'

extern fn fdopen(fd: c_int, mode: *const c_char) -> *void;
error: conflicting types for 'fdopen'
  (C backend; stdio.h declares it as returning FILE*)
```

Interim: diagnostics go to stdout via `println`.

### G3: `extern fn` prototypes clash with libc headers

Generated prototypes conflict with installed headers unless the C types match
exactly, and there is no way to name `FILE*`.

```
extern fn strtod(text: *const c_char, end: *void) -> f64;
error: conflicting types for 'strtod'
  (C backend; stdlib.h declares the second parameter as char**)
```

Interim: declare the exact C signature (`end: **c_char`).

### Python compiler defects

- `mort/lexer.py:207`: a decimal literal over 4300 digits raises an uncaught
  `ValueError: Exceeds the limit (4300 digits) for integer string conversion`.
  Reproduce with `let x: u8 = 999...;` (5000 nines).
- `mortc.py:197`: source that is not UTF-8 raises an uncaught
  `UnicodeDecodeError` traceback instead of a diagnostic.
- `mort/codegen.py:179` (`c_type`): taking the address of a global array crashes
  codegen with an uncaught `KeyError: '[u8;2]'`, because the type checks but no
  C type exists for a pointer to an array. Reproduce with
  `let G: [u8; 2] = [1, 2]; fn main() -> int { let p = &G; return 0; }`. The
  subset gate rejects this program; the compiler crash itself is not fixed on
  this branch.
