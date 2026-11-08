# Mort roadmap

Audit date 2026-11-08, at commit `25255e6` (compiler `0.136.0`, language
contract `0.41.0`). Numbers come from commands run in that tree on Windows 11,
Python 3.14.3, with `python -m ziglang cc` as the backend. Hour figures are
estimates. Throwaway probe scripts lived outside the repo.

## Exit criteria status

| | Criterion | Status | Measured evidence |
| --- | --- | --- | --- |
| a | Written spec | **Partial** | `docs/language-specification.md`: 1070 lines, 51,441 bytes, normative EBNF in section 3. Keywords match the lexer 33 of 33. Type rules are prose, not formal. The memory model covers only threads and data races (section 12). Pointer rules are left to the programmer (section 4.2) and layout is unspecified. 5 grammar divergences below |
| b | 200+ passing tests | **Met** | `python -m pytest tests/ -q`: 438 passed, 0 failed, 0 skipped, 5:10. Conformance: 28 of 28 |
| c | Fuzzed parser never crashes | **Met for crashes; hardening open** | `mortc fuzz --cases 150000 --seed 20261108`: 0 crashes in 47.8s. Throwaway mutation fuzzer over examples, std and kernel: 73,827 cases, 0 crashes, slowest case 0.02s. Single-token semantic fuzz: 6,663 cases, 0 crashes. Open: quadratic parse time and an uncaught `MemoryError`, gap 11 |
| d | Compiles a 1000+ line program | **Met** | `kernel/kmain.mx`, 1900 lines: builds, boots, `kernel/test.py smoke` 4 of 4. MortOS `kmain.mx` + `net/*.mx`, 5995 lines: compiled to an object (0.37s front end, 2.4s C). Hosted: `examples/json.mx` runs, 3008 lines with imports. Generated 13,505-line program ran with the independently computed checksum 625750 |
| e | Self-hosts to stage 2 | **Not met, not started** | There is no Mort compiler written in Mort, so there was nothing to run. `git ls-files` has no such source. `self-host` and `bootstrap` have 0 hits in `BACKLOG.md` here and in MortOS. No `0xmortuex` repo name suggests one; `nanomort` is Python |
| f | CI green | **Met** | `gh run list -R 0xmortuex/Mort -L 5`: 5 of 5 success, including the scheduled fuzz run. Of the last 60 runs: 59 success, 1 cancelled, 0 failed. The newest run is `c17f6fe`, one commit ahead of the audited tree |
| g | Docs | **Partial** | README quickstart: present (Usage, `mortc new`). Language tour: missing. Contributor guide: missing, no `CONTRIBUTING.md`. `CHANGELOG.md`: present but stale, newest entry 0.54.0 (2026-08-01) against compiler 0.136.0 |

## Feature matrix

| Feature | State | Evidence |
| --- | --- | --- |
| Type inference | Works | `let a = 5` and `id(a)` compiled and ran. `test_types_and_inference`. Inferred from arguments only, so `vec.new<i64>()` needs its type argument |
| Generics | Works | `test_generic_function_inference_and_monomorphization_run`. No bounds: `fn f<T: Ord>` is a parse error |
| Enums, ADTs, matching | Partial | `test_multi_payload_enums_generic_construction_and_destructuring_run`; exhaustiveness enforced. Nested patterns rejected ("payload match patterns require one binding name"). `match` is a statement only |
| Traits | Missing | `trait` and `impl` are parse errors. No method-call syntax |
| Modules and imports | Partial | `test_namespaced_module_alias_and_pub_visibility_run`. `pub` applies to functions only. A file that declares `module x;` and defines `main` fails with "no 'main' function defined" |
| Result and Option | Works | `test_result_try_propagates_success_and_error`; probe printed 42 and 3 |
| Closures | Missing | Lambdas and nested `fn` are parse errors. Plain function pointers work (`test_function_values_callbacks_and_generic_higher_order_calls_run`) |
| Freestanding | Works | `test_freestanding_object_builds`; the kernel builds and boots. MortOS still supplies `memset` and friends in `runtime.c` |
| Inline asm with operands | Missing | `asm(STRING);` is the entire syntax. GNU-style and Rust-style operands are parse errors, and `asm` cannot be an expression. Only the compiler's own port-I/O builtins bind operands |
| Unsafe raw pointers | Partial | Pointers and integer casts work (`test_pointer_ops_codegen`). No `unsafe` boundary exists. `p + 1` is rejected, so arithmetic goes through integer casts or indexing |
| MMIO and volatile | **Missing, miscompiles** | No syntax, and codegen emits plain `*p`. At `-O2` a test with two stores and a poll loop on `0xFEE00000` kept only the last store, deleted the loop, and returned a constant |
| Interrupt handlers | Partial | Mort functions called from hand-written stubs (`kernel/idt.s`). No language support; `interrupt fn` and `@interrupt` are errors |
| Fixed-width integers | Works | `test_fixed_width_runtime_integer_semantics_are_defined_under_ubsan`; probe wrapped `u8` and `i32` correctly |
| Packed structs | Missing | `packed struct` and `@packed` are errors. `struct { u8, u32 }` has `sizeof` 8 |

