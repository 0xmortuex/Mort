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

- 17 of the repo's 105 `.mx` files are already inside the subset, but they total
  only 193 lines and the largest is 30, so they are nowhere near enough on their
  own. Section 5.1 sets the real corpus requirement.
- `check_subset.py --stats` on the current sources: 4850 integer literals
  (mostly the Unicode tables), 1394 variable references, 548 binary and 208 unary
  operators, 381 calls, 84 functions, 7 structs, 1 enum.
- Generated C for `lex_main.mx`: 2192 lines, 127,719 bytes. It holds byte-stable
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
- Types: `bool`, `void`, `u8`, `u32`, `u64`, `i64`, `int`, `c_char`, `c_int`,
  `*T`, `*const T`, named struct and enum types, `[T; N]` at global scope only,
  used only by indexing it (`G[i]`, `&G[i]`): any other use is an array value.
  The same list applies to casts, `sizeof<T>()` and `for` headers.
- Statements: `let`/`const`, plain and compound assignment, `if`/`else`,
  `while`, `loop`, range `for`, `match` with at least one unit-variant arm of a
  declared enum (plus optional `_`; no integer, bool or char match), `return`, `break`,
  `continue`, blocks, expression statements.
- Expressions: integer, char, string and bool literals, variables (a function
  name may appear only as the target of a direct call), unary
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
| `f64`, float literals, float arithmetic | The compiler has no floating-point data, even in signatures. The lexer computes float literal values on integers (section 7), so it needs none |
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
- **Locale and libc numerics.** No `setlocale` call, and no libc number
  conversion: decimal-to-binary conversion is exact and done in Mort, so a host
  C library cannot change a float literal's value.
- **Toolchain.** The stage builds pin the `ziglang` version; the C compiler can
  affect `B1`/`B2`, but not `C1`/`C2` unless `B1` is miscompiled, which is what
  the check detects.

## 5. How the Mort compiler's output is compared

Each phase has a Python reference and a defined text dump, like the lexer:

- Tokens: `py_lex_dump.py` format (done, see section 7).
- AST: `py_ast_dump.py` format (done, see section 10): every node's kind, fields
  and line, generated reflectively from the Python AST and produced directly by
  the Mort parser.
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

### 5.1 Corpus requirement

The 17 existing subset files (193 lines) cannot show that a compiler works. Each
differential test from T3 on must run over all of the following, and must assert
the thresholds so that a shrinking corpus fails:

| Class | Content | Required size |
| --- | --- | --- |
| Self | Every `selfhost/*.mx` | Grows to the whole compiler |
| Existing subset | The 17 repo files, including the 9 conformance programs (the 6 conformance `reject` cases among them count as compiler-rejected and add nothing to the accepted-line floor) | All 17 |
| Stubbed kernel | `kernel/kmain.mx` with `outb`/`outw`/`inb`/`inw` calls redirected to stub functions, `asm` statements removed and `u16` changed to `u32` | At least 1500 lines. Measured: 1905 lines, 0 gate violations, compiles to 1986 lines of C |
| Generated | A seeded generator (`selfhost/tools/gen_subset_programs.py`, task T2c) producing valid subset programs that use every node kind and every binary operator on each integer type pair, including literal coercion and overflow edges | At least 20 programs totalling at least 6000 lines; at least 3 of 1500 lines or more |
| Rejected | Every other repo `.mx`: the rest of `conformance/` (29 files in all), `std/` (5204 lines) and the original kernel; 88 files, 9242 lines today | Clean located diagnostic on all; never a crash and never accepted |

Totals are checked per phase: at least 8000 lines of accepted programs, counting
only Existing subset, Stubbed kernel and Generated, and at least one real (not
generated) program of 1500 lines or more. Self and Rejected do not count: rejected
files exercise only the diagnostic path, and counting them would meet the floor
with no accepted program at all. Existing subset and Stubbed kernel total 2098
lines, so Generated must supply the rest, which is why its floor is 6000 lines and
why the generator comes first (T2c, before T3). It needs only the Python compiler
as its oracle. Most accepted lines are therefore generated, and the real-program
rule above is the check on that. T3's parser must in
addition match the Python AST for every repo `.mx` whose constructs it parses;
files using grammar outside the subset must get the located rejection diagnostic.
The stubbed kernel transform lives in the test code, so it tracks changes to
`kmain.mx`.

## 6. Tasks

Ordered; each is one agent's work with a mergeable result. Hours are honest
estimates for an agent including debugging, with wide bands since only T1 has a
measured data point.

