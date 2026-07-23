"""Lexer: turns Mort source text into a flat list of tokens."""
import math

from .tokens import Token, T, KEYWORDS
from .errors import MortError

_TWO_CHAR = {
    "->": T.ARROW,
    "=>": T.FAT_ARROW,
    "..": T.DOTDOT,
    "==": T.EQ,
    "!=": T.NE,
    "<=": T.LE,
    ">=": T.GE,
    "&&": T.AND,
    "||": T.OR,
    "<<": T.SHL,
    ">>": T.SHR,
    "+=": T.PLUS_ASSIGN,
    "-=": T.MINUS_ASSIGN,
    "*=": T.STAR_ASSIGN,
    "/=": T.SLASH_ASSIGN,
    "%=": T.PERCENT_ASSIGN,
    "&=": T.AMP_ASSIGN,
    "|=": T.PIPE_ASSIGN,
    "^=": T.CARET_ASSIGN,
}

_THREE_CHAR = {
    "<<=": T.SHL_ASSIGN,
    ">>=": T.SHR_ASSIGN,
    "..=": T.DOTDOTEQ,
}

_ONE_CHAR = {
    "(": T.LPAREN,
    ")": T.RPAREN,
    "{": T.LBRACE,
    "}": T.RBRACE,
    ",": T.COMMA,
    ";": T.SEMI,
    ":": T.COLON,
    ".": T.DOT,
    "&": T.AMP,
    "[": T.LBRACKET,
    "]": T.RBRACKET,
    "=": T.ASSIGN,
    "+": T.PLUS,
    "-": T.MINUS,
    "*": T.STAR,
    "/": T.SLASH,
    "%": T.PERCENT,
    "<": T.LT,
    ">": T.GT,
    "!": T.BANG,
    "|": T.PIPE,
    "^": T.CARET,
    "~": T.TILDE,
}


