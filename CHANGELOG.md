# Changelog

## Unreleased

### Added

- `volatile_read<T>(pointer)` and `volatile_write<T>(pointer, value)` builtins
  for `u8`..`u64` and `i8`..`i64`, usable in hosted and freestanding code, with
  misuse reported at the call's line and column. Plain `*p` at `-O2` can delete
  MMIO stores and poll loops; these accesses are not elided or reordered
  relative to each other. Specified in section 7.4 of the language
  specification.
- `eprintln(text)` writes to stderr; `arg_count()` and `arg_get(index)` read the
  command line, and an out-of-range index is a located runtime error.

### Changed: front-end limits

Hostile or malformed input now gets a located diagnostic instead of a crash or a
hang. These limits are in section 11 of the language specification:

- Integer literals have at most 4300 digits in any base.
- A folded constant needs at most 262144 bits.
- A type the compiler constructs is at most 65536 characters long.
- A program creates at most 16384 generic function instantiations, and a generic
  type argument is at most 256 constructors deep and 4096 characters long.
- Compiling a program costs at most 1000000 work units, and replaying deferred
  statements and cleanup at most a separate 48000000 units.

### Fixed

- The checker rejects out-of-range integer literals, `==` and `!=` on `void`
  values, pointer-to-array types, array types nested where the C backend has no
  form for them, and type arguments on non-generic builtins.
- Invalid UTF-8 source, over-deep nesting, oversized literals and array-repeat
  expansion report located diagnostics. Every parser diagnostic has a column.
- Generics instantiated from `test` blocks are checked.
- Generic-argument lookahead is linear, and branch snapshots are no longer
  quadratic.
- The fuzzer covers these crash and hang classes.

### Self-hosting (incomplete)

**Mort does not compile itself.** `selfhost/` holds a compiler being written in
Mort, tested by comparing its output with the Python compiler's. See
`selfhost/PLAN.md`. Merged so far:

- The lexer, with a differential test against `mort/lexer.py`.
- `support.mx` (checked allocation, buffers, vectors, a hash map, a string
  interner, located diagnostics) and `bignum.mx` (arbitrary-precision integers).
- The parser and flat AST, with a differential test against `mort/parser.py`.
- A seeded generator of valid programs for those tests.

The checker, the C code generator and the driver are not merged, so there is no
stage-1 compiler and no bootstrap yet.

## 0.55.0 to 0.137.0 - 2026-08-03 to 2026-11-08

Almost entirely standard-library additions, one version per addition, each
continuing to implement the unchanged Mort 0.41 language contract. Grouped by
module; each entry gives its version and date. The newest version tag is
`v0.45.0`, so these versions, like 0.46.0 to 0.54.0, exist only as commits.

### `std.algorithm`

- 0.95.0 (2026-09-22): std.algorithm.min and max, the slice-level counterparts to std.math.min/max.
- 0.99.0 (2026-09-26): std.algorithm.dedup, the slice-level counterpart to std.vec.dedup.
- 0.104.0 (2026-10-02): std.algorithm.find_by, predicate-based slice search.
- 0.124.0 (2026-10-26): std.algorithm.any/all, predicate checks over a slice.
- 0.129.0 (2026-10-31): std.algorithm.count and std.vec.count predicate tallies.
- 0.131.0 (2026-11-02): std.algorithm.min_by/max_by, comparator-taking extrema over a slice.
- 0.137.0 (2026-11-08): std.algorithm.rotate_left/rotate_right, bare-slice counterparts to std.vec's rotate.

### `std.ascii`

- 0.102.0 (2026-09-30): std.ascii.is_alnum, is_upper, is_lower.
- 0.103.0 (2026-10-01): std.ascii.is_print and is_punct.
- 0.108.0 (2026-10-06): std.ascii.is_hex_digit, a 0-9/a-f/A-F predicate.

### `std.bytes`

- 0.78.0 (2026-09-01): std.bytes.to_hex/from_hex, arbitrary-length hex encode/decode.
- 0.127.0 (2026-10-29): std.bytes.compare, lexicographic ordering of two byte buffers.
- 0.128.0 (2026-10-30): std.bytes.xor, byte-wise XOR of two buffers into a destination.
- 0.132.0 (2026-11-03): std.bytes.and/or, bitwise AND/OR counterparts to xor.
- 0.136.0 (2026-11-07): std.bytes.not, bitwise-complement counterpart to xor/and/or.

### `std.map`

- 0.58.0 (2026-08-09): std.map.remove, filling the missing key-deletion gap.
- 0.65.0 (2026-08-16): std.map.key_at / value_at for indexed entry enumeration.
- 0.68.0 (2026-08-19): std.map.find_by, predicate-based entry search.
- 0.93.0 (2026-09-19): std.map.get_ref, a pointer to the live value slot.
- 0.110.0 (2026-10-08): std.map.keys and std.map.values.

### `std.math`

- 0.81.0 (2026-09-04): std.math.lcm, the least-common-multiple counterpart to gcd.
- 0.84.0 (2026-09-07): std.math.sign and is_power_of_two, small generic numeric predicates.
- 0.126.0 (2026-10-28): std.math.is_even/is_odd, parity checks over a generic numeric T.

