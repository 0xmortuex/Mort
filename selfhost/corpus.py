"""The accepted-program corpus of selfhost/PLAN.md section 5.1, as code.

Every differential test takes its programs from here, so that the floors are
stated once. The classes that count towards the accepted-line floor:

    existing   repo programs inside the bootstrap subset (the 17 of the plan)
    kernel     kernel/kmain.mx with its port I/O stubbed
    generated  tools/gen_subset_programs.py, subset profile

Only programs the Python compiler really accepts are counted, so the conformance
`reject` cases are in the existing class but add nothing to the floor. The full
profile of the generator is outside the subset and is not counted either.
"""
import collections
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT))

import check_subset  # noqa: E402
import mortc  # noqa: E402
from mort.errors import MortError  # noqa: E402
from test_lexer_differential import repo_mx_files  # noqa: E402

# The plan's floors. Lowering one of these is a decision to record in the plan.
EXISTING_FILES = 17
MIN_KERNEL_LINES = 1500
MIN_GENERATED_PROGRAMS = 20
MIN_GENERATED_LINES = 6000
MIN_LARGE_PROGRAMS = 3
LARGE_PROGRAM_LINES = 1500
MIN_ACCEPTED_LINES = 8000
MIN_REAL_PROGRAM_LINES = 1500


def line_count(path_or_text):
    text = path_or_text
    if isinstance(path_or_text, Path):
        text = path_or_text.read_text(encoding="utf-8")
    return len(text.splitlines())


def existing_subset_files():
    """Repo programs outside selfhost/ that pass the subset gate.

    Each file is checked alone in a scratch directory, because the gate reads
    every sibling and some siblings (the reject cases) do not even parse.
    """
    found = []
    for path in repo_mx_files():
        if path.parent.name == "selfhost":
            continue
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / path.name
            shutil.copy(path, copy)
            if not check_subset.check_file(copy, collections.Counter()):
                found.append(path)
    return found


def compiles(path):
    """True when the Python compiler accepts this file, hosted or freestanding."""
    for freestanding in (False, True):
        try:
            mortc.compile_files_to_c([str(path)], freestanding=freestanding)
            return True
        except MortError:
            continue
    return False


# Every port-I/O builtin the kernel calls, with its stub. `u16` is outside the
# subset, so the port and word parameters are widened along with the kernel.
KERNEL_STUBS = """
fn stub_outb(port: u32, value: u8) { }
fn stub_outw(port: u32, value: u32) { }
fn stub_inb(port: u32) -> u8 { return 0; }
fn stub_inw(port: u32) -> u32 { return 0; }
"""


def stubbed_kernel_source():
    """kernel/kmain.mx made subset-clean; lives here so it tracks kmain.mx."""
    source = (ROOT / "kernel" / "kmain.mx").read_text(encoding="utf-8")
    for name in ("outb", "outw", "inb", "inw"):
        source = re.sub(rf"\b{name}\(", f"stub_{name}(", source)
    source = re.sub(r'asm\("[^"]*"\);[ \t]*', "", source)
    source = re.sub(r"\bu16\b", "u32", source)
    return source + KERNEL_STUBS


def write_stubbed_kernel(directory):
    path = Path(directory) / "kmain_stubbed.mx"
    path.write_bytes(stubbed_kernel_source().encode("utf-8"))
    return path
