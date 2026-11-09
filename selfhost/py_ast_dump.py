#!/usr/bin/env python3
"""Reference AST dump: the Python parser, in the format the Mort parser emits.

Dump format (one line per node, pre-order, LF endings, pure ASCII):

    <Kind>[ <field>=<value>]*

Kind is the Python class name, and fields appear in constructor order, taken
reflectively from ``vars(node)``. Tuples, which only occur as StructLit fields,
are the pseudo-kind ``Tuple`` with fields ``0`` and ``1``. Values:

    "text"      the string's UTF-8 bytes; bytes outside 0x20..0x7E, and both
                backslash and double quote, are written \\xNN (lowercase hex)
    #123        an integer, in decimal, arbitrary precision
    12:5        a (line, column) source position
    f<16 hex>   a float's IEEE-754 binary64 bit pattern
    true false  a bool
    none        None
    @           one child node
    [N          a list of N child nodes
    ["a","b"]   a non-empty list of strings

An empty list is always ``[0``. Children follow their parent as further lines,
in field order, with no end markers: the counts make the format unambiguous.
There is no indentation, so deeply nested trees stay linear in size.

Only the fields a parse can set are dumped. ``CHECKER_FIELDS`` lists what later
phases fill in; each is asserted to still hold its initial value, so a parser
change that sets one fails here instead of going unnoticed.

Positions are compared wherever the Python AST records them: ``line`` everywhere,
``col`` on every node class (``none`` where the parser does not set it),
``Call.paren_col`` and ``Call.name_pos``. Diagnostics carry the same line and
column as ``MortError.format()``.

Diagnostics: on bad input there is no dump, only the text of the error, which
must equal what the Mort parser writes to stderr: ``MortError.format()`` with
the file name set.

Depth: both parsers apply the frame budget of mort/depth.py. This reference
runs the unmodified Python parser under the real budget, from the same frames
`mortc.compile_files_to_c` calls it from, and the Mort parser counts the frames
the Python one would be using.
"""
import functools
import re
import struct
import sys
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import py_lex_dump  # noqa: E402,F401  (lifts the int-string digit limit)
from mort import mort_ast as A  # noqa: E402
from mort.errors import MortError  # noqa: E402
from mort.depth import with_frame_budget  # noqa: E402
from mort.lexer import Lexer, decode_source  # noqa: E402
from mort.parser import Parser  # noqa: E402


NESTING_MESSAGE = "source nesting exceeds the compiler safety limit"


@functools.cache
def frame_budget():
    """The Mort compiler's frame budget, so the two can never drift apart."""
    text = (HERE / "ast.mx").read_text(encoding="ascii")
    return int(re.search(r"const COMPILE_FRAME_BUDGET: u64 = (\d+);", text).group(1))


BASE_NODE_FIELDS = {"type": None, "is_lit": False, "const_val": None}

# Field -> initial value, per class. "=name" means equal to the name field.
CHECKER_FIELDS = {
    "Program": {"import_aliases": {}},
    "ImportDecl": {"resolved_path": None},
    "StructDecl": {"destructor_symbol": None},
    "FnDecl": {"module": None, "symbol_name": "=name", "import_aliases": {}},
    "TestDecl": {"module": None, "import_aliases": {}},
    "Let": {"var_type": None},
    "For": {"var_type": None},
    "MatchArm": {
        "variant_name": None, "binding_name": None, "binding_type": None,
        "binding_names": [], "binding_types": [], "binding_indices": [],
        "payload_arity": 0, "binding_destructors": [],
    },
    "Match": {"exhaustive": False},
    "Var": {"resolved_function": None},
    "Binary": {"lowered_name": None},
    "Unary": {"is_cast_operand": False},
    "Call": {
        "resolved_name": None, "enum_name": None, "enum_variant": None,
        "enum_payload_type": None, "indirect": False,
    },
    "Try": {
        "result_type": None, "error_type": None, "return_type": None,
        "temp_name": None,
    },
    "Move": {"binding_name": None},
    "FieldAccess": {"resolved_function": None},
    "Index": {"obj_temp_name": None},
}


def escape(text):
    out = []
    for byte in text.encode("utf-8"):
        if byte in (0x5C, 0x22) or not 0x20 <= byte <= 0x7E:
            out.append(f"\\x{byte:02x}")
        else:
            out.append(chr(byte))
    return "".join(out)


def quoted(text):
    return f'"{escape(text)}"'


