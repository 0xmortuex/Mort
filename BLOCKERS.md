# Blockers

## Self-hosting

Language and stdlib gaps found while writing the Mort lexer (`selfhost/`). None
stopped the lexer. Plan and ranking: `selfhost/PLAN.md` section 8. Each is a
separate task and is not fixed on this branch.

### G1: no command-line access (resolved)

Fixed by the hosted builtins `arg_count()` and `arg_get(u64)`. An out-of-range
index is a located runtime error. C `main` only takes arguments in programs that
use them, so generated C for all other programs is unchanged.
`selfhost/lex_main.mx` still reads `MORT_LEX_IN` and `MORT_LEX_OUT`; its tests
assert that and nothing needs it changed yet.

### G2: no way to write to stderr (resolved)

Fixed by the hosted builtin `eprintln(*u8)`, with `selfhost/support.mx`'s
diagnostics using it. `selfhost/lex_main.mx` still reports on stdout; its tests
assert that and nothing needs it changed yet.

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
