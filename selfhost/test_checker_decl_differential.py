"""Differential test: selfhost's declaration checker must agree with mort/typechecker.py.

Run from the repository root:

    python -m pytest selfhost/test_checker_decl_differential.py -q

Every expected value comes from running the real Python checker; see
py_check_dump.py for the dump format and for what is compared when the Python
checker stops on an error only the type phase (T5) can see.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import py_check_dump  # noqa: E402
from test_lexer_differential import repo_mx_files  # noqa: E402

REPO_FILES = repo_mx_files()
MODES = ("hosted", "test", "freestanding")
EVENTS = py_check_dump.EVENTS_MARK


@pytest.fixture(scope="session")
def check_exe(tmp_path_factory):
    build = tmp_path_factory.mktemp("selfhost-check-build")
    exe = build / ("check_main.exe" if os.name == "nt" else "check_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "check_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, (
        f"compiling check_main.mx failed:\n{result.stdout}\n{result.stderr}")
    return exe


def run_mort(exe, manifest_text, tmp_path, timeout=120):
    manifest = tmp_path / "manifest.txt"
    manifest.write_bytes(manifest_text.encode("utf-8"))
    out = tmp_path / "out.txt"
    if out.exists():
        out.unlink()
    result = subprocess.run(
        [str(exe), str(manifest), str(out)], capture_output=True, timeout=timeout)
    dump = out.read_bytes().decode("ascii") if out.exists() else None
    return result.returncode, result.stderr.replace(b"\r\n", b"\n").rstrip(b"\n"), dump


def split_dump(text):
    tables, marker, events = text.partition(EVENTS)
    assert marker, f"no events marker in dump: {text!r}"
    return tables, events


def first_difference(expected, actual):
    for index, (want, got) in enumerate(zip(expected.splitlines(), actual.splitlines())):
        if want != got:
            return f"line {index + 1}\n  python: {want}\n  mort:   {got}"
    return (f"line counts differ: python {len(expected.splitlines())}, "
            f"mort {len(actual.splitlines())}")


def compare(exe, path, mode, tmp_path):
    """Run both implementations on one root file and assert they agree."""
    expected = py_check_dump.run_reference(path, mode)
    if expected.outcome == "load":
        return "load"  # import resolution is T8's; nothing here can be compared
    assert expected.outcome != "crash", f"{path}: Python crash"
    if expected.outcome == "parse":
        manifest = f"MODE {mode}\nFILE {expected.failing_file}\n"
        code, stderr, _dump = run_mort(exe, manifest, tmp_path)
        assert code == 1, f"{path}: {stderr!r}"
        assert stderr.decode("utf-8") == expected.error, path
        return "parse"
    code, stderr, text = run_mort(exe, expected.manifest, tmp_path)
    assert code in (0, 1), f"{path} ({mode}): exit {code}: {stderr!r}"
    assert text is not None, f"{path} ({mode}): no dump written"
    tables, events = split_dump(text)
    if expected.t4 or expected.outcome == "ok":
        if expected.outcome == "ok":
            assert code == 0, (
                f"{path} ({mode}): Mort rejected a valid program: {stderr!r}")
        else:
            assert code == 1, f"{path} ({mode}): expected an error: {expected.error}"
            assert stderr.decode("utf-8") == expected.error, (
                f"{path} ({mode})\n  python: {expected.error}\n"
                f"  mort:   {stderr.decode('utf-8')}")
        assert (expected.tables or "") == tables, (
            f"{path} ({mode}): tables differ: "
            f"{first_difference(expected.tables or '', tables)}")
        assert expected.events == events, (
            f"{path} ({mode}): events differ: "
            f"{first_difference(expected.events, events)}")
        return "error" if expected.t4 else "ok"
    # Python's first error needs types: Mort must have kept going over the
    # same names, or stopped later on an error of its own.
    if expected.tables is not None:
        assert expected.tables == tables, (
            f"{path} ({mode}): tables differ: "
            f"{first_difference(expected.tables, tables)}")
    assert events.startswith(expected.events), (
        f"{path} ({mode}): events are not a prefix: "
        f"{first_difference(expected.events, events)}")
    return "deferred"


def test_corpus_is_not_empty():
    assert len(REPO_FILES) >= 100


@pytest.mark.parametrize(
    "path", REPO_FILES, ids=[p.relative_to(ROOT).as_posix() for p in REPO_FILES])
def test_repo_file(check_exe, tmp_path, path):
    for mode in MODES:
        compare(check_exe, path, mode, tmp_path)


def assert_source_agrees(exe, tmp_path, source, mode="hosted"):
    path = tmp_path / "input.mx"
    path.write_bytes(source.encode("utf-8") if isinstance(source, str) else source)
    outcome = compare(exe, path, mode, tmp_path)
    assert outcome != "load", source
    return outcome


OK_MAIN = "fn main() -> int { return 0; }\n"

# (hosted) declarations and their rejections.
DECL_CASES = {
    "empty": "",
    "no_main": "fn f() {}",
    "main_wrong_params": "fn main(a: int) -> int { return 0; }",
    "main_wrong_ret": "fn main() -> u8 { return 0; }",
    "main_in_module": "module m;\nfn main() -> int { return 0; }",
    "main_extern": "extern fn main() -> int;",
    "main_generic": "fn main<T>() -> int { return 0; }",
    "alias": OK_MAIN + "type A = *u8;\ntype B = A;\nlet g: B = 0 as B;\n",
    "alias_cycle": OK_MAIN + "type A = B;\ntype B = A;\n",
    "alias_cycle_three": OK_MAIN + "type A = *B;\ntype B = C;\ntype C = A;\n",
    "alias_dup": OK_MAIN + "type A = u8;\ntype A = u8;\n",
    "alias_builtin_name": OK_MAIN + "type u8 = u8;\n",
    "alias_bool": OK_MAIN + "type bool = u8;\n",
    "alias_void_target": OK_MAIN + "type A = void;\n",
    "alias_unknown": OK_MAIN + "type A = Missing;\n",
    "alias_in_fn_sig": "type A = u8;\nfn f(a: A) -> *A { return &a; }\n" + OK_MAIN,
    "alias_in_tuple": OK_MAIN + "type A = u8;\nstruct S { a: (A, *A) }\n",
    "alias_in_fn_type": OK_MAIN + "type A = u8;\nstruct S { a: fn(A) -> A }\n",
    "alias_in_generic": OK_MAIN + "type A = u8;\nstruct G<T> { x: T }\nstruct S { a: G<A> }\n",
    "alias_struct_conflict": OK_MAIN + "type A = u8;\nstruct A { x: u8 }\n",
    "alias_enum_conflict": OK_MAIN + "type A = u8;\nenum A { X }\n",
    "enum_units": OK_MAIN + "enum E { A, B, C }\n",
    "enum_payloads": OK_MAIN + "enum E { A(u8), B(u8, *u8), C }\n",
    "enum_no_variants": OK_MAIN + "enum E {}\n",
    "enum_dup_variant": OK_MAIN + "enum E { A, A }\n",
    "enum_dup_name": OK_MAIN + "enum E { A }\nenum E { B }\n",
    "enum_template": OK_MAIN + "enum E<T> { A(T), B }\n",
    "enum_template_dup": OK_MAIN + "enum E<T> { A }\nenum E<U> { B }\n",
    "enum_template_dup_param": OK_MAIN + "enum E<T, T> { A }\n",
    "enum_unknown_payload": OK_MAIN + "enum E { A(Missing) }\n",
    "enum_payload_tuple_shape": OK_MAIN + "enum E { A(u8, u8) }\n",
    "enum_self_by_value": OK_MAIN + "enum E { A(E) }\n",
    "enum_self_through_ptr": OK_MAIN + "enum E { A(*E) }\n",
    "enum_then_struct_cycle": OK_MAIN + "enum E { A(S) }\nstruct S { e: E }\n",
    "struct_plain": OK_MAIN + "struct S { a: u8, b: *S, c: [u8; 4] }\n",
    "struct_empty": OK_MAIN + "struct S {}\n",
    "struct_dup_name": OK_MAIN + "struct S {}\nstruct S {}\n",
    "struct_enum_dup": OK_MAIN + "enum S { A }\nstruct S {}\n",
    "struct_dup_field": OK_MAIN + "struct S { a: u8, a: u8 }\n",
    "struct_unknown_field": OK_MAIN + "struct S { a: Missing }\n",
    "struct_void_field": OK_MAIN + "struct S { a: void }\n",
    "struct_field_forward": OK_MAIN + "struct S { t: *T }\nstruct T { s: *S }\n",
    "struct_field_forward_value": OK_MAIN + "struct S { t: T }\nstruct T { x: u8 }\n",
    "struct_self_by_value": OK_MAIN + "struct S { s: S }\n",
    "struct_cycle_two": OK_MAIN + "struct A { b: B }\nstruct B { a: A }\n",
    "struct_cycle_three": OK_MAIN + "struct A { b: B }\nstruct B { c: C }\nstruct C { a: A }\n",
    "struct_array_cycle": OK_MAIN + "struct A { b: [A; 2] }\n",
    "struct_tuple_cycle": OK_MAIN + "struct A { b: (A, u8) }\n",
    "struct_pointer_cycle": OK_MAIN + "struct A { b: *A, c: []A, d: fn(A) -> A }\n",
    "cycle_multiple_deps": OK_MAIN + "struct A { b: B, c: C }\nstruct B { a: A }\nstruct C { a: A }\n",
    "cycle_tuple_order": OK_MAIN + "struct A { x: (C, B) }\nstruct B { a: A }\nstruct C { a: A }\n",
    "array_size_zero": OK_MAIN + "struct S { a: [u8; 0] }\n",
    "array_of_void": OK_MAIN + "struct S { a: [void; 2] }\n",
    "slice_of_void": OK_MAIN + "struct S { a: []void }\n",
    "ptr_void_field": OK_MAIN + "struct S { a: *void }\n",
    "tuple_void_elem": OK_MAIN + "struct S { a: (void, u8) }\n",
    "fn_type_field": OK_MAIN + "struct S { a: fn(u8, *u8) -> bool, b: fn() -> void }\n",
    "fn_type_void_param": OK_MAIN + "struct S { a: fn(void) -> u8 }\n",
    "fn_type_unknown_ret": OK_MAIN + "struct S { a: fn() -> Missing }\n",
    "generic_struct": OK_MAIN + "struct G<T> { x: T, y: *G<T> }\nstruct S { g: G<u8> }\n",
    "generic_struct_two_uses": OK_MAIN + "struct G<T> { x: T }\nstruct S { a: G<u8>, b: G<u8> }\n",
    "generic_struct_wrong_arity": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<u8, u8> }\n",
    "generic_struct_bad_arg": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<Missing> }\n",
    "generic_struct_not_generic": OK_MAIN + "struct S { g: u8<u8> }\n",
    "generic_struct_unknown": OK_MAIN + "struct S { g: Missing<u8> }\n",
    "generic_struct_dup": OK_MAIN + "struct G<T> { x: T }\nstruct G<U> { y: U }\n",
    "generic_struct_dup_param": OK_MAIN + "struct G<T, T> { x: T }\n",
    "generic_struct_self": OK_MAIN + "struct G<T> { x: G<T> }\nstruct S { g: G<u8> }\n",
    "generic_struct_indirect_self": OK_MAIN + "struct G<T> { x: H<T> }\nstruct H<T> { y: G<T> }\nstruct S { g: G<u8> }\n",
    "generic_struct_field_unknown": OK_MAIN + "struct G<T> { x: Missing }\nstruct S { g: G<u8> }\n",
    "generic_struct_duplicate_field": OK_MAIN + "struct G<T> { x: T, x: *T }\nstruct S { g: G<u8> }\n",
    "generic_in_generic": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<G<u8>> }\n",
    "generic_enum_in_struct": OK_MAIN + "enum O<T> { Some(T), None }\nstruct S { o: O<u8> }\n",
    "generic_enum_self": OK_MAIN + "enum O<T> { Some(O<T>), None }\nstruct S { o: O<u8> }\n",
    "generic_enum_wrong_arity": OK_MAIN + "enum O<T> { Some(T) }\nstruct S { o: O<u8, u8> }\n",
    "generic_enum_unknown_payload": OK_MAIN + "enum O<T> { Some(Missing) }\nstruct S { o: O<u8> }\n",
    "generic_tuple_arg": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<(u8, u8)> }\n",
    "generic_array_arg": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<[u8; 3]> }\n",
    "generic_fn_arg": OK_MAIN + "struct G<T> { x: T }\nstruct S { g: G<fn(u8) -> u8> }\n",
    "generic_two_args": OK_MAIN + "struct G<A, B> { a: A, b: B }\nstruct S { g: G<u8, *u8> }\n",
    "generic_infinite_expansion": OK_MAIN + "struct G<T> { n: *G<*T> }\nstruct S { g: G<u8> }\n",
    "resource_ok": OK_MAIN + "resource struct R { x: u8 }\nfn destroy(value: *R) -> void {}\n",
    "resource_no_destroy": OK_MAIN + "resource struct R { x: u8 }\n",
    "resource_wrong_destroy": OK_MAIN + "resource struct R { x: u8 }\nfn destroy(value: R) -> void {}\n",
    "resource_two_destroy_forms": OK_MAIN + "resource struct R { x: u8 }\nfn destroy(v: *R) -> void {}\nfn destroy(w: *R) -> void {}\n",
    "resource_generic": OK_MAIN + "resource struct R<T> { x: T }\nfn destroy<T>(value: *R<T>) -> void {}\nstruct S { r: *R<u8> }\n",
    "resource_generic_mismatch": OK_MAIN + "resource struct R<T> { x: T }\nfn destroy(value: *R<u8>) -> void {}\n",
    "resource_alias_param": OK_MAIN + "type P = u8;\nresource struct R<T> { x: T }\nfn destroy<T>(value: *R<T>) -> void {}\nstruct S { r: *R<P> }\n",
    "fn_dup": OK_MAIN + "fn f() {}\nfn f() {}\n",
    "fn_extern_dup": OK_MAIN + "fn f() {}\nextern fn f();\n",
    "fn_builtin_name": OK_MAIN + "fn print() {}\n",
    "fn_named_volatile_read": OK_MAIN + "fn volatile_read() {}\n",
    "global_named_volatile_write": OK_MAIN + "let volatile_write: u8 = 1;\n",
    "extern_builtin_name": OK_MAIN + "extern fn alloc();\n",
    "extern_c_keyword": OK_MAIN + "extern fn while();\n",
    "extern_ok": OK_MAIN + "extern fn f(a: u8, b: *u8) -> c_int;\n",
    "fn_param_void": OK_MAIN + "fn f(a: void) {}\n",
    "fn_param_unknown": OK_MAIN + "fn f(a: Missing) {}\n",
    "fn_param_array": OK_MAIN + "fn f(a: [u8; 2]) {}\n",
    "fn_ret_unknown": OK_MAIN + "fn f() -> Missing { }\n",
    "fn_ret_array": OK_MAIN + "fn f() -> [u8; 2] { }\n",
    "fn_ret_void_ok": OK_MAIN + "fn f() -> void { }\n",
    "fn_extern_param_array": OK_MAIN + "extern fn f(a: [u8; 2]);\n",
    "fn_template": OK_MAIN + "fn f<T>(a: T) -> T { return a; }\n",
    "fn_template_unchecked_body": OK_MAIN + "fn f<T>(a: Missing) -> Missing { undefined_call(); }\n",
    "fn_template_dup_param": OK_MAIN + "fn f<T, T>() {}\n",
    "fn_template_dup_name": OK_MAIN + "fn f<T>() {}\nfn f<U>() {}\n",
    "fn_template_then_plain": OK_MAIN + "fn f<T>() {}\nfn f() {}\n",
    "fn_plain_then_template": OK_MAIN + "fn f() {}\nfn f<T>() {}\n",
    "global_ok": OK_MAIN + "let g: u8 = 1;\nconst h: u8 = 2;\n",
    "global_unknown_type": OK_MAIN + "let g: Missing = 1;\n",
    "global_array": OK_MAIN + "let g: [u8; 2] = [1, 2];\n",
    "global_array_nodecl": OK_MAIN + "let g = [1, 2];\n",
    "global_array_repeat": OK_MAIN + "let g: [u8; 3] = [0; 3];\n",
    "global_array_repeat_mismatch": OK_MAIN + "let g: [u8; 3] = [0; 2];\n",
    "global_array_size_mismatch": OK_MAIN + "let g: [u8; 3] = [1, 2];\n",
    "global_array_not_array": OK_MAIN + "let g: u8 = [1, 2];\n",
    "global_array_unknown_elem": OK_MAIN + "let g: [Missing; 1] = [1];\n",
    "global_conflict_global": OK_MAIN + "let g: u8 = 1;\nlet g: u8 = 2;\n",
    "global_conflict_fn": OK_MAIN + "fn g() {}\nlet g: u8 = 1;\n",
    "global_conflict_template": OK_MAIN + "fn g<T>() {}\nlet g: u8 = 1;\n",
    "global_conflict_struct": OK_MAIN + "struct g {}\nlet g: u8 = 1;\n",
    "global_conflict_enum": OK_MAIN + "enum g { A }\nlet g: u8 = 1;\n",
    "global_conflict_builtin": OK_MAIN + "let len: u8 = 1;\n",
    "global_refers_later_global": OK_MAIN + "let a: u8 = b;\nlet b: u8 = 1;\n",
    "global_refers_function": OK_MAIN + "fn f() {}\nlet a: fn() -> void = f;\n",
    "global_refers_undefined": OK_MAIN + "let a: u8 = missing;\n",
    "global_with_call": OK_MAIN + "fn f() -> u8 { return 1; }\nlet a: u8 = f();\n",
}

# (hosted) statements and name resolution; every body runs after a plain main.
BODY_CASES = {
    "body_empty": "",
    "body_let_read": "let x = 1; let y = x;",
    "body_let_self_reference": "let x = x;",
    "body_shadow_param": "let a = 1;",
    "body_redeclare": "let x = 1; let x = 2;",
    "body_redeclare_in_block": "let x = 1; { let x = 2; let y = x; }",
    "body_block_scope_ends": "{ let x = 1; } let y = x;",
    "body_if_scopes": "let a = 1; if a == 1 { let b = a; } else { let c = a; let b = c; }",
    "body_else_if_scopes": "let a = 1; if a == 1 { let b = 0; } else if a == 2 { let b = 1; } else { let b = 2; }",
    "body_while_scope": "let a = true; while a { let b = a; break; }",
    "body_while_redeclare": "let a = 1; while a == 1 { let a = 2; let b = a; break; }",
    "body_for_var": "for i in 0..3 { let j = i; }",
    "body_for_redeclare_var": "for i in 0..3 { let i = 1; }",
    "body_for_inclusive": "for i in 0..=3 { let j = i; }",
    "body_for_typed": "for i: u8 in 0..3 { let j = i; }",
    "body_for_scope_ends": "for i in 0..3 { } let j = i;",
    "body_for_unknown_bound": "for i in missing..3 { }",
    "body_undefined": "missing;",
    "body_undefined_in_expr": "let x = 1 + missing;",
    "body_undefined_call": "missing_fn();",
    "body_call_local": "let f = g; f();",
    "body_call_non_callable": "let f = 1; f();",
    "body_shadow_function_with_local": "let g = 1; let h = g;",
    "body_reference_global": "let a = global_x;",
    "body_shadow_global": "let global_x = 1; let a = global_x;",
    "body_assign_const": "const x: u8 = 1; x = 2;",
    "body_assign_mut": "let x: u8 = 1; x = 2;",
    "body_assign_undefined": "missing = 1;",
    "body_assign_field_const": "const s: S = S { a: 1 }; s.a = 2;",
    "body_assign_const_global": "global_const = 2;",
    "body_assign_index_const": "const a: *u8 = 0 as *u8; a[0] = 1;",
    "body_compound": "let x = 1; x += 2;",
    "body_compound_call_twice": "let x = 1; let p = g(); x += g();",
    "body_compound_const": "const x: u8 = 1; x += 2;",
    "body_compound_undefined": "missing += 1;",
    "body_return": "return;",
    "body_return_value": "return g();",
    "body_return_undefined": "return missing;",
    "body_break_outside": "break;",
    "body_continue_outside": "continue;",
    "body_break_in_if_outside": "if true { break; }",
    "body_break_in_loop": "while true { if true { break; } continue; }",
    "body_break_in_for": "for i in 0..3 { break; }",
    "body_loop_kw": "loop { break; }",
    "body_defer": "let p = 0 as *u8; defer free(p as *void);",
    "body_defer_undefined": "defer missing();",
    "body_defer_try": "defer try_it()?;",
    "body_asm": 'asm("nop");',
    "body_nested_block": "{ { { let a = 1; let b = a; } } }",
    "body_block_shadow_chain": "let a = 1; { let a = 2; { let a = 3; let b = a; } let c = a; } let d = a;",
    "body_match_units": "let e = E.A; match e { E.A => { } E.B => { } }",
    "body_match_wildcard": "let e = E.A; match e { E.A => { } _ => { } }",
    "body_match_two_wildcards": "let e = E.A; match e { _ => { } _ => { } }",
    "body_match_wildcard_not_last": "let e = E.A; match e { _ => { } E.A => { } }",
    "body_match_payload": "let p = P.A(1); match p { P.A(v) => { let w = v; } P.B => { } }",
    "body_match_payload_shadows": "let v = 9; let p = P.A(1); match p { P.A(v) => { let w = v; } P.B => { } }",
    "body_match_payload_two": "let p = Q.A(1, 2); match p { Q.A(a, b) => { let c = a; let d = b; } }",
    "body_match_payload_underscore": "let p = Q.A(1, 2); match p { Q.A(_, b) => { let c = b; } }",
    "body_match_payload_dup": "let p = Q.A(1, 2); match p { Q.A(a, a) => { } }",
    "body_match_payload_wrong_count": "let p = Q.A(1, 2); match p { Q.A(a) => { } }",
    "body_match_payload_not_var": "let p = P.A(1); match p { P.A(1) => { } P.B => { } }",
    "body_match_unknown_variant": "let p = P.A(1); match p { P.Z(v) => { } P.B => { } }",
    "body_match_unit_as_payload": "let e = E.A; match e { E.A(v) => { } E.B => { } }",
    "body_match_pattern_call_local": "let e = 1; match e { g2() => { } _ => { } }",
    "body_match_pattern_expr": "let e = 1; match e { 1 => { } _ => { } }",
    "body_match_try_pattern": "let e = 1; match e { t()? => { } _ => { } }",
    "body_match_arm_scope_ends": "let p = P.A(1); match p { P.A(v) => { } P.B => { let w = v; } }",
    "body_match_nested": "let e = E.A; match e { E.A => { match e { E.B => { } _ => { } } } E.B => { } }",
    "body_match_nonenum_subject": "let x = 1; match x { P.A(v) => { } _ => { } }",
    "body_match_generic_enum": "let o = O<u8>.Some(1); match o { O<u8>.Some(v) => { let w = v; } O<u8>.None => { } }",
    "body_struct_lit": "let s = S { a: 1 };",
    "body_struct_lit_unknown": "let s = Missing { a: 1 };",
    "body_struct_lit_unknown_field": "let s = S { b: 1 };",
    "body_struct_lit_dup_field": "let s = S { a: 1, a: 2 };",
    "body_struct_lit_missing_field": "let s = T2 { };",
    "body_struct_lit_missing_two": "let s = T3 { x: 1 };",
    "body_struct_lit_undefined_value": "let s = S { a: missing };",
    "body_struct_lit_array_field": "let s = H { a: [1, 2] };",
    "body_struct_lit_array_field_wrong_size": "let s = H { a: [1, 2, 3] };",
    "body_struct_lit_array_repeat_field": "let s = H { a: [1; 2] };",
    "body_struct_lit_generic": "let s = G<u8> { x: 1 };",
    "body_struct_lit_generic_bad": "let s = G<Missing> { x: 1 };",
    "body_struct_lit_generic_not_template": "let s = S<u8> { a: 1 };",
    "body_struct_lit_generic_wrong_arity": "let s = G<u8, u8> { x: 1 };",
    "body_struct_lit_via_alias": "let s = SA { a: 1 };",
    "body_field_enum_variant": "let e = E.A;",
    "body_field_enum_unknown_variant": "let e = E.Z;",
    "body_field_enum_payload_required": "let e = P.A;",
    "body_field_on_var": "let s = S { a: 1 }; let x = s.a;",
    "body_field_on_undefined": "let x = missing.a;",
    "body_field_unknown_on_value": "let s = S { a: 1 }; let x = s.z;",
    "body_field_module_no_alias": "let x = nothing.f;",
    "body_field_generic_enum": "let e = O<u8>.None;",
    "body_call_enum_ctor": "let p = P.A(1);",
    "body_call_enum_ctor_wrong_count": "let p = P.A(1, 2);",
    "body_call_enum_ctor_unit": "let e = E.A(1);",
    "body_call_enum_ctor_unknown_variant": "let p = P.Z(1);",
    "body_call_enum_ctor_undefined_arg": "let p = P.A(missing);",
    "body_call_generic_enum_ctor": "let o = O<u8>.Some(1);",
    "body_call_builtin": 'println("hi"); print(1);',
    "body_call_builtin_undefined_arg": "println(missing);",
    "body_call_builtin_two_args": "file_read(missing_a, missing_b, 3);",
    "body_call_sizeof": "let n = sizeof<u8>();",
    "body_call_sizeof_bad": "let n = sizeof<Missing>();",
    "body_call_sizeof_void": "let n = sizeof<void>();",
    "body_call_sizeof_args": "let n = sizeof<u8>(1);",
    "body_call_sizeof_no_type": "let n = sizeof();",
    "body_call_sizeof_generic": "let n = sizeof<G<u8>>();",
    "body_call_len_undefined": "let n = len(missing);",
    "body_call_wrong_args": "g(1);",
    "body_call_generic": "tm(1);",
    "body_call_generic_explicit": "tm<i64>(1);",
    "body_call_generic_wrong_type_count": "tm<i64, i64>(1);",
    "body_call_generic_bad_type_arg": "tm<Missing>(1);",
    "body_call_generic_void_type_arg": "tm<void>(1);",
    "body_call_nongeneric_type_args": "g<i64>();",
    "body_call_generic_wrong_args": "tm(1, 2);",
    "body_call_args_order": "let a = 1; g3(a, g(), a);",
    "body_call_args_undefined": "g3(missing, 1, 2);",
    "body_fn_as_value": "let f = g;",
    "body_generic_fn_as_value": "let f = tm;",
    "body_module_call_no_alias": "nothing.f();",
    "body_method_style_on_local": "let s = 1; s.f();",
    "body_casts": "let a = 1 as u8; let b = a as *u8;",
    "body_cast_undefined": "let a = missing as u8;",
    "body_unary": "let a = 1; let b = -a; let c = ~a; let d = !true; let e = &a; let f = *e;",
    "body_binary": "let a = 1; let b = a + a * a - a / a % a;",
    "body_binary_undefined_right": "let a = 1 + missing;",
    "body_binary_undefined_left": "let a = missing + 1;",
    "body_compare_chain": "let a = 1; let b = a < a && a > a || a == a;",
    "body_index": "let p = 0 as *u8; let v = p[0];",
    "body_index_undefined_obj": "let v = missing[0];",
    "body_index_undefined_value": "let p = 0 as *u8; let v = p[missing];",
    "body_index_chain": "let p = 0 as **u8; let v = p[0][0];",
    "body_field_chain": "let s = S2 { s: S { a: 1 } }; let v = s.s.a;",
    "body_tuple": "let t = (1, 2); let a = t.0;",
    "body_tuple_undefined": "let t = (1, missing);",
    "body_try": "let x = try_it()?;",
    "body_move_local": "let a = 1; let b = move a;",
    "body_move_undefined": "let b = move missing;",
    "body_move_non_var": "let b = move g();",
    "body_move_global": "let b = move global_x;",
    "body_array_let": "let a: [u8; 2] = [1, 2];",
    "body_array_let_nodecl": "let a = [1, 2]; let b = [1; 2];",
    "body_array_let_size": "let a: [u8; 2] = [1];",
    "body_array_let_repeat_size": "let a: [u8; 2] = [1; 3];",
    "body_array_let_mismatch": "let a: u8 = [1];",
    "body_array_let_unknown": "let a: [Missing; 1] = [1];",
    "body_array_let_undefined_element": "let a: [u8; 2] = [1, missing];",
    "body_array_let_array_without_literal": "let a: [u8; 2] = g();",
    "body_array_expr_only_let": "g3([1, 2], 1, 2);",
    "body_array_in_expression": "let x = [1, 2] as u8;",
    "body_let_decl_unknown": "let x: Missing = 1;",
    "body_let_decl_alias": "let x: A0 = 1;",
    "body_let_void_value": "let x = g();",
    "body_let_null_no_decl": "let x = null;",
    "body_let_null_decl": "let x: *u8 = null;",
    "body_expr_stmt_binary": "1 + missing;",
    "body_nested_expr": "let a = 1; let b = g3(g3(a, a, a), (a + 1) * 2, -a);",
    "body_paren_chain": "let a = 1; let b = (((a)));",
    "body_builtin_type_args": 'println<u8>("x");',
    "body_builtin_type_args_undefined_arg": "print<u8>(missing);",
    "body_slice_type_args": "let p = 0 as *u8; let s = slice<u8>(p, 1);",
    "body_volatile_read": "let p = 0 as *u32; let v = volatile_read<u32>(p);",
    "body_volatile_write": "let p = 0 as *u32; volatile_write<u32>(p, 1);",
    "body_volatile_no_type": "let p = 0 as *u32; let v = volatile_read(p);",
    "body_volatile_bad_width": "let p = 0 as *u32; let v = volatile_read<bool>(p);",
    "body_volatile_undefined_arg": "let v = volatile_read<u32>(missing);",
    "body_volatile_write_undefined_value": "let p = 0 as *u32; volatile_write<u32>(p, missing);",
    "body_mixed_order": "let a = g(); let b = a; { let c = b; g3(a, b, c); }",
}

BODY_PRELUDE = """
let global_x: i64 = 1;
const global_const: i64 = 2;
enum E { A, B }
enum P { A(i64), B }
enum Q { A(i64, i64) }
enum O<T> { Some(T), None }
struct S { a: i64 }
struct S2 { s: S }
struct H { a: [i64; 2] }
struct G<T> { x: T }
struct T2 { p: i64 }
struct T3 { x: i64, y: i64, z: i64 }
type SA = S;
type A0 = i64;
fn g() -> i64 { return 0; }
fn g2() -> i64 { return 1; }
fn g3(a: i64, b: i64, c: i64) -> i64 { return a; }
fn tm<T>(a: T) -> T { return a; }
fn try_it() -> O<i64> { return O<i64>.None; }
fn t() -> i64 { return 0; }
"""


@pytest.mark.parametrize("name", sorted(DECL_CASES))
def test_declarations(check_exe, tmp_path, name):
    source = DECL_CASES[name]
    # infinite expansion recurses without end; run it at the compiler's own limit
    limit_note = name == "generic_infinite_expansion"
    if limit_note:
        expected = py_check_dump.run_reference(
            _written(tmp_path, source), "hosted", recursion_limit=2000)
        assert expected.error == (
            "error: source nesting exceeds the compiler safety limit")
        code, stderr, _dump = run_mort(check_exe, expected.manifest, tmp_path)
        assert code == 1
        assert stderr.decode("utf-8") == expected.error
        return
    assert_source_agrees(check_exe, tmp_path, source)


def _written(tmp_path, source):
    path = tmp_path / "input.mx"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(BODY_CASES))
def test_bodies(check_exe, tmp_path, name):
    source = f"{BODY_PRELUDE}\nfn main() -> int {{\n{BODY_CASES[name]}\nreturn 0;\n}}\n"
    assert_source_agrees(check_exe, tmp_path, source)


def test_bad_test_block_is_located(check_exe, tmp_path):
    source = 'test "a" {\nlet x = missing;\n}\ntest "a" { }\n'
    assert assert_source_agrees(check_exe, tmp_path, source, mode="test") == "error"


def test_duplicate_test_name(check_exe, tmp_path):
    source = 'test "a" { }\ntest "a" { }\n'
    assert assert_source_agrees(check_exe, tmp_path, source, mode="test") == "error"


def test_shadowing_events_are_compared(check_exe, tmp_path):
    source = (
        BODY_PRELUDE
        + "fn main() -> int {\nlet x = 1;\n{\nlet x = 2;\nlet y = x;\n}\nlet z = x;\nreturn 0;\n}\n")
    assert_source_agrees(check_exe, tmp_path, source)


def test_deep_left_chain_is_iterative(check_exe, tmp_path):
    chain = " + 1" * 4000
    source = f"fn main() -> int {{\nlet x = 1{chain};\nreturn 0;\n}}\n"
    outcome = assert_source_agrees(check_exe, tmp_path, source)
    assert outcome == "ok"


def test_deep_field_chain_is_iterative(check_exe, tmp_path):
    chain = ".next" * 4000
    source = (
        "struct N { next: *N }\nfn main() -> int {\n"
        f"let s = N {{ next: 0 as *N }};\nlet x = s{chain};\nreturn 0;\n}}\n")
    assert_source_agrees(check_exe, tmp_path, source)


def test_undefined_at_the_end_of_a_deep_chain(check_exe, tmp_path):
    chain = "1 + " * 4000
    source = f"fn main() -> int {{\nlet x = {chain}missing;\nreturn 0;\n}}\n"
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_very_deep_chain_does_not_use_the_c_stack(check_exe, tmp_path):
    """Beyond what the Python reference can run in reasonable time, so the
    expected count comes from the source: one reference per operand."""
    operands = 300_000
    path = tmp_path / "input.mx"
    path.write_text(
        "fn main() -> int {\nlet a = 1;\nlet x = a" + " + a" * (operands - 1)
        + ";\nreturn 0;\n}\n", encoding="utf-8")
    code, stderr, text = run_mort(
        check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr
    _tables, events = split_dump(text)
    assert events.count('var "a" local d1 L2\n') == operands


def test_multi_file_module_program(check_exe, tmp_path):
    (tmp_path / "lib.mx").write_text(
        "module util;\npub fn add(a: i64, b: i64) -> i64 { return a + b; }\n"
        "fn hidden() -> i64 { return 0; }\n", encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        "import lib as u;\nfn main() -> int { return u.add(1, 2); }\n",
        encoding="utf-8")
    assert compare(check_exe, main_path, "hosted", tmp_path) == "ok"


def test_private_function_across_modules(check_exe, tmp_path):
    (tmp_path / "lib.mx").write_text(
        "module util;\nfn hidden() -> i64 { return 0; }\n", encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        "import lib as u;\nfn main() -> int { return u.hidden(); }\n",
        encoding="utf-8")
    assert compare(check_exe, main_path, "hosted", tmp_path) == "error"


def test_module_private_function_used_locally(check_exe, tmp_path):
    source = "module m;\nfn f() -> i64 { return g(); }\nfn g() -> i64 { return 1; }\n"
    assert assert_source_agrees(check_exe, tmp_path, source, mode="test") == "ok"


def test_unknown_alias_reports_location(check_exe, tmp_path):
    source = "fn main() -> int {\nmissing.f();\nreturn 0;\n}\n"
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_rejection_is_clean_and_located(check_exe, tmp_path):
    expected = py_check_dump.run_reference(
        _written(tmp_path, "struct S { a: Missing }\nfn main() -> int { return 0; }\n"),
        "hosted")
    assert expected.t4
    code, stderr, _dump = run_mort(check_exe, expected.manifest, tmp_path)
    assert code == 1
    assert b":1: error:" in stderr


def test_output_is_deterministic(check_exe, tmp_path):
    expected = py_check_dump.run_reference(
        _written(tmp_path, BODY_PRELUDE + "fn main() -> int { return 0; }\n"), "hosted")
    first = run_mort(check_exe, expected.manifest, tmp_path)
    second = run_mort(check_exe, expected.manifest, tmp_path)
    assert first == second
    assert first[0] == 0


def test_python_reference_is_deterministic_across_hash_seeds(tmp_path):
    path = _written(
        tmp_path,
        "struct A { b: B, c: C }\nstruct B { a: A }\nstruct C { a: A }\n" + OK_MAIN)
    env = dict(os.environ)
    outputs = set()
    for seed in ("0", "1", "2", "12345"):
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, str(HERE / "py_check_dump.py"), str(path), "hosted"],
            capture_output=True, env=env)
        outputs.add((result.stdout, result.stderr))
    assert len(outputs) == 1


def test_parse_error_gets_the_parsers_diagnostic(check_exe, tmp_path):
    outcome = assert_source_agrees(check_exe, tmp_path, "fn main( -> int {}\n")
    assert outcome == "parse"


def test_missing_manifest_is_a_usage_error(check_exe, tmp_path):
    result = subprocess.run(
        [str(check_exe), str(tmp_path / "absent.txt"), str(tmp_path / "out.txt")],
        capture_output=True)
    assert result.returncode == 2
    assert b"cannot read" in result.stderr


def test_missing_source_is_a_usage_error(check_exe, tmp_path):
    manifest = tmp_path / "m.txt"
    manifest.write_text(f"MODE hosted\nFILE {tmp_path / 'absent.mx'}\n", encoding="utf-8")
    result = subprocess.run(
        [str(check_exe), str(manifest), str(tmp_path / "out.txt")], capture_output=True)
    assert result.returncode == 2


def test_long_alias_chain(check_exe, tmp_path):
    """Long, but within CK_MAX_DEPTH: both implementations accept it."""
    chain = "".join(f"type A{i} = *A{i + 1};\n" for i in range(400))
    source = chain + "type A400 = u8;\nstruct S { a: A0 }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "ok"


def test_alias_chain_past_the_depth_limit_is_a_clean_diagnostic(check_exe, tmp_path):
    chain = "".join(f"type A{i} = *A{i + 1};\n" for i in range(5000))
    source = chain + "type A5000 = u8;\n" + OK_MAIN
    path = _written(tmp_path, source)
    expected = py_check_dump.run_reference(path, "hosted", recursion_limit=2000)
    assert expected.outcome == "error"
    code, stderr, _dump = run_mort(check_exe, expected.manifest, tmp_path)
    assert code == 1
    assert stderr == b"error: source nesting exceeds the compiler safety limit"


def test_long_struct_chain_by_value(check_exe, tmp_path):
    decls = "".join(f"struct S{i} {{ next: S{i + 1} }}\n" for i in range(3000))
    source = decls + "struct S3000 { x: u8 }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "ok"


def test_long_cycle_by_value(check_exe, tmp_path):
    decls = "".join(f"struct S{i} {{ next: S{i + 1} }}\n" for i in range(800))
    source = decls + "struct S800 { x: S0 }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def _mutable_sources():
    """Corpus files that import nothing outside the standard library."""
    usable = []
    for path in REPO_FILES:
        text = path.read_text(encoding="utf-8")
        relative = any(
            line.strip().startswith("import ")
            and not line.strip().startswith("import std")
            for line in text.splitlines())
        if not relative and len(text) < 6000:
            usable.append(text)
    return usable


def _mutate(rng, source):
    """Line-level and name-level mistakes, a few per mutant."""
    lines = source.split("\n")
    for _ in range(rng.randint(1, 3)):
        index = rng.randrange(len(lines))
        op = rng.randrange(5)
        if op == 0:
            del lines[index]
            if not lines:
                lines = [""]
        elif op == 1:
            lines.insert(index, lines[index])
        elif op == 2:
            other = rng.randrange(len(lines))
            lines[index], lines[other] = lines[other], lines[index]
        else:
            words = [w for w in lines[index].split(" ") if w.isidentifier()]
            if words:
                word = rng.choice(words)
                replacement = rng.choice(("missing_name", "u8", "main", "x"))
                lines[index] = lines[index].replace(word, replacement, 1)
    return "\n".join(lines)


def test_seeded_mutations_agree(check_exe, tmp_path):
    import random
    rng = random.Random(0x5434)
    sources = _mutable_sources()
    assert len(sources) > 20
    counts = {"ok": 0, "error": 0, "deferred": 0, "parse": 0, "load": 0}
    for _ in range(120):
        source = _mutate(rng, rng.choice(sources))
        path = tmp_path / "mutant.mx"
        path.write_bytes(source.encode("utf-8"))
        counts[compare(check_exe, path, rng.choice(MODES), tmp_path)] += 1
    assert counts["error"] > 5, counts
    assert counts["ok"] > 5, counts
    print(counts)


def test_subset_gate_covers_the_new_files():
    result = subprocess.run(
        [sys.executable, str(HERE / "tools" / "check_subset.py")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
