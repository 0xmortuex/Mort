# Backlog

Real, finishable improvements — the daily agent picks ONE and ships it end-to-end.
Rules: check items off when done, add follow-ups you discover, never do two at once,
smallest reviewable change wins.

**Safety:** Mort is a real, well-tested language. The daily agent works only on
**verifiable, low-risk surface** — new `std` modules, docs, examples, and tests —
and runs the relevant tests before pushing (`pip install ziglang`, then a targeted
`pytest` and/or `python mortc.py --run` on a small program). It does **not** change
the compiler internals (`mort/parser.py`, `mort/typechecker.py`, `mort/codegen.py`)
autonomously; those need careful human review with the full suite.

## Standard library (each module compiles + is tested by a small program)
- [x] `std.strings` — slice helpers over `[]const u8`: `equal`, `starts_with`, `ends_with`, `index_of` (substring search), `trim` (ASCII whitespace), `parse_i64` / `parse_u64` (returning `Option<i64>` / `Option<u64>`). Shipped 2026-07-25: `std/strings.mx`, covered by `test_std_strings_helpers_cover_equal_prefix_trim_and_parse` in `tests/test_mort.py` (16 assertions, `--run` exits 0), registered in `pyproject.toml`'s `mort-stdlib` list, version bumped to 0.50.0.
- [ ] `std.ascii` — `to_upper` / `to_lower` / `is_alpha` / `is_digit` / `is_space` on `u8`, plus in-place case conversion over a `[]u8`.
- [ ] `std.sort` — in-place comparison sort over `[]T` taking a caller `fn(a: T, b: T) -> bool` less-than (insertion or heap sort; document the complexity in the module header). Note: `std.algorithm.sort<T>` already exists but only supports `<`-comparable element types, not a caller-supplied comparator — this item is the comparator-taking variant, so it's still open and not a duplicate.
- [ ] `std.json` follow-up: `object_get_path(doc, index, "a.b.c")` walking nested objects by a dotted key, with a test.
- [ ] `std.strings` follow-up: `split(text, separator)` and `replace(text, old, new)`. `split` needs an allocation story (likely a `Vec<[]const u8>` of borrowed views into the source, which shouldn't own resources and should fit the existing ownership model) — worth checking against `std.vec` before committing.

## Docs & examples
- [ ] `examples/json.mx` — parse a small JSON config and print a couple of fields using `std.json`; make it run in the suite.
- [ ] `docs/stdlib.md` — a one-paragraph reference per bundled `std` module listing its public functions (ground each against the module source).
- [ ] `docs/ownership.md` — the resource / `move` rules with worked examples: `move` on return, `match move`, and containers of resources.

## Tests
- [ ] More `std.json` edge cases: deep nesting, `\uXXXX` + surrogate pairs, exponent/negative numbers, and malformed inputs asserting the reported error position.

## Larger (sketch in the item before shipping — may need human review)
- [ ] Resource-aware container API: `Vec.get` returns an aliasing copy (unsafe for resource elements) and `Vec.destroy` frees only the backing array. Design a safe `get_ref` returning `*T` and an element-dropping destroy without breaking the plain-`T` API.
