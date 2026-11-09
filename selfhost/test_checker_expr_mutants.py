"""Planted bugs in the Mort type checker: the differential must catch each one.

Each mutant patches one line of a copy of the checker, builds it, and runs
programs aimed at that line. The test passes only if Mort disagrees with the
Python checker on at least one of them, and the unmodified checker agrees on
the same programs (test_probes_agree_on_the_real_checker).

    python -m pytest selfhost/test_checker_expr_mutants.py -q
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from test_checker_decl_differential import OK_MAIN, check_exe  # noqa: E402,F401
from test_checker_expr_differential import (  # noqa: E402
    EXPR_CASES, FN_ONLY_CASES, LIBRARY, MODULE_CASES, compare, full_body,
)


def named(*names):
    return [full_body(EXPR_CASES[name]) for name in names]


def fn_level(*names):
    return [FN_ONLY_CASES[name] + "\n" + OK_MAIN for name in names]


def module(*names):
    """A probe of two files: (library source, root source)."""
    return [(LIBRARY, MODULE_CASES[name]) for name in names]


MUTANTS = {
    "generics_for_annotation_not_substituted": (
        "check_call.mx",
        "    } else if (kind == NodeKind.LET || kind == NodeKind.FOR) && (*n).ty != NO_TEXT {\n        (*n).ty = ck_substitute",
        "    } else if kind == NodeKind.LET && (*n).ty != NO_TEXT {\n        (*n).ty = ck_substitute",
        named("gen_for_typed", "gen_for_typed_bad", "gen_for_typed_range"),
    ),
    "modules_private_function_usable_as_value": (
        "check_expr.mx",
        "            if !(*decl).flag || (*decl).kind == NodeKind.EXTERN_FN_DECL {",
        "            if false {",
        module("private_value"),
    ),
    "returns_match_counted_from_first_arm": (
        "check_expr.mx",
        "            for j: u64 in 0..ck_items_len(arms) {\n                if !ck_block_always_returns",
        "            for j: u64 in 0..1 {\n                if !ck_block_always_returns",
        fn_level("ret_match_first_arm_only", "ret_match_middle_arm_open"),
    ),
    "folding_shift_cap_starts_at_64": (
        "bignum.mx",
        "    if b.len > 1 || bn_mag_u64(b) > 64 {",
        "    if b.len > 1 || bn_mag_u64(b) >= 64 {",
        named("shift_count_64", "shift_cap_observable"),
    ),
    "folding_shift_cap_starts_at_66": (
        "bignum.mx",
        "    if b.len > 1 || bn_mag_u64(b) > 64 {",
        "    if b.len > 1 || bn_mag_u64(b) > 65 {",
        named("shift_cap_observable", "shift_count_65"),
    ),
    "folding_shift_cap_removed": (
        "bignum.mx",
        "    if b.len > 1 || bn_mag_u64(b) > 64 {",
        "    if b.len > 1 {",
        named("shift_cap_observable", "shift_count_66"),
    ),
    "ranges_c_long_is_64_bit": (
        "check_expr.mx",
        '    cx_add_range(c, "c_long", "-2147483648", "2147483647");',
        '    cx_add_range(c, "c_long", "-9223372036854775808", "9223372036854775807");',
        named("range_c_long_over", "range_c_long_under"),
    ),
    "floats_f32_boundary_excluded": (
        "check_expr.mx",
        "((*e).num & 0x7FFFFFFFFFFFFFFF) > 0x47EFFFFFE0000000",
        "((*e).num & 0x7FFFFFFFFFFFFFFF) >= 0x47EFFFFFE0000000",
        named("f32_max_fits"),
    ),
    "floats_f32_limit_one_step_up": (
        "check_expr.mx",
        "((*e).num & 0x7FFFFFFFFFFFFFFF) > 0x47EFFFFFE0000000",
        "((*e).num & 0x7FFFFFFFFFFFFFFF) > 0x47EFFFFFE0000001",
        named("f32_next_double_over"),
    ),
    "inference_literal_operand_not_retagged": (
        "check_expr.mx",
        "        cx_retag_literal(c, right, lt);\n        return lt;",
        "        return lt;",
        named("bin_unify_lit_right", "bin_unify_lit_right_overflow"),
    ),
    "inference_const_pointer_ignores_pointee": (
        "check_expr.mx",
        "            && ck_pointee(c, expected) == ck_pointee(c, et) {",
        "            && true {",
        [full_body("let a: *u8 = null; let b: *const i64 = a;")],
    ),
    "generics_conflicting_inference_accepted": (
        "check_call.mx",
        "        return previous == actual;",
        "        return true;",
        named("gen_conflict", "gen_literal_conflict"),
    ),
    "generics_cast_not_substituted": (
        "check_call.mx",
        "    if kind == NodeKind.FN_DECL || kind == NodeKind.PARAM || kind == NodeKind.CAST {",
        "    if kind == NodeKind.FN_DECL || kind == NodeKind.PARAM {",
        ["fn cast_to<T>(a: i64) -> T { return a as T; }\n"
         "fn main() -> int { let b = cast_to<u8>(1); return 0; }\n"],
    ),
    "generics_instance_bodies_not_checked": (
        "checker.mx",
        "        if ck_names_len((*f).names) != 0 {\n            continue;\n        }\n        ck_function_body(c, f);",
        "        if ck_names_len((*f).names) != 0 || ts_find(intern_text((*c).s, (*f).symbol), '<') != intern_text((*c).s, (*f).symbol).len {\n            continue;\n        }\n        ck_function_body(c, f);",
        named("gen_body_error", "gen_body_error_instance_only"),
    ),
    "exhaustiveness_single_missing_variant_allowed": (
        "check_expr.mx",
        "        if missing.len != 0 && !wildcard_seen {",
        "        if missing.len > 1 && !wildcard_seen {",
        named("match_missing", "match_payload_missing_arm"),
    ),
    "exhaustiveness_missing_names_unsorted": (
        "check_expr.mx",
        "            cx_sort_texts(c, &missing);\n",
        "",
        ["enum Z1 { C, A, B }\nfn f(z: Z1) { match z { Z1.B => { } } }\n" + OK_MAIN],
    ),
    "exhaustiveness_flag_wrong_for_non_enums": (
        "check_expr.mx",
        "        (*facts).exhaustive = wildcard_seen;",
        "        (*facts).exhaustive = true;",
        named("match_int_no_wildcard") + fn_level("ret_match_open"),
    ),
    "moves_move_does_not_consume": (
        "check_expr.mx",
        "        (*b).bused = true;\n        cx_mark_moved(c, b);\n    }\n    ck_ev_head(c, \"move\", name);",
        "        (*b).bused = true;\n    }\n    ck_ev_head(c, \"move\", name);",
        named("move_use_after", "move_twice"),
    ),
    "moves_else_branch_not_merged": (
        "check_expr.mx",
        "    if !else_returns {\n        ptrvec_push(&live, after_else as *void);\n    }",
        "",
        named("move_in_else_branch", "move_in_else_if"),
    ),
    "moves_returning_branch_leaks_its_moves": (
        "check_expr.mx",
        "    if !then_returns {\n        ptrvec_push(&live, after_then as *void);\n    }",
        "    ptrvec_push(&live, after_then as *void);",
        named("move_in_returning_branch"),
    ),
    "moves_repeating_loop_check_removed": (
        "check_expr.mx",
        "        if (*c).loop_depth != 0 && (*b).bloop < (*c).loop_depth {",
        "        if false {",
        named("move_in_loop", "move_in_for"),
    ),
    "moves_deferred_destroy_consumes": (
        "check_expr.mx",
        "        (*c).checking_defer = true;",
        "        (*c).checking_defer = false;",
        named("destroy_in_defer"),
    ),
    "folding_shift_left_becomes_right": (
        "check_expr.mx",
        'if cx_op_is(c, op, "<<") { *out = BnOp.SHL; return true; }',
        'if cx_op_is(c, op, "<<") { *out = BnOp.SHR; return true; }',
        named("bin_shift", "lit_expr_adopt") + [full_body("let a: u8 = 1 << 3;")],
    ),
    "folding_negation_dropped": (
        "check_expr.mx",
        "(*e).cv = cx_box_big(bn_neg(*cx_big(operand)));",
        "(*e).cv = (*operand).cv;",
        named("lit_neg_u8", "lit_i8_min"),
    ),
    "folding_range_upper_bound_off_by_one": (
        "check_expr.mx",
        "    if bn_cmp(value, (*range).low) < 0 || bn_cmp(value, (*range).high) > 0 {",
        "    if bn_cmp(value, (*range).low) < 0 || bn_cmp(value, (*range).high) >= 0 {",
        [full_body("let a: u8 = 255;")],
    ),
    "folding_division_by_constant_zero_allowed": (
        "check_expr.mx",
        "                && (*right).cv != (0 as *void) && bn_is_zero(*cx_big(right)) {",
        "                && false {",
        named("bin_div_zero", "bin_mod_zero"),
    ),
    "returns_nested_loop_break_counts": (
        "check_expr.mx",
        "        if kind == NodeKind.WHILE || kind == NodeKind.FOR {\n            continue;\n        }\n        if kind == NodeKind.BLOCK {\n            if ck_block_breaks",
        "        if kind == NodeKind.WHILE {\n            if ck_block_breaks(c, (*st).b) {\n                return true;\n            }\n            continue;\n        }\n        if kind == NodeKind.BLOCK {\n            if ck_block_breaks",
        fn_level("ret_loop_break_nested"),
    ),
}


def probe_disagreements(exe, probes, tmp_path):
    failures = []
    for index, source in enumerate(probes):
        path = tmp_path / f"probe{index}.mx"
        if isinstance(source, tuple):
            (tmp_path / "lib.mx").write_bytes(source[0].encode("utf-8"))
            source = source[1]
        path.write_bytes(source.encode("utf-8"))
        try:
            compare(exe, path, "hosted", tmp_path)
        except AssertionError as error:
            failures.append(str(error).splitlines()[0])
    return failures


def build_mutant(tmp_path, filename, old, new):
    source = tmp_path / "src"
    source.mkdir()
    for path in HERE.glob("*.mx"):
        shutil.copy(path, source / path.name)
    target = source / filename
    text = target.read_text(encoding="ascii").replace("\r\n", "\n")
    assert text.count(old) == 1, f"{filename}: pattern occurs {text.count(old)} times: {old!r}"
    target.write_bytes(text.replace(old, new).encode("ascii"))
    exe = tmp_path / "mutant.exe"
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(source / "check_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, f"mutant does not compile:\n{result.stdout}\n{result.stderr}"
    return exe


def test_probes_agree_on_the_real_checker(check_exe, tmp_path):
    for name, (_file, _old, _new, probes) in MUTANTS.items():
        assert not probe_disagreements(check_exe, probes, tmp_path), name


def test_there_are_enough_mutants():
    assert len(MUTANTS) >= 8


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_caught(tmp_path, name):
    filename, old, new, probes = MUTANTS[name]
    exe = build_mutant(tmp_path, filename, old, new)
    failures = probe_disagreements(exe, probes, tmp_path)
    assert failures, f"mutant {name} survived its probes"
