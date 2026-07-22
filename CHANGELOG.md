# Changelog

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