| # | Task | Depends on | Hours |
| --- | --- | --- | ---: |
| T1 | Lexer with differential test | none | done |
| T2 | `support.mx`: string interner, hash map (lookup-only), vectors, diagnostics | T1 | done |
| T2b | `bignum.mx`: signed arbitrary-precision integers for checker constant folding | T2 | done (about 1 hour; estimated 4 to 8) |
| T2c | `selfhost/tools/gen_subset_programs.py` and the corpus thresholds of section 5.1 | T1 | done |
| T3 | AST in flat structs, parser, AST dump | T2, T2c | done |
| T4 | Checker part 1: names, declarations, type strings, scopes | T3 | done |
| T5 | Checker part 2: expressions, literal coercion, const folding, return analysis | T4, T2b | done (about 2 hours elapsed, see section 12; estimated 14 to 24) |
| T6 | C codegen part 1: type and declaration emission, statements, helper selection | T5 | done (section 14; estimated 12 to 20) |
| T7 | C codegen part 2: expressions, full byte-exact agreement | T6 | done (section 15; estimated 8 to 14) |
| T8 | Driver, import resolution, stage script, fixpoint test | T7 | 4 to 8 |
| T9 | Rejecting out-of-subset programs, CI gate | T8 | 2 to 4 |
| | **Remaining total** | | **40 to 70** |

T3 lists T2c as a dependency but was completed first. T2c does not reopen T3: it only extends the corpus T3's differential test runs over.

Not included: fixing gap G3 in section 8 if something comes to need it, and any
Python compiler bugs the differential tests expose.

Acceptance criteria:

- **T2.** Unit tests through a Mort test program with a Python-checked expected
  list. Interner returns equal handles for equal text; no API exposes table
  iteration order.
- **T2b.** Signed magnitude over the same limbs as `lexer_float.mx`'s `Big`, with
  `+ - *`, C-semantics `/` and `%` (`_c_div`, `_c_mod`), `& | ^ ~` with Python's
  infinite two's-complement meaning on negatives, `<<` including the checker's
  `count > 64` cap to `1 << 65`, `>>`, comparison, and parsing from the lexer's
  decimal token text. It is needed because `Checker._const_value` is arbitrary
  precision and its range check decides accept or reject: `~0`, `1 << 64` and a
  4300-digit literal must behave as in Python, and a fixed-width fold cannot.
  Literals are bounded (section 7) and shifts are capped, so a value's size grows
  only with expression length, and simple quadratic multiplication is enough.
  Differential test: at least 100,000 seeded operations against Python integers,
  covering negatives, zero, and values around 2^32, 2^64 and 4300 digits.
- **T2c.** With a fixed seed the generator's output is byte-identical across runs
  and `PYTHONHASHSEED` values. Every generated program passes `check_subset.py`
  and compiles with the Python compiler, and a test asserts the node-kind and
  operator coverage and the section 5.1 size floors, so a shrinking corpus fails.
- **T3.** Mort parser output matches the Python AST dump over every `.mx` in the
  repo (widened from the subset corpus and `selfhost/*.mx`, so the parser reads
  the whole language), including source lines. Parse errors in the corpus
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

Sizing notes. T2b can run alongside T3 and T4 and must land before T5. T5 is the riskiest: the Python checker's literal handling is
subtle and the corpus exercises it only partly, so budget for the top of its
range. Expect 7 to 10 thousand lines of Mort in total, which is a guess from the
one measured ratio.

## 7. Lexer status