### `std.option`

- 0.85.0 (2026-09-09): std.option.is_some/is_none/unwrap_or.
- 0.87.0 (2026-09-11): std.option.map, applying a function to a Some payload.
- 0.91.0 (2026-09-17): std.option.unwrap_or_else and std.result.unwrap_or_else.
- 0.106.0 (2026-10-04): std.option.and_then, the flat-map counterpart to map.
- 0.113.0 (2026-10-12): std.option.filter, keep Some only if a predicate holds.
- 0.117.0 (2026-10-16): std.option.or_else, None-side counterpart to and_then.
- 0.118.0 (2026-10-17): std.option.and, eager counterpart to and_then.
- 0.119.0 (2026-10-18): std.option.or and std.result.and/or, eager and/or-else counterparts.
- 0.120.0 (2026-10-19): std.option.take and std.option.replace, pointer-based mutators.

### `std.owned_string`

- 0.122.0 (2026-10-21): std.owned_string.from_slice, build an owned String from any slice.
- 0.123.0 (2026-10-22): std.owned_string.append, grow an owned String in place.

### `std.result`

- 0.86.0 (2026-09-10): std.result.is_ok/is_err/unwrap_or.
- 0.88.0 (2026-09-13): std.result.map, applying a function to an Ok payload.
- 0.89.0 (2026-09-14): std.result.map_err, the error-side counterpart to map.
- 0.90.0 (2026-09-15): std.result.ok and std.option.ok_or, converting between the two enums.
- 0.107.0 (2026-10-05): std.result.and_then, the flat-map counterpart to map.
- 0.115.0 (2026-10-14): std.result.or_else, error-side recovery counterpart to and_then.
- 0.116.0 (2026-10-15): std.result.err, error-side counterpart to ok.
- 0.121.0 (2026-10-20): std.result.replace, pointer-based mutator.

### `std.sort`

- 0.100.0 (2026-09-27): examples/sort_struct.mx, demonstrating std.sort on a struct.

### `std.strings`

- 0.55.0 (2026-08-03): std.strings.replace for owned-buffer find/replace.
- 0.56.0 (2026-08-07): std.strings.join, the inverse of split.
- 0.57.0 (2026-08-08): std.strings.contains, a bool wrapper over index_of.
- 0.61.0 (2026-08-12): std.strings.trim_start / trim_end, one-sided counterparts to trim.
- 0.63.0 (2026-08-14): std.strings.repeat, text repeated count times.
- 0.64.0 (2026-08-15): std.strings.last_index_of, the reverse counterpart to index_of.
- 0.66.0 (2026-08-17): std.strings.split_once, borrowed-view split at first separator.
- 0.67.0 (2026-08-18): std.strings.rsplit_once, the reverse counterpart to split_once.
- 0.71.0 (2026-08-23): std.strings.count, non-overlapping occurrence counting.
- 0.72.0 (2026-08-25): std.strings.from_u64 / from_i64, decimal int-to-string.
- 0.74.0 (2026-08-28): std.strings.to_hex, decimal-to-hex counterpart.
- 0.75.0 (2026-08-29): std.strings.parse_hex, to_hex's inverse.
- 0.83.0 (2026-09-06): std.strings.is_empty, matching Vec/Map's is_empty predicate.
- 0.92.0 (2026-09-18): std.strings.replacen, a count-limited variant of replace.
- 0.96.0 (2026-09-23): std.strings.split_whitespace, splitting on whitespace runs.
- 0.98.0 (2026-09-25): std.strings.lines, splitting text into lines on \n/\r\n.
- 0.101.0 (2026-09-29): std.strings.to_upper and to_lower.
- 0.105.0 (2026-10-03): std.strings.splitn, the count-limited counterpart to split.
- 0.111.0 (2026-10-10): std.strings.strip_prefix and strip_suffix.
- 0.112.0 (2026-10-11): std.strings.pad_start and pad_end.

### `std.vec`

- 0.59.0 (2026-08-10): std.vec.remove, the arbitrary-index counterpart to std.map.remove.
- 0.60.0 (2026-08-11): std.vec.insert, the arbitrary-position counterpart to Vec.remove.
- 0.62.0 (2026-08-13): std.vec.is_empty / std.map.is_empty convenience wrappers.
- 0.69.0 (2026-08-20): std.vec.index_of / contains, membership check for Vec.
- 0.70.0 (2026-08-21): std.vec.first / std.vec.last, peek at the ends without hand-rolled length math.
- 0.73.0 (2026-08-26): std.vec.swap, in-place two-element exchange.
- 0.76.0 (2026-08-30): std.vec.reverse, in-place reversal built on swap.
- 0.77.0 (2026-08-31): std.vec.slice_reverse, reverse for bare []T slices.
- 0.79.0 (2026-09-02): std.vec.rotate_left/rotate_right, in-place rotation via three reversals.
- 0.80.0 (2026-09-03): std.vec.get_ref, a non-aliasing pointer accessor for resource elements.
- 0.82.0 (2026-09-05): std.vec.dedup, in-place consecutive-duplicate collapsing.
- 0.94.0 (2026-09-21): std.vec.truncate, shrinking a Vec down to at most N elements.
- 0.97.0 (2026-09-24): std.vec.extend, bulk-appending a slice onto a Vec.
- 0.109.0 (2026-10-07): std.vec.clone, a value-copying duplicate of a Vec.
- 0.114.0 (2026-10-13): std.vec.swap_remove, O(1) remove-and-swap-last counterpart to remove.
- 0.125.0 (2026-10-27): std.vec.any/all, predicate checks over a Vec.
- 0.130.0 (2026-11-01): std.vec.find_by, predicate search wrapper over algorithm.find_by.
- 0.133.0 (2026-11-04): std.vec.sort_by, comparator-sort wrapper over std.sort.sort.
- 0.134.0 (2026-11-05): std.vec.min_by/max_by, comparator-extrema wrappers over std.algorithm.
- 0.135.0 (2026-11-06): std.vec.min/max/sort, plain <-comparable wrappers over std.algorithm.

