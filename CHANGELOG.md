# Changelog

## 0.27.0 — 2026-07-23

Mort's native tuple release.

### Added

- Heterogeneous tuple types such as `(i64, bool, *u8)` and tuple literals such
  as `(42, true, "Mort")`.
- Zero-based tuple field access and mutation with `value.0`, including chained
  access such as `nested.0.1`.
- Contextual tuple-literal coercion for annotated bindings, returns, function
  arguments, arrays, struct fields, and globals.
- Tuple composition through type aliases, generic inference and
  monomorphization, callbacks, arrays, slices, pointers, and `sizeof<T>()`.

### Code generation

- Each concrete tuple receives one deterministic, reusable C11 struct layout.
- Aggregate definitions are dependency ordered, so structs can contain tuples
  by value and tuples can contain structs by value regardless of source order.
- Tuple literals lower to typed C compound literals and retain left-to-right
  `try` propagation semantics.

### Validation

- Nested, generic, mutable, aliased, global, callback, array, slice, and
  struct-composed tuples compile under `-Wall -Werror` and execute natively.
- Invalid arity, element types, fields, comparisons, matches, and `void`
  elements receive compile-time diagnostics.
- 254 compiler, tuple, error-propagation, callback, packaging, CLI, native,
  LSP, package, and kernel tests pass.

## 0.26.0 — 2026-07-23

Mort's general error-propagation release.

### Added

- `try` can now appear throughout eager expressions rather than only as a
  complete `let` initializer.
- Supported contexts include assignments and compound assignments, arithmetic,
  function arguments, struct fields, array elements, indices, match subjects,
  range bounds, `if`/`while` conditions, casts, and nested expressions.
- Short-circuit `&&` and `||` lower to conditional C11 control flow, so a
  fallible right operand executes only when the source expression requires it.

### Semantics

- Nested `try` expressions evaluate left-to-right into typed temporaries.
- Propagated errors retain compatible `Result` error payloads and execute every
  active lexical `defer` exactly once.
- Fallible `while` conditions are reevaluated and propagated on each iteration.
- `try` inside `defer` and match patterns is rejected because those contexts
  have deferred or pattern-only evaluation semantics.

### Validation

- Success/error, nested eager-expression, loop, match, index, cleanup, and
  skipped short-circuit paths compile under `-Wall -Werror` and execute natively.
- 243 compiler, error-propagation, callback, packaging, CLI, native, LSP,
  package, and kernel tests pass.

## 0.25.1 — 2026-07-23

Mort's module-callback interoperability patch.

### Added

- Public imported functions such as `numbers.add` can now be captured as typed
  callback values through their module alias.
- Imported callback capture enforces module visibility and rejects private or
  unresolved members before C generation.

### Validation

- Imported callbacks compile and execute across separate source modules.
- 240 compiler, callback, module, packaging, CLI, native, LSP, package, and
  kernel tests pass.

## 0.25.0 — 2026-07-23

Mort's first-class callbacks release.

### Added

- Function pointer types with `fn(Parameter, ...) -> Return` syntax.
- Function declarations and external functions as statically typed values.
- Inferred or annotated callback bindings, mutable callback reassignment, and
  callback globals/struct fields/generic fields.
- Checked indirect calls with argument count, parameter, return, and
  non-callable-value diagnostics.
- Callback parameters and callback-returning Mort functions.
- Generic higher-order functions that infer type parameters through callback
  signatures.
- Nullable callback values for interoperable C APIs.

### Code generation

- Portable C11 declarators for function pointer variables, constants, fields,
  parameters, external signatures, and return values.
- Function prototypes now precede global initializers, allowing constant global
  callbacks without declaration-order constraints.

### Validation

- Callback programs compile under `-Wall -Werror` and execute through direct,
  indirect, returned, global, struct-field, and generic call paths.
- 239 compiler, callback, packaging, CLI, standard-library, native, LSP,
  package, and kernel tests pass.

## 0.24.1 — 2026-07-23

Mort's distributable-release patch.

### Added

- Tagged releases now build a wheel and source archive, verify the wheel in an
  isolated environment, and publish both artifacts to GitHub Releases.
- A `dev` packaging extra provides the build, test, and Zig toolchain
  dependencies used by contributors and CI.
- Direct Git installation is documented for users who do not keep a checkout.

### Improved

- Packaging/compiler version synchronization is regression-tested.
- The roadmap now reflects the completed generic ecosystem, LSP, caching,
  diagnostics, formatter, and machine-wide packaging work.

### Validation

- 233 compiler, packaging, CLI, standard-library, native, LSP, package, and
  kernel tests pass.

## 0.24.0 — 2026-07-23

Mort's install-anywhere toolchain release.

### Added

- Standard `pyproject.toml` packaging with a machine-wide `mortc` console
  command and Python 3.10–3.12 metadata.
- Wheel-distributed standard-library sources with automatic source-checkout or
  installed-layout discovery.
- `mortc std [--path]` to list and locate all bundled modules.
- `mortc doctor` to report Mort/Python versions, standard-library health, the
  native C backend, and the Zig freestanding backend.
- CI now installs the package and verifies the global entry point before
  running compiler and kernel tests.

### Validation

- Editable and isolated wheel installations both expose `mortc` and all 15
  standard modules outside the repository.
- 232 compiler, packaging, CLI, numeric-safety, standard-library, native, LSP,
  package, and kernel tests pass.

## 0.23.1 — 2026-07-23

Mort's numeric-safety patch.

### Fixed

- Constant integer division and remainder by zero are rejected by the type
  checker instead of reaching invalid generated C.
- Non-finite floating-point literals are rejected during lexing.
- Finite `f64` literals that overflow an annotated `f32` are rejected during
  contextual narrowing.

### Validation

- 231 compiler, numeric-safety, standard-library, native execution, LSP,
  package, and kernel tests pass.

## 0.23.0 — 2026-07-22

Mort's portable data-utilities release.

### Added

- `std.random.Random` with deterministic nonzero seeding, `next_u64`,
  `next_u32`, bounded/between generation, and byte-slice filling.
- `std.bytes` with length-safe fill, zero, copy, and equality operations over
  mutable and const slices.
- Generic `std.algorithm` slice sorting, reversal, containment, and
  `Option<u64>` indexed search.

### Portability

- All three modules are written entirely in Mort and require no hosted runtime,
  OS API, C library extension, or platform-specific linker flag.

### Validation

- 227 compiler, standard-library, generic, native execution, LSP, package, and
  kernel tests pass.

## 0.22.0 — 2026-07-22

Mort's editor-intelligence release.

### Added

- Parser-backed LSP document symbols for modules, aliases, structs, enums,
  globals, external declarations, functions, and tests.
- Completion for Mort keywords, primitive types, builtins, imports, and
  document-level declarations, with useful fallback results on incomplete code.
- Signature hover for builtins and source functions.
- Nested-call-aware signature help with active-parameter tracking.
- LSP whole-document formatting through Mort's comment-preserving formatter.

### Improved

- `initialize` now advertises completion, outline, hover, signature-help, and
  formatting capabilities alongside full-document synchronization.

### Validation

- 225 compiler, LSP, formatting, native execution, package, and kernel tests
  pass.

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