(The lexer's 1011 lines in section 1 are its size before `support.mx` existed.)

- `selfhost/lexer.mx` (996 lines, after `Span` and `Buf` moved to `support.mx`),
  `lexer_float.mx` (314), `lex_main.mx` (97), generated `lexer_unicode.mx`, plus
  `unicode_dump_main.mx` (71, test helper) for the exhaustive Unicode check.
- Dump format: defined at the top of `py_lex_dump.py`. Token per line:
  `line:col KIND value`, with integer values in canonical decimal, floats as
  binary64 bit patterns, and bytes other than printable ASCII escaped. On a
  lexical error, the prefix of tokens is dumped followed by
  `ERROR line:col message`.
- Reads source as Python does: strict UTF-8 and text-mode newline translation.
  Columns count code points. Invalid UTF-8 gives
  `<line>:<col> source is not valid UTF-8 (byte 0x<NN>)` at the first invalid
  byte, the behaviour of `decode_source` on `fix/frontend-crashes`. The test
  compares against that function itself: from `mort/lexer.py` once merged, from
  that branch until then.
- **Integer literal limit.** An integer literal may have at most 4300 digits, in
  any base. Digits are counted without the prefix and without `_` separators;
  leading zeros count. A longer literal that is otherwise valid is rejected at its
  first character with `<kind> integer literal has <n> digits; the limit is 4300`,
  where kind is `decimal`, `hex`, `binary` or `octal`. Scanner errors such as a
  bad trailing character win over the limit. Tuple indexes are decimal literals.
  Base conversion stays simple and cheap: without a limit it was quadratic (400k
  digits took two minutes). The decimal rule and wording are those of
  `fix/frontend-crashes`. Python on main has no limit, so `py_lex_dump.py` applies
  the rule until the Python side is reconciled at merge; that merge must also add
  the hex, binary and octal cases.
- **Floats** are converted exactly by `lexer_float.mx`, with no `f64` and no
  `strtod`: the first 800 significant digits (plus a nonzero marker digit if
  anything nonzero was dropped, which cannot cross a tie because ties have at most
  767 significant digits) become a big integer ratio, and the quotient and
  remainder give binary64 rounded half to even. Only those digits reach the
  bignum, so cost is linear in literal length and needs no limit. An earlier
  version called host `strtod`, which misrounded ties and failed the byte-identity
  goal.
- Identifier and repr classification tables are generated from Python's
  `str.isalpha`, `isalnum` and `isprintable` at Unicode 16 (Python 3.14), and
  verified over every code point. A different Python's Unicode data skips that
  one test.

Test: `python -m pytest selfhost -q` runs against every `.mx` in the repo,
232 edge cases, 6000 generated float rounding literals, huge literals with a
time bound, token-level mutation (at least 75% of cases must carry 10 or more
dump lines), a separate invalid-bytes mutation class, the Unicode check, and the
subset gate with a negative test per excluded construct.

## 7b. Bignum status (T2b)

- `selfhost/bignum.mx` (765 lines), driver `selfhost/bignum_test_main.mx`, test
  `selfhost/test_bignum.py`. It does not share lexer_float's `Big`: both are the
  same limb layout, but flat-namespace names stay apart (`bn_`, `BigInt`), and a
  test links the two together.
- Covers everything `Checker._fold_binary` and `_const_value` need, through
  `bn_fold_binary` (Python's `_c_div`/`_c_mod` for `/` and `%`, the `1 << 65` cap),
  plus Python's flooring `//` and `%`, `bn_not`, `bn_neg`, `bn_bit_length` and
  `bn_cmp`. Parsing takes bases 2 to 36 and decimal formatting matches `str`.
- `bn_parse` must accept more than 4300 digits: a maximal 4300-digit hex literal
  reaches the checker as 5178 canonical decimal digits.
- The checker owns `MAX_CONSTANT_BITS` (2^18), checked after the fold as Python
  does, so a product can reach 2^19 bits. Library hard limit: `BN_MAX_BITS`, 2^20
  bits, an internal-error exit that a checked program cannot reach.
- Uses `sup_die` and `sup_alloc_array` from `support.mx`.
- Test: 300,000 seeded operations per run against Python `int` and the real
  checker fold (fresh driver build), a boundary cross product, all bases, diagnostics
  and 18 seeded mutants, plus fixed zero-negation cases that catch a 19th directly. Allocation failure is a real `malloc` null: the size comes
  from input and the pointer escapes, because clang otherwise deletes the unused
  `malloc` and its check.

## 8. Language and stdlib gaps

Exact errors are in `BLOCKERS.md`.

| Gap | Status |
| --- | --- |
| G1, command-line access | Fixed: builtins `arg_count()` and `arg_get(u64)`. An out-of-range index is a located runtime error. C `main` takes arguments only in programs that use them |
| G2, writing to stderr | Fixed: builtin `eprintln(*u8)` |
| G3, `extern fn` prototypes clash with libc headers | Open and not needed: the support layer's only extern is `exit` |

`lex_main.mx` uses both builtins, and later drivers should too.

## 9. Support layer

`selfhost/support.mx` holds everything later phases share: `Span`, `Buf`,
`U64Vec`, `PtrVec`, `Map` (u64 to u64), `Interner` and located diagnostics.

- Misuse (bad index, impossible allocation) is one line on stderr and exit 2. A
  compile error is `diag_fatal`, in `MortError.format()` shape, and exit 1.
- Tables have no iteration API. `selfhost/test_support.py` fails if any source
  outside the reviewed functions reads table fields or calls table internals,
  if `support.mx` gains another table function, or if it casts a pointer to an
  integer.
- Interner handles count up from first appearance and are all that a table can
  hand back. Later phases should put interner handles and vector indices in maps,
  and must never put addresses in one.
- `Map` has no removal. T4's scopes need a map per scope or an undo log.

Two Python-side defects found by this work; separate tasks, not fixed here:

- `mort/lexer.py` crashes with `ValueError` on a decimal literal over 4300 digits
  (CPython's int-string limit). The dump tool lifts that limit, so the comparison
  uses the spec's arbitrary-precision behavior.
- `mortc.py` crashes with a `UnicodeDecodeError` traceback on source that is not
  UTF-8; the Mort lexer gives a clean diagnostic.
- `mort/codegen.py` treats every AST string starting with `[]` as a slice type,
  so `println("[]")` raises `KeyError`. Found by T3; see `BLOCKERS.md`.

## 10. Parser status (T3)

`selfhost/ast.mx` (130 lines), `parser.mx` (1212), `pyrepr.mx` (419), `ast_dump.mx`
(395) and `parse_main.mx` (82, the driver: `parse_main INPUT OUTPUT`). The Python
parser is 866 lines, so the ratio is 1.4 for the parser alone and 2.6 with the
AST, the diagnostic `repr`, the dump and the driver. Time: about 40 minutes of agent time
against the 10 to 16 hours estimated, because the Python parser is regular and
the test harness already existed from T1. T4 and T5 should not assume this ratio
carries over: the checker's logic is far less mechanical.

- Dump format: defined at the top of `py_ast_dump.py`. One line per node with no
  indentation, so a deep tree stays linear in size. Fields the checker fills in
  are excluded, and the dumper asserts they still hold their initial values.
- Positions are compared wherever the Python AST has them: `line`, `col` (on
  every node class, `none` unless the parser set it: `Let`, `IntLit`, `CharLit`,
  `Unary`, `Binary`, `ArrayRepeat`), `Call.paren_col` and `Call.name_pos`.
- Diagnostics equal `MortError.format()` byte for byte, including the line and
  column of every parse error, the invalid UTF-8 byte and position from
  `decode_source`, the array size limit, and Python's `repr` of a token's value. Floats use dtoa's mode 0 digit loop on exact integers
  (`float_repr_main.mx` checks it against Python in bulk); no `f64`, no `strtod`.
  The first error exits 1 with no dump.
- Depth: `MAX_NESTING` (1000) counts entries to the five recursive rules (types,
  expressions, unary, blocks, `if`). Python's own limit is a side effect of its
  frame count, so the reference is the Python parser plus the same counter
  (`GuardedParser`). Message, line and column match, the column being the one
  `mortc.py` reports when the Python parser runs out of stack. Measured limits of the
  unguarded Python parser: 65 nested parentheses, 497 blocks, 993 pointer stars,
  975 unary operators, all within 1000 units, so this rejects nothing it accepts.
  Measured C stack margin on Windows, worst shape: 16892 units at `-O2`, 7932 at
  `-O0`.
- `<` lookahead is one precomputed pass over the tokens, not a scan per `<`. The
  tokens a scan can see are never yet split by a type close, so the answers match.
- Tree depth is not bounded by `MAX_NESTING`: operator, postfix and cast chains
  are built by loops. The parser and dump never recurse over the tree.

For T4: **chains make left-deep trees of any depth, so a recursive checker over
the AST will overflow the C stack.** It needs an AST-depth check or iteration.

Reconciled with `fix/frontend-crashes`: the Mort parser now records the same
columns on diagnostics and nodes as the Python parser and enforces the array size
limit. That branch's AST-depth check (`_check_nesting`) did not survive to its
merge: nesting there is the located `RecursionError` path in `mortc.py`, so
`GuardedParser` stays as the fixed limit and only gained the column.

## 11. Declaration checker status (T4)

`selfhost/typestr.mx` (the type-string helpers), `checker.mx` (declaration phase
and name resolution) and `check_main.mx` (the driver: `check_main MANIFEST
OUTPUT`). Time: roughly one to two hours of agent time against the 10 to 16 hours
estimated, because the checker's declaration half is a direct port and the
harness existed from T1 and T3. Do not assume T5 goes the same way: that half is
where the Python checker's behaviour stops being mechanical.

- **Scope is the whole language at the declaration level.** Generics (templates
  and instantiation on use), aliases, resources, payload enums and modules are
  all handled, because the differential runs over every `.mx`, not only the subset.
- **Reference.** `py_check_dump.py` runs the real `compile_files_to_c` with
  `Checker` swapped for a logging subclass and code generation stopped, so the
  loader and merge step are the compiler's own. It writes a manifest (files in
  load order, with import aliases) for `check_main`; T8 replaces the manifest with
  real import resolution in Mort.
- **What is compared.** Tables are taken at the end of the declaration phase,
  before any body runs: later generic instances come from T5's inference. Events
  are in evaluation order and generic instance bodies produce none. When Python's
  first error is one T4 implements (`T4_MESSAGES`), or there is no error, tables,
  events and the diagnostic must match exactly. When it needs a type, Mort carries
  on, and Python's events must be a prefix of Mort's.
- **Type-directed payload patterns.** Python picks a payload binding from the
  subject's type; T4 picks it from the pattern's dotted prefix being an enum. They
  can only differ on programs that are type errors. The reference detects that,
  stops comparing there and counts the error as T5's. T5 must remove this
  approximation.
- **Hash order.** Python iterates a `set` when it looks for aggregate cycles, so
  the named cycle varied with `PYTHONHASHSEED` (`BLOCKERS.md`). The reference
  replaces `set` in the checker's namespace with an insertion-ordered one; the
  Mort checker visits fields in declaration order, which is that order.
- **Test blocks** are checked in every mode, as `check()` does; only the `main`
  requirement depends on test mode. `selfhost/corpus/test_blocks.mx` and the
  `TEST_BLOCK_CASES` run them hosted, in test mode and freestanding.
- **Tables** also list `program.structs` (with the resource flag and destructor)
  and `program.enums` in order, so instance order is compared.
- **Generic limits.** Unbounded struct and enum expansion used over 1.5 GB in
  seconds. `ck_charge_generic` applies the limits Python has for generic functions
  (16384 instantiations, type arguments of at most 4096 characters and 256 levels)
  to struct and enum instances too, with the message `generic instantiation of 'X'
  ...` at the template's line, before validating arguments. See the known
  difference below. These tests run Mort alone, under a 500 MB limit.
- **Type work and length limits.** `checker.mx` ports `WorkMeter`: every program
  is metered from the start of checking, types shorter than 64 characters cost
  nothing, and each primitive charges its length divided by 64 (`ck_pointee`,
  `ck_slice_elem`, `ck_array_parts`, `ck_generic_parts`, `ck_tuple_parts`,
  `ck_function_parts`, `ck_substitute`, `ck_resolve`, `ck_valid`), so nothing is
  charged per case. Instances charge `_charge_instantiation`'s `8 x (1 + fields)`
  (enums, `8` per variant and payload plus `8`) after their arguments validate.
  Every type built goes through `ck_made`, which charges it and rejects more than
  65536 characters. Spending past 1,000,000 units reports `type processing exceeds
  the limit of 1000000 work units in one program`, or `generic instantiation
  exceeds ...` once a generic has been instantiated, at `_work_location`'s node.
  Alias names are cached during cycle tracing and valid answers are remembered.
  These diagnostics are in `T4_MESSAGES`, so the differential compares them when
  they arise in the declaration phase; the hostile tests also compare them with
  Python under the 500 MB guard.
- **Depth.** Expressions use an explicit work stack. Type and alias recursion
  counts `CK_MAX_DEPTH` (989), which is the real `mortc.py --check` boundary for
  alias chains (987 direct and 493 pointer links accepted, one more rejected,
  pinned by a test against the real compiler), and reports Python's located
  `source nesting exceeds ...` text at the node reached, using the first token's
  column on that line when the node has none; statement recursion is bounded by
  the parser's `MAX_NESTING`. Long
  by-value struct chains and cycles are iterative. The Python reference is
  quadratic on deep chains, so differential cases stop at 4000 operands, and one
  300,000-operand case runs the Mort checker alone.
- **Bignum.** Not needed at T4: there is no constant folding here, and array
  sizes are compared as the parser's canonical decimal text. T5's folding depends
  on the bignum task (T2b) being tracked on `feat/selfhost-plan`.
- **`Call.name_pos`** (from `main`, via `feat/selfhost-t2`) is excluded from the AST
  dump, because the Mort parser has no columns yet. T5 needs it for the volatile
  builtins' located errors, which are T5-class messages here.
- **`AST.Node`** gained `file`, `mod_name`, `symbol` and `import_aliases`, set by
  the merge step and not dumped by the parser.
- **Known limits.** Unused-binding warnings are not produced (they depend on
  moves and belong with T5). Import-resolution errors are counted but compared
  only when they are parse errors; the corpus has none of the other kind.

Reconciled with `fix/frontend-crashes` (in `main` at `c7e1a20`): columns on
diagnostics, `_valid_type(t, behind_pointer)`, `generic_parts` splitting through
`_split_type_list`, `_charge_instantiation`'s sites and costs with one meter, the
meter's messages and locations (`work_site`, `current_statement`,
`_work_location`), and the alias boundary with its located overflow.

Still open:

- **Known difference: Mort-only limits.** The 256-level, 4096-character and 16384
  instance checks are, in Python, function-instantiation limits; `ck_charge_generic`
  applies them to struct and enum arguments too. Landing `fix/frontend-crashes` did
  not remove this: `mortc.py --check` on `main` accepts a struct argument 257 levels
  deep or 5000 characters long, which Mort rejects, and reports `struct G<T> { n:
  *G<*T> }` as `source nesting exceeds the compiler safety limit` where Mort reports
  the 256-level limit. `struct D<T> { a: *D<(T, T)> }` hits Mort's 4096 limit and
  Python's 65536 cap; `test_known_difference_doubling_tuple_argument` pins both
  sides. To close it, decide one rule: either Python's `_instantiate_struct` and
  `_instantiate_enum` apply the limits before validating arguments, and the
  specification says so (it now names only generic functions), or
  `ck_charge_generic` drops them for structs and enums and Mort relies on the meter
  and a depth limit that matches Python's. The second changes what the Mort-only
  limit tests expect; move those cases to the differential when it is done.
- **T5's half of the budget.** Function instantiation (with its shared counter and
  `work_site`), type inference charges, drop analysis, move snapshots and
  `spend_replay` happen in bodies. Until T5, a budget reached in a body is not
  compared (`BUDGET_MESSAGE` in `py_check_dump.py`): 2000 fields of a pointer to a
  40,000-character alias pass here and exhaust the budget in Python.