### Fixes

- 0.110.1 (2026-10-08): Fix freestanding builds pulling in the hosted runtime.

## 0.54.0 - 2026-08-01

Standard library addition. The compiler version moves to 0.54 while
continuing to implement the unchanged Mort 0.41 language contract.

### Added

- `std.strings`: `split(text, separator)` splits a `[]const u8` on every
  occurrence of `separator`, returning a `Vec<[]const u8>` of views borrowed
  from `text` (no copies). Matches Python's `str.split(sep)` semantics:
  consecutive or edge separators produce empty elements; an empty
  `separator` returns the whole text as a single element.

## 0.53.0 - 2026-07-28

Standard library addition. The compiler version moves to 0.53 while
continuing to implement the unchanged Mort 0.41 language contract.

### Added

- `std.json`: `object_get_path` walks nested objects by a dot-separated key
  path (e.g. `"a.b.c"`), returning `None` as soon as a segment is missing or
  an intermediate value is not an object.

## 0.50.0 - 2026-07-25

New standard library module. The compiler version moves to 0.50 while
continuing to implement the unchanged Mort 0.41 language contract.

### Added

- `std.strings`: slice helpers over `[]const u8` — `equal`, `starts_with`,
  `ends_with`, `index_of` (substring search), `trim` (ASCII whitespace), and
  `parse_i64` / `parse_u64` (returning `Option<i64>` / `Option<u64>`).

## 0.49.0 - 2026-07-25

Generic containers of resources. The compiler version moves to 0.49 while
continuing to implement the unchanged Mort 0.41 language contract.

### Fixed

- `Vec<T>` and `Map<K, V>` can now hold resource-owning element types. Two
  causes are addressed:
  - The ownership checker rejected every assignment whose target type was a
    resource, including writes through a raw pointer (`ptr[i] = x`, `*ptr = x`).
    Such storage is manually managed and is not a binding the checker tracks, so
    it no longer reports "cannot overwrite a resource binding" there. Writes to
    real bindings (`x = other`) are still rejected, and const-pointer writes are
    still rejected separately.
  - `move` may now be applied to a copyable (non-resource) value, where it is a
    plain copy that does not consume the source. This lets generic code store an
    element with `move item` regardless of whether the element is a resource.
- `std.vec` (`push`, `set`) and `std.map` (`insert`) now move elements into
  their backing slots, so a resource element's ownership transfers into the
  container instead of being dropped when the call returns. Draining with `pop`
  moves each element back out; a new end-to-end test confirms every element of a
  `Vec` of resources is destroyed exactly once with no double free or leak.

## 0.48.0 - 2026-07-25

Ownership-checker fix. The compiler version moves to 0.48 while continuing to
implement the unchanged Mort 0.41 language contract.

### Fixed

- The move checker was flow-insensitive when merging branches: a branch that
  destroyed (or moved) a resource and then `return`ed still marked that resource
  as moved on the path where the branch was not taken. The natural cleanup
  pattern `if !ok { destroy(&r); return; } use(&r);` was wrongly rejected with
  "use of moved resource". Branches (and `match` arms) that always return are
  now excluded from the post-branch move state, since control never falls
  through them. Soundness is unaffected: a branch that moves without diverging
  is still merged, so real use-after-move is still reported.

## 0.47.0 - 2026-07-25

Standard-library release. The compiler version moves to 0.47 while continuing
to implement the unchanged Mort 0.41 language contract.

### Standard library

- New `std.json` module: a from-scratch JSON parser and serializer built on a
  flat arena (one `Vec<Node>`, a linked child table, and a single byte pool)
  so nothing nested owns a resource and the whole document stays inside Mort's
  move rules. Parses objects, arrays, strings with full escape handling
  (including `\uXXXX` and surrogate pairs to UTF-8), numbers, booleans, and
  null; rejects malformed input with an error position. Index-based accessors
  (`root`, `kind`, `is_*`, `as_bool`/`as_number`/`as_string`, `array_len`/
  `array_get`, `object_len`/`object_key`/`object_value_at`/`object_get`) read
  without copying. `stringify` serializes back to a compact owned byte buffer;
  structure, strings, booleans, null, and integer-valued numbers round-trip
  exactly, with non-integer numbers emitted at a bounded nine-digit precision.
