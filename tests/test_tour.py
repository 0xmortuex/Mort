"""Every code block in docs/tour.md is compiled, and its output is checked."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mortc

TOUR = ROOT / "docs" / "tour.md"
FENCE = re.compile(r"^```([\w-]*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
needs_cc = pytest.mark.skipif(
    mortc.find_c_compiler() is None, reason="no C compiler on PATH")


def _blocks():
    return [(m[1], m[2]) for m in FENCE.finditer(TOUR.read_text(encoding="utf-8"))]


def _pairs(kind, answer):
    blocks = _blocks()
    return [
        (source, blocks[index + 1][1])
        for index, (language, source) in enumerate(blocks)
        if language == kind and blocks[index + 1][0] == answer
    ]


def _id(pair):
    return pair[0].strip().splitlines()[0][:40]


RUN = _pairs("mort", "output")
REJECT = _pairs("mort-reject", "error")


def test_every_block_in_the_tour_is_tested():
    blocks = _blocks()
    languages = [language for language, _ in blocks]
    assert set(languages) <= {"mort", "output", "mort-reject", "error"}, languages
    assert len(blocks) == 2 * (len(RUN) + len(REJECT))
    assert len(RUN) == languages.count("mort")
    assert len(REJECT) == languages.count("mort-reject")
    assert RUN and REJECT


@needs_cc
@pytest.mark.parametrize("source, expected", RUN, ids=[_id(p) for p in RUN])
def test_tour_program_runs(source, expected, tmp_path):
    program = tmp_path / "tour.mx"
    program.write_text(source, encoding="utf-8")
    exe = tmp_path / ("tour.exe" if os.name == "nt" else "tour")
    build = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(program), "-o", str(exe)],
        capture_output=True, text=True, timeout=120, check=False)
    assert build.returncode == 0, build.stderr
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.parametrize("source, expected", REJECT, ids=[_id(p) for p in REJECT])
def test_tour_program_is_rejected(source, expected, tmp_path):
    program = tmp_path / "tour.mx"
    program.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), str(program), "--check"],
        capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode != 0
    assert expected.strip() in result.stderr
