# Blockers

## Self-hosting

Language and stdlib gaps found while writing the Mort lexer (`selfhost/`). None
stopped the lexer. Status: `selfhost/PLAN.md` section 8.

### G1: no command-line access (resolved)

Fixed by the hosted builtins `arg_count()` and `arg_get(u64)`. An out-of-range
index is a located runtime error. C `main` only takes arguments in programs that
use them, so generated C for all other programs is unchanged.
`selfhost/lex_main.mx` takes its paths as arguments.
`selfhost/unicode_dump_main.mx`, a test helper, still reads `MORT_LEX_OUT`.

### G2: no way to write to stderr (resolved)

Fixed by the hosted builtin `eprintln(*u8)`, with `selfhost/support.mx`'s
diagnostics and `selfhost/lex_main.mx` using it.

### G3: `extern fn` prototypes clash with libc headers

Generated prototypes conflict with installed headers unless the C types match
exactly, and there is no way to name `FILE*`.

```
extern fn strtod(text: *const c_char, end: *void) -> f64;
error: conflicting types for 'strtod'
  (C backend; stdlib.h declares the second parameter as char**)
```

Interim: declare the exact C signature (`end: **c_char`).

### T8 and T9: nothing blocked; one environment limit

Stage-2 reached the fixpoint with no language gap. With an empty zig cache under a
500 MB per-process limit, the C compiler fails:

```
error: unable to generate DLL import .lib file for ntdll: OutOfMemory
error: unable to build compiler_rt: OutOfMemory
error: unable to build libzigc: OutOfMemory
```

`bootstrap.py` gives the C compiler its own limit, 1500 MB, for that reason.

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
- `mort/codegen.py:205` (`_collect_slice_types`) walks every string in the AST
  and treats any that starts with `[]` as a slice type, string literals included.
  `fn main() -> int { println("[]"); return 0; }` raises an uncaught
  `KeyError: ''` at `codegen.py:179`. Found writing `selfhost/parser.mx`, which
  emits the two characters with separate pushes instead. T6 and T7 reproduce this
  scan's output, so fixing it changes what they must match.
- `mort/typechecker.py:443` (`_validate_aggregate_cycles`): `for dependency in
  graph[name]` iterates a `set`, so which of several by-value cycles is reported
  depends on `PYTHONHASHSEED`. With `struct A { b: B, c: C } struct B { a: A }
  struct C { a: A }` the message is either `A -> B -> A` or `A -> C -> A`. Found
  by T4, whose reference (`selfhost/py_check_dump.py`) makes that iteration
  insertion-ordered so the comparison is stable.

- `mort/codegen.py` has no bound on two doublings, found while porting `_gen_expr`
  (T7). A slice `Index` that was not prepared (inside `defer`, or an unprepared `while`
  condition) writes its object twice, so `s[0][0]...` doubles the text per level; and
  `slice` and `len` of a slice generate their arguments twice (once discarded), so
  nested `slice<u8>(...)` calls double the work and the string table per level. A
  few dozen levels exhaust memory. The Mort generator shares or reuses that text, so
  it accepts what Python compiles at a sensible size and reports only output that
  would be enormous (`selfhost/PLAN.md` section 15); Python is not changed here.

Found by the program generator (T2c). None is fixed here; the generator stays
clear of each one, and says so where it does:

- All-literal comparisons do not range-check, and the value wraps at run time.
  Spec 2.3 and 7.1 make an out-of-range literal expression a compile-time error;
  the checker's `_unify_ints` returns `i64` for two literals with no check.
  `fn main() -> int { if 11981946837996067700 > 5 { print(1); } else { print(2); } return 0; }`
  prints 2, and `18446744073709551615 == 0 - 1` is true.
- A unary operator on a bare character literal is narrowed to `u8` when nothing
  gives it a type, though spec 2.3 calls the literal untyped: `print(~'\xab');`
  prints 84 and `print(-'\xab');` prints 85, while `print(~171);` and
  `let x: i64 = ~'\xab'; print(x);` print -172.
- `print` takes `int64_t` (`mort_print`, `mort/codegen.py`), so a `u64` above 2^63
  prints as negative: `let x: u64 = 18446744073709551615; print(x);` prints -1.
  The spec says `print` prints an integer.
- `mort/parser.py` `_looks_like_struct_lit` counts `>` but not `>>`, so
  `let b = Box<Box<u8>> { inner: Box<u8> { inner: 1 } };` is "unexpected token
  '{'". `Box<Box<u8> > { ... }` parses; the grammar allows both. The generic call
  and qualifier lookaheads do handle `>>`.
- Spec section 7.3 says `print` accepts a bool; the checker rejects it ("print
  expects an integer or float, got bool").
- Spec section 9 says `try` requires an enum with `Ok` and `Err` variants; the
  checker insists the enum is named exactly `Result` with two type arguments.
- `let z: f32 = -37.5;` is "annotated f32 but the value is f64": only a bare
  literal adopts f32, and `-37.5` is a unary f64 expression.
- `selfhost/checker.mx` on `feat/selfhost-t4` (seen only when T4 and T2c are merged
  together): `check_main` in hosted mode does not check `test` block bodies, though
  the Python checker does. For
  `fn main() -> int { return 0; } test "t" { let x: u8 = 1; assert(x == 1); }`
  Python's events include the test's `bind`, `var` and `call`; Mort's stop after
  `fn "main"`. It fails the three generated full programs with a test block
  (`full6`, `full7`, `full8`) in the declaration-checker differential. Fixed by
  T4, which checks test blocks in every mode; those programs now pass.