- **Validity depth.** `_valid_type` now takes two Python frames per level, so
  `mortc.py --check` rejects a pointer field beyond 494 levels, where Mort, which
  counts one level, accepts up to `CK_MAX_DEPTH`. `CK_MAX_DEPTH` is pinned on alias
  chains only; the differential's reference runs with a higher recursion limit and
  does not see this. T5 should recount frames when it ports `_check_expr`.
- **Walk order.** The apply-aliases walk matches Python's preorder at the level of
  program lists and of the children's `a`, `b`, `c`, `items` slots, but Python uses
  each node's field order; if the differential finds a limit reported at a different
  node, it is this.
- `_coerce`'s literal path and `_require_representable` are T5's to port, and
  neither changes a T4 message.
## 14. C generator status (T6)

`selfhost/codegen.mx` (types, declarations, drop helpers, the program head),
`cg_stmt.mx` (statements, defers, `try` lowering), `cg_helpers.mx` (helper
selection and the integer helpers), the generated `cg_helper_text.mx` (constant
concurrency, network and random text, from `tools/gen_helper_text.py`, a test
keeps it current), `manifest.mx` (split out of `check_main.mx`) and `cg_main.mx`
(the driver). Time: about 90 minutes by commit timestamps, against 12 to 20
estimated, because the port is mechanical and the harness existed. About an hour of
that went on reconciling T5 with `main` here before `feat/selfhost-t5` did the same;
this branch adopted T5's own reconciliation for the checker files.

