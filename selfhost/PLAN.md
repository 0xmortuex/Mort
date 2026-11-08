# Mort self-hosting plan

Goal: the Python compiler builds a compiler written in Mort (stage-1), stage-1
builds itself (stage-2), and stage-2's output is byte-identical to stage-1's.

This file was written alongside the lexer, the first working piece. Every
measurement below came from this tree (compiler 0.137.0, commit `912fa02` plus
this branch) on Windows 11, Python 3.14.3, `python -m ziglang cc`.

## 1. Measured scope

The audit's 150 to 300 hours assumes porting all of the compiler. Most of it is
not needed to compile a compiler. `measure_coverage`-style line tracing of the
Python compiler (`sys.settrace`, compared with each file's `co_lines()`), while
compiling `selfhost/lex_main.mx` and a probe program that adds `match` and
imports:

| Python file | Executable lines | Hit by the subset | Share |
| --- | ---: | ---: | ---: |
| `mort/parser.py` | 746 | 536 | 72% |
| `mort/typechecker.py` | 2413 | 879 | 36% |
| `mort/codegen.py` | 2280 | 838 | 37% |
| **Total to port** | **5439** | **2253** | **41%** |

The lexer (303 source lines of Python) took 1011 lines of Mort. Roughly 350 of
those are not compiler logic: UTF-8 decoding, bignum conversion, Python `repr`
reproduction, the dump, and buffers. That is the only measured Python-to-Mort
ratio so far, and everything that follows treats it as uncertain.

Other figures:

- 17 of the repo's 105 `.mx` files are already inside the subset. That is the
  starting corpus for the later differential tests.
- `check_subset.py --stats` on the current sources: 4708 integer literals
  (mostly the Unicode tables), 1097 variable references, 432 binary and 131 unary
  operators, 338 calls, 72 functions, 6 structs, 1 enum.
- Generated C for `lex_main.mx`: 1778 lines, 106,103 bytes. It holds byte-stable
  across `PYTHONHASHSEED` 0, 1, 2 and 12345, and contains no absolute paths.

Two honest limits on the 41%. The trace covers the lexer's constructs and a
small probe, so the true figure will rise a few points when the compiler uses
more of the subset. And hit lines include shared infrastructure that a port has
to rewrite anyway.

## 2. The bootstrap subset

The compiler in Mort is written in a language smaller than Mort, and needs only
to accept that language. `selfhost/tools/check_subset.py` is the executable
definition; `python -m pytest selfhost` runs it on all sources, and its negative
tests show each exclusion is detected.

**In.**

- Declarations: `fn`, `extern fn` (libc), `struct` with no type parameters,
  `enum` with unit variants only, global `let`/`const`, one-segment relative
  `import` (no alias).
- Types: `bool`, `void`, `u8`, `u32`, `u64`, `i64`, `int`, `f64` (only in extern
  signatures), `c_char`, `c_int`, `*T`, `*const T`, named struct and enum types,
  `[T; N]` at global scope only.
- Statements: `let`/`const`, plain and compound assignment, `if`/`else`,
  `while`, `loop`, range `for`, `match` over unit variants, `return`, `break`,
  `continue`, blocks, expression statements.
- Expressions: integer, char, string and bool literals, variables, unary
  (`! - ~ & *`), all binary operators, calls, `as` casts (including enum to
  integer), struct literals, field access, indexing, `sizeof<T>()`,
  `Enum.Variant`.
- Builtins: `alloc`, `free`, `sizeof`, `println`, `eprintln`, `print`, `file_open`,
  `file_read`, `file_write`, `file_close`, `arg_count`, `arg_get`.

**Deliberately out, and why.**

| Excluded | Reason |
| --- | --- |
| Generics | The largest single saving: no monomorphization or inference in the checker. Cost: a few hand-written vectors |
| `resource`, `move`, `defer` | No ownership dataflow to port. The compiler is a batch process that never frees (allocates, then exits) |
| Enum payloads, payload `match` | AST and tokens use flat structs with a kind tag |
| Tuples, slices, `[x; N]`, local arrays | Each is a C aggregate strategy in codegen and a shape in the checker. Pointer plus length is enough |
| Function pointers | Never needed for a compiler |
| `try` | Errors propagate as return values |
| Float literals, float arithmetic | The compiler has no floating-point data. The lexer still lexes floats for everybody else |
| `asm`, port I/O | Irrelevant to a compiler |
| `module`, `pub`, `import std.*` | Whole-program flat namespace, and no stdlib dependency, so stage-1 needs no `std/` at all |
| Tests, type aliases, `null` literal | Not needed (`0 as *T` is used) |

A compiler restricted to this subset rejects other programs, cleanly (section 6,
T9). It is not a replacement for `mortc.py`; widening it to the full language is
post-bootstrap work and is not part of this plan.

## 3. The stages

Everything is built from `selfhost/mortc_main.mx` (created by T8).

| Name | Definition |
| --- | --- |
| S0 | The Python compiler at a recorded commit |
| C0 | `S0.compile_files_to_c([root])` written as UTF-8 with LF endings |
| B1 | `zig cc -std=c11 -O2 C0` = **stage-1** |
| C1 | B1 compiling `root` to C |
| B2 | `zig cc -std=c11 -O2 C1` = **stage-2** |
| C2 | B2 compiling `root` to C |

**The fixpoint check, exactly:** `C1` and `C2` are equal as byte sequences,
compared with `cmp` and recorded by SHA-256. This is the release gate. A failing
check prints the first differing byte offset and line.

**A stronger check on top:** `C0` and `C1` are also byte-identical, because the
Mort compiler's codegen must reproduce the Python codegen's output for every
program in the subset (see section 5). `C0 == C1` is stated and tested
separately; it does not replace the fixpoint check. Compiling `C1` and `C2` with
the same `zig cc` and flags must then give the same binary, but binary equality
is not a gate: PE timestamps and compiler versions are outside this project's
control.

## 4. Preventing nondeterminism

Rules for all of the Mort compiler code, each enforced by the fixpoint and
differential tests:

- **Iteration order.** No output may depend on hash-table order. Tables are
  lookup-only; anything emitted comes from insertion-ordered vectors, in source
  order, with explicit sorts where Python sorts.
- **Generated names.** `mort_str_N`, `mort_return_N` and friends come from
  per-compilation counters advanced in a fixed traversal order. The Python
  counters are the reference.
- **Time and environment.** No clocks, process IDs, random numbers or
  environment in the output. No `__DATE__`/`__TIME__`. Build scripts set
  `SOURCE_DATE_EPOCH=0`.
- **Paths.** No absolute paths in generated C (checked by a test scan of the
  output for the build directory). Diagnostics may contain paths; output does
  not.
- **Newlines.** Source loading translates `\r\n` and `\r` to `\n` exactly as
  Python's text mode does (the lexer does this now), so `core.autocrlf` cannot
  change results. Output is written in binary with LF only.
- **Memory and pointers.** Pointer values are never printed. Unwritten memory is
  never formatted.
- **Locale.** No `setlocale` call anywhere; `strtod` is used only in the C
  locale.
- **Toolchain.** The stage builds pin the `ziglang` version; the C compiler can
  affect `B1`/`B2`, but not `C1`/`C2` unless `B1` is miscompiled, which is what
  the check detects.

## 5. How the Mort compiler's output is compared

Each phase has a Python reference and a defined text dump, like the lexer:

- Tokens: `py_lex_dump.py` format (done, see section 7).
- AST: a structural dump of every node's kind, fields and line, generated
  reflectively from the Python AST and produced directly by the Mort parser.
- Checked AST: per-expression resolved type (the Python type string) and folded
  constant, keyed by source order.
- C text: compared to `CodeGen(...).generate()` byte for byte, over the subset
  corpus.

Why insist on matching the Python output instead of designing better C? The
Python output is the only oracle available. A new C emission would need a new
review of bounds checks, shift semantics and overflow helpers; matching it
inherits all of that. It also makes every phase testable before the next one
exists, which the fixpoint check alone cannot do.

One deliberate departure from `ROADMAP.md` gap 2, which suggests replacing type
strings with a real type model during the port. Do not do it in this phase.
Matching the Python output exactly gains nothing from a richer model, and the
strings are small and interned here. Introduce a type model after bootstrap.

## 6. Tasks

Ordered; each is one agent's work with a mergeable result. Hours are honest
estimates for an agent including debugging, with wide bands since only T1 has a
measured data point.

| # | Task | Depends on | Hours |
| --- | --- | --- | ---: |
| T1 | Lexer with differential test | none | done |
| T2 | `support.mx`: string interner, hash map (lookup-only), vectors, diagnostics | T1 | 3 to 5 |
| T3 | AST in flat structs, parser, AST dump | T2 | 10 to 16 |
| T4 | Checker part 1: names, declarations, type strings, scopes | T3 | 10 to 16 |
| T5 | Checker part 2: expressions, literal coercion, const folding, return analysis | T4 | 14 to 24 |
| T6 | C codegen part 1: type and declaration emission, statements, helper selection | T5 | 12 to 20 |
| T7 | C codegen part 2: expressions, full byte-exact agreement | T6 | 8 to 14 |
| T8 | Driver, import resolution, stage script, fixpoint test | T7, G1 | 4 to 8 |
| T9 | Rejecting out-of-subset programs, CI gate | T8, G2 | 2 to 4 |
| | **Remaining total** | | **63 to 107** |

Not included: fixing the gaps in section 8 (2 to 5 hours), and any Python
compiler bugs the differential tests expose.

Acceptance criteria:

- **T2.** Unit tests through a Mort test program with a Python-checked expected
  list. Interner returns equal handles for equal text; no API exposes table
  iteration order.
- **T3.** Mort parser output matches the Python AST dump over the subset corpus
  and all `selfhost/*.mx`, including source lines. Parse errors in the corpus
  produce a located diagnostic and exit 1, never a crash, with a recursion-depth
  limit in place of Python's `RecursionError` handling. Seeded mutation test.
- **T4.** Declaration tables and scope resolution match a Python-side dump over
  the corpus. Redeclarations and unknown names give a located diagnostic.
- **T5.** Per-expression type and const dump matches Python across the corpus,
  including literal adoption, `as` casts and enum comparison. The `reject`
  cases in the corpus that fall inside the subset are rejected.
- **T6.** Declaration and statement parts of `CodeGen` output match in isolation
  on the corpus (type definitions, prototypes, globals, control flow).
- **T7.** Full generated C for every program in the corpus equals Python's output
  byte for byte. This is where `C0 == C1` first becomes testable.
- **T8.** `python selfhost/bootstrap.py` builds B1 and B2 and verifies
  `C1 == C2` and `C0 == C1`, printing hashes. The check fails loudly on a
  mismatch. No paths or timestamps in the C.
- **T9.** Every repo `.mx` outside the subset is rejected with a located
  diagnostic, not accepted or crashed. CI job on Linux and Windows runs the
  bootstrap and both equality checks.

Sizing notes. T5 is the riskiest: the Python checker's literal handling is
subtle and the corpus exercises it only partly, so budget for the top of its
range. Expect 7 to 10 thousand lines of Mort in total, which is a guess from the
one measured ratio.

## 7. Lexer status

- `selfhost/lexer.mx` (1011 lines), `lex_main.mx` (101), generated
  `lexer_unicode.mx`, plus `unicode_dump_main.mx` (71, test helper) for the
  exhaustive Unicode check.
- Dump format: defined at the top of `py_lex_dump.py`. Token per line:
  `line:col KIND value`, with integer values in canonical decimal, floats as
  binary64 bit patterns, and bytes other than printable ASCII escaped. On a
  lexical error, the prefix of tokens is dumped followed by
  `ERROR line:col message`.
- Reads source as Python does: strict UTF-8 and text-mode newline translation.
  Columns count code points.
- Integer values use small-decimal bignum conversion, so a literal of any length
  is handled.
- Float parsing calls `strtod`, which is correct here on the hard cases tested
  (halfway points, subnormals, more than 700 digits). This depends on the host
  C library being correctly rounded; a platform with an incorrect `strtod` will
  fail the differential test and need an in-Mort conversion.
- Identifier and repr classification tables are generated from Python's
  `str.isalpha`, `isalnum` and `isprintable` at Unicode 16 (Python 3.14), and
  verified over every code point. A different Python's Unicode data skips that
  one test.

Test: `python -m pytest selfhost -q` runs against every `.mx` in the repo, 198
edge cases, a seeded mutation test, the Unicode check and the subset gate.

## 8. Language and stdlib gaps

Ranked by cost to the project. None blocked the lexer; each becomes its own
task and exact errors are in `BLOCKERS.md`. Neither of the top two distorts the
lexer's design: each is confined to one small driver function.

| Rank | Gap | Effect | Size |
| ---: | --- | --- | --- |
| G1 | No command-line access | Compiler paths come from env vars until fixed. T8 can proceed with env vars, but a usable compiler needs argv | 1 to 2h |
| G2 | No way to write to stderr | Diagnostics go to stdout | 1 to 2h |
| G3 | `extern fn` prototypes clash with libc headers unless the C types match exactly | Must spell signatures carefully; `FILE*` functions cannot be declared at all | 1h, or part of ROADMAP gap 8 |

Two Python-side defects found by this work; separate tasks, not fixed here:

- `mort/lexer.py` crashes with `ValueError` on a decimal literal over 4300 digits
  (CPython's int-string limit). The dump tool lifts that limit, so the comparison
  uses the spec's arbitrary-precision behavior.
- `mortc.py` crashes with a `UnicodeDecodeError` traceback on source that is not
  UTF-8; the Mort lexer gives a clean diagnostic.