- End-to-end test parses nested objects/arrays, checks accessors, and confirms
  malformed input is rejected.

## 0.46.0 - 2026-07-25

Mort's developer-insight release. The compiler version moves to 0.46 while
continuing to implement the unchanged Mort 0.41 language contract.

### Developer tooling

- `mortc coverage` performs compiler-native statement instrumentation, runs all
  project tests, merges counters, and writes deterministic per-file JSON.
- `mortc bench` records bounded warmups and iterations with raw nanosecond
  samples and min, median, mean, p95, and max summaries.
- `mortc profile` writes a standard `pstats` profile for the complete
  compiler/build pipeline and prints the hottest cumulative-time entries.
- `mortc doc` generates deterministic Markdown or machine-readable JSON for
  modules, functions and visibility, generics, structs/resources, enums,
  aliases, fields, variants, and source locations.
- End-to-end tests execute and validate every new command and artifact.

## 0.45.0 - 2026-07-25

Mort's cross-release compatibility release. The compiler version moves to 0.45
while continuing to implement the unchanged Mort 0.41 language contract.

### Compatibility enforcement

- CI discovers every tagged release on the active language-version line and
  runs each release's original conformance suite unchanged against the current
  compiler and standard library.
- The gate requires multiple release generations, uses validated archive
  extraction, and blocks both ordinary changes and tagged releases on any
  historical source or behavior regression.
- A machine-checked deprecation ledger requires a named replacement and at
  least one full minor language-version transition before removal.

## 0.44.0 - 2026-07-25

Mort's authenticated-package release. The compiler version moves to 0.44 while
continuing to implement the unchanged Mort 0.41 language contract.

### Registry security

- Registry format 2 requires Ed25519 signatures for publisher records and
  operator checkpoints. Trust roots are pinned outside downloaded metadata.
- Signed records bind the package/version identity, immutable Git commit,
  source URL/ref, publication timestamp, and complete portable content hash.
- Publisher additions, releases, and key revocations are recorded in a
  hash-chained log; clients reject rollback or rewriting of a cached prefix.
- Revoked publishers are excluded from dependency solving. Both Git checkouts
  and offline mirrors must match the signed package-content hash.
- An offline administration tool handles publisher enrollment, immutable
  publication, revocation, checkpoint signing, and independent verification.
- CI independently verifies the canonical empty registry and adversarial tests
  cover tampered records, log entries, checkpoints, untrusted roots, rollback,
  history rewriting, and revocation.

## 0.43.0 - 2026-07-25

Mort's reproducible-release release. The compiler version moves to 0.43 while
continuing to implement the unchanged Mort 0.41 language contract.

### Reproducible builds

- Release builds derive `SOURCE_DATE_EPOCH` from the tagged Git commit and fix
  Python's hash seed and timezone.
- The release frontend and setuptools backend are pinned to the non-yanked
  versions `build==1.5.0` and `setuptools==83.0.0`.
- Source archives receive canonical gzip and tar timestamps, numeric ownership,
  owner/group names, and PAX metadata. Wheels already honor the fixed source
  epoch.
- An automated gate exports the same revision into two clean source trees,
  builds both independently, and requires byte-identical SHA-256 hashes for the
  wheel and source distribution. CI and tagged releases both run the proof.

## 0.42.0 - 2026-07-25

Mort's cross-backend compiler-correctness release. The compiler version moves
to 0.42 while continuing to implement the unchanged Mort 0.41 language
contract.

### Compiler validation

- A black-box differential gate compiles and executes exact-output programs
  with GCC, Clang, and Zig cc at `-O0`, `-O1`, `-O2`, `-O3`, and `-Os`.
- Three independently structured metamorphic variants cover range and while
  loops, recursion and iteration, closed-form substitution, declaration
  reordering, short-circuit side effects, and specified signed wrapping.
- Every execution is checked against a fixed language-level oracle before the
  results are compared, preventing unanimous backend miscompilation from being
  accepted as correct.
- The 45-combination matrix is a required Linux CI job and a release gate.

## 0.41.0 - 2026-07-25

Mort's verified TLS and bounded HTTPS release.

### Added

- `std.tls` provides blocking, move-only TLS 1.2/1.3 client streams with
  partial/full sends, bounded receives, protocol-version reporting, close
  notification, and deterministic native cleanup.
- Every successful handshake requires a trusted certificate chain, valid
  certificate time, and matching DNS name or IP address. There is no insecure
  verification switch.
- `tls.connect` uses a pinned Mozilla root set; `connect_with_ca` supports
  private services with explicit PEM roots while retaining all identity and
  chain checks.
- `std.https` applies Mort's injection-safe, caller-bounded HTTP/1.1 subset
  over verified TLS, including default-root and private-CA `GET` helpers.
- `examples/https_client.mx` demonstrates an authenticated HTTPS request.

### Security, portability, and validation

- The compiler ships the official Mbed TLS 3.6.6 LTS archive and certifi
  2026.6.17 root set, verifies their published SHA-256 digests before
  extraction, and compiles a native backend only when TLS is used.