## Tooling matrix

| Tool | State | Evidence |
| --- | --- | --- |
| Formatter | Partial | `mortc fmt`, `test_fmt_check_and_write_commands`. Only re-indents and collapses blank lines. Idempotent on all 68 files sampled, but 17 of them are not in formatted state |
| Linter | Partial | `--warn-unused --deny-warnings` only. No lint command |
| Package manager | Works, empty | `mortc add`, `fetch`, lockfiles and signed registry (`test_registry_dependency_resolves_from_offline_mirror`). `registry/index.json` has 0 packages |
| LSP | Partial | `mortc lsp`, `test_lsp_completion_symbols_and_hover_use_language_structure`. Advertises diagnostics, completion, symbols, hover, signature help and formatting. No go-to-definition, references or rename |
| REPL | Missing | `mortc repl` reports "cannot find file 'repl'" |
| Test runner | Works | `mortc test` ran a scratch project: 1 file passed. `mortc coverage` also works |
| Doc generator | Works, signatures only | `mortc doc` produced output; `_package_api` does not read comments |
| VS Code highlighting | Missing | No grammar or extension in `git ls-files`, none installed locally |

## Stdlib, allocators, benchmarks

- 29 modules, 5204 lines: containers, strings, JSON, files, threads, tasks,
  TCP/UDP, HTTP/1.1, TLS client, WebSocket.
- Allocators: **none** of bump, free-list or slab exist in `std`. `alloc` and
  `free` are hosted builtins and are unavailable in freestanding builds. MortOS
  hand-rolls a first-fit heap (`MortOS/kmain.mx:1031`).
- Benchmarks: `mortc bench` is a timing tool. There is no benchmark suite and
  no `BENCHMARKS.md`.

## Calling other languages

`extern fn` calling C works (`test_extern_function_links_and_runs`) and C
callbacks work through function pointers, so outbound C ABI is the one existing
story. It is one-directional in practice:

| Need | State |
| --- | --- |
| Call C from Mort | Works |
| Pass struct by value to C | Accepted by the checker, but layout is not guaranteed (spec section 11) |
| Variadic C functions | Parse error |
| `extern` data | Parse error |
| Expose Mort to C | No `export`. Symbols are `mort_<name>`, an implementation detail under the compatibility policy |
| Shared library output, generated C header | Missing |

Vex is a Python-hosted bytecode VM. Its README does not mention an FFI, so Vex
interop needs new work on the Vex side as well as gap 8 below.

## Spec against parser

16 grammar spot-checks were compiled against the parser. These matched: `pub`
on functions only, `asm` form, no trailing comma in parameters or arguments,
integer-literal array sizes, typed inclusive `for`, compound shifts, cast
chains, empty structs, generic struct literals. These diverged:

| # | Program | Spec says | Compiler does |
| --- | --- | --- | --- |
| 1 | `get()(1)` | Valid (`postfix` allows a call on any primary) | "only named functions can be called" |
| 2 | `h.cb(1)` on a function-typed field | Valid | "unknown module or import alias 'h'". `let g = h.cb; g(1)` works |
| 3 | `B<B<i64>> { v: ... }` | Valid | Parser bug: `_looks_like_struct_lit` has no `T.SHR` case, unlike its two sibling lookaheads |
| 4 | `if P { x: 1 }.x == 1 { ... }` | Valid | Rejected by the intended no-struct-literal rule, which the grammar omits |
| 5 | `E<i64>.A(1)` | Not derivable | Accepted, and it is the normal way to build a generic variant |

