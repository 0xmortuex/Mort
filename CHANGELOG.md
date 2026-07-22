# Changelog

## 0.21.0 — 2026-07-22

Mort's precise control-flow release.

### Added

- Inclusive `start..=end` ranges with overflow-safe iteration at integer maxima.
- First-class `loop { ... }` infinite loops.
- Non-fallthrough analysis for unconditional loops without reachable `break`.

### Improved

- Range start and end expressions are evaluated once, in source order, before
  iteration begins.
- Inclusive loops preserve correct increment behavior across `continue`, while
  `break` and lexical `defer` keep their existing guarantees.

### Validation

- 223 compiler, control-flow, native execution, package, tooling, and kernel
  tests pass.

## 0.20.0 — 2026-07-22

Mort's source-ergonomics and pointer-safety release.

### Added

- Typed `null` for pointer initialization, assignment, comparison, return
  values, and arguments, with rejection outside pointer contexts.
- Character and hexadecimal character literals with a checked `u8` type.
- Binary and octal integer literals and `_` numeric separators.
- Nestable `/* block comments */` throughout Mort source.
- Arithmetic, bitwise, and shift compound assignments (`+=` through `>>=`)
  for every assignable lvalue, preserving single target evaluation.

### Improved

- The formatter now ignores braces inside character literals and nested block
  comments, including comments spanning multiple lines.
- Literal diagnostics reject malformed bases, separators, escapes, and
  unterminated comments before parsing.

### Validation

- 220 compiler, literal, pointer, formatter, native execution, package,
  tooling, and kernel tests pass.

## 0.19.0 — 2026-07-22

Mort's floating-point and type-alias release.

### Added

- Native `f32` and `f64` types.
- Decimal and scientific-notation floating-point literals.
- Checked float arithmetic, comparisons, casts, and literal narrowing.
- Precision-preserving numeric output for floats.
- Representation-transparent `type Name = Target;` aliases.
- Alias expansion through pointers, slices, arrays, generics, structs, enums,
  functions, casts, and constructors, with cycle/collision diagnostics.

### Validation

- 209 compiler, floats, aliases, generics, native execution, package, tooling,
  and kernel tests pass.

## 0.18.0 — 2026-07-22

Mort's immutable-bindings and portable-I/O release.

### Added

- Immutable local and global `const` bindings with C-level const lowering.
- Transitive const protection for field/index assignment and address taking.
- Portable hosted `std.fs` file handles and `std.time` clocks.
- `-O0` through `-O3`, `-Os`, and `-g` backend build controls.
- Build-mode-aware incremental cache fingerprints.
- Full-package content hashes in lockfile format version 2.
- `mortc fetch --locked` for non-mutating dependency-drift checks.

### Validation

- 200 compiler, const-safety, file/time, package-lock, optimization, native
  execution, and kernel tests pass.

## 0.17.0 — 2026-07-22

Mort's editor, incremental-build, and portable-stdlib release.

### Added

- Opt-in unused-binding warnings with underscore suppression.
- `--deny-warnings` and human/JSON warning diagnostics.
- Dependency-free `mortc lsp` with unsaved-buffer and import-aware diagnostics.
- Content-addressed project build caching with deterministic invalidation.
- Seeded grammar and mutation fuzzing through `mortc fuzz`.
- Portable `std.env`, `std.process`, and generic `std.math` modules.

### Validation

- 193 compiler, warning, LSP, cache, fuzz, standard-library, native execution,
  and kernel tests pass.

## 0.16.0 — 2026-07-22

Mort's machine-diagnostics release.

### Added

- `--check` for a fast front-end-only compiler pass without a C backend.
- `--diagnostic-format json` for stable editor and CI integration.
- Structured severity, location, range, source-line, and message fields.
- Source-aware JSON diagnostics for lexer, parser, and type-checker failures.

### Validation

- 187 compiler, diagnostics, cleanup, collections, native execution, and kernel
  tests pass.

## 0.15.0 — 2026-07-22

Mort's lexical-cleanup release.

### Added

- `defer` in functions, nested blocks, conditionals, loops, and match arms.
- Inner-to-outer cleanup on returns and normal scope exits.
- Loop-scope cleanup before `break` and `continue`.
- Full active-scope cleanup when `try` propagates a `Result.Err`.
- Return-value evaluation before cleanup, preserving resource-dependent results.

### Validation

- 186 compiler, cleanup, generics, collections, native execution, and kernel
  tests pass.

