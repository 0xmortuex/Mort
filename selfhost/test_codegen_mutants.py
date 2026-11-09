"""Planted bugs in the Mort C generator: the differential must catch each one.

Each mutant patches one line of a copy of the generator, builds it, and runs
programs aimed at that line. The test passes only if Mort disagrees with the
Python generator on at least one of them, and the unmodified generator agrees on
the same programs (test_probes_agree_on_the_real_generator).

    python -m pytest selfhost/test_codegen_mutants.py -q
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

from test_codegen_differential import (  # noqa: E402,F401
    DECLARATIONS, PRELUDE, STATEMENTS, cg_exe, cg_forced_exe, compare,
    compare_with_effects,
)
import py_codegen_dump  # noqa: E402
from test_codegen_expr_differential import (  # noqa: E402
    CJK, assert_condition_texts_match, body, expression_program, replay_cjk_program,
    slice_while_chain,
)

MAIN = "fn main() -> int { return 0; }\n"
CONDITION_PROBE = "condition text"


def statement(name):
    return f"{PRELUDE}\nfn main() -> int {{\n{STATEMENTS[name]}\nreturn 0;\n}}\n"


def declaration(name):
    return f"{DECLARATIONS[name]}\n{MAIN}"


# Name: (file, old text, new text, probe programs, forced effects or None). An
# entry with forced effects is built and run through the test-only forced driver.
MUTANTS = {
    "type_tag_keeps_arrow": (
        "codegen.mx",
        '    b = cg_step(&b, "->", "_to_");\n',
        "",
        [declaration("tag_of_function_type")],
    ),
    "const_pointer_spelled_wrong": (
        "codegen.mx",
        'buf_push_z(&b, " const*");',
        'buf_push_z(&b, "const*");',
        [statement("let_pointer_const"), f"const G: *const *u8 = null;\n{MAIN}"],
    ),
    "tuples_sorted_by_text_only": (
        "codegen.mx",
        "    cg_sort(g, &(*g).tuple_types, true);\n",
        "    cg_sort(g, &(*g).tuple_types, false);\n",
        ["struct S { a: (i64, (u8, bool)), b: (bool, i64), c: (a, (b, c)) }\n"
         "struct a { x: i64 }\nstruct b { x: i64 }\nstruct c { x: i64 }\n" + MAIN],
    ),
    "definition_order_ignores_dependencies": (
        "codegen.mx",
        "                if !map_has(&emitted, u64vec_get(&(*a).deps, j)) {\n"
        "                    all = false;\n"
        "                }\n",
        "",
        [declaration("struct_nested_order"), declaration("struct_tuple_dep")],
    ),
    "last_enum_variant_gets_comma": (
        "codegen.mx",
        "        if i + 1 < ck_items_len(variants) {\n",
        "        if i + 1 <= ck_items_len(variants) {\n",
        [declaration("enum_plain")],
    ),
    "payload_enum_keeps_plain_tag_name": (
        "codegen.mx",
        '    if has_payload {\n        buf_push_z(&tag, "_tag");\n    }\n',
        "",
        [declaration("enum_payload_tuple")],
    ),
    "break_runs_every_defer": (
        "cg_stmt.mx",
        "        cg_emit_defers(g, u64vec_get(&(*g).loop_defer_bases, (*g).loop_defer_bases.len - 1));\n",
        "        cg_emit_defers(g, 0);\n",
        [statement("defer_on_break"),
         f"fn g() -> i64 {{ return 1; }}\nfn main() -> int {{\ndefer g();\nwhile true {{ break; }}\nreturn 0;\n}}\n"],
    ),
    "inclusive_for_compares_with_less": (
        "cg_stmt.mx",
        '        buf_push_z(&l5, " != ");\n',
        '        buf_push_z(&l5, " < ");\n',
        [statement("for_inclusive")],
    ),
    "exhaustive_match_keeps_its_condition": (
        "cg_stmt.mx",
        "        if (*arm).a == (0 as *Node) || (exhaustive && index == count - 1) {\n",
        "        if (*arm).a == (0 as *Node) {\n",
        [statement("match_enum")],
    ),
    "array_drop_index_off_by_one": (
        "codegen.mx",
        'span_z("&(*value)[i - 1]")',
        'span_z("&(*value)[i]")',
        [declaration("drop_array")],
    ),
    # Expression text never has the `(a)(b)` shape, so this is probed on its text rule.
    "condition_strips_parens_of_call": (
        "cg_cond.mx",
        "                    if i == s.len - 1 {\n",
        "                    if true {\n",
        CONDITION_PROBE,
    ),
    "short_circuit_or_loses_negation": (
        "cg_stmt.mx",
        "        if cx_op_is(c, op, \"||\") {\n            buf_push(&open, '!');\n        }\n",
        "",
        [declaration("try_short_circuit")],
    ),
    "signed_shift_forgets_its_wrap": (
        "cg_helpers.mx",
        "        if cg_has_int(g, CG_OP_SHL, at) || cg_has_int(g, CG_OP_SHR, at) {\n"
        "            cg_use_int(g, CG_OP_WRAP, at);\n"
        "        }\n",
        "",
        # The real generator's effects are already closed over shifts, so the
        # request must come from a set holding only the shift.
        [MAIN],
        py_codegen_dump.Effects(ints=[("shl", "i32")]),
    ),
    "slice_found_only_in_declared_types": (
        "codegen.mx",
        "    cg_note_type(g, (*n).etype);\n",
        "",
        [f"{PRELUDE}\nfn main() -> int {{\nlet u: *u8 = null;\nslice<u8>(u, 1);\nreturn 0;\n}}\n"],
    ),
    "replay_charges_half_per_node": (
        "cg_stmt.mx",
        "const CG_REPLAY_UNITS_PER_NODE: u64 = 32;",
        "const CG_REPLAY_UNITS_PER_NODE: u64 = 16;",
        ["fn g() -> i64 { return 1; }\nfn main() -> int {\n"
         + "defer g();\n" * 3000 + "if true { return 1; }\n" * 3000 + "return 0;\n}\n"],
    ),
    "float_cast_range_wrong": (
        "cg_helpers.mx",
        '"value > -32769.0 && value < 32768.0"',
        '"value > -32768.0 && value < 32768.0"',
        [MAIN],
        py_codegen_dump.Effects(ints=[("float_cast", "i16")]),
    ),
    "stdlib_not_included_for_bounds": (
        "codegen.mx",
        "                || cg_flag(g, CgFlag.BOUNDS) || cg_flag(g, CgFlag.ARGS)\n",
        "                || cg_flag(g, CgFlag.ARGS)\n",
        [MAIN],
        py_codegen_dump.Effects(flags=["used_bounds"]),
    ),
}


def expr(name):
    return expression_program(name)


def rep(text):
    return body(text)


# Expression kinds, generics, pattern matching, moves, defers, integers and floats.
EXPRESSION_MUTANTS = {
    # literals and integer helpers
    "int_above_limit_by_length_only": (
        "cg_expr.mx", "            return digits.data[i] > limit[i];\n",
        "            return digits.data[i] < limit[i];\n",
        [rep("let a: i64 = 1000000000000000000;")]),
    "int_at_the_limit_counts_as_above": (
        "cg_expr.mx", "            return digits.data[i] > limit[i];\n        }\n    }\n    return false;\n",
        "            return digits.data[i] > limit[i];\n        }\n    }\n    return true;\n",
        [rep("let a: i64 = 9223372036854775807;")]),
    "int_literal_suffix_boundary": (
        "cg_expr.mx", "        if bn_mag_cmp(value, (*g).i32_max) > 0 {\n",
        "        if bn_mag_cmp(value, (*g).i32_max) >= 0 {\n",
        [rep("let a: i64 = 2147483647 - 1 + 1;")]),
    "int64_min_pattern_lost": (
        "cg_expr.mx", "            let pattern = bn_add(value, (*g).two64);\n",
        "            let pattern = bn_copy(value);\n",
        [rep("let d: i64 = -9223372036854775807 - 1;")]),
    "unary_negate_becomes_complement": (
        "cg_expr.mx", "            cg_fixed_unary(g, at, span_eq_z(op, \"-\"), t);\n",
        "            cg_fixed_unary(g, at, false, t);\n", [expr("unary_i32")]),
    "signed_shift_count_unchecked": (
        "cg_expr.mx", "    let signed_count = cg_is_signed_count(g, right_type);\n",
        "    let signed_count = false;\n", [expr("binary_shift_i32")]),
    "div_uses_rem_helper_name": (
        "cg_expr.mx", '            buf_push_z(p, "div_");\n', '            buf_push_z(p, "rem_");\n',
        [expr("binary_arith_i64")]),
    "pointer_cast_loses_uintptr": (
        "cg_expr.mx", '    if ts_starts_with(source_type, "*") {\n', "    if false {\n",
        [expr("casts_pointer")]),
    # floats
    "float_cast_helper_misnamed": (
        "cg_expr.mx", 'buf_push_z(p, "mort_float_to_");', 'buf_push_z(p, "mort_float_");',
        [expr("casts_from_float")]),
    "float_whole_number_keeps_no_point": (
        "cg_expr.mx", "    if plain {\n", "    if !plain {\n", [rep("let a: f64 = 100.0;")]),
    "f32_suffix_on_f64": (
        "cg_expr.mx", 'span_eq_z(cg_text(g, (*e).etype), "f32")',
        'span_eq_z(cg_text(g, (*e).etype), "f64")', [expr("casts_from_f32")]),
    "g17_rounds_ties_up": (
        "cg_float.mx", "    if doubled > 0 || (doubled == 0 && (digits.data[16] % 2) == 1) {\n",
        "    if doubled >= 0 {\n", [rep("let a: f64 = 450359962737049.125;")]),
    # expression kinds
    "index_generates_object_first": (
        "cg_expr.mx", "            let index = cg_gx(g, (*e).b);\n",
        "            let index = tx_new(4);\n"
        "            tx_free(cg_gx(g, (*e).a));\n"
        "            tx_app_txt(index, cg_gx(g, (*e).b));\n",
        [expr("index_evaluates_index_first")]),
    "unprepared_slice_index_keeps_one_object": (
        "cg_expr.mx", "        tx_app_span(t, obj);\n", '        tx_app_z(t, "m_other");\n',
        [expr("index_slice_while_unprepared")]),
    "prepared_slice_index_ignores_temporary": (
        "cg_expr.mx",
        "            if ts_is_slice((*g).s, (*(*e).a).etype) && (*e).num != 0 {\n",
        "            if false {\n", [expr("index_slice")]),
    "constant_array_index_gets_bounds": (
        "cg_expr.mx",
        "    if !ts_is_array((*g).s, obj_type) || (*g).freestanding || (*(*e).b).cv != (0 as *void) {\n",
        "    if !ts_is_array((*g).s, obj_type) || (*g).freestanding {\n", [expr("index_array")]),
    "array_repeat_zero_boundary": (
        "cg_expr.mx", "    if count > CG_ZERO_REPEAT_MIN && cg_is_zero_element(vs) {\n",
        "    if count >= CG_ZERO_REPEAT_MIN && cg_is_zero_element(vs) {\n", [expr("array_repeats")]),
    "big_repeat_separator_loses_space": (
        "cg_expr.mx", '            cw_span(w, span_z(", "));\n', '            cw_span(w, span_z(","));\n',
        [rep("let a: [u8; 500000] = [7; 500000];")]),
    "slice_len_field_not_renamed": (
        "cg_expr.mx", '        if span_eq_z(field, "len") {\n', "        if false {\n",
        [expr("index_slice_field_chain")]),
    # generics and calls
    "generic_struct_literal_tag_not_mangled": (
        "cg_expr.mx",
        "    let tag = cg_text(g, cg_type_tag(g, (*e).name));\n"
        "    let p = cg_scratch_reset(g);\n"
        '    buf_push_z(p, "(struct mort_");\n',
        "    let tag = cg_text(g, (*e).name);\n"
        "    let p = cg_scratch_reset(g);\n"
        '    buf_push_z(p, "(struct mort_");\n', [expr("generic_values")]),
    "generic_instance_call_not_mangled": (
        "cg_expr.mx",
        '        buf_push_z(p, "mort_");\n        cg_sym(g, p, resolved);\n',
        '        buf_push_z(p, "mort_");\n        buf_push_span(p, cg_text(g, resolved));\n',
        [expr("generic_values")]),
    "indirect_call_loses_m_prefix": (
        "cg_expr.mx",
        '        buf_push_z(p, "m_");\n        buf_push_span(p, name_text);\n        buf_push(p, \'(\');\n',
        "        buf_push_span(p, name_text);\n        buf_push(p, '(');\n",
        [expr("fn_pointer")]),
    # pattern matching and enums
    "enum_payload_field_prefix_lost": (
        "cg_expr.mx", '    buf_push_z(p, ", .data.v_");\n', '    buf_push_z(p, ", .data.");\n',
        [expr("enum_payloads")]),
    "tuple_payload_binding_loses_field": (
        "cg_stmt.mx", "        if (*f).payload_arity > 1 {\n", "        if false {\n",
        [expr("enum_tuple_payload")]),
    "unit_enum_constant_case": (
        "cg_expr.mx", '        tx_app_z(t, "MORT_");\n        tx_app_span(t, tag);\n        tx_app_z(t, "_");\n        tx_app_span(t, field);\n    }\n    return t;\n',
        '        tx_app_z(t, "mort_");\n        tx_app_span(t, tag);\n        tx_app_z(t, "_");\n        tx_app_span(t, field);\n    }\n    return t;\n',
        [expr("enum_compare")]),
    # moves and defers
    "move_leaves_source_live": (
        "cg_expr.mx", '    buf_push_z(p, " = false), ");\n', '    buf_push_z(p, " = true), ");\n',
        [expr("move_resource")]),
    "plain_move_treated_as_resource": (
        "cg_expr.mx", "        if f != (0 as *Facts) && !(*f).is_resource_move {\n",
        "        if false {\n", [expr("move_plain")]),
    "deferred_destroy_clears_liveness": (
        "cg_expr.mx", "    if f == (0 as *Facts) || (*f).destroys_binding == NO_TEXT || (*f).deferred_destroy {\n",
        "    if f == (0 as *Facts) || (*f).destroys_binding == NO_TEXT {\n",
        [expr("destroy_deferred")]),
    "defer_destroy_keeps_scope_drop": (
        "cg_stmt.mx", "                if !((*item).is_resource && (*item).name == binding) {\n",
        "                if true {\n", [expr("destroy_deferred")]),
    "replay_charge_ignores_big_repeats": (
        "cg_stmt.mx",
        "            let text = cg_true_len(g, ts_sub(buf_span((*g).cur), first_len, (*(*g).cur).len))\n"
        "                - ((*g).emitted - first_line);\n",
        "            let text = (*(*g).cur).len - first_len - (*g).emitted + first_line;\n"
        "            text = 0;\n",
        ["fn g(v: i64) -> i64 { return v; }\nfn main() -> int {\n" + "defer g(1);\n" * 2000
         + "if true { return 1; }\n" * 2000 + "return 0;\n}\n"]),
    "replay_charge_counts_bytes": (
        "cg_expr.mx",
        "            if (text.data[i] & 0xC0) != 0x80 {\n                total += 1;\n            }\n",
        "            total += 1;\n",
        [replay_cjk_program()]),
    # const arrays of function pointers keep the const on the elements.
    "const_fn_array_loses_const": (
        "codegen.mx",
        "            cg_var_decl(g, out, elem, buf_span(&inner), 0 as *Span, binding_const);\n",
        "            cg_var_decl(g, out, elem, buf_span(&inner), 0 as *Span, false);\n",
        [statement("const_array_of_fn"), declaration("global_const_array_of_fn")]),
    # Doubled work is charged by what is really generated.
    "second_generation_always_reused": (
        "cg_expr.mx", "    if first.reusable {\n", "    if true {\n",
        [expr("slice_strings_generated_twice")]),
    "shared_token_written_as_a_repeat": (
        "cg_expr.mx",
        "    if (*big).kind == CG_BIG_SHARED {\n        cw_text(w, g, (*big).value);\n        return;\n    }\n",
        "", [slice_while_chain(18)]),
    "type_charges_count_bytes": (
        "typestr.mx", "        if (t.data[i] & 0xC0) != 0x80 {\n", "        if true {\n",
        ["struct " + CJK * 2000 + " { x: i64 }\nfn id<T>(x: T) -> T { return x; }\n"
         "fn main() -> int { let a = id(" + CJK * 2000 + " { x: 1 }); return 0; }\n"]),
    # What an independent mutation pass found the probes did not reach.
    "try_runs_only_its_own_scope_defers": (
        "cg_stmt.mx",
        "    (*g).indent += 1;\n    cg_emit_defers(g, 0);\n    let ret = buf_new(128);\n",
        "    (*g).indent += 1;\n    cg_emit_defers(g, (*g).defer_scopes.len - 1);\n    let ret = buf_new(128);\n",
        [declaration("try_in_nested_scope_runs_outer_defers")]),
    "wildcard_binding_uses_position": (
        "cg_stmt.mx", "            buf_push_u64(&payload, u64vec_get((*f).binding_indices, i));\n",
        "            buf_push_u64(&payload, i);\n", [expr("enum_wildcard_binding")]),
    "folded_i32_max_gets_ll": (
        "codegen.mx", "bn_from_u64(0x7FFFFFFF)", "bn_from_u64(0x7FFFFFFE)", [expr("int_fold_edges")]),
    "folded_i64_max_gets_ull": (
        "codegen.mx", "i64_max: bn_from_u64(0x7FFFFFFFFFFFFFFF)", "i64_max: bn_from_u64(0x7FFFFFFFFFFFFFFE)",
        [expr("int_fold_edges")]),
    "folded_i64_min_keeps_ll": (
        "cg_expr.mx", "        if bn_is_negative(value) {\n            let pattern",
        "        if false {\n            let pattern", [expr("int_fold_edges")]),
    # Helper marks reached only from a compound assignment: real-run flags no longer
    # hide them, because nothing tells the generator which helpers a program uses.
    "compound_assign_loses_wrap_mark": (
        "cg_expr.mx", "        cg_use_int(g, CG_OP_WRAP, at);\n", "",
        [rep("let a: i64 = 1;\na += 2;")]),
    "compound_assign_loses_div_mark": (
        "cg_expr.mx", "            cg_use_int(g, CG_OP_DIV, at);\n", "",
        [rep("let a: i64 = 1;\na /= 2;")]),
    "compound_assign_loses_rem_mark": (
        "cg_expr.mx", "            cg_use_int(g, CG_OP_REM, at);\n", "",
        [rep("let a: i64 = 1;\na %= 2;")]),
    "compound_assign_loses_shl_mark": (
        "cg_expr.mx", "        cg_use_int(g, CG_OP_SHL, at);\n", "",
        [rep("let a: u8 = 1;\nlet n: u32 = 1;\na <<= n;")]),
    "compound_assign_loses_shr_mark": (
        "cg_expr.mx", "        cg_use_int(g, CG_OP_SHR, at);\n", "",
        [rep("let a: u8 = 1;\nlet n: u32 = 1;\na >>= n;")]),
    "compound_assign_loses_shift_count_mark": (
        "cg_expr.mx", "        (*g).used_shift_count = true;\n", "",
        [rep("let a: i64 = 1;\nlet n: i32 = 2;\na <<= n;")]),
}

MUTANTS.update(EXPRESSION_MUTANTS)


def condition_disagreements(exe, tmp_path):
    try:
        assert_condition_texts_match(exe, tmp_path)
    except AssertionError as error:
        return [str(error).splitlines()[0]]
    return []


def probe_disagreements(exe, entry, tmp_path):
    probes = entry[3]
    if probes is CONDITION_PROBE:
        return condition_disagreements(exe, tmp_path)
    effects = entry[4] if len(entry) > 4 else None
    failures = []
    for index, source in enumerate(probes):
        try:
            if effects is not None:
                compare_with_effects(exe, tmp_path, effects, "hosted")
            else:
                path = tmp_path / f"probe{index}.mx"
                path.write_bytes(source.encode("utf-8"))
                compare(exe, path, "hosted", tmp_path)
        except AssertionError as error:
            failures.append(str(error).splitlines()[0])
    return failures


def build_mutant(tmp_path, filename, old, new, driver="cg_main"):
    source = tmp_path / "src"
    source.mkdir()
    for path in HERE.glob("*.mx"):
        shutil.copy(path, source / path.name)
    target = source / filename
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.count(old) == 1, f"{filename}: pattern occurs {text.count(old)} times: {old!r}"
    target.write_bytes(text.replace(old, new).encode("utf-8"))
    exe = tmp_path / "mutant.exe"
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(source / f"{driver}.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, f"mutant does not compile:\n{result.stdout}\n{result.stderr}"
    return exe


def test_probes_agree_on_the_real_generator(cg_exe, cg_forced_exe, tmp_path):
    for name, entry in MUTANTS.items():
        if entry[3] is CONDITION_PROBE:
            continue
        exe = cg_forced_exe if len(entry) > 4 else cg_exe
        assert not probe_disagreements(exe, entry, tmp_path), name


def test_there_are_enough_mutants():
    assert len(MUTANTS) >= 6
    assert len(EXPRESSION_MUTANTS) >= 10


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_caught(tmp_path, name):
    entry = MUTANTS[name]
    driver = "cg_forced_main" if len(entry) > 4 else "cg_main"
    if entry[3] is CONDITION_PROBE:
        driver = "cond_strip_main"
    exe = build_mutant(tmp_path, entry[0], entry[1], entry[2], driver)
    failures = probe_disagreements(exe, entry, tmp_path)
    assert failures, f"mutant {name} survived its probes"
