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
        manifest = py_check_dump.failing_manifest(mode, expected)
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
    "generic_shape_ptr": OK_MAIN + "struct G<T> { f: *T }\nstruct S { g: G<u8> }\n",
    "generic_shape_const_ptr": OK_MAIN + "struct G<T> { f: *const T }\nstruct S { g: G<u8> }\n",
    "generic_shape_slice": OK_MAIN + "struct G<T> { f: []T }\nstruct S { g: G<u8> }\n",
    "generic_shape_const_slice": OK_MAIN + "struct G<T> { f: []const T }\nstruct S { g: G<u8> }\n",
    "generic_shape_tuple": OK_MAIN + "struct G<T> { f: (T, *T, u8) }\nstruct S { g: G<u8> }\n",
    "generic_shape_fn": OK_MAIN + "struct G<T> { f: fn(T, *const T) -> []T }\nstruct S { g: G<u8> }\n",
    "generic_shape_array": OK_MAIN + "struct G<T> { f: [T; 3], h: [[*T; 2]; 3] }\nstruct S { g: G<u8> }\n",
    "generic_shape_nested_generic": OK_MAIN + "struct H<A, B> { a: A, b: B }\nstruct G<T> { f: H<T, *T> }\nstruct S { g: G<u8> }\n",
    "array_rule_pointer_to_array": OK_MAIN + "struct S { a: *[u8; 2] }\n",
    "array_rule_array_of_arrays": OK_MAIN + "struct S { a: [[u8; 2]; 3] }\n",
    "array_rule_function_parameter": OK_MAIN + "struct S { a: fn([u8; 2]) -> void }\n",
    "array_rule_function_result": OK_MAIN + "struct S { a: fn() -> [u8; 2] }\n",
    "array_rule_slice_of_arrays": OK_MAIN + "struct S { a: [][u8; 2] }\n",
    "array_rule_slice_of_arrays_behind_pointer": OK_MAIN + "struct S { a: *[][u8; 2] }\n",
    "array_rule_memo_is_per_position": OK_MAIN + "struct S { a: *[][u8; 2], b: [][u8; 2] }\n",
    "generic_tuple_argument_splits_at_the_top_level": OK_MAIN + "struct G<A, B> { a: A, b: B }\nstruct S { g: G<(u8, u8), i64> }\n",
    "generic_enum_shape_tuple": OK_MAIN + "enum O<T> { A((T, *const T)), B(T, []T) }\nstruct S { o: O<u8> }\n",
    "instance_order": OK_MAIN + "struct G<T> { x: T }\nstruct H<T> { y: T }\nenum E<T> { A(T) }\nstruct S { a: H<u8>, b: G<u8>, c: E<i64>, d: H<i64>, e: E<u8> }\n",
    "instance_order_nested": OK_MAIN + "struct G<T> { x: T }\nstruct H<T> { y: G<T> }\nstruct S { a: H<*u8> }\n",
    "instance_resource_flag": OK_MAIN + "resource struct R<T> { x: T }\nstruct P<T> { x: T }\nfn destroy<T>(value: *R<T>) -> void {}\nstruct S { a: *R<u8>, b: P<u8> }\n",
    "instance_resource_from_global": OK_MAIN + "resource struct R<T> { x: T }\nfn destroy<T>(value: *R<T>) -> void {}\nlet g: *R<u8> = null;\n",
    "resource_generic_other_param_name": OK_MAIN + "resource struct R<T> { x: T }\nfn destroy<U>(value: *R<T>) -> void {}\n",
    "resource_generic_extra_param": OK_MAIN + "resource struct R<T> { x: T }\nfn destroy<T, U>(value: *R<T>) -> void {}\n",
    "resource_generic_destroy_for_plain": OK_MAIN + "resource struct R { x: u8 }\nfn destroy<T>(value: *R) -> void {}\n",
    "resource_destroy_wrong_return": OK_MAIN + "resource struct R { x: u8 }\nfn destroy(value: *R) -> i64 { return 0; }\n",
    "resource_destroy_two_params": OK_MAIN + "resource struct R { x: u8 }\nfn destroy(value: *R, n: u8) -> void {}\n",
    "alias_multi_payload": OK_MAIN + "type A0 = u8;\nenum E { A(A0, *A0), B(A0) }\n",
    "alias_multi_payload_generic": OK_MAIN + "type A0 = u8;\nenum E<T> { A(A0, T) }\nstruct S { e: E<A0> }\n",
    "alias_to_tuple_payload": OK_MAIN + "type P = (u8, *u8);\nenum E { A(P) }\n",
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
    "body_try_nested_in_defer_call": "defer g3(1, 2, try t());",
    "body_try_nested_in_defer_binary": "defer 1 + try t();",
    "body_try_nested_in_defer_struct": "defer S { a: try t() };",
    "body_try_nested_in_defer_tuple": "defer (1, try t());",
    "body_try_nested_in_defer_index": "let p = 0 as *i64; defer p[try t()];",
    "body_try_nested_in_defer_cast": "defer try t() as u8;",
    "body_try_nested_in_defer_field": "defer try t().a;",
    "body_try_nested_in_pattern": "let e = 1; match e { g3(1, try t(), 2) => { } _ => { } }",
    "body_try_nested_in_payload_pattern": "let p = P.A(1); match p { P.A(try t()) => { } P.B => { } }",
    "body_alias_enum_ctor": "let p = PA.A(1);",
    "body_alias_chain_enum_ctor": "let p = PB.A(1);",
    "body_alias_enum_ctor_unknown_variant": "let p = PA.Z(1);",
    "body_alias_pattern": "let p = PA.A(1); match p { PA.A(v) => { let w = v; } PA.B => { } }",
    "body_alias_generic_enum_ctor": "let o = OA.Some(1);",
    "body_alias_generic_enum_variant": "let o = OA.None;",
    "body_alias_generic_pattern": "let o = OA.Some(1); match o { OA.Some(v) => { let w = v; } OA.None => { } }",
    "body_alias_unit_variant": "let e = EA.A;",
    "body_alias_unit_variant_unknown": "let e = EA.Z;",
    "body_alias_struct_literal_generic": "let s = GA { x: 1 };",
    "body_alias_type_argument": "let n = sizeof<A0>();",
    "body_alias_generic_call_type_argument": "tm<A0>(1);",
    "body_defer_try_keyword": "defer try try_it();",
    "body_try_keyword": "let x = try try_it();",
    "body_match_try_keyword_pattern": "let e = 1; match e { try t() => { } _ => { } }",
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
type PA = P;
type PB = PA;
type EA = E;
type OA = O<u8>;
type GA = G<u8>;
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
    assert_source_agrees(check_exe, tmp_path, source)


