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

Python's AST records lines, and a column only in ``Call.name_pos``, so ``line``
and that field are the only positions dumped. Diagnostics carry a column only
where the Python front end gives one (the lexer, at this commit).

Diagnostics: on bad input there is no dump, only the text of the error, which
must equal what the Mort parser writes to stderr: ``MortError.format()`` with
the file name set.

Depth: the Mort parser has an explicit limit, while Python's limit is a side
effect of its frame count and differs from version to version. So this reference
applies the same limit by counting entries to the same five recursive parser
methods (GuardedParser). Parsing is otherwise the unmodified Python parser.
"""
import functools
import re
import struct
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import py_lex_dump  # noqa: E402,F401  (lifts the int-string digit limit)
from mort import mort_ast as A  # noqa: E402
from mort.errors import MortError  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.parser import Parser  # noqa: E402


NESTING_MESSAGE = "source nesting exceeds the compiler safety limit"
INVALID_UTF8_MESSAGE = "source is not valid UTF-8"


@functools.cache
def max_nesting():
    """The Mort parser's limit, so that the two can never drift apart."""
    text = (HERE / "parser.mx").read_text(encoding="ascii")
    return int(re.search(r"const MAX_NESTING: u64 = (\d+);", text).group(1))


BASE_NODE_FIELDS = {"type": None, "is_lit": False, "const_val": None}

# Parser-set position fields the Mort AST does not carry yet. Columns are not
# dumped (see the module docstring); T5 needs Call.name_pos for the volatile
# builtins' diagnostics and must add it to the Mort parser and to this dump.
NOT_DUMPED = {"Call": {"name_pos"}}

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
            if field in NOT_DUMPED.get(name, ()):
                continue
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


class GuardedParser(Parser):
    """The Python parser plus the Mort parser's explicit nesting limit."""

    def __init__(self, tokens, limit):
        super().__init__(tokens)
        self._nesting = 0
        self._limit = limit

    def _guarded(self, method):
        self._nesting += 1
        if self._nesting > self._limit:
            raise MortError(NESTING_MESSAGE, self._peek().line)
        result = method()
        self._nesting -= 1
        return result

    def _expression(self):
        return self._guarded(super()._expression)

    def _unary(self):
        return self._guarded(super()._unary)

    def _type_name(self):
        return self._guarded(super()._type_name)

    def _block(self):
        return self._guarded(super()._block)

    def _if_stmt(self):
        return self._guarded(super()._if_stmt)


def parse_source(source, limit=None):
    """Parse decoded source with the nesting limit and a large frame budget."""
    tokens = Lexer(source).tokenize()
    parser = GuardedParser(tokens, max_nesting() if limit is None else limit)
    saved = sys.getrecursionlimit()
    sys.setrecursionlimit(1_000_000)
    try:
        return parser.parse()
    finally:
        sys.setrecursionlimit(saved)


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
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except UnicodeDecodeError:
        text = f"{filename}: error: {INVALID_UTF8_MESSAGE}"
        return Result(dump=None, diagnostic=text.encode("utf-8"))
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
