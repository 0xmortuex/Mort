# Mort production-readiness checklist

Mort is developed toward broad production use, but no non-trivial programming
language can truthfully guarantee that it is error-free or ideal for every
project. This checklist makes the remaining work explicit. An item is checked
only when an automated gate or named artifact provides repeatable evidence.

## Compiler correctness and resilience

- [x] The lexer, parser, checker, code generator, CLI, package resolver, LSP,
  formatter, standard library, native examples, and kernel have regression
  coverage.
- [x] Invalid source is exercised by deterministic mutation and structured
  adversarial fuzzing.
- [x] Excessive source nesting produces a Mort diagnostic instead of escaping
  as a Python recursion crash.
- [x] Generated hosted C is compiled and executed in the native test suite.
- [x] Generated freestanding code builds the MORT OS kernel.
- [x] A coverage-guided Atheris/libFuzzer target runs daily and on relevant
  pull requests with a persistent seed corpus and retained minimized crashes.
- [x] Hosted builds have first-class AddressSanitizer,
  UndefinedBehaviorSanitizer, and leak-sanitizer controls.
- [x] CI and release gates exercise representative generated C under ASan and
  UBSan with leak detection enabled.
- [x] Exact-output differential and metamorphic tests cover GCC, Clang, and
  Zig cc at `-O0`, `-O1`, `-O2`, `-O3`, and `-Os` in CI and release gates.
  Evidence: the [v0.42 CI matrix](https://github.com/0xmortuex/Mort/actions/runs/30154832916)
  and [v0.42 release gates](https://github.com/0xmortuex/Mort/actions/runs/30154924377).
- [x] Mort 0.41 has a versioned
  [normative specification](language-specification.md) and a black-box
  [executable conformance suite](../conformance/README.md), gated on Linux,
  Windows, macOS, and releases.
- [ ] An independent compiler and security audit has been completed and all
  critical findings are closed.

## Dependency and supply-chain safety

- [x] Lockfiles include versions, Git revisions, manifest hashes, and portable
  package-content hashes.
- [x] Vendored TLS sources and the default CA bundle are versioned,
  SHA-256-pinned, verified before extraction, license-attributed, and isolated
  behind a cache key that includes their content and compiler identity.
- [x] Existing Git caches refresh and detach at the revision requested by the
  manifest rather than silently reusing stale content.
- [x] Cached repositories must have the expected origin and no local changes.
- [x] Registry indexes are size-bounded and fully schema-validated before use.
- [x] Registry package names and Git refs are validated.
- [x] Dependency sources and entry points cannot escape their package root,
  including through symlinks.
- [x] Lockfile hashing records symlink targets without following them.
- [x] Registry records authenticate immutable source archives or commits with
  publisher signatures.
- [x] The client verifies a transparent package log and supports revocation.
- [x] A clean-tree reproducibility gate builds the same Git revision twice and
  requires byte-identical wheel and source-archive SHA-256 hashes in CI and
  release workflows. The published v0.43.0 artifacts were independently
  rebuilt from the tag with exact matches: wheel
  `598d48103c0f2acb8d5cc397ce7bb3ce250dc8a2c92d1a87a61d027b579aeba2`
  and sdist
  `6bbae8ac95a6aaee41bd07469d37c791cb3c4df13f6e2cd84d87cb429c4af76c`.
  Evidence: [CI](https://github.com/0xmortuex/Mort/actions/runs/30155638801),
  [release gates](https://github.com/0xmortuex/Mort/actions/runs/30155738393),
  and [published artifacts](https://github.com/0xmortuex/Mort/releases/tag/v0.43.0).

## Platforms and compatibility

- [x] Package metadata supports Python 3.10 through the current stable 3.14.
- [x] CI is configured for Linux, Windows, and macOS with a portable Zig C
  backend, native tests, fuzzing, and a kernel build gate.
- [x] Linux, Windows, and ARM64 macOS are green for the current release,
  including native execution, fuzzing, packaging, and the x86-64 kernel build
  ([CI evidence](https://github.com/0xmortuex/Mort/actions/runs/30153547441)).
- [x] Python 3.13 and 3.14 have dedicated Linux CI jobs.
- [x] Native ARM64 Linux and macOS targets assert the host architecture and run
  the full tests, conformance suite, fuzzing, and kernel cross-build
  ([CI evidence](https://github.com/0xmortuex/Mort/actions/runs/30156538075)).
- [ ] WebAssembly, Android, and iOS have supported targets and platform APIs.
- [x] Backward source-compatibility and deprecation policies are enforced across
  multiple release generations.

## Language capabilities needed for broad application use

- [x] Static types, generics, algebraic enums, tuples, callbacks, ownership,
  deterministic destruction, slices, collections, and C interoperability.
- [x] Hosted native and x86 freestanding compilation.
- [ ] Borrowed-reference lifetimes prevent dangling pointers without requiring
  raw-pointer discipline.
- [x] Mort 0.41 defines thread-safety and data-race rules and provides
  cross-platform threads, joins, mutexes, sequentially consistent `AtomicI64`,
  synchronized conformance coverage, and positive/negative ThreadSanitizer
  gates.
- [x] Mort 0.38 provides portable nonblocking sockets, readiness waits,
  would-block handling, a structured task-group runtime, sequentially
  consistent cooperative cancellation, automatic cancel/join at scope exit,
  conformance cases, and sanitizer gates.
- [x] First-party cross-platform blocking TCP, UDP, and DNS with resource-safe
  sockets, specified stream/datagram semantics, source endpoints, truncation
  normalization, and loopback conformance coverage.
- [x] First-party bounded HTTP/1.1 with injection-safe serialization,
  unambiguous content-length framing, caller-controlled message limits,
  loopback conformance coverage, and sanitizer gates.
- [x] First-party bounded RFC 6455 WebSocket upgrades and frames with
  OS-random client keys/masks, strict framing, UTF-8 and close-code validation,
  loopback conformance coverage, and sanitizer gates.
- [x] First-party blocking TLS 1.2/1.3 clients with mandatory certificate-chain,
  validity-time, and hostname/IP verification, default checksum-pinned Mozilla
  roots, explicit private CA roots, bounded transfers, automatic cleanup,
  bounded HTTPS, and cross-platform success/mismatch/untrusted loopback gates.
- [x] Operating-system cryptographic random bytes on Windows, Linux, and
  macOS, with no deterministic fallback.
- [ ] Unicode text, locale, regular-expression, serialization, compression,
  general-purpose cryptography, and database libraries.
- [ ] Stable dynamic-library, plugin, and cross-language binding workflows.

## Developer experience and ecosystem

- [x] Project creation, build/run/test, formatting, diagnostics, caching,
  dependency resolution, offline fetches, and an LSP.
- [x] Wheels and source distributions are installed and smoke-tested before a
  GitHub release is published.
- [ ] A complete language reference, standard-library API reference, cookbook,
  and guided tutorial are published and versioned.
- [ ] Source-level debugging works in major editors with reliable Mort source
  locations and variable inspection.
- [x] Profiling, coverage reporting, benchmarking, and package documentation
  generation are integrated into the CLI.
- [ ] The public registry contains a useful audited package ecosystem rather
  than only an index format.
- [ ] Large maintained applications outside the compiler and MORT OS complete
  sustained release cycles on Mort.
- [x] A [security and vulnerability-response policy](../SECURITY.md) and
  [version-support and compatibility promise](compatibility-policy.md) are
  published; private GitHub vulnerability reporting is enabled.

## Release claim rule

Mort remains **alpha** while any unchecked item above is material to the project
being considered. A green test suite proves the behavior covered by that suite;
it does not prove the absence of every compiler bug. The project will not use
“error-free,” “finished for every project,” or equivalent wording unless all
applicable items are checked and the evidence supports that exact scope.