- **Scope of "in isolation", at T6.** Statements contain expressions, whose text was
  T7. `cg_expr` wrote a placeholder and `py_codegen_dump.py` replaced only `_gen_expr`.
  T7 removed both (section 15).
- **Tests.** `test_codegen_differential.py`: every repo `.mx` in three modes, the
  generated corpus, stubbed kernel, compiler sources, constructs one at a time,
  every flag and integer helper alone and together, hash seeds, paths, hostile
  programs under the 500 MB guard (81 of the 142 repo programs reach C in hosted mode;
  the rest are compared as rejections). `test_codegen_mutants.py`: 17 planted bugs.
- **Recursion.** `cg_prepare_try`, `ck_contains_try` and the collectors are
  iterative on the unbounded left spine; pointer prefixes in `c_type` and
  `visit_type` loop, because aliases can spell tens of thousands of `*`. Statements
  and other type constructors recurse within the parser and alias limits.
- **Known limits.** `--coverage` output is not ported (not needed to bootstrap).
  Type-text charges count UTF-8 bytes where Python counts code points, as the
  checker does, so an all-non-ASCII type name is charged up to 3 to 4 times what
  Python charges and can hit a budget that far early (an open difference). The
  replay budget was the same until T7 and rejected a valid CJK program at about 44%
  of the limit; it now counts code points. A `KeyError` in
  Python's `c_type` is an internal-error exit (2) here.