- TLS builds are cached by compiler identity, platform, architecture,
  sanitizer flags, integration-source hash, TLS-source hash, and CA hash.
- Zig compilation uses writable Mort-owned global/local caches, honors
  `MORT_CACHE_DIR`, and falls back to the operating-system temporary directory
  when the conventional user cache is unavailable.
- Selective archive extraction rejects links and unsafe paths. Upstream
  licenses and version/checksum provenance are shipped with every package.
- A local certificate fixture proves successful TLS and bounded HTTPS,
  hostname mismatch rejection, and untrusted-chain rejection without relying
  on public network services. The same path runs on Windows, Linux, macOS,
  AddressSanitizer, and UndefinedBehaviorSanitizer.
- The full regression suite contains 330 tests, and all 28 Mort 0.41
  conformance cases pass through the public compiler CLI.

## 0.40.0 - 2026-07-23

Mort's secure-random and bounded WebSocket release.

### Added

- `std.crypto.random_fill` and `random_u64` use the operating-system CSPRNG:
  system-preferred CNG on Windows and the OS random device on Linux/macOS.
- The compiler links Windows CNG automatically only when secure randomness is
  used.
- `std.websocket` implements RFC 6455 client/server HTTP upgrades, the
  published SHA-1/Base64 accept transform, random client keys and masks, and
  text, binary, close, ping, and pong frames.
- Frame encoding and parsing cover minimal 7-bit, 16-bit, and 64-bit lengths,
  caller-bounded receives, direction-specific masking, UTF-8 validation, and
  close status validation.

### Security and validation

- WebSocket handshakes reject missing/duplicate required headers, invalid
  Base64 keys, wrong version/status/accept values, and injected client fields.
- Frames reject reserved bits/opcodes, fragmentation, wrong masking direction,
  nonminimal lengths, payloads above capacity, malformed UTF-8, and invalid
  close payloads.
- The loopback exercises the RFC accept vector, 126-byte and 65,536-byte
  binary frames, masked/unmasked text, and a normal close handshake.
- CSPRNG and WebSocket run under strict generated-C warnings, the
  Windows/Linux/macOS conformance matrix, AddressSanitizer, and
  UndefinedBehaviorSanitizer.
- The full regression suite contains 321 tests, and all 27 Mort 0.40
  conformance cases pass through the public compiler CLI.

## 0.39.0 - 2026-07-23

Mort's bounded HTTP/1.1 foundation release.

### Added

- First-party `std.http` serializes injection-checked requests and responses
  with exact decimal `Content-Length` framing and `Connection: close`.
- A bounded reader consumes one caller-buffered message and exposes header-end,
  content-length, framed-length, and response-status parsing.
- `http.get` performs a complete cleartext HTTP GET over `std.net` without
  conflating HTTP with TLS.
- `examples/http_loopback.mx` runs an in-process Mort HTTP server and client
  through DNS and TCP.

### Security and validation

- Duplicate or malformed `Content-Length`, any `Transfer-Encoding`, framing
  overflow, premature EOF, caller-buffer exhaustion, and CRLF injection inputs
  are rejected.
- The normative Mort 0.39 contract explicitly limits the API to one bounded
  message per close-delimited connection and forbids treating it as chunked,
  persistent, pipelined, HTTP/2, or HTTPS support.
- HTTP loopback runs under strict generated-C warnings, the
  Windows/Linux/macOS conformance matrix, AddressSanitizer, and
  UndefinedBehaviorSanitizer.
- The full regression suite contains 318 tests, and all 25 Mort 0.39
  conformance cases pass through the public compiler CLI.

## 0.38.0 - 2026-07-23

Mort's asynchronous-I/O and structured-task foundation release.

### Added

- Cross-platform nonblocking socket mode uses `ioctlsocket` on Windows and
  `fcntl` on Linux/macOS.
- Readiness waits use `WSAPoll` or POSIX `poll` with immediate, finite, and
  indefinite timeouts plus stable readable/writable/error result bits.
- Thread-local would-block detection lets event loops distinguish retryable
  nonblocking results from other socket failures.
- Move-only `std.task.TaskGroup` resources own child tasks, provide
  sequentially consistent cooperative cancellation, aggregate joined results,
  reject post-cancellation spawns, and cancel/join automatically at scope exit.

### Semantics and validation

- The Mort 0.38 specification defines advisory readiness, timeout behavior,
  retry requirements, cancellation progress points, task context lifetimes,
  owner-thread restrictions, join ordering, and structured scope exit.
- `examples/async_io.mx` proves a zero-time timeout, would-block detection, a
  bounded wakeup, and non-busy stream completion.
- `examples/tasks.mx` proves explicit cancellation/join and automatic
  cancel-and-join of an otherwise nonterminating child during lexical cleanup.
- Generated programs using only part of the concurrency runtime compile cleanly
  under strict unused-function warnings.
- CI runs asynchronous I/O under AddressSanitizer/UndefinedBehaviorSanitizer
  and structured tasks under ThreadSanitizer/UndefinedBehaviorSanitizer.
- The full regression suite contains 317 tests, and all 24 Mort 0.38
  conformance cases pass through the public compiler CLI.

