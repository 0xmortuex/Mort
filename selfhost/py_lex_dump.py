#!/usr/bin/env python3
"""Reference token dump: the Python lexer, in the format the Mort lexer emits.

Dump format (one line per token, LF endings, pure ASCII):

    <line>:<col> <KIND>[ <value>]

KIND is the name of the token in mort/tokens.py. Value by kind:

    INT     canonical decimal, arbitrary precision
    CHAR    decimal byte value
    FLOAT   16 lowercase hex digits: the IEEE-754 binary64 bit pattern
    EOF     no value and no separator
    others  the escaped UTF-8 bytes of the token text

Escaping is bytewise: 0x20..0x7E stay literal except backslash, which becomes
two backslashes; every other byte becomes \\xNN (lowercase hex).

Lines and columns are 1-based and count Unicode code points, as the Python
lexer does.

Integer literals are limited to 4300 digits in any base (leading zeros count,
the prefix and `_` separators do not). See PLAN.md section 7.

A lexical error stops the dump after the tokens already produced, with:

    ERROR <line>:<col> <escaped message>

Source that is not valid UTF-8 has no tokens and the single line

    ERROR 0:0 source is not valid UTF-8

The Python driver has no diagnostic for that case, so this line is defined here.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mort.errors import MortError  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.tokens import T  # noqa: E402

# The language promises arbitrary-precision integer literals. CPython caps
# int<->str conversion at 4300 digits unless told otherwise.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

INVALID_UTF8 = "ERROR 0:0 source is not valid UTF-8\n"

MAX_INTEGER_DIGITS = 4300
_PREFIX_KINDS = {"0x": "hex", "0b": "binary", "0o": "octal"}


class ReferenceLexer(Lexer):
    """The Python lexer plus the integer literal limit from PLAN.md.

    mort/lexer.py on main has no limit (it crashes past 4300 decimal digits).
    fix/frontend-crashes adds the decimal half with this exact message. Applied
    here, after the normal checks, so the order matches that branch.
    """

    def _number(self, line, col):
        start = self.i
        count = len(self.tokens)
        super()._number(line, col)
        if len(self.tokens) == count or self.tokens[-1].type != T.INT:
            return
        spelling = self.src[start:self.i].replace("_", "")
        kind = _PREFIX_KINDS.get(spelling[:2].lower())
        digits = spelling[2:] if kind else spelling
        if len(digits) > MAX_INTEGER_DIGITS:
            self.tokens.pop()
            raise MortError(
                f"{kind or 'decimal'} integer literal has {len(digits)} digits; "
                f"the limit is {MAX_INTEGER_DIGITS}",
                line, col)


def escape(raw):
    out = []
    for byte in raw:
        if byte == 0x5C:
            out.append("\\\\")
        elif 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def format_token(token):
    head = f"{token.line}:{token.col} {token.type.name}"
    if token.type == T.EOF:
        return head
    if token.type in (T.INT, T.CHAR):
        return f"{head} {token.value}"
    if token.type == T.FLOAT:
        return f"{head} {struct.pack('>d', token.value).hex()}"
    return f"{head} {escape(token.value.encode('utf-8'))}"


def dump_source(source):
    """Dump already-decoded source text."""
    lexer = ReferenceLexer(source)
    error = None
    try:
        lexer.tokenize()
    except MortError as caught:
        error = caught
    lines = [format_token(token) for token in lexer.tokens]
    if error is not None:
        message = escape(error.msg.encode("utf-8"))
        lines.append(f"ERROR {error.line}:{error.col} {message}")
    return "".join(line + "\n" for line in lines)


def dump_file(path):
    """Read a file the way mortc.py does and dump it."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except UnicodeDecodeError:
        return INVALID_UTF8
    return dump_source(source)


def main(argv):
    if len(argv) != 2:
        print("usage: py_lex_dump.py FILE", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(dump_file(argv[1]).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
