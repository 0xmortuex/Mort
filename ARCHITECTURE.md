# Mort architecture

Audited at commit `25255e6` (compiler `0.136.0`, language contract `0.41.0`) on
2026-11-08. `origin/main` was one commit ahead at the time (`c17f6fe`, a
`std.algorithm` addition). Line counts are from `wc -l`.

## Overview

Mort is a statically typed systems language. The compiler is written in Python
and has no IR: it goes straight from a typed AST to one C11 translation unit,
then calls a C compiler. The only third-party Python dependency is
`cryptography`, used by the package registry. See `pyproject.toml`.

```
.mx source
  |  import resolution     mortc.py: compile_files_to_c     recursive, per file
  |  Lexer                 mort/lexer.py                    text -> tokens
  |  Parser                mort/parser.py                   tokens -> AST, recursive descent
  |  (programs merged)     mortc.py: _compile_programs      all files -> one Program
  |  Checker               mort/typechecker.py              annotates AST, monomorphizes, checks moves
  |  CodeGen               mort/codegen.py                  AST -> one C11 string
  v  C backend             cc | gcc | clang | zig cc        C -> executable or object
```

Entry points:

| Function in `mortc.py` | Purpose |
| --- | --- |
| `main` | Dispatches subcommands, otherwise falls through to `_compile_main` |
| `_compile_main` | Compile flags, backend selection, link, `--run` |
| `compile_files_to_c` | File-based compile with imports and packages |
| `compile_to_c` / `compile_sources_to_c` | In-memory compile. Rejects `import`. Used by the kernel builds and the fuzzers |
| `find_c_compiler` / `find_zig` | `MORT_CC`, then `CC`, then `cc`, `gcc`, `clang`, then Zig. Freestanding always uses Zig |

## Modules

| Path | Lines | Role |
| --- | ---: | --- |
| `mortc.py` | 1233 | CLI driver, import loader, backend invocation, project commands (build, test, coverage, bench, profile, doc, fmt) |
| `mort/tokens.py` | 143 | Token enum and the 33 keywords |
| `mort/lexer.py` | 303 | Hand-written scanner. Keeps string escapes raw for the C backend |
| `mort/parser.py` | 866 | Recursive descent, about one method per precedence level. Types are parsed into strings. The module docstring grammar is out of date; the spec's EBNF is the current one |
| `mort/mort_ast.py` | 407 | AST node classes |
| `mort/typechecker.py` | 2748 | `Checker`: aliases, generic instantiation, inference, literal coercion, const folding, move and resource dataflow, builtin signatures |
| `mort/codegen.py` | 2697 | `CodeGen`: C emission. Lines 447 to 1521 are embedded C runtime helpers (integer semantics, threads, sockets, secure random) |
| `mort/errors.py` | 91 | `MortError` and `MortWarning`, the single diagnostic type for all phases |
| `mort/formatter.py` | 86 | Line-based re-indenter. Does not use the parser |
| `mort/lsp.py` | 407 | Dependency-free stdio LSP server |
| `mort/fuzz.py` | 129 | Deterministic mutation and adversarial front-end fuzzer (`mortc fuzz`) |
| `mort/project.py` | 923 | `mort.toml`, SemVer solving, Git and registry dependencies, lockfiles |
| `mort/registry_security.py` | 309 | Ed25519 registry signatures and the append-only log |
| `mort/tls_backend.py` | 421 | Verifies, builds and caches vendored Mbed TLS on first use |
| `mort/vendor/` | n/a | Checksum-pinned Mbed TLS 3.6.6, Mozilla CA bundle, `mort_tls.c` |
| `std/*.mx` | 5204 total | 29 standard modules, written in Mort and compiled with the user's program |
| `kernel/` | `kmain.mx` 1900 | MORT OS in Mort plus hand-written `boot.s` and `idt.s`. Older than the standalone MortOS repo |
| `conformance/` | 28 cases | Black-box suite tied to spec sections by `manifest.json` |
| `fuzz/` | n/a | Atheris target and seed corpus |
| `tests/` | `test_mort.py` 9951 | Main suite, plus registry, release-compatibility, differential-backend and reproducible-build gates |
| `tools/` | n/a | `build_release.py`, `registry_admin.py` |
| `docs/` | n/a | Normative spec, stdlib reference, ownership guide, compatibility policy, production-readiness checklist |

## Data flow details

**Imports.** `compile_files_to_c` loads files depth first and dedupes by real
path. `std.x` resolves to `std/x.mx`, a package name resolves to its entry
file, and anything else is relative to the importing file. Each file's aliases
are attached to its functions.