## 0.37.0 - 2026-07-23

Mort's portable UDP datagram release.

### Added

- Cross-platform UDP bind and connected-peer sockets use WinSock on Windows and
  POSIX sockets on Linux/macOS.
- `std.net.send_to` resolves IPv4/IPv6 destinations and sends one atomic
  datagram, while `receive_from` returns the numeric source host and port.
- UDP sockets reuse the move-only `std.net.Socket` ownership model and close
  automatically on every lexical exit.
- `examples/udp_loopback.mx` performs a DNS-resolved ping/pong exchange through
  operating-system-assigned ephemeral ports without public network access.

### Semantics and validation

- The Mort 0.37 specification defines datagram boundaries, zero-length
  datagrams, truncation, source-address buffers, connected UDP limitations,
  and UDP's delivery and ordering boundaries.
- Windows `WSAEMSGSIZE` and POSIX receive truncation are normalized: the bytes
  that fit are returned and the remainder of that datagram is discarded.
- The UDP conformance case deliberately truncates an eight-byte datagram into a
  four-byte buffer before replying to the reported source endpoint.
- UDP loopback runs under strict generated-C warnings, the Windows/Linux/macOS
  conformance matrix, AddressSanitizer, and UndefinedBehaviorSanitizer.
- The full regression suite contains 315 tests, and all 22 Mort 0.37
  conformance cases pass through the public compiler CLI.

## 0.36.0 - 2026-07-23

Mort's portable TCP and DNS foundation release.

### Added

- Cross-platform hosted TCP connects, listeners, accepts, shutdown, partial
  sends and receives, and bound-port queries use WinSock on Windows and the
  POSIX socket API on Linux/macOS.
- Host-name resolution uses the native resolver and tries eligible IPv4 and
  IPv6 addresses.
- Move-only `std.net.Socket` resources close native handles automatically and
  provide `send_all` and `receive_exact` loops for complete byte transfers.
- `examples/tcp_loopback.mx` runs a threaded ping/pong exchange through
  `"localhost"` and an operating-system-assigned ephemeral port.

### Semantics and validation

- The Mort 0.36 specification defines socket ownership, TCP stream behavior,
  partial transfers, end-of-stream, shutdown, buffer lifetimes, concurrency,
  error signaling, and host-environment portability boundaries.
- A black-box TCP/DNS loopback case runs in the conformance matrix on Windows,
  Linux, and ARM64 macOS without requiring public internet access.
- Networking-only generated C compiles under strict C11 warnings without an
  accidental pthread dependency, and Windows builds link WinSock
  automatically.
- Representative TCP code runs under AddressSanitizer and
  UndefinedBehaviorSanitizer in CI.
- The full regression suite contains 314 tests, and all 21 Mort 0.36
  conformance cases pass through the public compiler CLI.

## 0.35.0 - 2026-07-23

Mort's hosted-concurrency release.

### Added

- Cross-platform hosted threads lower to Win32 threads on Windows and pthreads
  on Linux/macOS, with `fn(*void)->i64` callbacks, opaque handles, joins, result
  transfer, and millisecond sleeps.
- Cross-platform mutexes provide creation, locking, unlocking, destruction, and
  synchronization across the same supported desktop targets.
- Heap-backed `AtomicI64` values provide sequentially consistent load, store,
  exchange, fetch-add, fetch-sub, and compare-exchange operations.
- Move-only `std.thread.Thread`, `std.mutex.Mutex`, and
  `std.atomic.AtomicI64` wrappers automatically release or join live handles.
- ThreadSanitizer is a first-class hosted sanitizer and may be combined with
  UndefinedBehaviorSanitizer.

### Semantics and validation

- The Mort 0.35 specification defines spawn/join and mutex happens-before
  edges, sequentially consistent atomic ordering, context lifetimes, ownership
  of handles, and the language's data-race rule.
- The conformance suite runs a synchronized two-thread workload using both a
  mutex and atomics.
- CI executes the safe workload under ThreadSanitizer and requires the same
  sanitizer to reject an intentional non-atomic race fixture.
- The full regression suite contains 311 tests, and all 20 Mort 0.35
  conformance cases pass through the public compiler CLI.

## 0.34.0 - 2026-07-23

Mort's specified-semantics and conformance release.

### Language semantics

- Fixed-width runtime integer addition, subtraction, multiplication, unary
  negation, and bitwise operations now use explicit N-bit wrapping without
  relying on C signed-overflow behavior.
- Runtime shifts have defined behavior at and beyond the operand width, signed
  right shift is explicitly arithmetic, and negative runtime counts fail with
  a source-line diagnostic.
- Runtime division and remainder by zero fail through a controlled Mort
  diagnostic. Signed `MIN / -1` wraps to `MIN`, and `MIN % -1` is zero.
- Compound assignments preserve one evaluation of their target location while
  using the same defined arithmetic as their binary operators.
- Indexing a computed slice now captures the slice once instead of potentially
  evaluating the object expression once for data and again for length.

### Specification and compatibility

- `docs/language-specification.md` is the first versioned normative Mort
  language specification, covering grammar, typing, integer semantics,
  control flow, modules, ownership, cleanup, error propagation, profiles, and
  portability boundaries.