Also, the `literal` production used by `primary` is never defined.

## Ranked gaps

`[MortOS]` marks items MortOS is likely to need from the language.

### 1. Volatile memory access `[MortOS]`

- **Missing:** volatile loads and stores.
- **Why:** this is a silent miscompile for device registers. Both kernels build
  at `-O2`, and the demonstration above shows real accesses being deleted.
  Framebuffer writes survive today only because they are not re-read or
  overwritten.
- **Size:** 3 to 5h for `volatile_read<T>` and `volatile_write<T>` builtins,
  8 to 12h for a `*volatile T` type.
- **Depends on:** nothing.
- **Files:** `mort/typechecker.py`, `mort/codegen.py`, the spec, `conformance/`.
  The parser too if a type is chosen.

### 2. Self-hosting

- **Missing:** everything: stage 1, stage 2, and the fixed-point check.
- **Why:** the only exit criterion with no progress. It would also be the
  strongest test of the language outside the kernel.
- **Size:** 150 to 300h. About 6,600 lines of Python to port (lexer, parser,
  checker, codegen and driver core). Roughly 1,100 lines of the codegen are
  embedded C text and should port cheaply.
- **Depends on:** command-line access (`int main(void)` is hard-coded and `std`
  has no `argv`, 1 to 2h), gap 3, and preferably gap 12's nested patterns and
  `match` expressions. A real type representation should replace strings during
  the port. Decide early whether stage 1 reuses `mortc.py` for packaging.
- **Files:** new `compiler/` tree, `.github/workflows/ci.yml` for a
  stage-1-equals-stage-2 gate.

### 3. Front end accepts programs it should reject

- **Missing:** checks for cases that reach the C compiler or silently pass.
  Confirmed:
  - `let x: i64 = 18446744073709551616;` passes `--check`, against spec
    section 2.3.
  - `f() == f()` on `void` reaches C and fails there.
  - Slice `==` fails in C (already noted in `BACKLOG.md`).
  - Invalid string escapes such as `"\xZZ"` fail in C.
  - 2 of 25 sampled fuzz-accepted mutants failed C compilation.
- **Why:** an unsound literal check is a spec violation. A raw C error is a poor
  diagnostic, and it blocks reliable self-hosting.
- **Size:** 4 to 10h.
- **Depends on:** nothing.
- **Files:** `mort/typechecker.py` (`_coerce`, binary checks), `mort/lexer.py`,
  `conformance/`.

### 4. Inline asm with operands `[MortOS]`

- **Missing:** input, output and clobber binding, and `asm` as a value.
- **Why:** kernels need `rdtsc`, `cpuid`, `cr3`, `lidt` and `int` with registers
  bound. Today these require separate `.s` files or new compiler builtins.
- **Size:** 8 to 14h.
- **Depends on:** nothing.
- **Files:** `mort/parser.py`, `mort/mort_ast.py`, `mort/typechecker.py`,
  `mort/codegen.py:2162`, spec section 3.

### 5. Packed structs and defined layout `[MortOS]`

- **Missing:** `packed`, explicit alignment, and a guaranteed C-compatible
  layout.
- **Why:** GDT, IDT, packet and on-disk structures currently need manual byte
  offsets, as `kernel/kmain.mx` does for multiboot info. FFI by-value structs
  depend on this too.
- **Size:** 6 to 10h.
- **Depends on:** nothing.
- **Files:** `mort/parser.py`, `mort/codegen.py` (`_gen_struct`), the spec.

### 6. Documentation

- **Missing:** a language tour, `CONTRIBUTING.md`, and 82 minor versions of
  changelog. README's test badge says 330 (measured 438).
  `docs/compatibility-policy.md` calls the kernel target x86-64, while
  `kernel/build.py` builds 32-bit.
- **Why:** this closes exit criterion (g) cheaply. The policy document itself
  requires changelog entries for breaking changes.
- **Size:** 8 to 14h.
- **Depends on:** nothing.
- **Files:** new `docs/tour.md`, new `CONTRIBUTING.md`, `CHANGELOG.md`,
  `README.md`.

### 7. Reconcile the spec with the parser

- **Missing:** fixes for the 5 divergences above, a `literal` production, type
  layout rules, and the `module` + `main` behavior.
