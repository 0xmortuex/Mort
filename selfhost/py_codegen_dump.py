#!/usr/bin/env python3
"""Reference for the Mort C generator: the real, unmodified mort.codegen.CodeGen.

``reference`` returns exactly the bytes ``CodeGen.generate()`` writes for a file.
Since T7 nothing is handed to the Mort side: the compiler driver cg_main.mx takes
no effects input and computes helper use and the string table itself.

``ForcedCodeGen`` and ``forced_text`` remain only for the test-only driver
cg_forced_main.mx. They reach helper combinations no real program can (threads in
a freestanding build), so the head text is still compared for those.

Forced file (ASCII, LF):

    FLAG used_print          a CodeGen `used_*` attribute
    INT div i64              an element of used_int_helpers
    STR 6162                 one string-table entry, as hex of its UTF-8
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import py_check_dump  # noqa: E402
from mort.codegen import CodeGen  # noqa: E402
from mort.errors import MortError  # noqa: E402

FLAG_NAMES = (
    "used_inb", "used_outb", "used_inw", "used_outw", "used_inl", "used_outl",
    "used_println", "used_eprintln", "used_print", "used_print_float", "used_assert",
    "used_alloc", "used_free", "used_len", "used_bounds", "used_time", "used_file",
    "used_args", "used_threads", "used_mutexes", "used_atomics", "used_network",
    "used_secure_random", "used_tls",
)

# Deep left spines are compared with Python, whose generator recurses on them.
RECURSION_LIMIT = 1_000_000


class Effects:
    """What the test-only driver is told to pretend expressions contributed."""

    def __init__(self, flags=(), ints=(), strings=()):
        self.flags = tuple(flags)
        self.ints = tuple(ints)
        self.strings = tuple(strings)


class ForcedCodeGen(CodeGen):
    """The real generator with extra helper use installed before the first line."""

    def __init__(self, program, effects, freestanding=False, test_mode=False):
        super().__init__(program, freestanding=freestanding, test_mode=test_mode)
        self._effects = effects

    def _emit(self, s=""):
        # Bodies and the head are all emitted after _generate_program resets its
        # state, so the first line is the moment to install the forced effects.
        if self._effects is not None:
            effects, self._effects = self._effects, None
            for name in effects.flags:
                setattr(self, name, True)
            self.used_int_helpers.update(effects.ints)
            self.strings.extend(effects.strings)
        super()._emit(s)


def forced_text(effects):
    lines = [f"FLAG {name}\n" for name in effects.flags]
    lines += [f"INT {operation} {typ}\n" for operation, typ in effects.ints]
    lines += [f"STR {value.encode('utf-8').hex()}\n" for value in effects.strings]
    return "".join(lines)


def generate(program, freestanding, test_mode):
    """(C text or None, error text or None, the generator)."""
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(RECURSION_LIMIT)
    generator = CodeGen(program, freestanding=freestanding, test_mode=test_mode)
    try:
        return generator.generate(), None, generator
    except MortError as error:
        return None, error.format(), generator
    finally:
        sys.setrecursionlimit(limit)


def reference(path, mode):
    """The check outcome plus, for a valid program, the C.

    Returns (check Result, C text or None, generator error text or None).
    """
    result = py_check_dump.run_reference(path, mode, exact=True)
    if result.outcome != "ok":
        return result, None, None
    program = py_check_dump.LAST[0].program
    text, error, _ = generate(program, mode == "freestanding", mode == "test")
    return result, text, error


def effects_of(path, mode):
    """What the real generator's expressions used, for tests that must not be vacuous."""
    result = py_check_dump.run_reference(path, mode, exact=True)
    assert result.outcome == "ok", result.error
    program = py_check_dump.LAST[0].program
    _, error, generator = generate(program, mode == "freestanding", mode == "test")
    assert error is None, error
    return generator


def main(argv):
    if len(argv) != 3 or argv[2] not in ("hosted", "test", "freestanding"):
        print("usage: py_codegen_dump.py FILE hosted|test|freestanding", file=sys.stderr)
        return 2
    _, text, error = reference(Path(argv[1]).resolve(), argv[2])
    if error:
        print(error, file=sys.stderr)
        return 1
    if text is not None:
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