def is_ast_object(value):
    return isinstance(value, A.Node) or type(value).__module__ == A.__name__


def format_field(value, children):
    """Render one field, appending any child objects to `children`."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"#{value}"
    if isinstance(value, float):
        return "f" + struct.pack(">d", value).hex()
    if isinstance(value, str):
        return quoted(value)
    if isinstance(value, tuple):
        line, column = value
        return f"{line}:{column}"
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            return "[" + ",".join(quoted(item) for item in value) + "]"
        children.extend(value)
        return f"[{len(value)}"
    if is_ast_object(value):
        children.append(value)
        return "@"
    raise TypeError(f"cannot dump {type(value).__name__}: {value!r}")


def check_pristine(node, field, expected):
    value = getattr(node, field)
    if expected == "=name":
        expected = node.name
    if value != expected:
        raise AssertionError(
            f"{type(node).__name__}.{field} was set by the parser: {value!r}; "
            "dump it instead of treating it as a checker field")


def describe(item):
    """Return (line, children) for one AST object or StructLit field tuple."""
    children = []
    if isinstance(item, tuple):
        fields = [(str(index), value) for index, value in enumerate(item)]
        name = "Tuple"
    else:
        name = type(item).__name__
        skipped = dict(CHECKER_FIELDS.get(name, {}))
        if isinstance(item, A.Node):
            skipped.update(BASE_NODE_FIELDS)
        fields = []
        for field, value in vars(item).items():
            if field in skipped:
                check_pristine(item, field, skipped[field])
            else:
                fields.append((field, value))
    parts = [name]
    for field, value in fields:
        parts.append(f"{field}={format_field(value, children)}")
    return " ".join(parts) + "\n", children


def dump_program(program):
    """Iterative: operator chains make left-deep trees of any depth."""
    out = []
    stack = [program]
    while stack:
        line, children = describe(stack.pop())
        out.append(line)
        stack.extend(reversed(children))
    return "".join(out)


# Before Python 3.11 every Python frame also uses C stack, so the reference
# parses on a thread with room.
REFERENCE_STACK_BYTES = 128 * 1024 * 1024


def _on_large_stack(work):
    """Run work() on a thread with a large C stack, re-raising what it raises."""
    outcome = []

    def run():
        try:
            outcome.append((work(), None))
        except (MortError, RecursionError) as error:
            outcome.append((None, error))

    saved_size = threading.stack_size(REFERENCE_STACK_BYTES)
    try:
        thread = threading.Thread(target=run)
        thread.start()
    finally:
        threading.stack_size(saved_size)
    thread.join()
    result, error = outcome[0]
    if error is not None:
        raise error
    return result


def _parsed_as_source_file(parser):
    """`_parse_source`: the nesting error, at the token the parser had reached."""
    try:
        return parser.parse()
    except RecursionError:
        token = parser._peek()
        raise MortError(NESTING_MESSAGE, token.line, token.col) from None


def _loaded(parser):
    """`load`."""
    return _parsed_as_source_file(parser)


@with_frame_budget
def _budgeted(parser):
    """`compile_files_to_c`, so the parser starts in the frame it starts in there."""
    return _loaded(parser)


def parse_source(source):
    """Parse decoded source under the real compiler's frame budget.

    Three frames separate the budget from `Parser.parse`, as in `mortc.py`; only
    the budget decides nesting, so the real parser is the reference unmodified.
    """
    tokens = Lexer(source).tokenize()
    parser = Parser(tokens)
    return _on_large_stack(lambda: _budgeted(parser))


class Result(types.SimpleNamespace):
    """Either `dump` (text) or `diagnostic` (bytes); the other is None."""


def dump_source(source, filename):
    try:
        program = parse_source(source)
    except MortError as error:
        error.filename = filename
        return Result(dump=None, diagnostic=error.format().encode("utf-8"))
    return Result(dump=dump_program(program), diagnostic=None)


def dump_file(path):
    """Read a file the way mortc.py does and dump it."""
    filename = str(path)
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        source = decode_source(data, filename)
    except MortError as error:
        return Result(dump=None, diagnostic=error.format().encode("utf-8"))
    return dump_source(source, filename)


def main(argv):
    if len(argv) != 2:
        print("usage: py_ast_dump.py FILE", file=sys.stderr)
        return 2
    result = dump_file(argv[1])
    if result.dump is None:
        sys.stderr.buffer.write(result.diagnostic + b"\n")
        return 1
    sys.stdout.buffer.write(result.dump.encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
