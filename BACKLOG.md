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
- [x] `std.ascii` — `to_upper` / `to_lower` / `is_alpha` / `is_digit` / `is_space` on `u8`, plus in-place case conversion over a `[]u8`. Shipped 2026-07-26: `std/ascii.mx` (`upper_inplace` / `lower_inplace` over `[]u8`), covered by `test_std_ascii_classification_and_case_conversion` in `tests/test_mort.py` (14 assertions, `--run` exits 0), registered in `pyproject.toml`'s `mort-stdlib` list, version bumped to 0.51.0.
- [x] `std.sort` — in-place comparison sort over `[]T` taking a caller `fn(a: T, b: T) -> bool` less-than (insertion or heap sort; document the complexity in the module header). Note: `std.algorithm.sort<T>` already exists but only supports `<`-comparable element types, not a caller-supplied comparator — this item is the comparator-taking variant, so it's still open and not a duplicate. Shipped 2026-07-27: `std/sort.mx` (`sort<T>` stable in-place insertion sort, plus `is_sorted<T>` over `[]const T`), covered by `test_std_sort_orders_by_caller_comparator_and_reports_sortedness` in `tests/test_mort.py` (6 assertions across ascending/descending/single-element cases, `--run` exits 0), registered in `pyproject.toml`'s `mort-stdlib` list, version bumped to 0.52.0.
- [x] `std.json` follow-up: `object_get_path(doc, index, "a.b.c")` walking nested objects by a dotted key, with a test. Shipped 2026-07-28: `std/json.mx` (`object_get_path`, built on the existing `object_get_view` + `std.strings.index_of`), covered by `test_std_json_object_get_path_walks_dotted_nested_keys` in `tests/test_mort.py` (6 assertions incl. missing intermediate keys and a leaf-as-object lookup, `--run` exits 0), version bumped to 0.53.0.
- [ ] `std.strings` follow-up: `split(text, separator)` and `replace(text, old, new)`. `split` needs an allocation story (likely a `Vec<[]const u8>` of borrowed views into the source, which shouldn't own resources and should fit the existing ownership model) — worth checking against `std.vec` before committing.

## Docs & examples
- [x] `examples/json.mx` — parse a small JSON config and print a couple of fields using `std.json`. Shipped 2026-07-29: parses `{"name":"mort","version":3,"debug":true}` and prints the name's byte length, the version number, and the debug flag (`4\n3\n1\n`); added to `EXPECTED` in `tests/test_mort.py::test_examples_run`, `--run` exits 0, full suite (334 tests) green.
- [ ] `docs/stdlib.md` — a one-paragraph reference per bundled `std` module listing its public functions (ground each against the module source).
- [ ] `docs/ownership.md` — the resource / `move` rules with worked examples: `move` on return, `match move`, and containers of resources.

## Tests
- [x] More `std.json` edge cases: deep nesting, `\uXXXX` + surrogate pairs, exponent/negative numbers, and malformed inputs asserting the reported error position. Shipped 2026-07-30: `test_std_json_edge_cases_cover_nesting_unicode_exponents_and_errors` in `tests/test_mort.py` (11 assertions — 4-level nested object ending in an array of mixed values, a BMP `é` escape plus a `😀` surrogate pair decoding to the expected UTF-8 byte length, `-1.5e2` / `2e-2` exponent parsing, and two malformed-input cases with an exact expected `error_pos`: a trailing comma in an object at `error_pos == 7`, and an unterminated array at `error_pos == length`), `--run` exits 0, full suite (335 tests) green. No compiler or std module changes, so no version bump.
- [ ] Malformed-JSON coverage still open: duplicate object keys (what does `object_get` return?), a bare top-level scalar with trailing garbage (e.g. `"1 2"`), and non-UTF-8/invalid-continuation-byte input in string values.

## Larger (sketch in the item before shipping — may need human review)
- [ ] Resource-aware container API: `Vec.get` returns an aliasing copy (unsafe for resource elements) and `Vec.destroy` frees only the backing array. Design a safe `get_ref` returning `*T` and an element-dropping destroy without breaking the plain-`T` API.