- `conformance/` provides a black-box, implementation-independent manifest and
  runner with hosted execution, modules, generics, aggregates, ownership,
  integer edge cases, and required rejection diagnostics.
- `mortc --language-version` identifies the implemented language contract
  independently of the compiler command's ordinary version output.
- CI runs the conformance suite on Linux, Windows, and macOS, and release gates
  require it before artifacts can be published.

### Validation

- Generated integer edge cases execute cleanly under UndefinedBehaviorSanitizer.
- The full regression suite contains 309 tests, and all 19 initial conformance
  cases pass through the public compiler CLI.

## 0.33.0 — 2026-07-23

Mort's reliability and supply-chain hardening release.

### Fixed

- Cached Git dependencies now fetch updates and detach at the exact branch,
  tag, semantic-version selection, or commit requested by the manifest.
- Relative filesystem Git URLs resolve from the declaring project, making
  manifests independent of the caller's working directory.
- Direct Git wildcard ranges such as `1.x` now use semantic-version selection
  instead of being mistaken for literal branch names.
- Excessively nested source now produces a controlled Mort diagnostic instead
  of exposing a Python `RecursionError`.
- Freestanding compilation now always selects Zig's explicit x86-64
  bare-metal target, instead of accidentally feeding x86 assembly to an ARM64
  host compiler on macOS.
- Hosted builds can enable address, undefined-behavior, and leak sanitizers
  from either CLI flags or `mort.toml`.
- `MORT_CC` and `CC` can select a multi-argument C backend command explicitly.
- The documented Python requirement now agrees with package metadata:
  Python 3.10 or newer.
- Distribution metadata uses the current SPDX license format and removes the
  setuptools license configuration scheduled for removal in 2027.

### Security and reproducibility

- Existing dependency caches must have the requested origin and a clean
  worktree before Mort updates them.
- Registry indexes are limited to 4 MiB and every package, version, source, and
  ref is schema-validated before resolution.
- Registry package traversal is rejected before any index or mirror access.
- Dependency sources and entry points are confined to their package root,
  including after symlink resolution.
- Portable content hashing records symlink targets rather than following them.
- Registry cache replacement is atomic, preventing interrupted writes from
  leaving a partial trusted index.
- Manifest edits and lockfile refreshes also use durable sibling writes and
  atomic replacement; package hashing records directory symlinks as well as
  file symlinks without following either.
- Source distributions now include the changelog, production documentation,
  examples, fuzz harness and corpus, registry metadata, and kernel sources.

### Validation

- CI now gates Linux, Windows, and macOS with native tests, 10,000 adversarial
  fuzz cases per job, Python static analysis, toolchain diagnostics, and a
  MORT OS kernel build.
- Dedicated Python 3.13 and 3.14 jobs cover current Python releases, and a
  Clang job executes representative generated programs under ASan and UBSan.
- GitHub workflow dependencies use their current Node.js 24 action generations,
  removing the platform's Node.js 20 deprecation warnings.
- The complete matrix is green on Linux, Windows, and ARM64 macOS; the macOS
  job also proves the x86-64 freestanding cross-target rather than a host
  architecture object.
- Release publication now requires the full test suite, 20,000 fuzz cases,
  a kernel build, an exact tag/version match, and an installed-wheel smoke test.
- Structured valid-program fuzzing now spans control flow, arrays, structs,
  payload enums, tuples, and generics, with a permanent adversarial corpus.
- A coverage-guided Atheris/libFuzzer target runs a seeded corpus on relevant
  pull requests and for 15 minutes every day, preserving minimized crashes.
- 302 compiler, ownership, package-security, registry, native, LSP, and kernel
  tests pass.

## 0.32.0 — 2026-07-23

Mort's ownership-completion release.

### Added

- `match move value` consumes an owning tagged enum and transfers its active
  resource payload into typed arm bindings.
- Owning match bindings participate in the normal move checker and automatic
  lexical destruction, including returns and propagated errors.
- Resource bindings created inside a loop may move once per iteration.
- Moving a resource declared outside a repeating loop remains a compile-time
  error because later iterations could otherwise reuse moved storage.

### Safety

- Owning enum matches must enumerate every variant so the active payload always
  has a defined destruction path.
- An owning payload cannot be discarded with `_`; it must be bound, moved, or
  destroyed.
- Consuming an owning enum disables the source live flag, while the selected
  arm owns exactly one extracted payload, preventing leaks and double drops.

### Validation

- Owning enum extraction and per-iteration loop moves compile under
  `-Wall -Werror` and execute with exactly-once destruction.
- 288 compiler, ownership, SemVer, registry, package, native, LSP, and kernel
  tests pass.

## 0.31.0 — 2026-07-23

Mort's semantic package ecosystem release.

### Added

- Strict SemVer 2.0 parsing and precedence, including prerelease identifiers.
- Exact, caret, tilde, wildcard, and comma-separated comparison constraints.
- Git dependencies can use constraints such as `git+URL#^1.2.0`; Mort selects
  the highest compatible semantic-version tag and clones that exact release.