def _written(tmp_path, source):
    path = tmp_path / "input.mx"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(BODY_CASES))
def test_bodies(check_exe, tmp_path, name):
    source = f"{BODY_PRELUDE}\nfn main() -> int {{\n{BODY_CASES[name]}\nreturn 0;\n}}\n"
    assert_source_agrees(check_exe, tmp_path, source)


TEST_BLOCK_CASES = {
    "valid": 'test "a" { let x = 1; let y = x; }\n' + OK_MAIN,
    "valid_no_main": 'test "a" { let x = 1; }\n',
    "undefined": 'test "a" { let x = missing; }\n' + OK_MAIN,
    "undefined_line": 'test "a" {\n\nlet x = missing;\n}\n' + OK_MAIN,
    "duplicate_name": 'test "a" { }\ntest "a" { }\n' + OK_MAIN,
    "duplicate_after_error": 'test "a" { let x = missing; }\ntest "a" { }\n' + OK_MAIN,
    "two_blocks_same_local": 'test "a" { let x = 1; }\ntest "b" { let x = 2; let y = x; }\n' + OK_MAIN,
    "redeclare": 'test "a" { let x = 1; let x = 2; }\n' + OK_MAIN,
    "calls_function": 'fn f() -> i64 { return 1; }\ntest "a" { let x = f(); }\n' + OK_MAIN,
    "uses_global": 'let g: i64 = 1;\ntest "a" { let x = g; }\n' + OK_MAIN,
    "shadows_global": 'let g: i64 = 1;\ntest "a" { let g = 2; let x = g; }\n' + OK_MAIN,
    "control_flow": 'test "a" { for i in 0..3 { if i == 1 { break; } } while false { continue; } }\n' + OK_MAIN,
    "match_payload": 'enum P { A(i64), B }\ntest "a" { let p = P.A(1); match p { P.A(v) => { let w = v; } P.B => { } } }\n' + OK_MAIN,
    "break_outside": 'test "a" { break; }\n' + OK_MAIN,
    "in_module": 'module m;\nfn g() -> i64 { return 1; }\ntest "a" { let x = g(); }\n',
    "after_function_error": 'fn f() { missing_a(); }\ntest "a" { missing_b(); }\n' + OK_MAIN,
    "function_then_block_order": 'fn f() { let a = 1; }\ntest "a" { let b = 2; }\nfn h() { let c = 3; }\n' + OK_MAIN,
    "generic_call": 'fn tm<T>(a: T) -> T { return a; }\ntest "a" { let x = tm(1); }\n' + OK_MAIN,
    "builtins": 'test "a" { assert(true); println("x"); }\n' + OK_MAIN,
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", sorted(TEST_BLOCK_CASES))
def test_test_blocks_in_every_mode(check_exe, tmp_path, name, mode):
    assert_source_agrees(check_exe, tmp_path, TEST_BLOCK_CASES[name], mode=mode)


def test_invalid_test_block_is_rejected_when_hosted(check_exe, tmp_path):
    """Regression: test blocks are checked whatever the mode."""
    source = 'test "a" { let x = missing; }\n' + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"
    assert assert_source_agrees(check_exe, tmp_path, source, mode="freestanding") == "error"


def run_mort_bounded(exe, manifest_text, tmp_path, limit=500 * 1024 * 1024, extra=()):
    """Run the Mort checker under a hard memory limit: past it, allocation
    fails and the checker exits 2 instead of taking the machine down."""
    manifest = tmp_path / "manifest.txt"
    manifest.write_bytes(manifest_text.encode("utf-8"))
    out = tmp_path / "out.txt"
    command = [str(exe), str(manifest), str(out), *map(str, extra)]
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("Basic", BasicLimits),
                ("Io", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        assert job, ctypes.get_last_error()
        info = ExtendedLimits()
        info.Basic.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit
        assert kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)), ctypes.get_last_error()
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert kernel32.AssignProcessToJobObject(job, int(process._handle)), (
            ctypes.get_last_error())
        try:
            _stdout, stderr = process.communicate(timeout=60)
        finally:
            process.kill()
            kernel32.CloseHandle(job)
    else:
        import resource

        def apply_limit():
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=apply_limit)
        _stdout, stderr = process.communicate(timeout=60)
    return process.returncode, stderr.replace(b"\r\n", b"\n").rstrip(b"\n")