**Whole-program compilation.** `_compile_programs` flattens every file into one
`Program`. There is no separate compilation; caching exists only at the project
level, as a fingerprint of all inputs in `.mort/build-cache.json`.

**Namespacing.** `module a.b;` namespaces functions only (`symbol_name`
`a.b.f`). `pub` applies only to functions. Structs, enums and aliases share one
global namespace, so `Vec<T>` is simply global after `import std.vec;`.

**Types are strings.** The parser produces strings such as `*const u8`,
`[i64;3]`, `Option<i64>`, `fn(i64)->i64` and `(i64,bool)`. The checker and
codegen take them apart with helpers such as `generic_parts`, `function_parts`
and `_split_type_list`. There is no type object model.

**Generics.** Monomorphization happens in the checker. `_instantiate_function`
deep-copies the template AST (`typechecker.py:647`) and substitutes types.
`_instantiate_struct` and `_instantiate_enum` do the same for aggregates. There
are no bounds, so errors surface per instantiation.

**Ownership.** `resource struct` is move-only with a required
`destroy(*R) -> void`. The checker tracks moves with branch-aware snapshots
(`_move_snapshot`, `_merge_move_snapshots`). Codegen emits a `mort_live_*` flag
per resource binding and runs defer and drop scopes on every exit.

**Integer semantics.** Fixed-width arithmetic goes through generated helpers
(`_gen_integer_helpers`, `_fixed_binary`, `_fixed_shift`) so that wrapping,
shifts and division are defined rather than inheriting C undefined behavior.

**Code generation order.** `generate()` builds function bodies into a side
buffer first. That sets the `used_*` flags, so only the helpers and headers a
program actually needs are emitted ahead of the bodies.

**Backend signalling.** Codegen writes `MORT_REQUIRES_PTHREAD`, `_WINSOCK`,
`_BCRYPT` and `_TLS` markers into the C text. `_compile_main` searches the text
for them to pick link flags and to trigger the TLS build.

## Conventions

- Mort function `foo` becomes C `mort_foo`. Locals become `m_x`. Hand-written
  assembly relies on this directly: `kernel/idt.s` does `call mort_on_key`.
- `extern fn` symbols keep their exact names.
- Every user-facing compile failure should be a `MortError`. `RecursionError`
  is translated to "source nesting exceeds the compiler safety limit".
  `mort/depth.py` sets that limit in the compiler's own Python frames, so a
  program is accepted or rejected the same way on every supported Python.
- Compiler version (`mort.__version__`) and language version
  (`mort.__language_version__`) are independent. See
  `docs/compatibility-policy.md`.
- `BACKLOG.md` is worked by a daily agent limited to `std`, docs, examples and
  tests. Compiler internals are explicitly excluded from that loop.
- Freestanding: `--freestanding` compiles a 64-bit x86 object with Zig. The
  kernels instead call `compile_to_c(..., freestanding=True)` and cross-compile
  to 32-bit `x86-freestanding-none` themselves (`kernel/build.py`).

## Build and test

Setup:

```
python -m pip install -e ".[dev]"
```

There is no `uv.lock` in this repo; CI uses pip. A C compiler is optional for
front-end work. On Windows `pip install ziglang` is enough.

| Task | Command | Measured 2026-11-08 |
| --- | --- | --- |
| Tests | `python -m pytest tests/ -q` | 438 passed, 0 failed, 0 skipped, 5:10 |
| Conformance | `python conformance/run.py --mortc mortc` | 28 of 28 (see the note below) |
| Fuzz | `mortc fuzz --cases 10000 --seed 1` | 10,000 cases in 3.7s |
| Kernel build | `python kernel/build.py check` | valid multiboot ELF |
| Kernel boot | `python kernel/test.py smoke` (needs QEMU) | 4 of 4 |
| Lint | `ruff check` with the file list in `ci.yml` | not run in this audit |

Windows note: `conformance/run.py` run with its default `--mortc` fails with
`WinError 193` when the checkout path contains a space, because `_command`
splits the path with `shlex`. Installed `mortc` avoids this. The figure above
was obtained through a small launcher at a path without spaces.

## CI

`.github/workflows/ci.yml` runs on push and pull request:

- released-source compatibility
- the test matrix: Ubuntu on Python 3.10, 3.12, 3.13 and 3.14, plus Windows and
  macOS on 3.12 (pytest, conformance, fuzz, kernel build)
- GCC, Clang and Zig differential at five optimization levels
- native ARM64 Linux and macOS
- ASan and UBSan
- TSan
- Atheris smoke test
- build and boot of the standalone MortOS repo
- package verification and reproducible builds

`fuzz.yml` runs Atheris for 15 minutes daily. `release.yml` repeats the gates on
version tags.
