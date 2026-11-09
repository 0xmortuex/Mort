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

MAIN = "fn main() -> int { return 0; }\n"


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
    "condition_strips_parens_of_call": (
        "cg_stmt.mx",
        "                    if i == s.len - 1 {\n",
        "                    if true {\n",
        [f"fn t() -> bool {{ return true; }}\nfn main() -> int {{ if t() {{ }} return 0; }}\n"],
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


def probe_disagreements(exe, entry, tmp_path):
    probes = entry[3]
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
        exe = cg_forced_exe if len(entry) > 4 else cg_exe
        assert not probe_disagreements(exe, entry, tmp_path), name


def test_there_are_enough_mutants():
    assert len(MUTANTS) >= 6


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_caught(tmp_path, name):
    entry = MUTANTS[name]
    driver = "cg_forced_main" if len(entry) > 4 else "cg_main"
    exe = build_mutant(tmp_path, entry[0], entry[1], entry[2], driver)
    failures = probe_disagreements(exe, entry, tmp_path)
    assert failures, f"mutant {name} survived its probes"