## 0.14.0 — 2026-07-22

Mort's generic-functions and typed-collections release.

### Added

- Inferred and explicit generic function calls with native monomorphization.
- Recursive generic functions and generic calls across module boundaries.
- Nested type-pattern inference with conflict and missing-argument diagnostics.
- `sizeof<T>()` for portable typed allocation.
- Allocation-backed `std.vec.Vec<T>` with growth, access, slices, and cleanup.
- Allocation-backed `std.map.Map<Key, Value>` with linear lookup, replacement,
  growth, membership checks, and cleanup.

### Validation

- 186 compiler, generics, collections, package, native execution, and kernel
  tests pass.

## 0.13.0 — 2026-07-22

Mort's generic-sum and structured-error release.

### Added

- Monomorphized generic enums with native tagged-union layouts.
- Bundled `std.option.Option<T>` and `std.result.Result<Value, Error>` types.
- Explicit generic variant construction and exhaustive payload matching.
- `try` propagation for `Result` values, with checked error-type compatibility.
- Nested generic type parsing without changing expression shift semantics.

### Validation

- 177 compiler, generics, error propagation, package, native execution, and
  kernel tests pass.

## 0.12.0 — 2026-07-22

Mort's portable-dependency and deterministic-cleanup release.

### Added

- Function-scoped `defer` cleanup across every return path.
- Git dependencies through `mortc add --git URL --ref BRANCH_OR_TAG`.
- Project-local `.mort/deps` package caching.
- Exact Git commit revisions in `mort.lock`.
- Relative local-dependency lock records for cross-machine portability.

### Validation

- 168 compiler, cleanup, package, native execution, and kernel tests pass.

## 0.11.0 — 2026-07-22

Mort's algebraic-data and generic-layout release.

### Added

- Payload-carrying enum variants compiled as tagged native unions.
- Exhaustive match bindings such as `Result.Value(value)`.
- Payload constructor arity and type validation.
- Monomorphized generic structs with multiple type parameters.
- Concrete generic layouts in parameters, returns, literals, and fields.

### Validation

- 164 compiler, generic-layout, native execution, project, and kernel tests pass.

## 0.10.0 — 2026-07-22

Mort's namespaced-package and safe-data release.

### Added

- Explicit `module` namespaces and qualified function calls.
- Import aliases through `import module as alias;`.
- Private-by-default module functions and enforced `pub` visibility.
- Local path dependencies with `mortc add --path` and `mortc fetch`.
- Recursive dependency resolution and deterministic SHA-256 `mort.lock` files.
- Mutable `[]T` and read-only `[]const T` slices.
- Slice construction, fields, parameters, returns, length, and checked indexing.
- Allocation-backed `std.owned_string` construction, concatenation, views, and cleanup.

### Validation

- 157 compiler, project, package, FFI, native execution, and kernel tests pass.

## 0.9.0 — 2026-07-22

Mort's project, safety, and native-testing release.

### Added

- `mort.toml` projects and `mortc new`, `build`, `run`, `test`, and `fmt`.
- Recursive local and `std` imports with cycle-safe source deduplication.
- First-class `test "name" { ... }` blocks and generated native test harnesses.
- Enums and exhaustive `match` statements.
- Hosted `println`, `assert`, `len`, `alloc`, and `free` primitives.
- Compile-time constant-index checks and hosted runtime array bounds checks.
- Guaranteed-return control-flow checking for non-void functions.
- C-native integer types and `*const T` pointers for accurate FFI signatures.
- Source excerpts in diagnostics and a comment-preserving formatter.

### Validation

- 151 compiler, native execution, project workflow, FFI, and kernel tests pass.

## 0.8.0 — 2026-07-22

Mort's first real-project compatibility release.

### Added

- Multi-file compilation with one shared, statically checked namespace.
- Typed C-ABI declarations through `extern fn`.
- Native object/library linking through `--link` and `-l`.
- `*void` for opaque foreign handles.
- Typed pointer indexing and addressable array elements.
- `break` and `continue` with loop-context validation.
- Source filenames in multi-file compiler diagnostics.
- Bundled `string` and `memory` standard-library modules through `--std`.
- `mortc --version` and executable interoperability/standard-library examples.

### Compatibility

- Existing single-file builds and `compile_to_c` callers remain supported.
- Freestanding/kernel compilation retains its previous behavior.
- All compiler, native execution, and kernel validation tests pass.