## 15. Expression generator status (T7)

`selfhost/cg_expr.mx` (`_gen_expr`, helper selection, the output writer),
`cg_float.mx` (`format(value, ".17g")`), `cg_cond.mx` (`_gen_cond`'s text rule) and
two test helpers, `float_g17_main.mx` and `cond_strip_main.mx`. Python's `_gen_expr`
is about 260 lines; the port is about 1550 lines of Mort, the difference being
buffers, the explicit spine and the writer. `cg_main.mx` now takes only the program.
`cg_forced_main.mx` and `cg_forced.mx` are test-only and reach helper combinations no
program can (a freestanding build with threads); the compiler never reads a forced
file, and `py_codegen_dump.reference` is the real `CodeGen` with no patches. T6's
placeholder is gone, so section 14's "in isolation" no longer applies.

- **Evaluation order.** Python's order is reproduced because it is visible in the
  output: an `obj[index]` generates its index first, which sets `mort_str_N`
  numbering. Helper use, charges and `current_node` follow the same sequence.
- **Double-ended text** (`Txt`). A left-deep operator chain wraps its left operand
  on both sides at each level; with ordinary strings that is quadratic. A chain of
  300,000 non-constant operands takes about a second under the 500 MB guard. The left
  spine (`Binary.left`, `Cast.expr`, `FieldAccess.obj`, `Index.obj`) uses an explicit
  stack; everything else recurses within the parser's `MAX_NESTING`.
- **Big array repeats.** A repeat of more than 1 MiB of text stays a short token
  (0xFF cannot occur in UTF-8 source) in every buffer and expands when the output is
  written, so Python's 256 MiB limit is reachable under the guard; `len(v)` and the
  replay charge count the expanded text in code points.
- **Temporaries** live in the node's `num` field (Python's `lowered_name`,
  `temp_name`, `obj_temp_name` are set the same way, by the same preparation pass).