def assert_generic_limit(exe, tmp_path, source, line, template, problem):
    """Python applies these limits to generic functions only (see PLAN.md,
    section 11), so the expected text is built from its rule."""
    path = _written(tmp_path, source)
    code, stderr = run_mort_bounded(exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1, stderr
    assert stderr.decode("utf-8") == (
        f"{path}:{line}: error: generic instantiation of {template!r} {problem}")


NESTED_TOO_DEEP = "has a type argument nested more than 256 levels deep"


# These expansions reach the frame budget first, in both compilers, before the
# nesting limit on type arguments Mort also applies.
def test_unbounded_generic_struct_expansion_is_rejected(check_exe, tmp_path):
    source = "struct G<T> { n: *G<*T> }\nstruct S { g: G<u8> }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_unbounded_generic_enum_expansion_is_rejected(check_exe, tmp_path):
    source = "enum O<T> { S(*O<*T>), N }\nstruct S { g: O<u8> }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_deeply_nested_generic_arguments_are_rejected(check_exe, tmp_path):
    depth = 900
    nested = "G<" * depth + "u8" + ">" * depth
    source = f"struct G<T> {{ x: T }}\nstruct S {{ a: {nested} }}\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_doubling_generic_arguments_are_rejected(check_exe, tmp_path):
    assert_generic_limit(
        check_exe, tmp_path,
        "struct H<A, B> { a: *A, b: *B }\nstruct D<T> { a: *D<H<T, T>> }\n"
        "struct S { d: D<u8> }\n" + OK_MAIN,
        2, "D", "has a type argument longer than 4096 characters")


def test_too_many_generic_instances_are_rejected(check_exe, tmp_path):
    count = 16385
    declarations = "".join(f"struct T{i} {{ }}\n" for i in range(count))
    fields = ", ".join(f"f{i}: G<T{i}>" for i in range(count))
    assert_generic_limit(
        check_exe, tmp_path,
        f"struct G<T> {{ x: T }}\n{declarations}struct S {{ {fields} }}\n" + OK_MAIN,
        1, "G", "exceeds the limit of 16384 in one program")


def test_generic_work_budget_is_enforced(check_exe, tmp_path):
    template = ", ".join(f"f{i}: T" for i in range(9999))
    uses = ", ".join(f"g{i}: W<T{i}>" for i in range(150))
    declarations = "".join(f"struct T{i} {{ }}\n" for i in range(150))
    assert_type_limit(
        check_exe, tmp_path,
        f"struct W<T> {{ {template} }}\n{declarations}struct S {{ {uses} }}\n" + OK_MAIN,
        152, GENERIC_WORK_EXCEEDED)


@pytest.mark.parametrize(
    "first,repeated,count",
    [("", "let a{i}: *(A12, A12, T{i}) = null;", 600),
     ("let f: fn() -> *(A12, A12) = null;", "f();", 6000)],
    ids=["statement", "call"])
def test_body_budget_is_located_as_python_locates_it(check_exe, tmp_path, first, repeated, count):
    """`_work_location`: a null has no column, so the statement's is used, and
    a call with no arguments reports its parenthesis. T5 charges bodies as Python
    does, so which statement runs out, and where, come from the real checker."""
    source = (alias_chain(12, "({a}, {a})")
              + "".join(f"struct T{i} {{ }}\n" for i in range(600))
              + "fn main() -> int {\n  " + first + "\n"
              + "".join("  " + repeated.format(i=i) + "\n" for i in range(count))
              + "  return 0;\n}\n")
    path = _written(tmp_path, source)
    expected = py_check_dump.run_reference(path, "hosted", exact=True)
    assert expected.outcome == "error", expected.error
    assert TYPE_WORK_EXCEEDED in expected.error, expected.error
    code, stderr = run_mort_bounded(check_exe, expected.manifest, tmp_path)
    assert code == 1, stderr
    assert stderr.decode("utf-8") == expected.error


def test_generic_enum_work_budget_is_enforced(check_exe, tmp_path):
    variants = ", ".join(f"V{i}(T)" for i in range(9999))
    uses = ", ".join(f"g{i}: W<T{i}>" for i in range(150))
    declarations = "".join(f"struct T{i} {{ }}\n" for i in range(150))
    assert_type_limit(
        check_exe, tmp_path,
        f"enum W<T> {{ {variants} }}\n{declarations}struct S {{ {uses} }}\n" + OK_MAIN,
        1, GENERIC_WORK_EXCEEDED)


def test_known_difference_doubling_tuple_argument(check_exe, tmp_path):
    """PLAN.md, section 11: Mort's 4096-character argument limit applies to
    struct instances and Python's does not. Both exact results are pinned, so
    closing the gap fails here rather than passing unnoticed."""
    source = OK_MAIN + "struct D<T> { a: *D<(T, T)> }\nstruct S { d: D<u8> }\n"
    assert_generic_limit(
        check_exe, tmp_path, source, 2, "D", "has a type argument longer than 4096 characters")
    path = _written(tmp_path, source)
    expected = py_check_dump.run_reference(path, "hosted")
    assert expected.error == f"{path}:3: error: {TYPE_TOO_LONG}"


def test_deep_pointer_types_are_resolved_once(check_exe, tmp_path):
    """Re-resolving a deep type at each level costs cubic time; the guard's
    timeout turns that into a failure. Python on main has the same cost, so
    only the Mort side runs."""
    field_type = "*" * 480 + "u8"  # 2 frames per level: the budget allows 490
    fields = ", ".join(f"f{i}: {field_type}" for i in range(400))
    path = _written(tmp_path, f"struct S {{ {fields} }}\n" + OK_MAIN)
    code, stderr = run_mort_bounded(check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr


@pytest.mark.parametrize(
    "template,type_syntax",
    [("struct G<T> { x: *T }", "G"), ("enum G<T> { S(*T), N }", "G")],
    ids=["struct", "enum"])
def test_generic_limits_come_before_argument_validation(check_exe, tmp_path, template, type_syntax):
    """Validating the arguments is what recurses, so the limits must come
    first: an over-long unknown argument gets the limit, not "unknown type"."""
    source = f"{template}\nstruct S {{ a: {type_syntax}<{'*' * 257}Nope> }}\n" + OK_MAIN
    assert_generic_limit(check_exe, tmp_path, source, 1, "G", NESTED_TOO_DEEP)


TYPE_TOO_LONG = "type is longer than 65536 characters"
TYPE_WORK_EXCEEDED = "type processing exceeds the limit of 1000000 work units in one program"
GENERIC_WORK_EXCEEDED = "generic instantiation exceeds the limit of 1000000 work units in one program"


def alias_chain(count, link):
    return "type A0 = u8;\n" + "".join(
        f"type A{i + 1} = {link.format(a=f'A{i}')};\n" for i in range(count))


def assert_type_limit(exe, tmp_path, source, where, message=TYPE_TOO_LONG):
    """The memory limit guards Mort; the text and location are pinned here and
    must also be exactly what the Python checker reports."""
    path = _written(tmp_path, source)
    code, stderr = run_mort_bounded(exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1, stderr
    assert stderr.decode("utf-8") == f"{path}:{where}: error: {message}"
    assert py_check_dump.run_reference(path, "hosted").error == stderr.decode("utf-8")


@pytest.mark.parametrize(
    "link,line",
    [("({a}, {a})", 15), ("fn({a}, {a}) -> {a}", 10), ("G<{a}, {a}>", 15),
     ("*({a}, {a})", 15), ("[({a}, {a}); 2]", 14)],
    ids=["tuple", "function", "generic", "pointer_to_tuple", "array_of_tuple"])
def test_doubling_alias_expansion_is_bounded_and_located(check_exe, tmp_path, link, line):
    source = alias_chain(22, link) + "struct G<X, Y> { a: *X, b: *Y }\n" + OK_MAIN
    assert_type_limit(check_exe, tmp_path, source, line)


def test_doubling_aliases_declared_in_reverse_are_located_at_the_first_alias(check_exe, tmp_path):
    aliases = "".join(f"type A{i + 1} = (A{i}, A{i});\n" for i in range(21, -1, -1))
    assert_type_limit(check_exe, tmp_path, aliases + "type A0 = u8;\n" + OK_MAIN, 1)


def test_struct_field_type_over_the_limit_is_located(check_exe, tmp_path):
    source = alias_chain(13, "({a}, {a})") + "struct S { a: (A13, A13) }\n" + OK_MAIN
    assert_type_limit(check_exe, tmp_path, source, 15)


def test_annotation_over_the_limit_is_located(check_exe, tmp_path):
    source = (alias_chain(13, "({a}, {a})")
              + "fn main() -> int {\n  let x: (A13, A13) = missing;\n  return 0;\n}\n")
    assert_type_limit(check_exe, tmp_path, source, "16:3")


def _pad_name(extra):
    # A13 expands to 40957 characters; `(A13,pad)` is that plus 3 and the pad.
    return "L" * (65536 - 40957 - 3 + extra)


def test_type_of_exactly_the_limit_is_accepted(check_exe, tmp_path):
    pad = _pad_name(0)
    source = (alias_chain(13, "({a}, {a})") + f"type B = (A13, {pad});\n"
              f"struct {pad} {{ }}\n" + OK_MAIN)
    path = _written(tmp_path, source)
    code, stderr = run_mort_bounded(check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr


def test_type_one_past_the_limit_is_rejected(check_exe, tmp_path):
    pad = _pad_name(1)
    source = (alias_chain(13, "({a}, {a})") + f"type B = (A13, {pad});\n"
              f"struct {pad} {{ }}\n" + OK_MAIN)
    assert_type_limit(check_exe, tmp_path, source, 15)


def test_many_distinct_large_types_exhaust_the_budget(check_exe, tmp_path):
    uses = "".join(f"type B{i} = (A12, A12, T{i});\n" for i in range(2000))
    source = alias_chain(12, "({a}, {a})") + uses + OK_MAIN
    assert_type_limit(check_exe, tmp_path, source, 1575, TYPE_WORK_EXCEEDED)


def _large_valid_aliases(count):
    return (alias_chain(12, "({a}, {a})")
            + "".join(f"type B{i} = (A12, A12, T{i});\n" for i in range(count))
            + "".join(f"struct T{i} {{ }}\n" for i in range(count)) + OK_MAIN)


def test_budget_boundary_is_exact(check_exe, tmp_path):
    """222 and 223 are where Python changes its answer, so every charge on the
    path (expansion, validation, re-resolution) must match."""
    assert assert_source_agrees(check_exe, tmp_path, _large_valid_aliases(222)) == "ok"
    path = _written(tmp_path, _large_valid_aliases(222))
    code, stderr = run_mort_bounded(check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr
    assert_type_limit(check_exe, tmp_path, _large_valid_aliases(223), 236, TYPE_WORK_EXCEEDED)


def test_budget_names_generic_instantiation_once_a_generic_is_built(check_exe, tmp_path):
    structs = "".join(f"struct T{i} {{ }}\n" for i in range(400))
    fields = ", ".join(f"f{i}: H<T{i}>" for i in range(400))
    source = (alias_chain(12, "({a}, {a})") + "struct H<T> { a: (T, A12, A12) }\n"
              + structs + f"struct S {{ {fields} }}\n" + OK_MAIN)
    assert_type_limit(check_exe, tmp_path, source, 13, GENERIC_WORK_EXCEEDED)


def test_aggregate_walk_charges_every_field(check_exe, tmp_path):
    """Validation is remembered, but each by-value field's dependencies are
    walked, and charged, every time: measured the same in Python."""
    fields = ", ".join(f"f{i}: A13" for i in range(2000))
    source = alias_chain(13, "({a}, {a})") + f"struct S {{ {fields} }}\n" + OK_MAIN
    assert_type_limit(check_exe, tmp_path, source, 14, TYPE_WORK_EXCEEDED)


def test_repeated_use_of_a_large_pointer_type_is_validated_once(check_exe, tmp_path):
    """A pointer's aggregate walk is free, so validating `*A13` per field would
    be the only cost: re-charging it spends the budget on a few fields."""
    fields = ", ".join(f"f{i}: *A13" for i in range(300))
    source = alias_chain(13, "({a}, {a})") + f"struct S {{ {fields} }}\n" + OK_MAIN
    path = _written(tmp_path, source)
    code, stderr = run_mort_bounded(check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 0, stderr


def test_failed_validation_is_not_remembered_as_valid(check_exe, tmp_path):
    """Only a true answer may be kept: `q.a` asks whether `q` is a type, and a
    remembered "no" must not make `q` usable afterwards."""
    source = ("struct P { a: i64 }\nfn main() -> int {\n  let q = P { a: 1 };\n  let v = q.a;\n"
              "  let r: q = null;\n  return 0;\n}\n")
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


def test_alias_chain_below_the_type_limit_agrees_with_python(check_exe, tmp_path):
    source = alias_chain(13, "({a}, {a})") + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "ok"


def test_nesting_at_the_limit_still_agrees_with_python(check_exe, tmp_path):
    depth = 200
    nested = "G<" * depth + "u8" + ">" * depth
    source = f"struct G<T> {{ x: *T }}\nstruct S {{ a: *{nested} }}\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "ok"


def mortc_check(tmp_path, source):
    """The real command-line compiler, at Python's own recursion limit."""
    path = _written(tmp_path, source)
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(path), "--check"],
        capture_output=True, text=True)
    return result.returncode, result.stderr.strip()


@pytest.mark.parametrize(
    "star,accepted", [("", 993), ("*", 495)], ids=["direct", "pointer"])
def test_alias_chain_limit_matches_mortc(check_exe, tmp_path, star, accepted):
    def chain(count):
        links = "".join(f"type A{i} = {star}A{i + 1};\n" for i in range(count))
        return links + f"type A{count} = u8;\n" + OK_MAIN

    for count, rejected in ((accepted, False), (accepted + 1, True), (1500, True)):
        source = chain(count)
        python_code, python_error = mortc_check(tmp_path, source)
        assert (python_code != 0) == rejected, python_error
        path = _written(tmp_path, source)
        code, stderr, _dump = run_mort(
            check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
        if rejected:
            assert code == 1, stderr
            assert python_error.splitlines()[0] == f"mortc: {stderr.decode('utf-8')}"
            assert stderr.decode("utf-8").endswith(
                ": error: source nesting exceeds the compiler safety limit")
        else:
            assert code == 0, stderr


def test_alias_depth_error_uses_the_first_token_column(check_exe, tmp_path):
    """A declaration has no column of its own, so the real compiler reports the
    column of the first token on its line."""
    links = "".join(f"type A{i} = A{i + 1};\n" for i in range(1500))
    source = "   " + links + "type A1500 = u8;\n" + OK_MAIN
    python_code, python_error = mortc_check(tmp_path, source)
    assert python_code != 0
    path = _written(tmp_path, source)
    code, stderr, _dump = run_mort(check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1, stderr
    expected = f"{path}:1:4: error: source nesting exceeds the compiler safety limit"
    assert python_error.splitlines()[0] == f"mortc: {expected}"
    assert stderr.decode("utf-8") == expected


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
    """Past the frame budget: both compilers stop in _apply_type_aliases."""
    chain = " + 1" * 4000
    source = f"fn main() -> int {{\nlet x = 1{chain};\nreturn 0;\n}}\n"
    outcome = assert_source_agrees(check_exe, tmp_path, source)
    assert outcome == "error"


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


def test_very_deep_chain_is_rejected_without_the_c_stack(check_exe, tmp_path):
    """Far past the frame budget, and beyond what the Python reference runs in
    reasonable time: located, without recursing over the chain."""
    operands = 300_000
    path = tmp_path / "input.mx"
    path.write_text(
        "fn main() -> int {\nlet a = 1;\nlet x = a" + " + a" * (operands - 1)
        + ";\nreturn 0;\n}\n", encoding="utf-8")
    code, stderr, _text = run_mort(
        check_exe, f"MODE hosted\nFILE {path}\n", tmp_path)
    assert code == 1
    assert b":3:" in stderr
    assert b"error: source nesting exceeds the compiler safety limit" in stderr


def test_multi_file_module_program(check_exe, tmp_path):
    (tmp_path / "lib.mx").write_text(
        "module util;\npub fn add(a: i64, b: i64) -> i64 { return a + b; }\n"
        "fn hidden() -> i64 { return 0; }\n", encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        "import lib as u;\nfn main() -> int { return u.add(1, 2); }\n",
        encoding="utf-8")
    assert compare(check_exe, main_path, "hosted", tmp_path) == "ok"


def test_test_block_calls_through_its_own_import_alias(check_exe, tmp_path):
    """Regression: with no function in the root file, the last function checked
    is the imported module's, whose aliases must not be used for the test block."""
    (tmp_path / "lib.mx").write_text(
        "module util;\npub fn add(a: i64, b: i64) -> i64 { return a + b; }\n",
        encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        'import lib as u;\ntest "a" { let x = u.add(1, 2); }\n', encoding="utf-8")
    assert compare(check_exe, main_path, "test", tmp_path) == "ok"


def test_private_function_taken_as_a_value_across_modules(check_exe, tmp_path):
    (tmp_path / "lib.mx").write_text(
        "module util;\nfn hidden() -> i64 { return 0; }\n", encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        "import lib as u;\nfn main() -> int { let f = u.hidden; return 0; }\n",
        encoding="utf-8")
    assert compare(check_exe, main_path, "hosted", tmp_path) == "error"


def test_public_function_taken_as_a_value_across_modules(check_exe, tmp_path):
    (tmp_path / "lib.mx").write_text(
        "module util;\npub fn shown() -> i64 { return 0; }\n", encoding="utf-8")
    main_path = tmp_path / "main.mx"
    main_path.write_text(
        "import lib as u;\nfn main() -> int { let f = u.shown; return 0; }\n",
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
    assert stderr.decode("utf-8") == expected.error
    assert expected.error.endswith(":1:1: error: source nesting exceeds the compiler safety limit")


def test_long_struct_chain_by_value(check_exe, tmp_path):
    """Long enough to pass the frame budget of the aggregate walk."""
    decls = "".join(f"struct S{i} {{ next: S{i + 1} }}\n" for i in range(3000))
    source = decls + "struct S3000 { x: u8 }\n" + OK_MAIN
    assert assert_source_agrees(check_exe, tmp_path, source) == "error"


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