- **Why:** closes exit criterion (a). Item 3 is a three-line fix.
- **Size:** 4 to 8h. Calling arbitrary expressions adds parser and codegen work.
- **Depends on:** nothing.
- **Files:** `docs/language-specification.md`, `mort/parser.py`, `conformance/`.

### 8. Complete the FFI for Vex and other callers

- **Missing:** `export` with stable names, variadics, `extern` data, shared
  library output, generated headers.
- **Why:** the planned Vex interop cannot call into Mort without a stable
  inbound ABI.
- **Size:** 16 to 30h on the Mort side.
- **Depends on:** gap 5, and a Vex-side FFI.
- **Files:** `mort/parser.py`, `mort/codegen.py`, `mortc.py`.

### 9. Interrupt handler support `[MortOS]`

- **Missing:** a language form that generates correct entry and exit, or at
  least naked functions.
- **Why:** every new vector currently needs hand-written assembly.
- **Size:** 4 to 8h.
- **Depends on:** gap 4.
- **Files:** `mort/parser.py`, `mort/codegen.py`, `kernel/idt.s`.

### 10. Freestanding allocators in std `[MortOS]`

- **Missing:** bump, free-list and slab allocators usable without libc, and a
  way for `Vec` and `Map` to use them.
- **Why:** kernel code cannot reuse the standard containers.
- **Size:** 10 to 20h. Clean allocator parameterization is easier with traits.
- **Depends on:** partly gap 12.
- **Files:** new `std/alloc.mx`, `std/vec.mx`, `std/map.mx`.

### 11. Front-end resource limits

- **Missing:** bounds on compile-time cost.
  - `[0; N]` is expanded in memory (`codegen.py:2555`). With `N = 2000000000`
    under a 700 MB cap it raised an uncaught `MemoryError`, not a diagnostic.
    Without a cap it would try to allocate many gigabytes.
  - `<` lookahead is quadratic: 8000 chained terms took 40.7s, 4000 took 9.6s.
  - `mortc fuzz` has no per-case timeout, so it cannot detect a hang.
- **Why:** a small source file can exhaust the machine.
- **Size:** 3 to 5h.
- **Depends on:** nothing.
- **Files:** `mort/codegen.py`, `mort/parser.py`, `mort/fuzz.py`, `mortc.py`.

### 12. Language expressiveness

- **Missing:** traits, closures, nested patterns, `match` expressions, method
  syntax.
- **Why:** they shape every library API, and nested patterns would substantially
  shorten a self-hosted compiler.
- **Size:** 60 to 120h, with nested patterns and `match` expressions the
  cheapest first steps (10 to 20h).
- **Depends on:** a decision on trait dispatch (monomorphized only is the
  smallest).
- **Files:** `mort/parser.py`, `mort/typechecker.py`, `mort/codegen.py`, spec.

### 13. Editor and day-to-day tooling

- **Missing:** VS Code extension (4 to 8h; no client currently launches the
  LSP), go-to-definition and references (8 to 12h), AST-based formatter
  (16 to 24h), REPL (8 to 16h), `#line` directives for source-level debugging
  (4 to 8h; none are emitted today).
- **Why:** first impressions, and the checklist's debugging item.
- **Depends on:** nothing.
- **Files:** `mort/lsp.py`, `mort/formatter.py`, `mort/codegen.py`, new
  `editors/vscode/`.

### 14. Benchmarks

- **Missing:** a tracked suite and `BENCHMARKS.md` against C and the prior
  release.
- **Why:** performance claims are currently unmeasured.
- **Size:** 6 to 10h.
- **Depends on:** nothing.
- **Files:** new `benchmarks/`.

### 15. Small fixes

- `conformance/run.py` default fails on Windows paths containing a space (1h).
- `kernel/test*.py` do not run in CI for the in-repo kernel, which only gets
  `build.py check`. MortOS is booted in CI (1 to 2h).
- A file with `module x;` cannot define the hosted `main` (1 to 2h). Root cause
  not investigated.
- Outdated grammar docstring in `mort/parser.py` (minutes).

## Limits of this audit

- The Atheris harness was not run locally. Its evidence is CI only.
- `ruff`, `tests/differential_backends.py`, sanitizer jobs and
  `tests/release_compatibility.py` were not run locally.
- MortOS was compiled to an object here, not booted. Booting it is covered by
  the `mortos-kernel` CI job.
- Fuzz campaigns were capped at 60 seconds, and accepted mutants were compiled
  to C only in a 25-case sample.