- **Known differences, both only on runaway programs.** Python has no limit on
  either; each is a Mort-only diagnostic. A slice operand without a prepared
  temporary is written twice, so nesting doubles the text; the total duplicated is
  limited to 64 MiB. `slice` and `len` of a slice generate their arguments twice, so
  nesting doubles the work; regenerated nodes are limited to 4,000,000. No program
  under roughly 20 nesting levels of either reaches them. See `BLOCKERS.md`.
- **Tests.** `test_codegen_expr_differential.py`: about 110 expression constructs in
  three modes, every `used_*` flag and every integer helper from real programs
  asserted on the Python side too, string-table order, `.17g` on 150,000 random values
  plus constructed values and exact ties, the whole generated corpus compiled and run
  against its independent model, deep chains against Python and at scale, and hostile
  programs under the guard. `test_codegen_mutants.py`: 55 planted bugs.
- **Corrected test.** T6's `condition_strips_parens_of_call` survives on real
  programs, since expression text never has the `(a)(b)` shape; it is kept, probed
  through `cg_cond.mx` directly against Python's `_gen_cond`.

## 13. Generator status (T2c)

`selfhost/tools/gen_subset_programs.py` builds each construct as Mort text plus a
Python closure for its meaning, written from the language spec, so the expected
stdout, stderr and exit status never come from the compiler under test.
`selfhost/corpus.py` holds the 5.1 classes and floors, and
`selfhost/test_generated_corpus.py` enforces them. Run with
`MORT_SELFHOST_CORPUS_SCALE=n` for a larger corpus.

- `subset` profile: 25 programs, about 10,400 lines, three of them over 1500
  lines. A `matrix` program runs every integer operator on every ordered pair of
  subset integer types, and on literals, at their edges; `trap` programs end in
  each defined runtime failure. All pass the gate and match the model.
- `full` profile: generics (`>>` closers, inferred and explicit), payload enums,
  `match` bindings, tuples, slices, local arrays, function pointers (the language
  has no closures or traits), `try`, `defer`, `resource`/`move`, floats, modules
  across files, aliases, `asm`, `volatile`, every integer width. Not counted
  towards the floor, and only parser/checker-relevant, but every program also runs
  and is checked.
- The header of the generator states exactly what is not independently modelled
  (target-defined `c_*` behaviour, `test` block bodies, float format, `sizeof` of
  enums).
- Six planted defects in a copy of `parser.mx` (precedence swap, right-associative
  binary operators, lost `..=`, chained casts, operator line, `%` level) are all
  caught by the corpus, in the default test run; the real parser is not.
- The test wires the corpus into T4's declaration-checker differential through
  `test_checker_decl_differential.compare`. An early trial merge with T4 passed
  32 of 35, the other 3 hitting the unchecked-test-body divergence in
  `BLOCKERS.md`; with T4's test-block checking, all 35 run and pass.

