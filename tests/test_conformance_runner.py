"""The conformance runner must accept compiler paths containing spaces."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "conformance_run", ROOT / "conformance" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_script_path_with_spaces_is_one_argument(tmp_path):
    script = tmp_path / "dir with spaces" / "mortc.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    assert _load_runner()._command(str(script)) == [sys.executable, str(script)]


def test_executable_path_with_spaces_is_one_argument(tmp_path):
    executable = tmp_path / "dir with spaces" / "mortc"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    assert _load_runner()._command(str(executable)) == [str(executable)]


def test_default_compiler_runs_from_the_real_checkout():
    runner = _load_runner()
    command = runner._command(str(ROOT / "mortc.py"))
    assert command == [sys.executable, str(ROOT / "mortc.py")]
    assert runner._run([*command, "--language-version"]).returncode == 0


def test_quoted_command_with_arguments_still_splits():
    expected = ["mortc-wrapper", "--flag"]
    assert _load_runner()._command("mortc-wrapper --flag") == expected
