#!/usr/bin/env python3
"""Reference for T6: the real CodeGen with expressions replaced by placeholders.

The Mort generator (selfhost/codegen.mx, cg_stmt.mx, cg_helpers.mx) writes
declarations, statements and helpers now; expression text is task T7. To compare
everything else byte for byte, ``PlaceholderCodeGen`` is the unmodified
``mort.codegen.CodeGen`` with one method replaced: ``_gen_expr`` returns the same
placeholder ``cg_expr`` writes.

Expressions also have effects the program head depends on: which helpers are
used and the string table. A second, unmodified CodeGen runs first to find them,
and they are handed to both sides (``forced_text``), so helper selection is
compared on what each program really uses. T7 drops this file's forcing: its real
expression generator produces those effects itself.

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
from mort import mort_ast as A  # noqa: E402
from mort.codegen import CodeGen  # noqa: E402
from mort.errors import MortError  # noqa: E402

FLAG_NAMES = (
    "used_inb", "used_outb", "used_inw", "used_outw", "used_inl", "used_outl",
    "used_println", "used_eprintln", "used_print", "used_print_float", "used_assert",
    "used_alloc", "used_free", "used_len", "used_bounds", "used_time", "used_file",
    "used_args", "used_threads", "used_mutexes", "used_atomics", "used_network",
    "used_secure_random", "used_tls",
)


class Effects:
    """What expressions contribute to the program head."""

    def __init__(self, flags=(), ints=(), strings=()):
        self.flags = tuple(flags)
        self.ints = tuple(ints)
        self.strings = tuple(strings)


def placeholder(node):
    text = f"${type(node).__name__}@{node.line}:{node.col or 0}:{node.type or 'none'}$"
    if isinstance(node, (A.Binary, A.Unary, A.Cast)):
        return f"({text})"
    if isinstance(node, A.Call):
        return f"({text})({text})"
    return text


class PlaceholderCodeGen(CodeGen):
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

    def _gen_expr(self, e):
        self.current_node = e
        return placeholder(e)


def real_effects(program, freestanding, test_mode):
    """Run the real generator once. Returns (Effects, error text or None)."""
    meter = program.work_meter
    spent, replayed = meter.spent, meter.replayed
    generator = None
    try:
        generator = CodeGen(program, freestanding=freestanding, test_mode=test_mode)
        generator.generate()
    except MortError as error:
        return Effects(), error.format()
    finally:
        meter.spent, meter.replayed = spent, replayed
    return Effects(
        (name for name in FLAG_NAMES if getattr(generator, name)),
        sorted(generator.used_int_helpers),
        generator.strings,
    ), None


def forced_text(effects):
    lines = [f"FLAG {name}\n" for name in effects.flags]
    lines += [f"INT {operation} {typ}\n" for operation, typ in effects.ints]
    lines += [f"STR {value.encode('utf-8').hex()}\n" for value in effects.strings]
    return "".join(lines)


def reference(path, mode):
    """The check outcome plus, for a valid program, the placeholder C.

    Returns (check Result, C text or None, forced text or None, error text from
    the placeholder generator or None).
    """
    result = py_check_dump.run_reference(path, mode, exact=True)
    if result.outcome != "ok":
        return result, None, None, None
    checker = py_check_dump.LAST[0]
    program = checker.program
    freestanding = mode == "freestanding"
    test_mode = mode == "test"
    effects, _ = real_effects(program, freestanding, test_mode)
    try:
        text = PlaceholderCodeGen(
            program, effects, freestanding=freestanding, test_mode=test_mode).generate()
    except MortError as error:
        return result, None, forced_text(effects), error.format()
    return result, text, forced_text(effects), None


def main(argv):
    if len(argv) != 3 or argv[2] not in ("hosted", "test", "freestanding"):
        print("usage: py_codegen_dump.py FILE hosted|test|freestanding", file=sys.stderr)
        return 2
    _, text, _, error = reference(Path(argv[1]).resolve(), argv[2])
    if error:
        print(error, file=sys.stderr)
        return 1
    if text is not None:
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