**Follow-up for T4 and T5: the corpus is thin outside the subset.** Generics,
payload enums and cross-file modules (`module`, `pub`, `import ... as`) appear only
in the `full` profile, whose default size is 10 programs and 2,088 lines. Measured
at the default scale: 16 generic declarations, 9 payload enums, 3 module files, and
each comes from a fixed template with random values, not from random shapes. The
8000-line floor says nothing about these features, because the `subset` profile
cannot contain them. A pass over this corpus is weak evidence for generic
instantiation, payload binding and module visibility. T4 and T5 should keep their
own targeted cases there, and widen these generator sections (or run with a larger
`MORT_SELFHOST_CORPUS_SCALE`, which repeats the templates but adds no new shapes)
before relying on them.

## 12. Type checker status (T5)

`selfhost/check_expr.mx` (expressions, coercion, folding, statements, moves),
`check_call.mx` (calls, generics, every builtin) and `check_dump.mx` (the checked
dump). T4's walk in `checker.mx` is gone; declarations stay there. Time: about
two hours elapsed by commit timestamps, including a pause for a usage limit,
against 14 to 24 estimated, for the reason T4 gave: the
checker ports line by line and the harness was already there. Most of the time
went on the tests, not the port.

- **Corrections to this plan.** T5's scope grew from the subset to the whole
  language the Python checker handles, as T4's did. Generated programs and
  `corpus.py` are on `main` and were not in T4's base; they were copied over.
  `check_main` takes an optional third path for the checked dump, so T4's output
  and its tests are unchanged.
- **Dump.** `py_check_dump.checked_dump`: the post-check AST in `py_ast_dump`
  format, each line followed by ` |` and every checker-set field (`type`,
  `is_lit`, `const_val` on every node; `CHECKED_SUFFIX` for the rest), then the
  drop table, warnings and final tables. It reads `vars(node)` reflectively, so
  a new field shows up as a difference, and a test parses `mort/typechecker.py`
  and fails if it assigns a node attribute the dump does not cover. A constant
  above 256 bits prints as `~b<bits>:<fnv>` so the dump stays linear.
- **Recursion.** Only the left spine (`Binary.left`, `Index.obj`, `Cast.expr`, a
  `FieldAccess` on a non-variable) is unbounded, and it is walked with an explicit
  stack. Everything else recurses within the parser's 1000 units. Measured with
  that limit lifted, on Windows: stack overflow first at 8000 units at `-O0`
  (nested calls and unary), 32000 at `-O2`, so the margin is 8x. Tests run every
  recursive shape at the limit.
- **Constants.** Folded bottom-up as a node finishes, so `_const_value`'s walk
  disappears; it is pure, because a constant depends only on the literals under
  it. `MAX_CONSTANT_BITS` is `main`'s check. Mort also charges work for every
  retained constant (one unit per 1024 bits): without it, one huge value carried
  along a 300,000-operand chain would store a huge value at every node. That
  charge is Mort-only and cannot reject a program that does not fold over about
  128 MB of constants.
- **Hostile input.** Function instantiation follows `_instantiate_function` on
  `main`: count, `size x 8` work, then 4096-character and 256-level limits at the
  call, in that order. These tests run Mort alone under the 500 MB guard, because
  Python on this base has no limits and cannot finish them.
- **Deviation from the T4 base's Python, deliberately kept for now.** The base
  accepts `let x: i64 = 1 << 2000` (an i64 literal passes `_coerce` unchecked);
  the port does too, since it is compared with that Python.
- **Vacuous pass found.** The expression cases first passed because the shared
  prelude declared `destroy` twice, so all of them agreed on that one error.
  `test_cases_exercise_the_checker` now requires at least 150 accepted programs,
  200 rejections and no message on more than a tenth of the cases.
- **Tests.** `test_checker_expr_differential.py` (1437 cases: every repo `.mx` in
  three modes, the generated corpus, stubbed kernel, the compiler's own sources,
  about 590 expression cases, function-level, modules, chains, hostile programs,
  250 seeded source mutations). `test_checker_expr_mutants.py`: 43 planted bugs,
  all caught.

### Reconciled with `main`

The merge of T4 and `fix/frontend-crashes` is done, and every case above now
compares against that Python: columns from the parser (`Node.col`, `paren_col`),
`literal_operand` and `_require_representable`, the literal case first in
`_coerce`, `_value_str` in the shift and index messages, address-of-array, `void`
and array or slice comparisons, `_valid_type` on a cast, resources in owned places
and array repeats, duplicate match variants, `_valid_sizeof_type`, `current_statement`,
and instances from test bodies. The base's defects recorded during T5 are gone with
it: `1 << 2000` in an `i64` is rejected.