- Registry dependencies use `registry:package@constraint` or
  `mortc add package --registry CONSTRAINT`.
- A canonical versioned public registry index lives in `registry/index.json`.
- Project-specific `registry.url` and `registry.mirrors` settings plus
  `MORT_REGISTRY_URL` and `MORT_MIRRORS` machine-wide overrides.
- `mortc fetch --offline` resolves from cached indexes/checkouts and local
  mirrors without network access.

### Reproducibility and safety

- Selected Git tags must agree with the dependency's declared package version.
- Registry records must resolve to a package declaring the selected version.
- Lockfile format 3 records semantic versions, immutable Git revisions,
  manifest hashes, and complete portable content hashes.
- Online registry failures fall back to a cached index; offline mode emits
  targeted diagnostics when required data is unavailable.

### Validation

- 286 compiler, SemVer, registry, mirror, ownership, package, native, LSP, and
  kernel tests pass, including real local Git tags and an air-gapped mirror.

## 0.30.0 — 2026-07-23

Mort's compositional ownership release.

### Added

- Ordinary structs, heterogeneous tuples, payload enums, and fixed arrays now
  become move-only automatically when they contain resource-owning values.
- The compiler synthesizes typed recursive drop helpers for every concrete
  owning composite.
- Composite destruction walks fields and array elements in reverse order and
  switches on enum tags to destroy only the active payload.
- Move dataflow now treats exhaustive match arms as mutually exclusive, just
  like `if`/`else` branches.

### Safety

- Resource-containing composites require explicit `move` for transfers and
  receive the same use-after-move, double-move, overwrite, loop, and global
  diagnostics as direct resource structs.
- Generated drop helpers are dependency declared and call user destructors
  through checked prototypes.
- Matching an owning enum by value is rejected until ownership-preserving
  payload extraction is explicit, preventing hidden tagged-union copies.

### Validation

- Nested direct resources inside structs, tuples, tagged enum payloads, and
  arrays compile under `-Wall -Werror` and destroy in exact reverse order.
- Exclusive `if` and exhaustive-match branches can each transfer the same
  incoming resource exactly once.
- 271 compiler, ownership, enum, tuple, error-propagation, callback, packaging,
  CLI, native, LSP, package, and kernel tests pass.

## 0.29.0 — 2026-07-23

Mort's first ownership and automatic-resource-management release.

### Added

- `resource struct` declarations for move-only values that own external state.
- Every resource declaration requires exactly one type-correct
  `destroy(*Resource) -> void` function, including matching generic parameters.
- Explicit `move value` transfers ownership into bindings, parameters, and
  returns.
- Compile-time diagnostics for implicit resource copies, use-after-move,
  double moves, moves from loops, whole-resource overwrites, and resource
  globals.
- Conservative branch dataflow permits one move in each mutually exclusive
  `if`/`else` branch while preventing later access to a possibly moved value.

### Automatic destruction

- Resource locals and by-value parameters register typed cleanup immediately
  after initialization.
- Cleanups run in reverse declaration order on normal scope exits, returns,
  `break`, `continue`, and propagated errors using the existing lexical cleanup
  machinery.
- Per-binding live flags ensure moved sources never destroy transferred values.
- Existing explicit immediate or deferred destructor calls deactivate or
  replace implicit cleanup, preserving source compatibility.
- `Vec<T>`, `Map<Key, Value>`, and owned strings are now standard resource
  types; ordinary collection code no longer needs manual `defer destroy`.

### Validation

- Moves across bindings, calls, parameters, returns, and exclusive branches;
  nested cleanup; const resources; early returns; legacy deferred destruction;
  and generic standard resources compile under `-Wall -Werror`.
- 269 compiler, ownership, enum, tuple, error-propagation, callback, packaging,
  CLI, native, LSP, package, and kernel tests pass.

## 0.28.0 — 2026-07-23

Mort's rich enum-payload release.

### Added

- Enum variants can declare multiple payload fields, such as
  `Point(i64, i64)` or generic `Pair(Left, Right)` variants.
- Constructors accept and independently type-check each declared payload.
- Match patterns destructure every payload into typed lexical bindings, such as
  `Shape.Point(x, y)`.
- `_` ignores selected payload fields without creating unused bindings.
- A single tuple payload remains distinct from multiple payloads:
  `Wrapped((i64, bool))` matches one tuple binding, preserving structural APIs.

### Semantics and code generation

- Multi-field variants reuse Mort's deterministic native tuple layouts inside
  tagged-union storage without adding allocation or pointer indirection.
- Destructuring reads each payload field exactly once from the cached match
  subject and preserves exhaustive-match checking.
- Arity, individual field types, non-binding patterns, and duplicate binding
  names receive targeted compile-time diagnostics.
- Existing single-payload `Option`/`Result`, generic enums, `try` propagation,
  and imported standard-library enums remain source- and ABI-compatible.

### Validation

- Multi-field, generic, ignored-field, single-tuple, string, boolean, and
  exhaustive variants compile under `-Wall -Werror` and execute natively.
- 260 compiler, enum, tuple, error-propagation, callback, packaging, CLI,
  native, LSP, package, and kernel tests pass.

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