class Lexer:
    def __init__(self, src):
        self.src = src
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens = []

    def _peek(self, offset=0):
        j = self.i + offset
        return self.src[j] if j < len(self.src) else "\0"

    def _advance(self):
        c = self.src[self.i]
        self.i += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def _add(self, type, value, line, col):
        self.tokens.append(Token(type, value, line, col))

    def tokenize(self):
        while self.i < len(self.src):
            c = self._peek()
            if c in " \t\r\n":
                self._advance()
                continue
            # line comment
            if c == "/" and self._peek(1) == "/":
                while self.i < len(self.src) and self._peek() != "\n":
                    self._advance()
                continue
            if c == "/" and self._peek(1) == "*":
                self._block_comment()
                continue
            line, col = self.line, self.col
            if c == '"':
                self._string(line, col)
            elif c == "'":
                self._char(line, col)
            elif c.isdigit():
                self._number(line, col)
            elif c.isalpha() or c == "_":
                self._ident(line, col)
            else:
                self._symbol(line, col)
        self._add(T.EOF, None, self.line, self.col)
        return self.tokens

    def _number(self, line, col):
        # A decimal immediately after member-access punctuation is a tuple
        # index, not the start of a floating-point literal.  Keeping this
        # decision in the lexer lets chained access such as ``matrix.0.1``
        # become DOT, INT, DOT, INT instead of DOT, FLOAT(0.1).
        if self.tokens and self.tokens[-1].type == T.DOT:
            digits = ""
            while self._peek().isdigit():
                digits += self._advance()
            if self._peek().isalpha() or self._peek() == "_":
                raise MortError("tuple index must be a decimal integer", line, col)
            self._add(T.INT, int(digits), line, col)
            return
        # hex literal, e.g. 0xB8000
        if self._peek() == "0" and self._peek(1) in "xX":
            self._advance()
            self._advance()
            s = ""
            while self._peek() in "0123456789abcdefABCDEF_":
                s += self._advance()
            if not s or s.startswith("_") or s.endswith("_") or "__" in s:
                raise MortError("invalid hex literal '0x'", line, col)
            if self._peek().isalnum() or self._peek() == "_":
                raise MortError("invalid hex literal", line, col)
            self._add(T.INT, int(s.replace("_", ""), 16), line, col)
            return
        if self._peek() == "0" and self._peek(1) in "bBoO":
            prefix = self._peek(1).lower()
            self._advance()
            self._advance()
            allowed = "01_" if prefix == "b" else "01234567_"
            digits = ""
            while self._peek() in allowed:
                digits += self._advance()
            if (not digits or digits.startswith("_") or digits.endswith("_")
                    or "__" in digits or self._peek().isalnum() or self._peek() == "_"):
                kind = "binary" if prefix == "b" else "octal"
                raise MortError(f"invalid {kind} literal", line, col)
            self._add(T.INT, int(digits.replace("_", ""), 2 if prefix == "b" else 8), line, col)
            return
        s = ""
        while self._peek().isdigit() or self._peek() == "_":
            s += self._advance()
        if s.startswith("_") or s.endswith("_") or "__" in s:
            raise MortError("invalid numeric separator", line, col)
        if self._peek() == "." and self._peek(1).isdigit():
            s += self._advance()
            while self._peek().isdigit():
                s += self._advance()
            if self._peek() in "eE":
                s += self._advance()
                if self._peek() in "+-":
                    s += self._advance()
                if not self._peek().isdigit():
                    raise MortError("invalid floating-point exponent", line, col)
                while self._peek().isdigit():
                    s += self._advance()
            if self._peek().isalnum() or self._peek() == "_":
                raise MortError("invalid floating-point literal", line, col)
            value = float(s.replace("_", ""))
            if not math.isfinite(value):
                raise MortError("floating-point literal is out of range", line, col)
            self._add(T.FLOAT, value, line, col)
            return
        if self._peek() in "eE":
            s += self._advance()
            if self._peek() in "+-":
                s += self._advance()
            if not self._peek().isdigit():
                raise MortError("invalid floating-point exponent", line, col)
            while self._peek().isdigit():
                s += self._advance()
            if self._peek().isalnum() or self._peek() == "_":
                raise MortError("invalid floating-point literal", line, col)
            value = float(s.replace("_", ""))
            if not math.isfinite(value):
                raise MortError("floating-point literal is out of range", line, col)
            self._add(T.FLOAT, value, line, col)
            return
        # reject things like 12abc
        if self._peek().isalpha() or self._peek() == "_":
            raise MortError(f"invalid number literal near {s + self._peek()!r}", line, col)
        self._add(T.INT, int(s.replace("_", "")), line, col)

    def _block_comment(self):
        line, col = self.line, self.col
        self._advance()
        self._advance()
        depth = 1
        while depth:
            if self.i >= len(self.src):
                raise MortError("unterminated block comment", line, col)
            if self._peek() == "/" and self._peek(1) == "*":
                self._advance()
                self._advance()
                depth += 1
            elif self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                depth -= 1
            else:
                self._advance()

    def _char(self, line, col):
        self._advance()
        if self._peek() in ("\0", "\n", "'"):
            raise MortError("empty or unterminated character literal", line, col)
        if self._peek() == "\\":
            self._advance()
            escape = self._advance()
            escapes = {
                "n": 10, "r": 13, "t": 9, "0": 0,
                "\\": 92, "'": 39, '"': 34,
            }
            if escape == "x":
                if (self._peek() not in "0123456789abcdefABCDEF"
                        or self._peek(1) not in "0123456789abcdefABCDEF"):
                    raise MortError("invalid hexadecimal character escape", line, col)
                digits = self._advance() + self._advance()
                value = int(digits, 16)
            elif escape in escapes:
                value = escapes[escape]
            else:
                raise MortError(f"unknown character escape \\{escape}", line, col)
        else:
            value = ord(self._advance())
        if self._peek() != "'":
            raise MortError("character literal must contain exactly one byte", line, col)
        self._advance()
        if value > 255:
            raise MortError("character literal is outside the u8 range", line, col)
        self._add(T.CHAR, value, line, col)

    def _string(self, line, col):
        # Capture the raw inner text (escape sequences kept verbatim) so it can
        # be re-emitted straight into a C string literal — handy for asm().
        self._advance()  # opening quote
        raw = ""
        while True:
            c = self._peek()
            if c == "\0" or c == "\n":
                raise MortError("unterminated string literal", line, col)
            if c == "\\":  # keep the escape pair as-is (e.g. \n, \", \\)
                raw += self._advance()
                raw += self._advance()
                continue
            if c == '"':
                self._advance()
                break
            raw += self._advance()
        self._add(T.STRING, raw, line, col)

    def _ident(self, line, col):
        s = ""
        while self._peek().isalnum() or self._peek() == "_":
            s += self._advance()
        self._add(KEYWORDS.get(s, T.IDENT), s, line, col)

    def _symbol(self, line, col):
        c = self._advance()
        three = c + self._peek() + self._peek(1)
        if three in _THREE_CHAR:
            self._advance()
            self._advance()
            self._add(_THREE_CHAR[three], three, line, col)
            return
        two = c + self._peek()
        if two in _TWO_CHAR:
            self._advance()
            self._add(_TWO_CHAR[two], two, line, col)
            return
        if c in _ONE_CHAR:
            self._add(_ONE_CHAR[c], c, line, col)
            return
        raise MortError(f"unexpected character {c!r}", line, col)
