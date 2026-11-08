# Language decisions

Where the specification and the compiler disagreed, the language's design decided
which one changed. Each decision records what was found and why. The specification
is `docs/language-specification.md`; `tests/test_spec_conformance.py` keeps it and
the compiler in step.

Commit hashes below are on branch `docs/spec`.

## Compiler changed

| # | Disagreement | Decision | Commit |
| --- | --- | --- | --- |
| 1 | `B<B<i64>> { v: x }` failed: the struct-literal lookahead had no `>>` case | Bug. Its two sibling lookaheads handle `>>` | `16bbec2` |
| 2 | `if a < b { if c > (d) {} }` failed: the type-argument lookahead scanned into the block | Bug. A comparison must not depend on later statements. A type argument list never contains a brace, so the scan stops there | `62748b9` |
| 3 | `let a = h.r;` copied a resource out of a live owner, and both copies were destroyed | Bug. "Resources cannot be copied implicitly" is the core of the design. Raw-pointer and slice reads stay legal because that storage is manually managed | `318b33d` |
| 4 | A function type followed by another list element failed in function, tuple, generic and enum types, because `->` closed an angle bracket | Bug, and the limit Vex's `VexApi` worked around | `554364d`, `cee94cc` |
| 5 | `sizeof` on an array type raised `KeyError` | Bug. The spec defines `sizeof` of any concrete type | `7b9df23` |
| 6 | A variant named by two arms was accepted | Bug. The spec already called this a compile-time error | `ac2ae7b` |
| 7 | `==` on arrays compared addresses silently, and on slices failed in C | Bug. The spec defines no aggregate equality | `dfbfc56` |
| 7a | A `match` on a function value passed the checker and then failed in C | Bug. The same declaration spelling as the match binding fix | `d9f06f1` |

## Document changed

| # | Disagreement | Decision |
| --- | --- | --- |
| 8 | `get()(1)` was derivable but rejected | The call target must be a path. Section 3.4. Calling arbitrary expressions is new language surface |
| 9 | `h.cb(1)` on a function-typed field was derivable but rejected | A member path never reads a field. Section 19.7. `let g = h.cb; g(1)` is the form, and Vex hosts already use it |
| 10 | `if P { x: 1 }.x == 1 {}` was derivable but rejected | The no-struct mode is deliberate and was missing. Section 3.3 |
| 11 | `E<i64>.A(1)` was accepted but not derivable | It is the normal way to name a generic variant. Added `generic-qualifier` |
| 12 | The `literal` rule was used but never defined | Added |
| 13 | Trailing commas, `module` placement and the `>>` split were inexact or implicit | Grammar rewritten to the parser's actual LL(1) decisions, section 3.1 |
| 14 | `sizeof<[u8; 4]>()` is rejected, because the lookahead stops at `;` | Kept, and stated in section 3.3. Use an alias. Extending the scan would change more than this needs |
| 15 | `print` was documented as printing `bool` | It does not. Corrected, and `u64` printing through `i64` is now stated |
| 16 | Casting `null` was documented as allowed to function types | It is not. Corrected |
| 17 | `[T; 0]` was documented as valid | It is not. Arrays are positive-length |
| 18 | `try` was documented as working on any enum with `Ok` and `Err` | It works on `Result<V, E>` by name. Section 19.10 |
| 19 | A negated float literal does not adopt `f32` | Kept. `-1.5` is unary minus on a literal, and only a `FLOAT` token adopts `f32`. Section 19.3 |
| 20 | A discarded resource-bearing value is leaked, with no diagnostic | Stated in section 21.4. A leak is not unsafe. A lint belongs with future warnings work |
| 21 | Character literals were described as untyped | They are `u8` literals that adopt a type, like integer literals |
| 22 | Traits were expected among the type rules | Mort has none. Section 19.5 says so, and generic bodies are checked per instantiation |

## Known gaps left open

| # | Program | State |
| --- | --- | --- |
| 23 | `match` on `void`, `let b = a` for an array, pointer-to-float casts, invalid string escapes | Ill-formed, and rejected, but by the C backend rather than with a Mort diagnostic. Listed in section 22.4 |
| 24 | Out-of-range integer literal with `i64` annotation | Fixed on `fix/frontend-crashes`, not on this branch |
| 25 | Numeric front-end limits | Written by `fix/frontend-crashes`. Section 11 holds a marked placeholder |
