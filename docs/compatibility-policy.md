# Mort compatibility and version-support policy

Mort currently has alpha status, but version changes follow an explicit
contract rather than silently changing valid programs.

## Compiler and language versions

The compiler package and language contract have separate semantic versions.
`mortc --version` identifies the implementation release;
`mortc --language-version` identifies the normative language specification it
implements. A compiler minor release may retain the prior language version
when it adds tooling, validation, or implementation improvements without
changing source semantics.

Within one language-version line:

- patch compiler releases must remain source compatible with conforming code;
- bug fixes may reject code that was already invalid under the specification;
- generated C, diagnostic wording, optimization choices, and private runtime
  symbols are implementation details unless separately documented;
- additions to the standard library cannot change the behavior of existing
  valid calls.

## Breaking changes and deprecations

A deliberate source-breaking change requires a language-version increment, a
normative specification update, conformance cases, a changelog entry, and a
migration note. A feature scheduled for removal must first produce a
deprecation diagnostic for at least one minor language version and document a
replacement. Security fixes may use a shorter transition only when retaining
the old behavior would leave users exposed; that exception must be called out
in the security advisory and release notes.

The CI conformance suite is the executable minimum contract. New compilers are
expected to continue passing prior applicable conformance cases, and releases
must not silently rewrite the declared language version.

## Package and platform compatibility

Lockfiles pin dependency versions, Git revisions, and content hashes. The lock
format remains readable for at least the previous format generation or the CLI
must provide a documented migration. Registry records are immutable for a
published package version.

The supported host matrix is the one enforced by CI: Python 3.10 through 3.14,
x86-64 Linux and Windows, and native ARM64 Linux and macOS. A platform is not
claimed as supported until a native compile-and-run gate exists. The MORT OS
freestanding target remains x86-64 and is tested through Zig cross-compilation.

## Support window

The latest compiler minor line receives normal fixes. The preceding minor line
receives critical security fixes for 90 days after supersession; older lines
are unsupported. Exact currently supported lines are listed in
[SECURITY.md](../SECURITY.md). Because Mort is alpha, compatibility beyond
these written guarantees is not promised, and users should pin compiler and
dependency versions for production experiments.
