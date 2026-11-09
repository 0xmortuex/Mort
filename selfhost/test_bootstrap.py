"""The self-hosting proof (PLAN.md, T8 and T9).

Run from the repository root:

    python -m pytest selfhost/test_bootstrap.py -q

One session fixture runs selfhost/bootstrap.py from a clean directory, which is
the real thing: the Python compiler builds stage-1, stage-1 compiles the compiler
to C and stage-2 is built from that C, then stage-2 compiles the compiler again.
Everything below is checked against the stage-2 binary that run produced, with
the Python compiler as the reference for every expected byte.
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402
import check_subset  # noqa: E402
import corpus  # noqa: E402
import gen_subset_programs as gen  # noqa: E402
import mortc  # noqa: E402
from mort.errors import MortError  # noqa: E402
from test_lexer_differential import (  # noqa: E402
    SUBSET_ACCEPTED, SUBSET_SCOPE_ACCEPTED, SUBSET_SCOPE_VIOLATIONS, SUBSET_VIOLATIONS,
    repo_mx_files,
)

COMPILER_ROOT = HERE / "mortc_main.mx"
GATE_TEXT = "is outside the bootstrap subset"


def run_bootstrap(work_dir, *extra, root=ROOT, env=None, cwd=None, timeout=900):
    summary = Path(work_dir).parent / (Path(work_dir).name + "-summary.json")
    result = subprocess.run(
        [sys.executable, str(root / "selfhost" / "bootstrap.py"),
         "--work-dir", str(work_dir), "--json", str(summary), *extra],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    data = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else None
    return result, data


@pytest.fixture(scope="session")
def stages(tmp_path_factory):
    work = tmp_path_factory.mktemp("bootstrap") / "stages"
    result, data = run_bootstrap(work)
    assert result.returncode == 0, result.stdout + result.stderr
    return {"work": work, "summary": data, "stdout": result.stdout}


@pytest.fixture(scope="session")
def stage2(stages):
    return Path(stages["summary"]["stages"]["B2"]["path"])


def stage_c(stages, name):
    return Path(stages["summary"]["stages"][name]["path"]).read_bytes()


def run_stage(exe, *args, cwd=None, env=None, timeout=120):
    result = subprocess.run(
        [str(exe), *map(str, args)], capture_output=True, timeout=timeout, cwd=cwd, env=env)
    return result.returncode, result.stdout, result.stderr.replace(b"\r\n", b"\n").rstrip(b"\n")


def python_outcome(files, freestanding=False):
    """What the Python compiler does with these roots, as mortc.py reports it."""
    try:
        text = mortc.compile_files_to_c(
            [str(path) for path in files], freestanding=freestanding)
    except MortError as error:
        return "reject", f"mortc: {error.render()}".encode("utf-8")
    return "ok", text.encode("utf-8")


def stage2_outcome(exe, files, tmp_path, freestanding=False, cwd=None, std_dir=ROOT / "std",
                   gate=True):
    out = tmp_path / "stage2-out.c"
    if out.exists():
        out.unlink()
    flags = ["--freestanding"] if freestanding else []
    if not gate:
        flags.append("--no-subset-gate")
    if std_dir is not None:
        flags += ["--std-dir", str(std_dir)]
    code, _, stderr = run_stage(exe, *flags, "-o", out, *files, cwd=cwd)
    return code, stderr, out.read_bytes() if out.exists() else None


def assert_matches_python(exe, files, tmp_path, freestanding=False, cwd=None, gate=True):
    """Stage-2 and Python must accept with equal C, or reject with equal text."""
    kind, expected = python_outcome(files, freestanding)
    code, stderr, produced = stage2_outcome(exe, files, tmp_path, freestanding, cwd, gate=gate)
    if kind == "reject":
        assert code == 1, f"stage-2 exit {code}; python: {expected!r}; stderr: {stderr!r}"
        assert stderr == expected
        return "reject"
    assert code == 0, f"stage-2 rejected what Python accepted: {stderr!r}"
    assert produced == expected, "C differs"
    return "ok"


def write_tree(directory, tree):
    for relative, text in tree.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        path.write_bytes(data)


MAIN = "fn main() -> int { return 0; }\n"

# ----- the proof itself -----


def test_fixpoint_reached(stages):
    summary = stages["summary"]
    assert summary["fixpoint"] == "identical"
    assert summary["python_equal"] == "identical"
    c0, c1, c2 = stage_c(stages, "C0"), stage_c(stages, "C1"), stage_c(stages, "C2")
    assert c1 == c2
    assert c0 == c1
    assert len(c2) > 500_000
    assert c2.count(b"\r") == 0


def test_summary_records_real_hashes_and_who_built_what(stages):
    stage = stages["summary"]["stages"]
    import hashlib
    for name in ("C0", "C1", "C2"):
        data = Path(stage[name]["path"]).read_bytes()
        assert stage[name]["sha256"] == hashlib.sha256(data).hexdigest()
        assert stage[name]["bytes"] == len(data)
    # Each C after C0 came from executing the binary before it.
    assert stage["C1"]["built_by"] == stage["B1"]["path"]
    assert stage["C2"]["built_by"] == stage["B2"]["path"]
    assert stage["B1"]["path"] != stage["B2"]["path"]
    assert Path(stage["B1"]["path"]).exists() and Path(stage["B2"]["path"]).exists()


def test_stage2_is_built_from_c1_and_runs(stage2, tmp_path):
    source = tmp_path / "hello.mx"
    source.write_text('fn main() -> int { println("ok"); return 0; }\n', encoding="utf-8")
    assert assert_matches_python(stage2, [source], tmp_path) == "ok"


def test_output_prints_sizes_hashes_and_results(stages):
    out = stages["stdout"]
    summary = stages["summary"]["stages"]
    for name in ("C0", "C1", "C2"):
        assert summary[name]["sha256"] in out
        assert str(summary[name]["bytes"]) in out
    assert "C1 == C2" in out and "C0 == C1" in out


def test_generated_c_has_no_build_paths_or_clock_macros(stages):
    work = str(stages["work"]).encode("utf-8")
    for name in ("C0", "C1", "C2"):
        data = stage_c(stages, name)
        for needle in (work, work.replace(b"\\", b"\\\\"), str(ROOT).encode("utf-8"),
                       str(ROOT).encode("utf-8").replace(b"\\", b"/"),
                       b"__DATE__", b"__TIME__"):
            assert needle not in data, (name, needle)


def test_stage2_binary_has_a_real_compiler_driver(stage2):
    code, _, stderr = run_stage(stage2)
    assert code == 2
    assert b"usage" in stderr


# ----- failure must be loud -----


def test_compare_reports_first_differing_byte(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"line one\nline two\nline three\n")
    b.write_bytes(b"line one\nline TWO\nline three\n")
    with pytest.raises(bootstrap.StageMismatch) as caught:
        bootstrap.assert_identical(a, b, "C1", "C2")
    text = str(caught.value)
    assert "C1" in text and "C2" in text
    assert "byte 14" in text
    assert "line 2" in text


def test_compare_passes_only_equal_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"same\n")
    b.write_bytes(b"same\n")
    bootstrap.assert_identical(a, b, "C1", "C2")
    b.write_bytes(b"same")
    with pytest.raises(bootstrap.StageMismatch):
        bootstrap.assert_identical(a, b, "C1", "C2")


def copy_compiler_tree(destination, crlf=False):
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "mortc.py", destination / "mortc.py")
    shutil.copytree(ROOT / "mort", destination / "mort",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "std", destination / "std")
    (destination / "selfhost").mkdir()
    for path in HERE.glob("*.mx"):
        target = destination / "selfhost" / path.name
        data = path.read_bytes()
        if crlf:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        target.write_bytes(data)
    shutil.copy2(HERE / "bootstrap.py", destination / "selfhost" / "bootstrap.py")


def test_planted_generator_bug_fails_the_python_comparison(tmp_path):
    """A compiler that emits a different byte cannot reach C0 == C1."""
    tree = tmp_path / "tree"
    copy_compiler_tree(tree)
    target = tree / "selfhost" / "codegen.mx"
    text = target.read_bytes()
    needle = b'#include <stdint.h>'
    assert needle in text
    target.write_bytes(text.replace(needle, b'#include <stdint.h> ', 1))
    result, data = run_bootstrap(tmp_path / "work", "--opt", "0", root=tree)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "C0 and C1 differ" in output, output
    assert "first differing byte" in output
    assert "line" in output
    assert data is None or data["python_equal"] != "identical"


# ----- determinism -----


def hostile_env(tmp_path):
    env = dict(os.environ)
    env.update({
        "PYTHONHASHSEED": "12345",
        "SOURCE_DATE_EPOCH": "99999",
        "LANG": "C",
        "LC_ALL": "tr_TR.UTF-8",
        "TZ": "Asia/Tokyo",
        "MORT_CC": "this-compiler-does-not-exist",
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "PATH": env.get("PATH", "") + os.pathsep + str(tmp_path),
    })
    return env


def test_stage2_output_does_not_depend_on_cwd_env_or_invocation(stages, stage2, tmp_path):
    expected = stage_c(stages, "C2")
    # A relative path needs the sources' drive, and tmp_path can be on another one
    # on Windows; so this directory sits next to the checkout, outside the tree.
    with tempfile.TemporaryDirectory(prefix="some where else ", dir=ROOT.parent) as scratch:
        other_cwd = Path(scratch)
        relative = os.path.relpath(COMPILER_ROOT, other_cwd)
        assert not os.path.isabs(relative)
        output = other_cwd / "again.c"
        code, _, stderr = run_stage(
            stage2, "-o", output, relative, cwd=other_cwd, env=hostile_env(tmp_path))
        assert code == 0, stderr
        assert output.read_bytes() == expected


def test_compiler_sources_at_a_path_with_spaces_and_crlf_endings_give_the_same_c(
        stages, stage2, tmp_path):
    tree = tmp_path / "dir with spaces" / "Mort tree"
    copy_compiler_tree(tree, crlf=True)
    output = tmp_path / "from spaces.c"
    code, _, stderr = run_stage(stage2, "-o", output, tree / "selfhost" / "mortc_main.mx",
                                env=hostile_env(tmp_path))
    assert code == 0, stderr
    assert output.read_bytes() == stage_c(stages, "C2")


def test_full_bootstrap_from_another_place_gives_the_same_stages(stages, tmp_path):
    """Second, independent bootstrap: different directory, spaces, CRLF sources,
    environment, hash seed and working directory."""
    tree = tmp_path / "second copy" / "Mort tree"
    copy_compiler_tree(tree, crlf=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    env = hostile_env(tmp_path)
    env.pop("MORT_CC")
    result, data = run_bootstrap(tmp_path / "w d" / "stages", root=tree, env=env, cwd=cwd)
    assert result.returncode == 0, result.stdout + result.stderr
    first = stages["summary"]["stages"]
    second = data["stages"]
    for name in ("C0", "C1", "C2"):
        assert second[name]["sha256"] == first[name]["sha256"], name


# ----- the driver against Python, on import resolution -----

CHAIN = {
    "main.mx": "import a;\nfn main() -> int { return a_value(); }\n",
    "a.mx": "import b;\nfn a_value() -> int { return b_value(); }\n",
    "b.mx": "fn b_value() -> int { return 0; }\n",
}

MODULES = {
    "main.mx": "import helpers as h;\nfn main() -> int { return h.value(); }\n",
    "helpers.mx": "module m.helpers;\npub fn value() -> int { return 0; }\n",
}

IMPORT_CASES = {
    "single_file": {"main.mx": MAIN},
    "chain": CHAIN,
    "nested_directory": {
        "main.mx": "import sub.inner;\nfn main() -> int { return inner_value(); }\n",
        "sub/inner.mx": "fn inner_value() -> int { return 0; }\n",
    },
    "diamond_loads_each_file_once": {
        "main.mx": "import left;\nimport right;\nfn main() -> int { return left_v() + right_v(); }\n",
        "left.mx": "import base;\nfn left_v() -> int { return base_v(); }\n",
        "right.mx": "import base;\nfn right_v() -> int { return base_v(); }\n",
        "base.mx": "fn base_v() -> int { return 0; }\n",
    },
    "import_cycle": {
        "main.mx": "import other;\nfn main() -> int { return other_v(); }\n",
        "other.mx": "import main;\nfn other_v() -> int { return 0; }\n",
    },
    "load_order_is_import_order": {
        "main.mx": "import z;\nimport y;\nfn main() -> int { return z_v() + y_v(); }\n",
        "z.mx": "fn z_v() -> int { return 0; }\n",
        "y.mx": "fn y_v() -> int { return 0; }\n",
    },
    "module_alias_call": MODULES,
    "module_default_alias": {
        "main.mx": "import helpers;\nfn main() -> int { return helpers.value(); }\n",
        "helpers.mx": "module m.helpers;\npub fn value() -> int { return 0; }\n",
    },
    "missing_module": {"main.mx": "import nowhere;\n" + MAIN},
    "missing_nested_module": {"main.mx": "import a.b.c;\n" + MAIN},
    "error_in_imported_file": {
        "main.mx": "import bad;\n" + MAIN,
        "bad.mx": "\n\nfn broken( {\n",
    },
    "type_error_in_imported_file": {
        "main.mx": "import bad;\n" + MAIN,
        "bad.mx": "fn f() -> int {\n    let x: u8 = 1000;\n    return x as int;\n}\n",
    },
    "invalid_utf8_in_imported_file": {
        "main.mx": "import bad;\n" + MAIN,
        "bad.mx": b"fn f() { }\n\xc3\x28 // bad\n",
    },
    "invalid_utf8_truncated_sequence": {
        "main.mx": b"fn main() -> int { return 0; }\n// \xe2\x82",
    },
    "lex_error_in_imported_file": {
        "main.mx": "import bad;\n" + MAIN,
        "bad.mx": "fn f() { let a = `; }\n",
    },
    "alias_conflict": {
        "main.mx": "import a as x;\nimport b as x;\n" + MAIN,
        "a.mx": "module m.a;\npub fn f() -> int { return 0; }\n",
        "b.mx": "module m.b;\npub fn f() -> int { return 0; }\n",
    },
    "alias_of_plain_file": {
        "main.mx": "import plain as p;\n" + MAIN,
        "plain.mx": "fn f() -> int { return 0; }\n",
    },
    "std_import_needs_module_segment": {"main.mx": "import std;\n" + MAIN},
    "std_import_three_segments": {"main.mx": "import std.a.b;\n" + MAIN},
    "std_import_missing_module": {"main.mx": "import std.no_such_module_here;\n" + MAIN},
    "std_import_used": {
        "main.mx": "import std.map;\n" + MAIN,
    },
    "duplicate_function_across_files": {
        "main.mx": "import other;\n" + MAIN,
        "other.mx": "fn main() -> int { return 1; }\n",
    },
    "diagnostic_on_last_line_without_newline": {"main.mx": "fn main() -> int { return x; }"},
    "diagnostic_excerpt_with_crlf": {"main.mx": b"fn main() -> int {\r\n  return x;\r\n}\r\n"},
    "diagnostic_excerpt_with_tab_and_unicode": {
        "main.mx": "fn main() -> int {\n\tlet é = 1;\n\treturn é + y;\n}\n",
    },
    "error_with_no_column": {"main.mx": "fn f() { }\n"},
    "empty_file": {"main.mx": ""},
}


@pytest.mark.parametrize("name", sorted(IMPORT_CASES))
def test_import_resolution_matches_python(stage2, tmp_path, name):
    write_tree(tmp_path, IMPORT_CASES[name])
    # Gate off: nested, module and std imports are outside the subset, and their
    # resolution must still equal Python's byte for byte.
    assert_matches_python(stage2, [tmp_path / "main.mx"], tmp_path, gate=False)


@pytest.mark.parametrize("name, reason", [
    ("nested_directory", b"import that is not one relative segment"),
    ("module_alias_call", b"import alias"),
    ("module_default_alias", b"qualified or payload call"),
    ("std_import_used", b"import that is not one relative segment"),
])
def test_gate_rejects_imports_outside_the_subset(stage2, tmp_path, name, reason):
    write_tree(tmp_path, IMPORT_CASES[name])
    assert python_outcome([tmp_path / "main.mx"])[0] == "ok"
    code, stderr, produced = stage2_outcome(stage2, [tmp_path / "main.mx"], tmp_path)
    assert code == 1 and produced is None, stderr
    assert reason in stderr and GATE_TEXT.encode("utf-8") in stderr, stderr
    assert b".mx:" in stderr, stderr


def test_import_cases_exercise_both_outcomes(stage2, tmp_path):
    outcomes = collections.Counter()
    for index, (name, tree) in enumerate(sorted(IMPORT_CASES.items())):
        directory = tmp_path / f"case{index}"
        directory.mkdir()
        write_tree(directory, tree)
        outcomes[python_outcome([directory / "main.mx"])[0]] += 1
    assert outcomes["ok"] >= 8 and outcomes["reject"] >= 12, outcomes


def test_two_roots_load_in_argument_order(stage2, tmp_path):
    write_tree(tmp_path, {
        "first.mx": "fn first_v() -> int { return 0; }\n",
        "main.mx": "fn main() -> int { return first_v(); }\n",
    })
    assert assert_matches_python(
        stage2, [tmp_path / "first.mx", tmp_path / "main.mx"], tmp_path) == "ok"


def test_same_root_twice_loads_once(stage2, tmp_path):
    write_tree(tmp_path, {"main.mx": MAIN})
    assert assert_matches_python(
        stage2, [tmp_path / "main.mx", tmp_path / "main.mx"], tmp_path) == "ok"


def test_relative_root_paths_resolve_against_the_working_directory(
        stage2, tmp_path, monkeypatch):
    write_tree(tmp_path, {
        "project/main.mx": "import lib.value;\nfn main() -> int { return v(); }\n",
        "project/lib/value.mx": "fn v() -> int { return y; }\n",
        "project/other/.keep": "",
    })
    monkeypatch.chdir(tmp_path / "project" / "other")
    code_files = ["../main.mx", "./.././main.mx", "..//lib/../main.mx"]
    if os.name == "nt":
        code_files.append("..\\main.mx")
    for relative in code_files:
        assert_matches_python(stage2, [relative], tmp_path,
                              cwd=tmp_path / "project" / "other")


@pytest.mark.skipif(os.name != "nt", reason="Windows path rules")
def test_windows_case_insensitive_paths_load_once(stage2, tmp_path):
    write_tree(tmp_path, {
        "main.mx": "import Helper;\nimport helper;\nfn main() -> int { return helper_v(); }\n",
        "helper.mx": "fn helper_v() -> int { return 0; }\n",
    })
    assert assert_matches_python(stage2, [tmp_path / "main.mx"], tmp_path) == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Windows path rules")
def test_windows_mixed_separators_in_the_diagnostic_path(stage2, tmp_path):
    write_tree(tmp_path, {"main.mx": "fn main() -> int { return x; }\n"})
    mixed = str(tmp_path / "main.mx").replace("\\", "/")
    kind, expected = python_outcome([mixed])
    assert kind == "reject"
    code, stderr, _ = stage2_outcome(stage2, [mixed], tmp_path)
    assert code == 1
    assert stderr == expected


def test_paths_with_spaces(stage2, tmp_path):
    write_tree(tmp_path, {
        "with space/main.mx": "import sub dir;\n" + MAIN,
    })
    # A space cannot be part of a module name, so this is a parse error in a
    # file whose path has a space: the path must survive into the diagnostic.
    assert assert_matches_python(stage2, [tmp_path / "with space" / "main.mx"], tmp_path) == "reject"


def import_chain(directory, depth):
    tree = {f"m{i}.mx": f"import m{i + 1};\nfn f{i}() -> int {{ return f{i + 1}(); }}\n"
            for i in range(depth)}
    tree[f"m{depth}.mx"] = f"fn f{depth}() -> int {{ return 0; }}\n"
    tree["main.mx"] = "import m0;\nfn main() -> int { return f0(); }\n"
    write_tree(directory, tree)
    return directory / "main.mx"


def test_import_depth_limit_matches_python(stage2, tmp_path):
    root = import_chain(tmp_path, 300)
    assert assert_matches_python(stage2, [root], tmp_path) == "ok"


def test_import_depth_boundary(stage2, tmp_path):
    """977 is the deepest chain inside the frame budget. Past it both compilers
    reject, at the same place and with the same text."""
    accepted = tmp_path / "accepted"
    assert assert_matches_python(stage2, [import_chain(accepted, 977)], tmp_path) == "ok"
    for depth in (978, 1100):
        root = import_chain(tmp_path / f"rejected{depth}", depth)
        assert assert_matches_python(stage2, [root], tmp_path) == "reject"
        assert b"exceeds the compiler safety limit" in python_outcome([root])[1]


# ----- command line -----


def test_no_arguments_is_a_usage_error(stage2):
    code, _, stderr = run_stage(stage2)
    assert code == 2 and b"usage: mortc_main" in stderr


def test_unknown_option_is_a_usage_error(stage2, tmp_path):
    source = tmp_path / "main.mx"
    source.write_text(MAIN, encoding="utf-8")
    code, _, stderr = run_stage(stage2, "--definitely-not-an-option", source)
    assert code == 2 and b"unknown option" in stderr


def test_missing_output_path_is_a_usage_error(stage2, tmp_path):
    code, _, stderr = run_stage(stage2, "-o")
    assert code == 2 and b"-o" in stderr


def test_missing_root_matches_python_wording(stage2, tmp_path):
    missing = tmp_path / "missing.mx"
    code, _, stderr = run_stage(stage2, missing)
    assert code == 1
    assert stderr == f"mortc: cannot find file {str(missing)!r}".encode("utf-8")


def test_unwritable_output_is_reported(stage2, tmp_path):
    source = tmp_path / "main.mx"
    source.write_text(MAIN, encoding="utf-8")
    code, _, stderr = run_stage(stage2, "-o", tmp_path / "no" / "such" / "dir" / "o.c", source)
    assert code == 2 and b"cannot write" in stderr


def test_check_mode_reports_like_python_and_writes_nothing(stage2, tmp_path):
    source = tmp_path / "main.mx"
    source.write_text(MAIN, encoding="utf-8")
    code, stdout, stderr = run_stage(stage2, "--check", source, cwd=tmp_path)
    assert code == 0, stderr
    assert stdout.replace(b"\r\n", b"\n") == b"mortc: check passed\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["main.mx"]


def test_check_mode_rejects_like_python(stage2, tmp_path):
    source = tmp_path / "main.mx"
    source.write_text("fn main() -> int { return nope; }\n", encoding="utf-8")
    code, _, stderr = run_stage(stage2, "--check", source)
    assert code == 1
    assert stderr == python_outcome([source])[1]


def test_freestanding_flag_matches_python(stage2, tmp_path):
    source = tmp_path / "boot.mx"
    source.write_text("fn entry() -> i64 { return 1; }\n", encoding="utf-8")
    assert assert_matches_python(stage2, [source], tmp_path, freestanding=True) == "ok"


def test_std_import_without_std_dir_is_located(stage2, tmp_path):
    source = tmp_path / "main.mx"
    source.write_text("import std.map;\n" + MAIN, encoding="utf-8")
    code, stderr, produced = stage2_outcome(stage2, [source], tmp_path, std_dir=None)
    assert code == 1 and produced is None
    assert f"{source}:1: error:".encode("utf-8") in stderr


# ----- T9: rejection and diagnostics over the corpus -----


def python_gate_problems(root):
    """check_subset's verdict on the whole import closure of a program, in a
    scratch copy, since it reads every sibling file in a directory."""
    captured = []
    original = mortc._compile_programs

    def capture(programs, *args, **kwargs):
        captured.extend(p.filename for p in programs)
        return original(programs, *args, **kwargs)

    mortc._compile_programs = capture
    try:
        try:
            mortc.compile_files_to_c([str(root)])
        except MortError:
            try:
                mortc.compile_files_to_c([str(root)], freestanding=True)
            except MortError:
                pass
    finally:
        mortc._compile_programs = original
    return [Path(name) for name in dict.fromkeys(captured)]


def gate_verdict(files, tmp_path):
    """True when every file of the closure passes check_subset, in a scratch
    directory holding only the closure."""
    common = Path(os.path.commonpath([str(path.parent) for path in files]))
    scratch = tmp_path / "gate"
    if scratch.exists():
        shutil.rmtree(scratch)
    copies = []
    for path in files:
        target = scratch / path.relative_to(common)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
        copies.append(target)
    return not any(
        check_subset.check_file(copy, collections.Counter()) for copy in copies)


def corpus_outcome(exe, path, freestanding, tmp_path, counts):
    """Compare one program with Python; the subset gate rejects what is outside."""
    try:
        kind, expected = python_outcome([path], freestanding)
    except Exception as crash:  # a known Python defect; Mort must stay clean
        code, stderr, _ = stage2_outcome(exe, [path], tmp_path, freestanding)
        assert code in (1, 2), f"{path}: python crashed ({crash!r}), stage-2 exit {code}: {stderr!r}"
        counts["python-crash"] += 1
        return
    code, stderr, produced = stage2_outcome(exe, [path], tmp_path, freestanding)
    assert code in (0, 1), f"{path}: exit {code}: {stderr!r}"
    if kind == "reject":
        assert code == 1, f"{path}: stage-2 accepted; python: {expected!r}"
        assert stderr == expected, f"{path}"
        counts["reject"] += 1
        return
    closure = python_gate_problems(path)
    if gate_verdict(closure, tmp_path):
        assert code == 0, f"{path}: stage-2 rejected an in-subset program: {stderr!r}"
        assert produced == expected, f"{path}: C differs"
        counts["accepted"] += 1
    else:
        assert code == 1, f"{path}: stage-2 accepted a program outside the subset"
        assert GATE_TEXT.encode("utf-8") in stderr, f"{path}: {stderr!r}"
        assert str(path).encode("utf-8") in stderr or b".mx:" in stderr, stderr
        counts["gate-rejected"] += 1


def test_every_repo_program_matches_python_or_the_gate(stage2, tmp_path):
    counts = collections.Counter()
    failures = []
    for path in repo_mx_files():
        for freestanding in (False, True):
            try:
                corpus_outcome(stage2, path, freestanding, tmp_path, counts)
            except AssertionError as error:
                failures.append(f"{path} (freestanding={freestanding}): {error}")
    assert not failures, "\n".join(failures[:20]) + f"\n... {len(failures)} failures"
    print(dict(counts))
    # The corpus must genuinely contain each kind of outcome.
    assert counts["reject"] >= 100, counts
    assert counts["gate-rejected"] >= 30, counts
    assert counts["accepted"] >= 10, counts


@pytest.mark.parametrize("spec", gen.default_corpus(1),
                         ids=lambda spec: spec.name)
def test_generated_subset_program_matches_python(stage2, tmp_path, spec):
    root = gen.write_program(gen.generate(spec), tmp_path / "p")
    assert assert_matches_python(stage2, [root], tmp_path) == "ok"


def test_stubbed_kernel_matches_python(stage2, tmp_path):
    root = corpus.write_stubbed_kernel(tmp_path)
    assert assert_matches_python(stage2, [root], tmp_path, freestanding=True) == "ok"


@pytest.mark.parametrize("name", sorted(SUBSET_VIOLATIONS))
def test_subset_gate_rejects_or_python_rejects_first(stage2, tmp_path, name):
    source = tmp_path / "bad.mx"
    # `module` must stay first; a main keeps Python from failing on that alone.
    source.write_text(SUBSET_VIOLATIONS[name] + "\nfn main() -> int { return 0; }\n",
                      encoding="utf-8")
    kind, expected = python_outcome([source])
    code, stderr, _ = stage2_outcome(stage2, [source], tmp_path)
    assert code == 1, stderr
    if kind == "reject":
        assert stderr == expected
    else:
        assert GATE_TEXT.encode("utf-8") in stderr, stderr
        assert stderr.startswith(f"mortc: {source}:".encode("utf-8")), stderr


@pytest.mark.parametrize("name", sorted(SUBSET_SCOPE_VIOLATIONS))
def test_subset_gate_applies_scope_rules(stage2, tmp_path, name):
    source_text, _reason = SUBSET_SCOPE_VIOLATIONS[name]
    source = tmp_path / "bad.mx"
    if "fn main" not in source_text:
        source_text += "\nfn main() -> int { return 0; }\n"
    source.write_text(source_text, encoding="utf-8")
    kind, expected = python_outcome([source])
    code, stderr, _ = stage2_outcome(stage2, [source], tmp_path)
    assert code == 1, stderr
    if kind == "reject":
        assert stderr == expected
    else:
        assert GATE_TEXT.encode("utf-8") in stderr, stderr


def test_subset_gate_accepts_the_whole_subset(stage2, tmp_path):
    source = tmp_path / "good.mx"
    source.write_text(SUBSET_ACCEPTED, encoding="utf-8")
    assert assert_matches_python(stage2, [source], tmp_path) == "ok"


@pytest.mark.parametrize("name", sorted(SUBSET_SCOPE_ACCEPTED))
def test_subset_gate_has_no_scope_false_rejects(stage2, tmp_path, name):
    source = tmp_path / "good.mx"
    source.write_text(SUBSET_SCOPE_ACCEPTED[name] + " fn main() -> int { return 0; }",
                      encoding="utf-8")
    assert assert_matches_python(stage2, [source], tmp_path) == "ok"


def test_gate_builtin_list_is_the_checker_list():
    """The gate's allowed builtins must stay the same as check_subset's."""
    text = (HERE / "subset.mx").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "SB_ALLOWED_BUILTINS" in line and '"' in line)
    assert set(line.split('"')[1].split()) == check_subset.ALLOWED_BUILTINS
